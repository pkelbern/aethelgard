"""Real-time world: movement, AI, attacks, guard, cooldowns and recoveries.

Nothing here depends on the screen. The client (pygame) only sends **commands** and draws the
state; in multiplayer the host runs this same code and distributes the result (GDD 14.5).

Timings (GDD 7.7):
- Attack: lasts ``attack_seconds / (1 + attack speed)``; the hit lands at ``attack_hit_fraction``
  of the animation. Taking a hard enough hit interrupts the attack.
- Cooldown: each active skill has its own; there is also a short global cooldown.
- Hit recovery: a hit that takes ≥ 1/12 of life locks the character for a moment.
- Block recovery: after blocking, the character is held in the block pose.
- Guard: once lowered, it can only be raised again after a short cooldown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .combat import (
    BlockOutcome,
    GuardState,
    monster_attack,
    monster_defense,
    player_attack,
    player_defense,
    player_guard,
    with_bonus,
)
from .events import (
    ConsumableUsed,
    DamageDealt,
    DamageMitigated,
    ExitReached,
    FloatingText,
    HitBlocked,
    LootPicked,
    MonsterKilled,
    PlayerDied,
    ProficiencyLeveledUp,
    SkillUsed,
)
from .items import GoldDrop, GroundItem
from .monsters import MonsterInstance, PackKind
from .pathfinding import find_path
from .projectiles import Projectile, line_of_sight
from .session import Equip, GameSession
from .timing import (
    Cooldowns,
    monster_hit_recovery,
    player_block_recovery,
    player_hit_recovery,
    skill_cooldown,
)
from .world_gen import AreaLayout, Exit, ExitKind

# Skills with behavior implemented in the prototype; the rest are in the backlog (M3-04).
TARGET_SKILLS = {"precise_strike", "shield_bash"}
AREA_SKILLS = {"wide_slash"}
SELF_SKILLS = {"bulwark"}
PROJECTILE_SKILLS = {"shield_throw"}
IMPLEMENTED_SKILLS = TARGET_SKILLS | AREA_SKILLS | SELF_SKILLS | PROJECTILE_SKILLS
# Passives whose effect the rules already read (combat.py, world.py); the rest are M3-05.
IMPLEMENTED_PASSIVES = {"sharp_blade", "defensive_stance", "parry", "heavy_shield", "iron_skin", "recovery", "steadfast"}


def is_implemented(node_id: str) -> bool:
    """Tooltips warn about nodes that accept points but do nothing in the prototype yet."""
    return node_id in IMPLEMENTED_SKILLS or node_id in IMPLEMENTED_PASSIVES

PLAYER_SPEED = 4.5  # tiles/s
MELEE_RANGE = 1.4
AGGRO_RADIUS = 8.0
EXIT_COOLDOWN = 0.8


# ---------------------------------------------------------------- commands


@dataclass(frozen=True)
class MoveTo:
    player_id: int
    x: float
    y: float


@dataclass(frozen=True)
class AttackTarget:
    player_id: int
    target_id: int


@dataclass(frozen=True)
class UseSkill:
    player_id: int
    skill_id: str
    target_id: int | None = None
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class SetGuard:
    player_id: int
    held: bool
    face_x: float
    face_y: float


@dataclass(frozen=True)
class Stop:
    player_id: int


@dataclass(frozen=True)
class PickUp:
    """Walk to a drop on the ground and take it."""

    player_id: int
    ground_id: int


@dataclass(frozen=True)
class UseConsumable:
    """Drink a potion or read a scroll from the character's consumables (``items.json`` consumables)."""

    player_id: int
    consumable_id: str


@dataclass(frozen=True)
class EquipItem:
    """Wear the backpack item at ``index`` (swaps with what is in its slot)."""

    player_id: int
    index: int


@dataclass(frozen=True)
class UnequipItem:
    player_id: int
    slot: str


@dataclass(frozen=True)
class Respawn:
    """A dead player gets up with full life. The client then takes them to town (the death penalty is the session's)."""

    player_id: int


Command = MoveTo | AttackTarget | UseSkill | SetGuard | Stop | Respawn | PickUp | EquipItem | UnequipItem | UseConsumable


@dataclass
class TownPortal:
    """Where a player's portal was opened. It stays until the next scroll replaces it (GDD 10.4)."""

    area_id: str
    floor: int
    x: float
    y: float


@dataclass
class AreaState:
    """An area as the players left it: it is restored, not regenerated, for the rest of the game."""

    layout: AreaLayout
    monsters: dict
    ground: dict

PORTAL_TOUCH = 0.7  # tiles: how close to a portal you must walk to go through it


# ---------------------------------------------------------------- actors


def cell_of(x: float, y: float) -> tuple[int, int]:
    return (math.floor(x + 0.5), math.floor(y + 0.5))


@dataclass
class PlayerActor:
    session: GameSession
    x: float
    y: float
    face_x: float = 1.0
    face_y: float = 0.0
    life: float = 0.0
    energy: float = 0.0
    path: list[tuple[int, int]] = field(default_factory=list)
    target_id: int | None = None
    pending_skill: str | None = None
    pickup_id: int | None = None
    restores: list[list] = field(default_factory=list)  # [stat, per second, seconds left] from potions
    # states: idle | attacking | hit_recovery | block_recovery | stunned | dead
    state: str = "idle"
    state_left: float = 0.0
    attack_elapsed: float = 0.0
    attack_duration: float = 0.0
    attack_hit_done: bool = False
    attack_skill: str | None = None
    attack_target: int | None = None
    guard_held: bool = False
    guarding: bool = False
    guard_time: float = 0.0
    bulwark_left: float = 0.0
    cooldowns: Cooldowns = field(default_factory=Cooldowns)
    exit_cooldown: float = EXIT_COOLDOWN
    god_mode: bool = False  # debugging

    @property
    def player_id(self) -> int:
        return self.session.player_id

    @property
    def max_life(self) -> float:
        return self.session.character.max_life

    @property
    def max_energy(self) -> float:
        return self.session.character.max_energy

    @property
    def busy(self) -> bool:
        return self.state in ("hit_recovery", "block_recovery", "stunned", "dead")

    @property
    def radius(self) -> float:
        return self.session.data.balance.collision.player_radius

    @property
    def collides(self) -> bool:
        return self.state != "dead"  # a body on the floor does not block anyone

    def reset_vitals(self) -> None:
        self.life = self.max_life
        self.energy = self.max_energy
        self.state, self.state_left = "idle", 0.0


@dataclass
class MonsterActor:
    monster: MonsterInstance
    x: float
    y: float
    pack_ids: list[int] = field(default_factory=list)
    leader_id: int | None = None
    alert: bool = False
    # states: idle | attacking | hit_recovery | stunned
    state: str = "idle"
    state_left: float = 0.0
    attack_elapsed: float = 0.0
    attack_hit_done: bool = False
    attack_cooldown: float = 0.0
    knock_vx: float = 0.0  # knockback velocity (tiles/s) while knock_left > 0
    knock_vy: float = 0.0
    knock_left: float = 0.0
    buffs: dict[str, float] = field(default_factory=dict)  # from leader auras, refreshed every step

    @property
    def id(self) -> int:
        return self.monster.instance_id

    @property
    def alive(self) -> bool:
        return self.monster.alive

    def stat(self, name: str) -> float:
        """Modifiers of the monster plus the auras around it right now."""
        return self.monster.stat(name) + self.buffs.get(name, 0.0)

    @property
    def speed(self) -> float:
        return self.monster.type.move_speed * 2.6 * (1 + self.stat("moveSpeedPct"))

    @property
    def attack_duration(self) -> float:
        return 1.4 / (self.monster.type.attack_rate * (1 + self.stat("attackSpeedPct")))

    @property
    def ranged(self) -> bool:
        return self.monster.type.projectile is not None

    @property
    def defense(self):
        """Passive defense with the aura bonus (a necromancer makes the dead tougher)."""
        return replace(monster_defense(self.monster), physical_reduction=self.stat("physicalReduction"))

    @property
    def aura(self):
        lead = self.monster.type.leader
        return lead.aura if lead else None

    @property
    def attack(self):
        """Numbers of its swing or shot, with aura damage on top."""
        return with_bonus(monster_attack(self.monster), self.buffs.get("damagePct", 0.0))

    @property
    def reach(self) -> float:
        return max(1.2, self.monster.type.range)

    @property
    def radius(self) -> float:
        return self.monster.type.radius * self.monster.rank.size_mult

    @property
    def collides(self) -> bool:
        return self.monster.type.collides and self.alive


# ---------------------------------------------------------------- world


class World:
    def __init__(self, sessions: list[GameSession]):
        self.sessions = {s.player_id: s for s in sessions}
        self.bus = sessions[0].bus
        self.data = sessions[0].data
        self.timing = self.data.balance.timing
        self.players: dict[int, PlayerActor] = {s.player_id: PlayerActor(s, 0, 0) for s in sessions}
        self.monsters: dict[int, MonsterActor] = {}
        self.projectiles: dict[int, Projectile] = {}
        self._next_projectile = 1
        self.ground: dict[int, GroundItem] = {}
        self._next_ground = 1
        self.portals: dict[int, TownPortal] = {}
        self._areas: dict[tuple[str, int], AreaState] = {}
        self.layout: AreaLayout | None = None
        self.area_id = ""
        self.floor = 0
        self.time = 0.0
        for p in self.players.values():
            p.reset_vitals()
        self.bus.subscribe(ProficiencyLeveledUp, self._on_level_up)

    # ------------------------------------------------------------ areas

    def enter_area(self, area_id: str, floor: int = 0, came_from: str | None = None, arrive_at: ExitKind | None = None,
                   at: tuple[float, float] | None = None) -> None:
        """Goes to an area. The first visit generates it; later visits bring it back as it was left
        (dead monsters stay dead, drops stay on the floor), so a town portal round trip makes sense."""
        if self.layout is not None and self.area_id:
            self._areas[(self.area_id, self.floor)] = AreaState(self.layout, self.monsters, self.ground)
        host = next(iter(self.sessions.values()))
        saved = self._areas.get((area_id, floor))
        if saved is not None:
            layout, packs = saved.layout, []
            self.monsters, self.ground = saved.monsters, saved.ground
        else:
            layout, packs = host.enter_area(area_id, floor)
            self.monsters, self.ground = {}, {}
        self.layout, self.area_id, self.floor = layout, area_id, floor
        self.projectiles.clear()

        start = layout.entry
        if came_from is not None:
            back = next((e for e in layout.exits if e.target_area == came_from), None)
            if back:
                start = back.pos
        elif arrive_at is not None:
            ex = next((e for e in layout.exits if e.kind == arrive_at), None)
            if ex:
                start = ex.pos
        if any(e.pos == start for e in layout.exits):
            start = self._inward(start)
        sx, sy = (float(at[0]), float(at[1])) if at is not None else (float(start[0]), float(start[1]))
        for p in self.players.values():
            p.x, p.y = sx, sy
            p.restores.clear()
            p.path.clear()
            p.target_id = None
            p.exit_cooldown = EXIT_COOLDOWN
            if p.state != "dead":
                p.state = "idle"

        spawn_i = 0
        rng = host.spawn_rng
        for pack in packs:
            if pack.kind == PackKind.BOSS:
                anchor = layout.boss_spot or layout.entry
            else:
                anchor = layout.spawn_points[spawn_i % max(1, len(layout.spawn_points))]
                spawn_i += 1
            ids = []
            for m in pack.members:
                actor = MonsterActor(m, float(anchor[0]), float(anchor[1]), leader_id=m.leader_id)
                for _ in range(24):  # a free spot near the anchor: bodies do not start on top of each other
                    x, y = anchor[0] + rng.uniform(-2.5, 2.5), anchor[1] + rng.uniform(-2.5, 2.5)
                    if layout.is_floor(cell_of(x, y)) and not self._overlaps_any(actor, x, y):
                        actor.x, actor.y = x, y
                        break
                self.monsters[m.instance_id] = actor
                ids.append(m.instance_id)
            for i in ids:
                self.monsters[i].pack_ids = ids

    def _inward(self, c: tuple[int, int]) -> tuple[int, int]:
        L = self.layout
        exits = {e.pos for e in L.exits}
        for dist in (2, 1):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx * dist, c[1] + dy * dist)
                if L.is_floor(n) and n not in exits:
                    return n
        return c

    # ------------------------------------------------------------ commands

    def apply(self, cmd: Command) -> str | None:
        """Applies a command. Returns a message when the command is refused."""
        p = self.players.get(cmd.player_id)
        if p is not None and isinstance(cmd, Respawn):
            if p.state == "dead":
                p.reset_vitals()
                p.path.clear()
                p.target_id = p.pending_skill = None
                p.guarding = p.guard_held = False
                p.bulwark_left = 0.0
            return None
        if p is None or p.state == "dead":
            return None
        if isinstance(cmd, SetGuard):
            p.guard_held = cmd.held
            if abs(cmd.face_x) + abs(cmd.face_y) > 1e-6:
                n = math.hypot(cmd.face_x, cmd.face_y)
                p.face_x, p.face_y = cmd.face_x / n, cmd.face_y / n
            return None
        if isinstance(cmd, UseConsumable):
            return self._use_consumable(p, cmd.consumable_id)
        if isinstance(cmd, EquipItem | UnequipItem):  # gear changes the fight, so it goes through the world
            r, msg = p.session.equip_from_backpack(cmd.index) if isinstance(cmd, EquipItem) else p.session.unequip(cmd.slot)
            if r is Equip.OK and not p.session.gear.has_shield:
                self._lower_guard(p, start_cooldown=False)
            p.life, p.energy = min(p.life, p.max_life), min(p.energy, p.max_energy)
            return None if r is Equip.OK else msg
        if isinstance(cmd, PickUp):
            if cmd.ground_id in self.ground:
                p.pickup_id, p.target_id, p.pending_skill = cmd.ground_id, None, None
                g = self.ground[cmd.ground_id]
                p.path = find_path(self.layout, cell_of(p.x, p.y), cell_of(g.x, g.y))
            return None
        p.pickup_id = None  # any other order cancels walking to a drop
        if isinstance(cmd, Stop):
            p.path.clear()
            p.target_id = None
            return None
        if isinstance(cmd, MoveTo):
            p.target_id, p.pending_skill = None, None
            p.path = find_path(self.layout, cell_of(p.x, p.y), cell_of(cmd.x, cmd.y))
            return None
        if isinstance(cmd, AttackTarget):
            if cmd.target_id in self.monsters:
                p.target_id, p.pending_skill = cmd.target_id, None
                p.path.clear()
            return None
        if isinstance(cmd, UseSkill):
            return self._use_skill(p, cmd)
        return None

    def skill_block_reason(self, p: PlayerActor, skill_id: str) -> str | None:
        s = p.session
        node = self.data.node(skill_id)
        if s.skills.points_in(skill_id) <= 0:
            return f"{node.name}: no points"
        if skill_id not in IMPLEMENTED_SKILLS:
            return f"{node.name}: not implemented yet"
        if not p.cooldowns.ready(skill_id):
            return f"{node.name}: cooldown {p.cooldowns.remaining(skill_id):.1f}s"
        if p.energy < node.energy_cost:
            return f"{node.name}: not enough Energy"
        return None

    def _use_skill(self, p: PlayerActor, cmd: UseSkill) -> str | None:
        reason = self.skill_block_reason(p, cmd.skill_id)
        if reason:
            return reason
        if cmd.skill_id in SELF_SKILLS:
            if p.busy or not p.cooldowns.ready("global"):
                return None
            self._commit_skill(p, cmd.skill_id)
            p.bulwark_left = p.session.skills.effect("bulwark", "durationSeconds")  # 3 s base + per point (skills.json)
            self._float(p.x, p.y, "Bulwark!", "info")
            return None
        if cmd.skill_id in PROJECTILE_SKILLS:
            return self._throw_shield(p, cmd)
        if cmd.skill_id in AREA_SKILLS:
            if cmd.x is not None and cmd.y is not None:
                dx, dy = cmd.x - p.x, cmd.y - p.y
                if abs(dx) + abs(dy) > 1e-6:
                    n = math.hypot(dx, dy)
                    p.face_x, p.face_y = dx / n, dy / n
            p.path.clear()
            p.target_id = None
            p.pending_skill = cmd.skill_id
            return None
        if cmd.target_id is None or cmd.target_id not in self.monsters:
            return "no target"
        p.target_id, p.pending_skill = cmd.target_id, cmd.skill_id
        p.path.clear()
        return None

    def _commit_skill(self, p: PlayerActor, skill_id: str) -> None:
        node = self.data.node(skill_id)
        p.energy -= node.energy_cost
        p.cooldowns.start(skill_id, skill_cooldown(node.cooldown, 0.0))
        p.cooldowns.start("global", self.timing.global_cooldown_seconds)
        self.bus.publish(SkillUsed(p.player_id, skill_id))

    # ------------------------------------------------------------ simulation step

    def step(self, dt: float) -> None:
        self.time += dt
        for p in self.players.values():
            self._step_player(p, dt)
        self._apply_auras()
        for m in list(self.monsters.values()):
            if m.alive:
                self._step_monster(m, dt)
        self._step_projectiles(dt)

    def _step_player(self, p: PlayerActor, dt: float) -> None:
        if p.state == "dead":
            return
        s = p.session
        p.cooldowns.tick(dt)
        p.exit_cooldown -= dt
        p.bulwark_left = max(0.0, p.bulwark_left - dt)
        regen = 0.005 if p.guarding else 0.02
        p.energy = min(p.max_energy, p.energy + p.max_energy * regen * dt)
        p.life = min(p.max_life, p.life + s.skills.effect("recovery", "lifeRegenPerSecond") * dt)
        for r in p.restores:  # potions: a share of the maximum over a few seconds
            step = min(dt, r[2])
            if r[0] == "life":
                p.life = min(p.max_life, p.life + r[1] * step)
            else:
                p.energy = min(p.max_energy, p.energy + r[1] * step)
            r[2] -= step
        p.restores = [r for r in p.restores if r[2] > 1e-9]

        # states that lock the character
        if p.state in ("hit_recovery", "block_recovery", "stunned"):
            p.state_left -= dt
            if p.state_left <= 0:
                p.state, p.state_left = "idle", 0.0
        if p.state in ("hit_recovery", "stunned"):
            self._lower_guard(p, start_cooldown=False)
            return

        # guard
        can_guard = s.gear.has_shield
        if p.guard_held and can_guard and not p.guarding and p.cooldowns.ready("guard_raise") and p.state != "attacking":
            p.guarding, p.guard_time = True, 0.0
        elif not p.guard_held and p.guarding and p.state != "block_recovery":
            self._lower_guard(p, start_cooldown=True)
        if p.guarding:
            p.guard_time += dt
        if p.state == "block_recovery":
            return

        # attack in progress
        if p.state == "attacking":
            p.attack_elapsed += dt
            if not p.attack_hit_done and p.attack_elapsed >= p.attack_duration * self.timing.attack_hit_fraction:
                p.attack_hit_done = True
                self._player_hit(p)
            if p.attack_elapsed >= p.attack_duration:
                p.state = "idle"
                if p.attack_skill is not None:
                    p.target_id = None  # a skill is a single swing
            return

        # area skill: starts in place
        if p.pending_skill in AREA_SKILLS and not p.guarding:
            if p.cooldowns.ready("global") and self.skill_block_reason(p, p.pending_skill) is None:
                self._begin_attack(p, None, p.pending_skill)
            p.pending_skill = None
            return

        # walk to a drop and take it
        if p.pickup_id is not None and self._step_pickup(p, dt):
            return

        # chase/attack the target
        if p.target_id is not None and not p.guarding:
            t = self.monsters.get(p.target_id)
            if t is None or not t.alive:
                p.target_id = p.pending_skill = None
            elif math.hypot(t.x - p.x, t.y - p.y) <= MELEE_RANGE:
                p.path.clear()
                self._face(p, t.x - p.x, t.y - p.y)
                if p.cooldowns.ready("global") and p.cooldowns.ready("attack"):
                    skill = p.pending_skill
                    if skill and self.skill_block_reason(p, skill):
                        skill = None
                    self._begin_attack(p, t.id, skill)
                    p.pending_skill = None
                return
            elif not p.path or math.dist(p.path[-1], (t.x, t.y)) > 1.5:
                p.path = find_path(self.layout, cell_of(p.x, p.y), cell_of(t.x, t.y))

        # movement
        if p.path:
            speed = PLAYER_SPEED * (self.data.balance.guard.move_speed_mult if p.guarding else 1.0)
            if self._step_towards(p, p.path[0], speed, dt):
                p.path.pop(0)
            if not p.guarding and p.path:
                self._face(p, p.path[0][0] - p.x, p.path[0][1] - p.y)

        if self.data.balance.loot.auto_pickup_gold:
            for g in [g for g in self.ground.values() if isinstance(g.drop, GoldDrop)]:
                if math.hypot(g.x - p.x, g.y - p.y) <= self.data.balance.loot.gold_pickup_range_tiles:
                    self._take(p, g)

        # town portal, in the world or in town
        portal = self.portals.get(p.player_id)
        if portal is not None and p.exit_cooldown <= 0:
            spot = self.portal_spot(p.player_id)
            if spot is not None and math.hypot(spot[0] - p.x, spot[1] - p.y) <= PORTAL_TOUCH:
                p.exit_cooldown = EXIT_COOLDOWN
                back = self.area_id == self.data.town_id
                kind = ExitKind.PORTAL_BACK if back else ExitKind.PORTAL_TO_TOWN
                self.bus.publish(ExitReached(p.player_id, kind.value, portal.area_id if back else self.data.town_id))
                return

        # exits
        if p.exit_cooldown <= 0 and self.layout:
            c = cell_of(p.x, p.y)
            for e in self.layout.exits:
                if e.pos == c:
                    p.exit_cooldown = EXIT_COOLDOWN
                    self.bus.publish(ExitReached(p.player_id, e.kind.value, e.target_area))
                    break

    # ------------------------------------------------------------ potions and scrolls

    def _use_consumable(self, p: PlayerActor, cid: str) -> str | None:
        s = p.session
        c = self.data.items.consumables.get(cid)
        if c is None:
            return f"unknown consumable '{cid}'"
        if s.consumables[cid] <= 0:
            return f"No {c.name} left"
        if not p.cooldowns.ready(cid):
            return None
        if c.portal:
            if self.area_id == self.data.town_id:
                return "You are already in town"
            self.portals[p.player_id] = TownPortal(self.area_id, self.floor, p.x, p.y)
            p.exit_cooldown = EXIT_COOLDOWN  # do not step through it the moment it opens
            self._float(p.x, p.y, "Town Portal", "info")
        elif c.restore:
            top = p.max_life if c.restore == "life" else p.max_energy
            secs = max(c.seconds, 1e-6)
            p.restores.append([c.restore, top * c.pct_of_max / secs, secs])
            self._float(p.x, p.y, c.name, "info")
        s.consumables[cid] -= 1
        p.cooldowns.start(cid, c.cooldown_seconds)
        self.bus.publish(ConsumableUsed(p.player_id, cid))
        return None

    def take_portal(self, player_id: int) -> None:
        """Goes through the player's portal: from the world to town, or from town back to where it was opened."""
        portal = self.portals.get(player_id)
        if portal is None:
            return
        if self.area_id == self.data.town_id:
            self.enter_area(portal.area_id, portal.floor)
        else:
            self.enter_area(self.data.town_id)
        spot = self.beside_portal(player_id)
        p = self.players[player_id]
        if spot is not None:
            p.x, p.y = spot

    def portal_spot(self, player_id: int) -> tuple[float, float] | None:
        """Where the player's portal stands in the current area: where it was opened, or next to the town waypoint."""
        portal = self.portals.get(player_id)
        if portal is None or self.layout is None:
            return None
        if self.area_id == portal.area_id and self.floor == portal.floor:
            return (portal.x, portal.y)
        if self.area_id == self.data.town_id:
            base = self.layout.waypoint or self.layout.entry
            c = self._inward((base[0] + 2, base[1] + 1))
            return (float(c[0]), float(c[1]))
        return None

    def beside_portal(self, player_id: int) -> tuple[float, float] | None:
        """A free spot a little away from the portal, where a player arrives after going through it."""
        spot = self.portal_spot(player_id)
        if spot is None:
            return None
        for dx, dy in ((1.5, 0), (0, 1.5), (-1.5, 0), (0, -1.5), (1, 1), (-1, -1)):
            if self.layout.is_floor(cell_of(spot[0] + dx, spot[1] + dy)):
                return (spot[0] + dx, spot[1] + dy)
        return spot

    def _lower_guard(self, p: PlayerActor, start_cooldown: bool) -> None:
        if p.guarding:
            p.guarding = False
            if start_cooldown:
                p.cooldowns.start("guard_raise", self.data.balance.guard.guard_raise_cooldown_seconds)

    def _begin_attack(self, p: PlayerActor, target_id: int | None, skill: str | None) -> None:
        ias = p.session.skills.stat_total("attackSpeedPct")
        p.state = "attacking"
        p.attack_elapsed = 0.0
        p.attack_duration = self.timing.attack_seconds / (1 + ias)
        p.attack_hit_done = False
        p.attack_skill = skill
        p.attack_target = target_id
        p.cooldowns.start("attack", p.attack_duration)
        if skill:
            self._commit_skill(p, skill)

    def _player_hit(self, p: PlayerActor) -> None:
        s = p.session
        skill = p.attack_skill
        targets: list[MonsterActor] = []
        if skill in AREA_SKILLS:
            arc = s.skills.effect("wide_slash", "arcDegrees")  # fixed arc (base); points raise the damage
            for m in self.monsters.values():
                if m.alive and math.hypot(m.x - p.x, m.y - p.y) <= MELEE_RANGE + 0.4:
                    if s.combat.in_arc(p.face_x, p.face_y, m.x - p.x, m.y - p.y, arc):
                        targets.append(m)
        else:
            t = self.monsters.get(p.attack_target) if p.attack_target is not None else None
            if t and t.alive and math.hypot(t.x - p.x, t.y - p.y) <= MELEE_RANGE + 0.6:
                targets.append(t)
        prof = self.data.node(skill).tree if skill else "blades"
        for t in targets:
            atk = player_attack(s.character, s.skills, s.gear, skill)
            r = s.combat.resolve_physical(atk, t.defense, s.combat_rng)
            if not r.hit:
                self._float(t.x, t.y, "miss", "miss")
                continue
            self._damage_monster(p, t, r.final, prof, crit=r.crit)
            if skill == "shield_bash" and t.alive:
                self._knock_back(t, p.x, p.y, s.skills.effect("shield_bash", "knockbackTiles"))
                self._stun_monster(t, s.skills.effect("shield_bash", "stunSeconds"))

    def _damage_monster(self, p: PlayerActor, t: MonsterActor, amount: float, prof: str, crit: bool = False) -> None:
        eff = t.monster.take_damage(amount)
        self.bus.publish(DamageDealt(prof, t.monster.xp_info, eff, p.player_id))
        self._float(t.x, t.y, f"{amount:.0f}{'!' if crit else ''}", "crit" if crit else "damage")
        self._alert(t)
        if not t.alive:
            self._kill(t, p)
            return
        rec = monster_hit_recovery(self.timing, amount, t.monster.max_hp, t.monster.rank.id)
        if rec > 0 and t.state != "stunned":
            t.state, t.state_left = "hit_recovery", rec
            t.attack_elapsed, t.attack_hit_done = 0.0, False  # swing interrupted

    def _cc_mult(self, t: MonsterActor) -> float:
        """Uniques and bosses resist crowd control (stun, knockback)."""
        return 1 - self.data.balance.combat.cc_reduction_unique_and_boss if t.monster.rank.id in ("unique", "boss") else 1.0

    def _knock_back(self, t: MonsterActor, from_x: float, from_y: float, tiles: float) -> None:
        """Pushes the monster away from (from_x, from_y) over a short time; walls and bodies stop it (see _step_monster)."""
        dx, dy = t.x - from_x, t.y - from_y
        n = math.hypot(dx, dy)
        secs = self.data.balance.combat.knockback_seconds
        if n < 1e-6 or tiles <= 0 or secs <= 0:
            return
        speed = tiles * self._cc_mult(t) / secs
        t.knock_vx, t.knock_vy, t.knock_left = dx / n * speed, dy / n * speed, secs

    def _stun_monster(self, t: MonsterActor, seconds: float) -> None:
        seconds *= self._cc_mult(t)
        t.state, t.state_left = "stunned", seconds
        t.attack_elapsed, t.attack_hit_done = 0.0, False
        self._float(t.x, t.y, "stunned", "info")

    def kill_all(self, p: PlayerActor) -> int:
        """Debug: kills every monster in the area as if ``p`` had hit them."""
        n = 0
        for m in list(self.monsters.values()):
            self._damage_monster(p, m, m.monster.hp, "blades")
            n += 1
        return n

    def _kill(self, t: MonsterActor, killer: PlayerActor) -> None:
        """Drops fall around the body (GDD 10.3); the player has to pick them up."""
        m = t.monster
        self.bus.publish(MonsterKilled(m.xp_info, m.rank.id, killer.player_id))
        s = killer.session
        for drop in s.loot.roll_drops(m.rank.id, m.level, 0.0, s.loot_rng):
            self.drop_at(drop, t.x, t.y, s.world_rng)
        del self.monsters[t.id]

    def drop_at(self, drop, x: float, y: float, rng) -> GroundItem:
        """Puts a drop on a floor spot near (x, y)."""
        spread = self.data.balance.loot.scatter_tiles
        gx, gy = x, y
        for _ in range(8):
            cx, cy = x + rng.uniform(-spread, spread), y + rng.uniform(-spread, spread)
            if self.layout.is_floor(cell_of(cx, cy)):
                gx, gy = cx, cy
                break
        g = GroundItem(self._next_ground, drop, gx, gy)
        self._next_ground += 1
        self.ground[g.id] = g
        return g

    def _take(self, p: PlayerActor, g: GroundItem) -> bool:
        stored = p.session.collect(g.drop)
        if stored:
            del self.ground[g.id]
        self.bus.publish(LootPicked(p.player_id, g.drop, stored))
        return stored

    def _step_pickup(self, p: PlayerActor, dt: float) -> bool:
        """True while this step was spent on reaching or taking the drop."""
        g = self.ground.get(p.pickup_id)
        if g is None:
            p.pickup_id = None
            return False
        if math.hypot(g.x - p.x, g.y - p.y) <= self.data.balance.loot.pickup_range_tiles:
            p.pickup_id = None
            p.path.clear()
            self._take(p, g)
            return True
        if not p.path:
            p.path = find_path(self.layout, cell_of(p.x, p.y), cell_of(g.x, g.y))
            if not p.path:  # already in the cell: walk the last bit straight
                self._step_towards(p, (g.x, g.y), PLAYER_SPEED, dt)
                return True
        return False

    # ------------------------------------------------------------ monsters

    def _nearest_player(self, m: MonsterActor) -> PlayerActor | None:
        alive = [p for p in self.players.values() if p.state != "dead"]
        return min(alive, key=lambda p: math.hypot(p.x - m.x, p.y - m.y)) if alive else None

    def _apply_auras(self) -> None:
        """Every living leader gives its aura stats to the other monsters in its radius (stacks across leaders)."""
        for m in self.monsters.values():
            m.buffs = {}
        for lead in self.monsters.values():
            aura = lead.aura
            if aura is None or not lead.alive:
                continue
            for o in self.monsters.values():
                if o is not lead and o.alive and math.hypot(o.x - lead.x, o.y - lead.y) <= aura.radius_tiles:
                    for k, v in aura.stats.items():
                        o.buffs[k] = o.buffs.get(k, 0.0) + v

    def _alert(self, m: MonsterActor) -> None:
        if m.alert:
            return
        m.alert = True
        for i in m.pack_ids:
            other = self.monsters.get(i)
            if other is not None:
                other.alert = True

    def _step_monster(self, m: MonsterActor, dt: float) -> None:
        m.attack_cooldown -= dt
        if m.knock_left > 1e-9:
            step = min(dt, m.knock_left)
            m.knock_left -= step
            if not self._try_move(m, m.x + m.knock_vx * step, m.y + m.knock_vy * step):
                m.knock_left = 0.0  # hit a wall or a body
        if m.state in ("hit_recovery", "stunned"):
            m.state_left -= dt
            if m.state_left <= 0:
                m.state = "idle"
            return
        target = self._nearest_player(m)
        if target is None:
            return
        dist = math.hypot(target.x - m.x, target.y - m.y)
        if not m.alert and dist <= AGGRO_RADIUS:
            self._alert(m)

        if m.state == "attacking":
            m.attack_elapsed += dt
            if not m.attack_hit_done and m.attack_elapsed >= m.attack_duration * 0.5:
                m.attack_hit_done = True
                if m.ranged:
                    self._shoot(m, target)
                elif math.hypot(target.x - m.x, target.y - m.y) <= m.reach + 0.6:
                    self._monster_hit(m, target)
            if m.attack_elapsed >= m.attack_duration:
                m.state = "idle"
                m.attack_cooldown = m.attack_duration * 0.3
            return

        if m.alert:
            sees = not m.ranged or line_of_sight(self.layout, (m.x, m.y), (target.x, target.y))
            if dist > m.reach or not sees:  # archers and casters get closer until they have a clear shot
                self._step_towards(m, (target.x, target.y), m.speed, dt)
                self._separate(m, dt)
            elif m.attack_cooldown <= 0:
                m.state, m.attack_elapsed, m.attack_hit_done = "attacking", 0.0, False
        elif m.leader_id is not None:
            leader = self.monsters.get(m.leader_id)
            if leader and math.hypot(leader.x - m.x, leader.y - m.y) > 4:
                self._step_towards(m, (leader.x, leader.y), m.speed * 0.6, dt)

    def _separate(self, m: MonsterActor, dt: float) -> None:
        """Gently pushes overlapping monsters apart (packs spawn close together)."""
        if not m.collides:
            return
        push_x = push_y = 0.0
        for o in self.monsters.values():
            if o is m or not o.collides:
                continue
            dx, dy = m.x - o.x, m.y - o.y
            d, touch = math.hypot(dx, dy), m.radius + o.radius
            if 1e-6 < d < touch:
                push_x += dx / d * (touch - d)
                push_y += dy / d * (touch - d)
        if push_x or push_y:
            k = min(1.0, 6 * dt)
            self._try_move(m, m.x + push_x * k, m.y + push_y * k)

    def _monster_hit(self, m: MonsterActor, p: PlayerActor) -> None:
        self._hit_player(p, m.attack, m.monster.xp_info, m.x, m.y, magic=False, melee=m)

    def _hit_player(self, p: PlayerActor, attack, info, from_x: float, from_y: float, magic: bool, melee: MonsterActor | None) -> None:
        """A swing or a shot reaches the player. (from_x, from_y) is where it comes from, for the guard arc.
        Magic gets past the guard unless Bulwark is up; only melee hits can be counterattacked."""
        s = p.session
        prof = player_guard(s.skills, s.gear, self.data.balance.guard)
        bulwark = p.bulwark_left > 0
        guard = GuardState(
            raised=p.guarding and prof.can_guard and (bulwark or not magic),
            in_arc=bulwark or s.combat.in_arc(p.face_x, p.face_y, from_x - p.x, from_y - p.y, prof.arc_degrees),
            perfect=p.guarding and p.guard_time <= prof.perfect_window,
            efficiency=prof.efficiency,
            energy=p.energy,
            free_block=bulwark,
            reduction=prof.block_reduction,
        )
        defense = player_defense(s.character, s.skills, s.gear)
        r = s.combat.resolve_physical(attack, defense, s.combat_rng, guard)
        if not r.hit:
            self._float(p.x, p.y, "dodged", "miss")
            return
        p.energy = max(0.0, p.energy - r.guard_energy_cost)

        if r.blocked:  # the guard stops most of the hit, never all of it (GDD 7.4)
            perfect = r.block == BlockOutcome.PERFECT
            self.bus.publish(HitBlocked(info, perfect, p.player_id))
            word = "parried!" if perfect else "blocked"
            self._float(p.x, p.y, f"{word} -{r.final:.0f}", "perfect" if perfect else "block")
            if perfect and melee is not None:
                self._counter(p, melee)
            else:
                fbr = s.skills.stat_total("fasterBlockRecovery")
                p.state, p.state_left = "block_recovery", player_block_recovery(self.timing, fbr)
                p.path.clear()
        elif r.block == BlockOutcome.GUARD_BROKEN:
            self._lower_guard(p, start_cooldown=True)
            p.state, p.state_left = "stunned", self.data.balance.guard.guard_break_stun_seconds
            p.path.clear()
            self._float(p.x, p.y, "guard broken", "hurt")
        if r.mitigated > 0:  # armor works on what got past the guard
            self.bus.publish(DamageMitigated(info, r.reached_armor, r.mitigated, p.player_id))
        if not r.blocked:
            self._float(p.x, p.y, f"-{r.final:.0f}", "hurt")
        if not p.god_mode:
            p.life -= r.final
        if p.life <= 0:
            p.life = 0
            p.state = "dead"
            p.path.clear()
            self.bus.publish(PlayerDied(p.player_id))
            return
        if not r.blocked and p.state != "stunned":
            fhr = s.skills.stat_total("fasterHitRecovery")
            rec = player_hit_recovery(self.timing, r.final, p.max_life, fhr)
            if rec > 0:
                p.state, p.state_left = "hit_recovery", rec  # interrupts attack and movement
                p.attack_hit_done = True

    # ------------------------------------------------------------ projectiles

    def _launch(self, **kw) -> Projectile:
        proj = Projectile(id=self._next_projectile, dir_x=1.0, dir_y=0.0, **kw)
        self._next_projectile += 1
        self.projectiles[proj.id] = proj
        return proj

    def _shoot(self, m: MonsterActor, target: PlayerActor) -> None:
        """Fires toward where the target is NOW; the shot never turns afterwards."""
        d = m.monster.type.projectile
        proj = self._launch(x=m.x, y=m.y, speed=d.speed, radius=d.radius, max_range=m.reach + 2, look=d.look,
                            owner="monster", owner_id=m.id, magic=d.magic, attack=m.attack, info=m.monster.xp_info)
        proj.aim(target.x, target.y)

    def _throw_shield(self, p: PlayerActor, cmd: UseSkill) -> str | None:
        s = p.session
        if not s.gear.has_shield:
            return "Shield Throw: needs a shield"
        if p.busy or p.state == "attacking" or not p.cooldowns.ready("global"):
            return None
        t = self.monsters.get(cmd.target_id) if cmd.target_id is not None else None
        tx, ty = (t.x, t.y) if t else (cmd.x, cmd.y)
        if tx is None or ty is None:
            return "no target"
        e = s.skills.effect
        self._commit_skill(p, "shield_throw")
        proj = self._launch(x=p.x, y=p.y, speed=e("shield_throw", "projectileSpeed"), radius=0.25, max_range=e("shield_throw", "rangeTiles"),
                            look="shield", owner="player", owner_id=p.player_id, skill="shield_throw",
                            bounces_left=math.floor(e("shield_throw", "bounces") + 1e-9), bounce_range=e("shield_throw", "bounceRangeTiles"))
        proj.aim(tx, ty)
        self._face(p, proj.dir_x, proj.dir_y)
        p.path.clear()
        p.target_id = None
        return None

    def _step_projectiles(self, dt: float) -> None:
        for proj in list(self.projectiles.values()):
            if not self._fly(proj, dt):
                del self.projectiles[proj.id]

    def _fly(self, proj: Projectile, dt: float) -> bool:
        """Moves one projectile. False when it is gone (hit, wall, out of range)."""
        for x, y in proj.substeps(dt):
            proj.traveled += math.hypot(x - proj.x, y - proj.y)
            proj.x, proj.y = x, y
            if not self.layout.is_floor(cell_of(x, y)):
                return False
            if proj.owner == "monster":
                for p in self.players.values():
                    if p.state != "dead" and math.hypot(p.x - x, p.y - y) < p.radius + proj.radius:
                        self._hit_player(p, proj.attack, proj.info, x - proj.dir_x, y - proj.dir_y, proj.magic, melee=None)
                        return False
            else:
                hit = next((m for m in self.monsters.values() if m.alive and m.id not in proj.hit_ids
                            and math.hypot(m.x - x, m.y - y) < m.radius + proj.radius), None)
                if hit is not None:
                    return self._player_projectile_hit(proj, hit)
        return not proj.spent

    def _player_projectile_hit(self, proj: Projectile, t: MonsterActor) -> bool:
        p = self.players.get(proj.owner_id)
        if p is None:
            return False
        s = p.session
        proj.hit_ids.add(t.id)
        r = s.combat.resolve_physical(player_attack(s.character, s.skills, s.gear, proj.skill), t.defense, s.combat_rng)
        if r.hit:
            self._damage_monster(p, t, r.final, self.data.node(proj.skill).tree, crit=r.crit)
        else:
            self._float(t.x, t.y, "missed", "miss")
        if proj.bounces_left <= 0:
            return False
        nxt = min((m for m in self.monsters.values() if m.alive and m.id not in proj.hit_ids
                   and math.hypot(m.x - proj.x, m.y - proj.y) <= proj.bounce_range
                   and line_of_sight(self.layout, (proj.x, proj.y), (m.x, m.y))),
                  key=lambda m: math.hypot(m.x - proj.x, m.y - proj.y), default=None)
        if nxt is None:
            return False
        proj.bounces_left -= 1
        proj.traveled = 0.0
        proj.max_range = proj.bounce_range + 1
        proj.aim(nxt.x, nxt.y)  # a new straight line toward where the next enemy is now
        return True

    def _counter(self, p: PlayerActor, m: MonsterActor) -> None:
        s = p.session
        bonus = s.skills.effect("parry", "counterDamagePct")
        if bonus <= 0 or not m.alive:
            return
        atk = with_bonus(player_attack(s.character, s.skills, s.gear), bonus, always_hit=True)
        r = s.combat.resolve_physical(atk, m.defense, s.combat_rng)
        self._damage_monster(p, m, r.final, "shield", r.crit)

    # ------------------------------------------------------------ helpers

    def _on_level_up(self, e: ProficiencyLeveledUp) -> None:
        p = self.players.get(e.player_id)
        if p and p.state != "dead":
            p.life = min(p.max_life, p.life + p.max_life * self.data.balance.character.level_up_heal_pct)
            name = p.session.proficiencies[e.proficiency_id].name
            self._float(p.x, p.y, f"{name} {e.new_level}!", "levelup")

    def _float(self, x: float, y: float, text: str, kind: str) -> None:
        self.bus.publish(FloatingText(x, y, text, kind))

    @staticmethod
    def _face(p: PlayerActor, dx: float, dy: float) -> None:
        n = math.hypot(dx, dy)
        if n > 1e-6:
            p.face_x, p.face_y = dx / n, dy / n

    # ------------------------------------------------------------ movement and collision

    def _bodies(self):
        yield from self.players.values()
        yield from self.monsters.values()

    def blocked_by_body(self, a, x: float, y: float):
        """The body ``a`` would overlap at (x, y), or None. Moving away from an overlap is always allowed,
        so actors that start on top of each other (spawns) can get apart."""
        if not a.collides:
            return None
        for o in self._bodies():
            if o is a or not o.collides:
                continue
            touch = a.radius + o.radius
            dx, dy = x - o.x, y - o.y
            if abs(dx) >= touch or abs(dy) >= touch:
                continue
            if dx * dx + dy * dy < touch * touch and math.hypot(dx, dy) < math.hypot(a.x - o.x, a.y - o.y):
                return o
        return None

    def _overlaps_any(self, a, x: float, y: float) -> bool:
        return a.collides and any(
            o is not a and o.collides and math.hypot(x - o.x, y - o.y) < a.radius + o.radius for o in self._bodies()
        )

    def _free(self, a, x: float, y: float) -> bool:
        return self.layout.is_floor(cell_of(x, y)) and self.blocked_by_body(a, x, y) is None

    def _try_move(self, a, x: float, y: float) -> bool:
        if self._free(a, x, y):
            a.x, a.y = x, y
            return True
        return False

    def _step_towards(self, a, target: tuple[float, float], speed: float, dt: float) -> bool:
        """Walks toward the target, sliding along walls and around bodies. True on arrival."""
        tx, ty = target
        dx, dy = tx - a.x, ty - a.y
        dist = math.hypot(dx, dy)
        step = speed * dt
        if dist <= step:
            return self._try_move(a, float(tx), float(ty)) or not self.layout.is_floor(cell_of(tx, ty))
        ux, uy = dx / dist, dy / dist
        nx, ny = a.x + ux * step, a.y + uy * step
        if self._try_move(a, nx, ny):
            return False
        body = self.blocked_by_body(a, nx, ny) if self.layout.is_floor(cell_of(nx, ny)) else None
        if body is None:  # a wall: slide along it, one axis at a time
            if abs(ux) > 1e-6 and abs(uy) > 1e-6:
                self._try_move(a, nx, a.y) or self._try_move(a, a.x, ny)
            return False
        if math.hypot(tx - body.x, ty - body.y) < a.radius + body.radius:
            return True  # the spot is taken by a body: count it as reached so the path goes on around it
        # a body: slide along its edge, on the side closer to where we are going
        ox, oy = a.x - body.x, a.y - body.y
        n = math.hypot(ox, oy) or 1.0
        tx_, ty_ = -oy / n, ox / n
        if tx_ * ux + ty_ * uy < 0:
            tx_, ty_ = -tx_, -ty_
        self._try_move(a, a.x + tx_ * step, a.y + ty_ * step) or self._try_move(a, a.x - tx_ * step, a.y - ty_ * step)
        return False

    def exit_at(self, x: float, y: float) -> Exit | None:
        c = cell_of(x, y)
        return next((e for e in self.layout.exits if e.pos == c), None) if self.layout else None
