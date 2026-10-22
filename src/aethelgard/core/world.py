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
    melee_edge,
    monster_attack,
    monster_defense,
    player_attack,
    player_defense,
    player_guard,
    with_bonus,
)
from .crafting import Crafter
from .data import MonsterTypeDef
from .events import (
    ActOpened,
    ConsumableUsed,
    DamageAbsorbed,
    DamageDealt,
    DamageMitigated,
    ExitReached,
    FloatingText,
    HitBlocked,
    ItemCrafted,
    LootPicked,
    MonsterKilled,
    PlayerDied,
    ProficiencyLeveledUp,
    SkillLeveledUp,
    SkillPracticed,
    SkillUsed,
    SpellBurst,
    StashChanged,
    WaypointReached,
)
from .items import ChestDrop, GoldDrop, GroundItem, ItemDrop, SharedStash
from .maneuvers import Dash, Maneuvers
from .monster_ai import MonsterBrains
from .monsters import MonsterInstance, PackKind
from .pathfinding import find_path
from .projectiles import Projectile, line_of_sight
from .rng import Rng
from .session import Equip, GameSession
from .spells import SpellCaster
from .timing import (
    Cooldowns,
    monster_hit_recovery,
    player_block_recovery,
    player_hit_recovery,
    skill_cooldown,
)
from .town import Vendor, item_price, sell_price
from .world_gen import FLOOR, AreaLayout, Exit, ExitKind

# Skills with behavior implemented in the prototype; the rest are in the backlog (M3-04).
TARGET_SKILLS = {"precise_strike", "shield_bash", "double_strike", "frenzy", "crushing_blow", "hammer_blow", "heavy_swing", "devastate"}
AREA_SKILLS = {"wide_slash", "whirling_blades", "ground_slam", "earthquake", "cleave"}  # arcDegrees in front (360 = all around), radiusTiles
TWO_HAND_HITS = {"double_strike", "frenzy", "whirling_blades"}  # one hit with each weapon in the same swing
BOW_SKILLS = {"power_shot", "multishot", "piercing_arrow"}  # fire arrows; aimed at a target or at the mouse
SELF_SKILLS = {"bulwark", "berserk", "war_cry"}  # on the caster: buffs (buff* stats) for durationSeconds; shouts (enemy* stats) on those around
DASH_SKILLS = {"charge", "blade_dance"}  # move fast and strike (core/maneuvers.py)
CHANNEL_SKILLS = {"whirlwind"}  # last while the key is held (ReleaseSkill), draining Energy per second
GROUND_BOW_SKILLS = {"rain_of_arrows"}  # a bow shot aimed at the ground that leaves arrows falling there
PROJECTILE_SKILLS = {"shield_throw"}
# every skills.json node with 'spell' (a test checks they match): cast in place toward a point (core/spells.py)
SPELL_SKILLS = {
    "fire_bolt", "fireball", "fire_wall", "meteor",
    "ice_bolt", "frost_nova", "blizzard", "glacial_spike",
    "spark", "chain_lightning", "ball_lightning", "thunderstorm",
    "heal", "holy_bolt", "blessing_of_might", "divine_aegis",
    "arcane_barrier", "elemental_ward", "thorn_ward", "sanctuary",
}
GROUND_SPELLS = {"wall", "meteor", "field", "storm"}  # spell.cast values aimed at the ground: only where the caster can see
IMPLEMENTED_SKILLS = (TARGET_SKILLS | AREA_SKILLS | SELF_SKILLS | PROJECTILE_SKILLS | BOW_SKILLS | SPELL_SKILLS | DASH_SKILLS
                      | CHANNEL_SKILLS | GROUND_BOW_SKILLS)
# Passives whose effect the rules already read (combat.py, world.py); the rest are M3-05.
IMPLEMENTED_PASSIVES = {
    "sharp_blade", "defensive_stance", "parry", "heavy_shield", "iron_skin", "recovery", "steadfast",
    "off_hand_training", "ambidexterity", "blade_guard", "bloodlust",
    "steady_aim", "eagle_eye", "quick_draw", "hunters_mark",
    "kindling", "ignite", "quick_casting", "searing_heat",
    "heavy_head", "bone_breaker", "concussive", "executioner",
    "great_weapon_mastery", "brute_force", "relentless", "overpower",
    "frostbite", "deep_freeze", "shatter", "glacial_mastery",
    "conductivity", "overcharge", "static_focus", "storm_mastery",
    "blessed_hands", "zeal", "devotion", "grace",
    "fortify", "mending_ward", "mana_shield", "unbreakable",
    "bleed", "combat_rhythm", "killing_blow", "riposte", "bastion", "second_wind", "elemental_resistance", "unshakable", "colossus",
}
RHYTHM_SECONDS = 2.0  # Combat Rhythm: stacks fall off this long after the last hit


def is_implemented(node_id: str) -> bool:
    """Tooltips warn about nodes that accept points but do nothing in the prototype yet."""
    return node_id in IMPLEMENTED_SKILLS or node_id in IMPLEMENTED_PASSIVES

MELEE_RANGE = 1.4
MELEE_REACH_AROUND = MELEE_RANGE + 0.4  # 360° hits (Whirlwind spins) reach this far to the edge of a body
DEFAULT_BODY_RADIUS = MonsterTypeDef.radius  # a man-sized body (monsters.json default); wider ones are reached that much sooner
AGGRO_RADIUS = 8.0
EXIT_COOLDOWN = 0.8
BODY_CELL = 2.5  # tiles per cell of the spatial grid of bodies (collision queries look at the 3 × 3 cells around)


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
class AttackInPlace:
    """Shift+click: swing or shoot toward (x, y) without moving; a swing hits whoever is in reach in front."""

    player_id: int
    x: float
    y: float


@dataclass(frozen=True)
class UseSkill:
    player_id: int
    skill_id: str
    target_id: int | None = None
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class ReleaseSkill:
    """The key of a channelled skill (Whirlwind) was let go."""

    player_id: int
    skill_id: str


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
class OpenPortal:
    """Open a town portal where the player stands (always available, with a cooldown)."""

    player_id: int


@dataclass(frozen=True)
class TravelWaypoint:
    """From the waypoint you stand on to another one you have touched before."""

    player_id: int
    area_id: str


@dataclass(frozen=True)
class Buy:
    """Buy the vendor's item at ``index`` (stand by the merchant)."""

    player_id: int
    index: int


@dataclass(frozen=True)
class BuyConsumable:
    player_id: int
    consumable_id: str


@dataclass(frozen=True)
class Sell:
    """Sell the backpack item at ``index``."""

    player_id: int
    index: int


@dataclass(frozen=True)
class StashPut:
    """Backpack item at ``index`` into the shared stash (stand by the stash), at cell ``at`` or the first free place."""

    player_id: int
    index: int
    at: tuple[int, int] | None = None


@dataclass(frozen=True)
class StashTake:
    player_id: int
    index: int
    at: tuple[int, int] | None = None  # cell in the backpack


@dataclass(frozen=True)
class ForgetTree:
    """Read a Scroll of Forgetting: the points of one tree come back to spend again."""

    player_id: int
    tree: str


@dataclass(frozen=True)
class MoveItem:
    """Moves an item to another place of the same grid: the backpack, or the stash (stand by it)."""

    player_id: int
    container: str  # bag | stash
    index: int
    x: int
    y: int


@dataclass(frozen=True)
class StashGold:
    """Move gold: positive puts it in the stash, negative takes it out (clamped to what there is)."""

    player_id: int
    amount: int


@dataclass(frozen=True)
class EquipItem:
    """Wear the backpack item at ``index`` in ``slot`` (None = its automatic slot); swaps with what is there."""

    player_id: int
    index: int
    slot: str | None = None


@dataclass(frozen=True)
class UnequipItem:
    player_id: int
    slot: str


@dataclass(frozen=True)
class Respawn:
    """A dead player gets up with full life. The client then takes them to town (the death penalty is the session's)."""

    player_id: int


Command = (
    MoveTo | AttackTarget | AttackInPlace | UseSkill | ReleaseSkill | SetGuard | Stop | Respawn | PickUp | EquipItem | UnequipItem | UseConsumable
    | OpenPortal | TravelWaypoint | Buy | BuyConsumable | Sell | StashPut | StashTake | StashGold | MoveItem | ForgetTree
)
@dataclass(frozen=True)
class Salvage:
    """Break the backpack item at ``index`` into crafting materials (stand by the crafter)."""

    player_id: int
    index: int


@dataclass(frozen=True)
class Craft:
    """Use a crafting recipe on the backpack item at ``index`` (stand by the crafter); the result takes its place."""

    player_id: int
    recipe_id: str
    index: int


TOWN_COMMANDS = (Buy, BuyConsumable, Sell, StashPut, StashTake, StashGold)
CRAFT_COMMANDS = (Salvage, Craft)


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
CORPSE_TOUCH = 1.0  # tiles: walking this close to your corpse takes your gear back
WAYPOINT_TOUCH = 0.9  # tiles: standing this close activates the waypoint and opens its list


# ---------------------------------------------------------------- actors


def cell_of(x: float, y: float) -> tuple[int, int]:
    return (math.floor(x + 0.5), math.floor(y + 0.5))


@dataclass
class Dot:
    """Damage over time on a monster (burning, bleeding): its XP goes to ``skill``/``prof``."""

    dps: float
    left: float
    owner: int
    skill: str = ""
    prof: str = ""
    tick: float = 0.0


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
    aim_x: float | None = None  # where a shot aimed at the ground goes (Multishot uses its distance)
    aim_y: float | None = None
    restores: list[list] = field(default_factory=list)  # [stat, per second, seconds left] from potions
    on_waypoint: bool = False  # standing on it; the list opens again only after stepping off
    portal_cast_left: float = 0.0  # opening a town portal: seconds still to stand still
    portal_at: tuple[float, float] | None = None  # where it is opening: a step in front of the player
    in_exit: bool = False  # inside an exit's zone: it takes you only when you walk in, not when you arrive in it
    # states: idle | attacking | hit_recovery | block_recovery | stunned | dead
    state: str = "idle"
    state_left: float = 0.0
    attack_elapsed: float = 0.0
    attack_duration: float = 0.0
    attack_hit_done: bool = False
    attack_skill: str | None = None
    attack_target: int | None = None
    attack_hand: str = "main"  # with two weapons the swings alternate main → off → main
    guard_held: bool = False
    guarding: bool = False
    guard_time: float = 0.0
    buffs: dict[str, float] = field(default_factory=dict)  # self skill → seconds left (Bulwark, Berserk, blessings)
    frenzy_stacks: int = 0  # Frenzy: attack speed per stack while frenzy_left > 0
    frenzy_left: float = 0.0
    rhythm_stacks: int = 0  # Combat Rhythm: hits in a row, while rhythm_left > 0
    rhythm_left: float = 0.0
    riposte_ready: bool = False  # Riposte: the next swing after a block hits harder
    barrier: float = 0.0  # Warding: damage the barrier still takes before life (while its buff lasts)
    barrier_max: float = 0.0
    barrier_skill: str = ""
    slow_pct: float = 0.0  # chilled by a monster (Cold Enchanted): moves and swings slower while slow_left > 0
    slow_left: float = 0.0
    deferred: list[list[float]] = field(default_factory=list)  # Unshakable: [damage per second, seconds left]
    dash: Dash | None = None  # Charge, Blade Dance (core/maneuvers.py)
    channel: str | None = None  # Whirlwind, while its key is held
    channel_tick: float = 0.0
    cooldowns: Cooldowns = field(default_factory=Cooldowns)
    exit_cooldown: float = EXIT_COOLDOWN
    god_mode: bool = False  # debugging

    @property
    def player_id(self) -> int:
        return self.session.player_id

    @property
    def bulwark_left(self) -> float:
        return self.buffs.get("bulwark", 0.0)

    @bulwark_left.setter
    def bulwark_left(self, seconds: float) -> None:
        self.buffs["bulwark"] = seconds

    def buff_total(self, stat: str) -> float:
        """A stat summed over the buffs up now (Berserk's buffDamagePct, a blessing's buffDefensePct…)."""
        e = self.session.skills.effect
        return sum(e(sid, stat) for sid, left in self.buffs.items() if left > 0)

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
    face_x: float = 0.0  # where it looks (unit vector): its target while alert, its leader while following
    face_y: float = 1.0
    # states: idle | attacking | hit_recovery | stunned
    state: str = "idle"
    state_left: float = 0.0
    attack_elapsed: float = 0.0
    attack_hit_done: bool = False
    attack_cooldown: float = 0.0
    knock_vx: float = 0.0  # knockback velocity (tiles/s) while knock_left > 0
    knock_vy: float = 0.0
    knock_left: float = 0.0
    base_speed: float = 3.75  # tiles/s before the type's moveSpeed (balance.json → movement)
    swing_seconds: float = 0.6  # longest swing animation (balance.json → timing.monsterSwingSeconds)
    buffs: dict[str, float] = field(default_factory=dict)  # from leader auras, refreshed every step
    dots: dict[str, Dot] = field(default_factory=dict)  # damage over time by kind: burn (Ignite), bleed (Bleed)
    debuffs: dict[str, list[float]] = field(default_factory=dict)  # stat → [value, seconds left] (War Cry: damagePct)
    chill_left: float = 0.0  # cold: slower to move and to attack while it lasts
    chill_pct: float = 0.0
    ai_cooldowns: dict[str, float] = field(default_factory=dict)  # core/monster_ai.py: charge, heal, revive, boss specials
    rush: list[float] | None = None  # charger: [dir x, dir y, tiles left]
    phase: int = 1  # bosses: 2 below phaseTwoAtPct of life
    boost: dict[str, float] = field(default_factory=dict)  # lasting stats (a boss's second phase)
    winding: object | None = None  # the boss special being wound up
    attack_duration_override: float = 0.0  # length of a wind-up, for the pose

    @property
    def burn_left(self) -> float:
        d = self.dots.get("burn")
        return d.left if d else 0.0

    def add_dot(self, kind: str, dot: Dot) -> None:
        """The stronger of the old and the new one stays (same kind never stacks)."""
        old = self.dots.get(kind)
        if old is None or dot.dps * dot.left >= old.dps * old.left:
            self.dots[kind] = dot

    @property
    def id(self) -> int:
        return self.monster.instance_id

    @property
    def alive(self) -> bool:
        return self.monster.alive

    def stat(self, name: str) -> float:
        """Modifiers of the monster plus the auras around it right now."""
        chill = self.chill_pct if self.chill_left > 0 and name in ("moveSpeedPct", "attackSpeedPct") else 0.0
        return self.monster.stat(name) + self.extra(name) - chill

    def extra(self, name: str) -> float:
        """What is on it right now from others: leader auras and debuffs (War Cry)."""
        return self.buffs.get(name, 0.0) + self.boost.get(name, 0.0) + sum(v for k, (v, _) in self.debuffs.items() if k == name)

    @property
    def speed(self) -> float:
        return self.monster.type.move_speed * self.base_speed * (1 + self.stat("moveSpeedPct"))

    @property
    def attack_interval(self) -> float:
        """Time from one swing to the next (unchanged pace: 1.4 s / attackRate, plus 30% pause)."""
        return 1.3 * 1.4 / (self.monster.type.attack_rate * (1 + self.stat("attackSpeedPct")))

    @property
    def attack_duration(self) -> float:
        """The swing itself: short and snappy; slow attackers just wait longer between swings."""
        if self.state == "winding":
            return self.attack_duration_override
        return min(self.swing_seconds / (1 + self.stat("attackSpeedPct")), self.attack_interval / 1.3)

    @property
    def ranged(self) -> bool:
        return self.monster.type.projectile is not None

    def resist(self, element: str) -> float:
        """Share of that element's spell damage it ignores: its type's, plus modifiers (Fire Enchanted)."""
        return self.monster.type.resist.get(element, 0.0) + self.stat(f"{element}Resist")

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
        return with_bonus(monster_attack(self.monster), self.extra("damagePct"))

    @property
    def reach(self) -> float:
        """From its center: arm's length (or its range) plus however much wider than a man it is, so a big
        body (a boss at 2.2 times the size) still reaches someone standing against it."""
        return max(1.2, self.monster.type.range) + max(0.0, self.radius - DEFAULT_BODY_RADIUS)

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
        self.spells = SpellCaster(self)
        self.moves = Maneuvers(self)
        self.brains = MonsterBrains(self)
        self.fallen: list[MonsterActor] = []  # dead monsters of this area (a necromancer may raise them)
        self._grid: dict[tuple[int, int], list] | None = None  # bodies by cell, rebuilt every step (M0-06)
        self.ground: dict[int, GroundItem] = {}
        self._next_ground = 1
        self.portals: dict[int, TownPortal] = {}
        self.vendor = Vendor(self.data)
        self.crafter = Crafter(self.data)
        self.stash: SharedStash | None = None  # shared by every character; the client loads and saves it
        self._town_visits = 0
        self._areas: dict[tuple[str, int], AreaState] = {}
        self.layout: AreaLayout | None = None
        self.area_id = ""
        self.floor = 0
        self.time = 0.0
        for p in self.players.values():
            p.reset_vitals()
        self.bus.subscribe(ProficiencyLeveledUp, self._on_level_up)
        self.bus.subscribe(SkillLeveledUp, self._on_skill_level_up)

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
            level = self.data.area(area_id).level_for_floor(floor)
            for cx, cy in layout.chests:  # closed chests of the hand-made pieces (M5-03): opened like a pick-up
                self.ground[self._next_ground] = GroundItem(self._next_ground, ChestDrop(level), float(cx), float(cy))
                self._next_ground += 1
        self.layout, self.area_id, self.floor = layout, area_id, floor
        self.projectiles.clear()
        self.spells.clear()
        self.brains.clear()
        self.fallen = []
        self._grid = None  # rebuilt at the next step; until then collision looks at every body
        if self.data.is_town(area_id):  # the merchant has new wares every time you come back
            self._town_visits += 1
            level = max(p.session.character.level for p in self.players.values())
            self.vendor.restock(level, Rng.for_label(host.master_seed, f"vendor:{self._town_visits}"))

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
            p.on_waypoint = True  # arriving on a waypoint does not open its list until you step off
            p.in_exit = True  # nor does an exit you arrive next to
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
                actor = MonsterActor(m, float(anchor[0]), float(anchor[1]), leader_id=m.leader_id,
                                     base_speed=self.data.balance.movement.monster_base_tiles_per_second,
                                     swing_seconds=self.timing.monster_swing_seconds)
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
        for dist in (3, 2, 1):  # outside the exit's zone when there is room
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
                p.buffs.clear()
                p.dash = p.channel = None
                p.deferred.clear()
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
        if isinstance(cmd, ReleaseSkill):
            if p.channel == cmd.skill_id:
                self.moves.stop_channel(p)
            return None
        if isinstance(cmd, AttackTarget | AttackInPlace | PickUp) or (isinstance(cmd, UseSkill) and cmd.skill_id not in CHANNEL_SKILLS):
            p.channel = None  # another action ends a spin (walking does not)
        if isinstance(cmd, AttackInPlace | AttackTarget | UseSkill) and self.data.is_town(self.area_id):
            return "You cannot fight in town"
        if isinstance(cmd, AttackInPlace):
            self._cancel_portal(p)
            if p.busy or p.guarding or p.state == "attacking":
                return None
            p.aim_x, p.aim_y = cmd.x, cmd.y
            p.path.clear()
            p.target_id = p.pending_skill = None
            self._face(p, cmd.x - p.x, cmd.y - p.y)
            if p.cooldowns.ready("global") and p.cooldowns.ready("attack"):
                self._begin_attack(p, None, None)
            return None
        if isinstance(cmd, OpenPortal):
            return self._open_portal(p)
        if isinstance(cmd, MoveTo | AttackTarget | AttackInPlace | UseSkill | PickUp | TravelWaypoint):
            self._cancel_portal(p)  # doing anything else stops the portal from opening
        if isinstance(cmd, TravelWaypoint):
            return self._travel(p, cmd.area_id)
        if isinstance(cmd, TOWN_COMMANDS):
            return self._town(p, cmd)
        if isinstance(cmd, CRAFT_COMMANDS):
            return self._craft(p, cmd)
        if isinstance(cmd, MoveItem):
            return self._move_item(p, cmd)
        if isinstance(cmd, ForgetTree):
            msg = p.session.forget_tree(cmd.tree)
            if msg is None:
                p.channel = None
                self._float(p.x, p.y, f"{self.data.proficiency(cmd.tree).name} forgotten", "levelup")
            return msg
        if isinstance(cmd, EquipItem | UnequipItem):  # gear changes the fight, so it goes through the world
            s = p.session
            r, msg = s.equip_from_backpack(cmd.index, cmd.slot) if isinstance(cmd, EquipItem) else s.unequip(cmd.slot)
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

    def skill_block_reason(self, p: PlayerActor, skill_id: str, ignore_energy: bool = False) -> str | None:
        s = p.session
        node = self.data.node(skill_id)
        if s.skills.points_in(skill_id) <= 0:
            return f"{node.name}: no points"
        need = self.data.proficiency(node.tree).skills_need
        if node.kind == "active" and not s.gear.meets(need):
            return f"{node.name}: needs {need.text}"
        if node.spell is not None and p.guarding:
            return f"{node.name}: lower your guard to cast"
        if skill_id not in IMPLEMENTED_SKILLS:
            return f"{node.name}: not implemented yet"
        if not p.cooldowns.ready(skill_id):
            return f"{node.name}: cooldown {p.cooldowns.remaining(skill_id):.1f}s"
        if not ignore_energy and p.energy < node.energy_cost:
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
            self.apply_self_skill(p, cmd.skill_id)
            return None
        if cmd.skill_id in PROJECTILE_SKILLS:
            return self._throw_shield(p, cmd)
        if cmd.skill_id == "charge":
            return self.moves.start_charge(p, cmd.skill_id, cmd.target_id)
        if cmd.skill_id in DASH_SKILLS:
            return self.moves.start_blade_dance(p, cmd.skill_id, cmd.x, cmd.y, cmd.target_id)
        if cmd.skill_id in CHANNEL_SKILLS:
            return self.moves.start_channel(p, cmd.skill_id)
        if cmd.skill_id in GROUND_BOW_SKILLS:  # aimed at the ground, like a spell, only where you can see
            t = self.monsters.get(cmd.target_id) if cmd.target_id is not None else None
            ax, ay = (t.x, t.y) if t is not None else (cmd.x, cmd.y)
            if ax is None or ay is None:
                return "no target"
            gx, gy = self.spells.aim_point(p, cmd.skill_id, (ax, ay), default_range=self.player_reach(p))
            if not self.layout.is_floor(cell_of(gx, gy)) or not line_of_sight(self.layout, (p.x, p.y), (gx, gy)):
                return f"{self.data.node(cmd.skill_id).name}: you cannot see there"
            self._face(p, ax - p.x, ay - p.y)
            p.aim_x, p.aim_y = gx, gy
            p.path.clear()
            p.target_id = None
            p.pending_skill = cmd.skill_id
            return None
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
        if cmd.skill_id in SPELL_SKILLS:  # cast from where you stand, toward the target or the mouse
            t = self.monsters.get(cmd.target_id) if cmd.target_id is not None else None
            ax, ay = (t.x, t.y) if t is not None else (cmd.x, cmd.y)
            node = self.data.node(cmd.skill_id)
            if node.spell.cast in ("self", "nova") and (ax is None or ay is None):  # on yourself: no aim needed
                ax, ay = p.x + p.face_x, p.y + p.face_y
            if ax is None or ay is None:
                return "no target"
            if node.spell.cast in GROUND_SPELLS:  # fire on the ground goes only where you can see
                gx, gy = self.spells.aim_point(p, cmd.skill_id, (ax, ay))
                if not self.layout.is_floor(cell_of(gx, gy)) or not line_of_sight(self.layout, (p.x, p.y), (gx, gy)):
                    return f"{node.name}: you cannot see there"
            self._face(p, ax - p.x, ay - p.y)
            p.aim_x, p.aim_y = ax, ay
            p.path.clear()
            p.target_id = None
            p.pending_skill = cmd.skill_id
            return None
        if cmd.skill_id in BOW_SKILLS and (cmd.target_id is None or cmd.target_id not in self.monsters) and cmd.x is not None:
            self._face(p, cmd.x - p.x, cmd.y - p.y)  # no target: shoot toward the mouse, from where you stand
            p.aim_x, p.aim_y = cmd.x, cmd.y
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
        p.cooldowns.start(skill_id, skill_cooldown(node.cooldown, p.session.stat("cooldownReduction")))
        p.cooldowns.start("global", self.timing.global_cooldown_seconds)
        self.bus.publish(SkillUsed(p.player_id, skill_id))
        if node.use_xp > 0 and "healPctOfMax" not in node.stat_ids:  # a skill that hits nothing (war cry) learns from use in a fight
            self._practice(p, skill_id, node.use_xp)

    def _practice(self, p: PlayerActor, skill_id: str, share: float) -> None:
        """XP for a skill that hits nothing, when a monster is near: the skill and its tree learn from it."""
        near = self._nearest_monster(p.x, p.y, self.data.skills.use_xp_radius_tiles)
        if near is not None and share > 0:
            self.bus.publish(SkillPracticed(p.player_id, skill_id, near.monster.xp_info, share))

    def apply_self_skill(self, p: PlayerActor, skill_id: str) -> None:
        """A skill on yourself: ``healPctOfMax`` heals at once (Heal), ``durationSeconds`` makes it a buff whose
        ``buff*`` stats count while it lasts (Bulwark, Berserk, blessings). Buffs last longer with buffDurationPct."""
        sk, node = p.session.skills, self.data.node(skill_id)
        heal = sk.effect(skill_id, "healPctOfMax")
        if heal > 0:
            amount = min(p.max_life - p.life, p.max_life * heal * (1 + p.session.stat("healingPct")))
            p.life += amount
            self._float(p.x, p.y, f"+{amount:.0f}", "heal")
            # healing teaches only when it is needed: a full heal of a quarter of your life is a whole use
            self._practice(p, skill_id, node.use_xp * min(1.0, amount / max(1.0, 0.25 * p.max_life)))
        secs = sk.effect(skill_id, "durationSeconds")
        barrier = sk.effect(skill_id, "barrierPctOfMax")
        if barrier > 0:  # Arcane Barrier: a pool of life that takes hits first
            p.barrier = p.barrier_max = p.max_life * barrier * (1 + p.session.stat("barrierStrengthPct"))
            p.barrier_skill = skill_id
        push, radius = sk.effect(skill_id, "knockbackTiles"), sk.effect(skill_id, "radiusTiles")
        if push > 0 and radius > 0:  # Sanctuary: everyone around is pushed away
            for m in self.monsters.values():
                if m.alive and math.hypot(m.x - p.x, m.y - p.y) <= radius + m.radius:
                    self._knock_back(m, p.x, p.y, push)
                    self._alert(m)
            self.bus.publish(SpellBurst(p.x, p.y, radius, node.spell.look if node.spell else skill_id))
        weaken = sk.effect(skill_id, "enemyDamageReduction")
        if weaken > 0:  # a shout (War Cry): enemies around deal less damage for a while; no buff on yourself
            radius = sk.effect(skill_id, "radiusTiles")
            for m in self.monsters.values():
                if m.alive and math.hypot(m.x - p.x, m.y - p.y) <= radius + m.radius:
                    m.debuffs["damagePct"] = [-min(0.9, weaken), secs]
                    self._alert(m)
            self.bus.publish(SpellBurst(p.x, p.y, radius, skill_id))
            self._float(p.x, p.y, f"{node.name}!", "info")
        elif secs > 0:
            p.buffs[skill_id] = secs * (1 + p.session.stat("buffDurationPct"))
            self._float(p.x, p.y, f"{node.name}!", "info")

    def _nearest_monster(self, x: float, y: float, radius: float) -> MonsterActor | None:
        near = [m for m in self.monsters.values() if m.alive and math.hypot(m.x - x, m.y - y) <= radius]
        return min(near, key=lambda m: math.hypot(m.x - x, m.y - y)) if near else None

    # ------------------------------------------------------------ simulation step

    def step(self, dt: float) -> None:
        self.time += dt
        self._rebuild_grid()
        for p in self.players.values():
            self._step_player(p, dt)
        self._apply_auras()
        sleep = self.data.balance.monster_ai.sleep_tiles
        awake = [p for p in self.players.values() if p.state != "dead"]
        for m in list(self.monsters.values()):
            if not m.alive:
                continue
            if not m.alert and all(abs(p.x - m.x) + abs(p.y - m.y) > sleep for p in awake):
                continue  # far from everyone and not hunting: not simulated (M4-06)
            self._step_monster(m, dt)
        self.brains.step(dt)
        self._step_projectiles(dt)
        self.spells.step(dt)
        self._step_afflictions(dt)

    def _step_afflictions(self, dt: float) -> None:
        """Damage over time (burning, bleeding) every magic.tickSeconds, and debuffs running out."""
        tick = self.data.balance.magic.tick_seconds
        for m in list(self.monsters.values()):
            if m.debuffs:
                for v in m.debuffs.values():
                    v[1] -= dt
                m.debuffs = {k: v for k, v in m.debuffs.items() if v[1] > 0}
            for kind, d in list(m.dots.items()):
                if not m.alive:
                    break
                d.left -= dt
                d.tick -= dt
                if d.tick <= 0:
                    d.tick += tick
                    p = self.players.get(d.owner)
                    if p is not None:  # hurts, never staggers; XP to the skill (or swing) that caused it
                        self._damage_monster(p, m, d.dps * tick, d.prof or self.data.node(d.skill).tree, recover=False, skill=d.skill or None)
                if d.left <= 0:
                    m.dots.pop(kind, None)

    def _step_player(self, p: PlayerActor, dt: float) -> None:
        if p.state == "dead":
            return
        s = p.session
        p.cooldowns.tick(dt)
        p.exit_cooldown -= dt
        p.buffs = {k: v - dt for k, v in p.buffs.items() if v - dt > 0}
        if p.barrier > 0:  # a barrier lasts as long as its buff; Mending Ward refills it
            if p.barrier_skill not in p.buffs:
                p.barrier = 0.0
            else:
                p.barrier = min(p.barrier_max, p.barrier + p.barrier_max * s.stat("barrierRegenPct") * dt)
        p.frenzy_left = max(0.0, p.frenzy_left - dt)
        if p.frenzy_left <= 0:
            p.frenzy_stacks = 0
        p.rhythm_left = max(0.0, p.rhythm_left - dt)
        p.slow_left = max(0.0, p.slow_left - dt)
        if p.rhythm_left <= 0:
            p.rhythm_stacks = 0
        if p.deferred:  # Unshakable: the delayed share of earlier hits, a little every frame
            due = sum(r[0] * min(dt, r[1]) for r in p.deferred)
            for r in p.deferred:
                r[1] -= dt
            p.deferred = [r for r in p.deferred if r[1] > 1e-9]
            if self._lose_life(p, due):
                return
        c = self.data.balance.character
        regen = c.guard_energy_regen_pct_per_second if p.guarding else c.energy_regen_pct_per_second
        p.energy = min(p.max_energy, p.energy + p.max_energy * regen * dt)
        regen_life = s.stat("lifeRegenPerSecond") + p.buff_total("lifeRegenPerSecond")  # Recovery, Grace, Divine Aegis, items
        p.life = min(p.max_life, p.life + regen_life * dt)
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
        if p.portal_cast_left > 0:  # standing still while the portal opens
            p.portal_cast_left -= dt
            if p.portal_cast_left <= 0:
                self._finish_portal(p)
            return
        if self.moves.step(p, dt):  # dashing (Charge, Blade Dance)
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
            release = self.data.balance.magic.cast_release_fraction if p.attack_skill in SPELL_SKILLS else self.timing.attack_hit_fraction
            if not p.attack_hit_done and p.attack_elapsed >= p.attack_duration * release:
                p.attack_hit_done = True
                self._player_hit(p)
            if p.attack_elapsed >= p.attack_duration:
                p.state = "idle"
                if p.attack_skill is not None:
                    p.target_id = None  # a skill is a single swing
            return

        # area skill (or a bow skill aimed at the ground): starts in place
        in_place = (p.pending_skill in AREA_SKILLS or p.pending_skill in SPELL_SKILLS or p.pending_skill in GROUND_BOW_SKILLS
                    or (p.pending_skill in BOW_SKILLS and p.target_id is None))
        if in_place and not p.guarding:
            if p.cooldowns.ready("global") and self.skill_block_reason(p, p.pending_skill) is None:
                self._begin_attack(p, None, p.pending_skill)
            p.pending_skill = None
            return

        # walk to a drop and take it
        if p.pickup_id is not None and self._step_pickup(p, dt):
            return

        # chase/attack the target (not while spinning: Whirlwind hits on its own)
        if p.target_id is not None and not p.guarding and p.channel is None:
            t = self.monsters.get(p.target_id)
            if t is None or not t.alive:
                p.target_id = p.pending_skill = None
            elif self.in_reach(p, t):
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
            speed = self.player_speed(p)
            if self._step_towards(p, p.path[0], speed, dt):
                p.path.pop(0)
            if not p.guarding and p.path:
                self._face(p, p.path[0][0] - p.x, p.path[0][1] - p.y)

        if self.data.balance.loot.auto_pickup_gold:
            for g in [g for g in self.ground.values() if isinstance(g.drop, GoldDrop)]:
                if math.hypot(g.x - p.x, g.y - p.y) <= self.data.balance.loot.gold_pickup_range_tiles:
                    self._take(p, g)

        # your corpse: walking over it puts your gear back on
        for c in list(s.corpses):
            spot = self.corpse_spot(c)
            if spot is not None and math.hypot(spot[0] - p.x, spot[1] - p.y) <= CORPSE_TOUCH:
                for item in s.recover_corpse(c):
                    self.drop_at(ItemDrop(item), p.x, p.y, s.world_rng)
                p.life, p.energy = min(p.life, p.max_life), min(p.energy, p.max_energy)
                self._float(p.x, p.y, "Gear recovered", "levelup")

        # waypoint: touching it activates it (saved) and asks the client to show the list
        wp = self.layout.waypoint if self.layout else None
        if wp is not None:
            here = math.hypot(wp[0] - p.x, wp[1] - p.y) <= WAYPOINT_TOUCH
            if here and not p.on_waypoint:
                new = self.area_id not in s.waypoints
                s.waypoints.add(self.area_id)
                self.bus.publish(WaypointReached(p.player_id, self.area_id, new))
            p.on_waypoint = here

        # town portal, in the world or in town
        portal = self.portals.get(p.player_id)
        if portal is not None and p.exit_cooldown <= 0:
            spot = self.portal_spot(p.player_id)
            if spot is not None and math.hypot(spot[0] - p.x, spot[1] - p.y) <= PORTAL_TOUCH:
                p.exit_cooldown = EXIT_COOLDOWN
                back = self.data.is_town(self.area_id)
                kind = ExitKind.PORTAL_BACK if back else ExitKind.PORTAL_TO_TOWN
                self.bus.publish(ExitReached(p.player_id, kind.value, portal.area_id if back else self.data.town_of(portal.area_id)))
                return

        # exits: a zone around the cell, entered on foot
        if self.layout:
            here = self.exit_at(p.x, p.y)
            if here is not None and not p.in_exit and p.exit_cooldown <= 0:
                p.exit_cooldown = EXIT_COOLDOWN
                p.in_exit = True
                self.bus.publish(ExitReached(p.player_id, here.kind.value, here.target_area))
                return
            p.in_exit = here is not None

    # ------------------------------------------------------------ waypoints and town

    def corpse_spot(self, c) -> tuple[float, float] | None:
        """Where a corpse lies in the current area, or None. One from an earlier game lies in town by the waypoint."""
        if self.layout is None or c.area_id != self.area_id or c.floor != self.floor:
            return None
        if c.x is not None:
            return (c.x, c.y)
        base = self.layout.waypoint or self.layout.entry
        cell = self._inward((base[0] - 2, base[1] + 1))
        return (float(cell[0]), float(cell[1]))

    def at_waypoint(self, p: PlayerActor, slack: float = 1.5) -> bool:
        wp = self.layout.waypoint if self.layout else None
        return wp is not None and math.hypot(wp[0] - p.x, wp[1] - p.y) <= slack

    def _travel(self, p: PlayerActor, area_id: str) -> str | None:
        if not self.at_waypoint(p):
            return "Stand on a waypoint to travel"
        if area_id not in p.session.waypoints:
            return "You have not found that waypoint yet"
        if area_id == self.area_id:
            return None
        self.enter_area(area_id, 0)
        wp = self.layout.waypoint
        if wp is not None:  # beside it, on the first free neighbour
            spot = next((c for c in ((wp[0] + 1, wp[1]), (wp[0], wp[1] + 1), (wp[0] - 1, wp[1]), (wp[0], wp[1] - 1)) if self.layout.is_floor(c)), wp)
            p.x, p.y = float(spot[0]), float(spot[1])
        return None

    def npc_near(self, p: PlayerActor, role: str) -> str | None:
        """Name of an NPC with ``role`` within reach of the player (merchant, stash, crafter), or None."""
        if self.layout is None:
            return None
        return next((name for name, (_, props) in self.layout.markers.items() if props.get("role") == role and self.marker_near(p, name)), None)

    def marker_near(self, p: PlayerActor, name: str) -> bool:
        """Is the player within talking reach of this map NPC?"""
        marker = self.layout.markers.get(name) if self.layout is not None else None
        return marker is not None and math.hypot(marker[0][0] - p.x, marker[0][1] - p.y) <= self.data.items.trade.npc_range_tiles

    def _craft(self, p: PlayerActor, cmd) -> str | None:
        """Salvaging and recipes at the crafter (core/crafting.py)."""
        s = p.session
        if not self.npc_near(p, "crafter"):
            return "Stand by the crafter"
        if not 0 <= cmd.index < len(s.inventory.items):
            return "no such item"
        item = s.inventory.items[cmd.index]
        if isinstance(cmd, Salvage):
            got = self.crafter.salvage_yield(item)
            s.inventory.take(cmd.index)
            s.materials.update(got)
            names = self.data.crafting.materials
            self.bus.publish(ItemCrafted(p.player_id, "salvage", ", ".join(f"{n} {names[m]}" for m, n in got.items())))
            return None
        recipe = next((r for r in self.data.crafting.recipes if r.id == cmd.recipe_id), None)
        if recipe is None:
            return "no such recipe"
        why = self.crafter.why_not(recipe, item, s.materials)
        if why:
            return why
        made = self.crafter.apply(recipe, item, s.character.level, s.loot_rng)
        if not s.inventory.replace(cmd.index, made):
            return "No room in the backpack"
        s.materials.subtract(recipe.cost)
        self.bus.publish(ItemCrafted(p.player_id, recipe.id, made.name))
        return None

    def _town(self, p: PlayerActor, cmd) -> str | None:
        s, d = p.session, self.data
        if isinstance(cmd, StashPut | StashTake | StashGold):
            if self.stash is None or not self.npc_near(p, "stash"):
                return "Stand by the stash"
            if isinstance(cmd, StashPut):
                if not 0 <= cmd.index < len(s.inventory.items):
                    return "no such item"
                if not self.stash.can_add(s.inventory.items[cmd.index]):
                    return "No room in the stash"
                item = s.inventory.take(cmd.index)
                self.stash.add(item, cmd.at)
            elif isinstance(cmd, StashTake):
                if not 0 <= cmd.index < len(self.stash.items):
                    return "no such item"
                if not s.inventory.can_add(self.stash.items[cmd.index]):
                    return "No room in the backpack"
                s.inventory.add(self.stash.withdraw(cmd.index), cmd.at)
            else:
                amount = min(cmd.amount, s.gold) if cmd.amount > 0 else -min(-cmd.amount, self.stash.gold)
                s.gold -= amount
                self.stash.gold += amount
            self.bus.publish(StashChanged(p.player_id))
            return None
        if not self.npc_near(p, "vendor"):
            return "Stand by the merchant"
        if isinstance(cmd, Buy):
            if not 0 <= cmd.index < len(self.vendor.stock):
                return "no such item"
            item = self.vendor.stock.items[cmd.index]
            price = item_price(d, item)
            if s.gold < price:
                return f"{item.name} costs {price} gold"
            if not s.inventory.can_add(item):
                return "No room in the backpack"
            s.gold -= price
            s.inventory.add(self.vendor.stock.take(cmd.index))
            return None
        if isinstance(cmd, BuyConsumable):
            c = d.items.consumables.get(cmd.consumable_id)
            if c is None or c.price <= 0:
                return "not for sale"
            if s.gold < c.price:
                return f"{c.name} costs {c.price} gold"
            if not s.has_room_for(cmd.consumable_id):
                return f"Your belt is full ({s.carry_limit(cmd.consumable_id)} {c.name}s)"
            s.gold -= c.price
            s.consumables[cmd.consumable_id] += 1
            return None
        if not 0 <= cmd.index < len(s.inventory.items):
            return "no such item"
        item = s.inventory.take(cmd.index)
        s.gold += sell_price(d, item)
        self.vendor.stock.add(item)  # D2 style: what you sold can be bought back while you stay (if the shelf has room)
        return None

    def _move_item(self, p: PlayerActor, cmd: MoveItem) -> str | None:
        if cmd.container == "stash":
            if self.stash is None or not self.npc_near(p, "stash"):
                return "Stand by the stash"
            grid = self.stash
        else:
            grid = p.session.inventory
        if not grid.move(cmd.index, cmd.x, cmd.y):
            return "It does not fit there"
        if cmd.container == "stash":
            self.bus.publish(StashChanged(p.player_id))
        return None

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
        if c.restore:
            top = p.max_life if c.restore == "life" else p.max_energy
            secs = max(c.seconds, 1e-6)
            p.restores.append([c.restore, top * c.pct_of_max / secs, secs])
            self._float(p.x, p.y, c.name, "info")
        s.consumables[cid] -= 1
        p.cooldowns.start(cid, c.cooldown_seconds)
        self.bus.publish(ConsumableUsed(p.player_id, cid))
        return None

    def _open_portal(self, p: PlayerActor) -> str | None:
        """Starts opening a portal: the player stands still for townPortal.castSeconds, then it appears."""
        if self.data.is_town(self.area_id):
            return "You are already in town"
        if not p.cooldowns.ready("town_portal"):
            return f"Town Portal: {p.cooldowns.remaining('town_portal'):.1f}s"
        if p.portal_cast_left > 0 or p.busy or p.state == "attacking":
            return None
        p.path.clear()
        p.target_id = p.pending_skill = p.pickup_id = None
        self._lower_guard(p, start_cooldown=False)
        p.portal_cast_left = max(1e-6, self.data.balance.town_portal.cast_seconds)
        front = (p.x + p.face_x, p.y + p.face_y)
        p.portal_at = front if self.layout.is_floor(cell_of(*front)) else (p.x, p.y)
        return None

    def _finish_portal(self, p: PlayerActor) -> None:
        p.portal_cast_left = 0.0
        x, y = p.portal_at or (p.x, p.y)
        p.portal_at = None
        self.portals[p.player_id] = TownPortal(self.area_id, self.floor, x, y)  # replaces the old one
        p.exit_cooldown = EXIT_COOLDOWN  # do not step through it the moment it opens
        p.cooldowns.start("town_portal", self.data.balance.town_portal.cooldown_seconds)
        self._float(p.x, p.y, "Town Portal", "info")

    def _cancel_portal(self, p: PlayerActor) -> None:
        if p.portal_cast_left > 0:
            p.portal_cast_left, p.portal_at = 0.0, None
            self._float(p.x, p.y, "portal stopped", "miss")

    @property
    def portal_cast_seconds(self) -> float:
        return self.data.balance.town_portal.cast_seconds

    def take_portal(self, player_id: int) -> None:
        """Goes through the player's portal: from the world to town, or from town back to where it was opened."""
        portal = self.portals.get(player_id)
        if portal is None:
            return
        back = self.data.is_town(self.area_id)
        if back:
            self.enter_area(portal.area_id, portal.floor)
        else:
            self.enter_area(self.data.town_of(portal.area_id))
        spot = self.beside_portal(player_id)
        p = self.players[player_id]
        if spot is not None:
            p.x, p.y = spot
        if back:  # used to come back: it closes behind you
            del self.portals[player_id]

    def portal_spot(self, player_id: int) -> tuple[float, float] | None:
        """Where the player's portal stands in the current area: where it was opened, or next to the town waypoint."""
        portal = self.portals.get(player_id)
        if portal is None or self.layout is None:
            return None
        if self.area_id == portal.area_id and self.floor == portal.floor:
            return (portal.x, portal.y)
        if self.area_id == self.data.town_of(portal.area_id):
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

    def player_speed(self, p: PlayerActor) -> float:
        """Tiles per second: base, + moveSpeedPct (boots), halved with the guard up."""
        base = self.data.balance.movement.player_tiles_per_second * (1 + p.session.stat("moveSpeedPct"))
        if p.slow_left > 0:
            base *= 1 - p.slow_pct
        return base * (self.data.balance.guard.move_speed_mult if p.guarding else 1.0)

    def player_reach(self, p: PlayerActor) -> float:
        """Melee reach, or the bow's range (+ Eagle Eye)."""
        s = p.session
        if s.gear.has_bow:
            return s.gear.main_hand.base.range_tiles + s.stat("bowRangeTiles")  # Eagle Eye, items
        return MELEE_RANGE

    @staticmethod
    def melee_gap(p: PlayerActor, t: MonsterActor) -> float:
        """Distance for melee, measured to the edge of the target's body: a wide wolf is reached sooner."""
        return math.hypot(t.x - p.x, t.y - p.y) - max(0.0, t.radius - DEFAULT_BODY_RADIUS)

    def in_reach(self, p: PlayerActor, t: MonsterActor) -> bool:
        """Close enough to strike, or (with a bow) in range and in sight."""
        d = math.hypot(t.x - p.x, t.y - p.y)
        if not p.session.gear.has_bow:
            return self.melee_gap(p, t) <= MELEE_RANGE
        return d <= self.player_reach(p) and line_of_sight(self.layout, (p.x, p.y), (t.x, t.y))

    def attack_speed(self, p: PlayerActor) -> float:
        """Attack speed bonus: generic, the weapon's own (great weapons are slower), passives for its tags
        (Quick Draw: bowAttackSpeedPct), Ambidexterity with two weapons, Frenzy stacks and buffs."""
        s, t, gear = p.session, p.session.skills, p.session.gear
        ias = s.stat("attackSpeedPct") + p.buff_total("buffAttackSpeedPct")
        w = gear.main_hand
        if w is not None:
            ias += w.base.attack_speed_pct + sum(s.stat(f"{tag}AttackSpeedPct") for tag in w.base.tags)
        if gear.dual_wielding:
            ias += t.effect("ambidexterity", "dualAttackSpeedPct")
        ias += p.rhythm_stacks * t.effect("combat_rhythm", "attackSpeedPerStackPct")
        if p.slow_left > 0:
            ias -= p.slow_pct
        return ias + p.frenzy_stacks * t.effect("frenzy", "attackSpeedPerStackPct")

    def swing_proficiency(self, p: PlayerActor) -> str:
        """Who gets the XP of a plain swing: Dual Wield with two weapons, else the proficiency of the main-hand
        weapon's tags (Blades for a sword, Maces for a mace…), else the unarmed one (proficiencies.json)."""
        gear = p.session.gear
        return self.data.proficiencies.for_weapon(gear.main_hand.base.tags if gear.main_hand is not None else (), gear.dual_wielding)

    def _begin_attack(self, p: PlayerActor, target_id: int | None, skill: str | None, commit: bool = True) -> None:
        ias = self.attack_speed(p)
        p.state = "attacking"
        p.attack_elapsed = 0.0
        spell = skill in SPELL_SKILLS
        p.attack_duration = self.spells.cast_seconds(p) if spell else self.timing.attack_seconds / (1 + ias)
        p.attack_hit_done = False
        p.attack_skill = skill
        p.attack_target = target_id
        dual = p.session.gear.dual_wielding
        p.attack_hand = ("off" if p.attack_hand == "main" else "main") if dual else "main"
        p.cooldowns.start("attack", p.attack_duration)
        if skill and commit:
            self._commit_skill(p, skill)

    def _player_hit(self, p: PlayerActor) -> None:
        s = p.session
        skill = p.attack_skill
        if skill in SPELL_SKILLS:
            self.spells.release(p, skill)
            return
        if skill in GROUND_BOW_SKILLS:
            self.spells.rain(p, skill)
            return
        if s.gear.has_bow:
            self._shoot_arrows(p, skill)
            return
        targets: list[MonsterActor] = []
        if skill in AREA_SKILLS:
            arc = s.skills.effect(skill, "arcDegrees")  # fixed arc (base); points raise the damage
            reach = s.skills.effect(skill, "radiusTiles") or MELEE_RANGE + 0.4
            for m in self.monsters.values():
                if m.alive and self.melee_gap(p, m) <= reach:
                    if s.combat.in_arc(p.face_x, p.face_y, m.x - p.x, m.y - p.y, arc):
                        targets.append(m)
        elif p.attack_target is not None:
            t = self.monsters.get(p.attack_target)
            if t and t.alive and self.melee_gap(p, t) <= MELEE_RANGE + 0.6:
                targets.append(t)
        else:  # in place (Shift+click): the closest one in reach in front, if any
            arc = self.data.balance.combat.in_place_arc_degrees
            near = [m for m in self.monsters.values() if m.alive and self.melee_gap(p, m) <= MELEE_RANGE + 0.6
                    and s.combat.in_arc(p.face_x, p.face_y, m.x - p.x, m.y - p.y, arc)]
            if near:
                targets.append(min(near, key=lambda m: math.hypot(m.x - p.x, m.y - p.y)))
        hands = ("main", "off") if skill in TWO_HAND_HITS else (p.attack_hand,)
        for t in targets:
            self.strike(p, t, skill, hands)
        if not targets:
            p.rhythm_stacks = 0  # a swing at nothing breaks the rhythm

    def strike(self, p: PlayerActor, t: MonsterActor, skill: str | None, hands: tuple[str, ...]) -> None:
        """One melee blow (or one per hand) on ``t``: rolled with every bonus, then what lands with it (stun by the
        weapon, Bleed, Frenzy and Combat Rhythm stacks, the skill's push or stun). XP: a skill feeds its own tree,
        a plain swing the weapon's proficiency. Swings, Whirlwind spins and Blade Dance all hit through here."""
        s = p.session
        prof = self.data.node(skill).tree if skill else self.swing_proficiency(p)
        for hand in hands:
            if not t.alive:
                break
            atk = self._hit_bonuses(p, t, player_attack(s.character, s.skills, s.gear, skill, hand))
            if t.ranged:  # closing in on an archer or caster pays off
                atk = melee_edge(atk, self.data.balance.combat)
            r = s.combat.resolve_physical(atk, t.defense, s.combat_rng)
            if not r.hit:
                self._float(t.x, t.y, "miss", "miss")
                p.rhythm_stacks = 0
                continue
            fire = s.gear.stat_total("fireDamage")  # items: flat fire on every blow, resisted like a spell
            amount = r.final + (self.spells.resisted(p, t, fire, "fire") if fire > 0 else 0.0)
            self._damage_monster(p, t, amount, prof, crit=r.crit, skill=skill)
            self._weapon_stun(p, t, s.gear.weapon(hand))
            self._after_blow(p, t, s.gear.weapon(hand), skill)
        if skill and t.alive:
            self._skill_on_hit(p, t, skill)

    def _after_blow(self, p: PlayerActor, t: MonsterActor, weapon, skill: str | None) -> None:
        """Passives that react to a landed blow: Combat Rhythm stacks, Bleed, Frenzy; Riposte is used up."""
        sk = p.session.skills
        p.riposte_ready = False
        top = round(sk.effect("combat_rhythm", "maxStacks"))
        if top > 0:
            p.rhythm_stacks = min(top, p.rhythm_stacks + 1)
            p.rhythm_left = RHYTHM_SECONDS
        if skill == "frenzy":
            p.frenzy_stacks = min(round(sk.effect("frenzy", "maxStacks")), p.frenzy_stacks + 1)
            p.frenzy_left = sk.effect("frenzy", "durationSeconds")
        chance = sk.effect("bleed", "bleedChance")
        if chance > 0 and t.alive and p.session.combat_rng.chance(chance):
            base = (weapon.base.dmg_min + weapon.base.dmg_max) / 2 if weapon is not None and weapon.base.dmg_max > 0 else 1.5
            t.add_dot("bleed", Dot(base * sk.effect("bleed", "bleedDamagePct"), sk.effect("bleed", "durationSeconds") or 4.0,
                                   p.player_id, skill or "", self.data.node(skill).tree if skill else self.swing_proficiency(p)))

    def _hit_bonuses(self, p: PlayerActor, t: MonsterActor, atk):
        """Bonuses that depend on the target: against its family (Bone Breaker: undeadDamagePct), against a stunned
        enemy (Executioner), and the player's buffs (Berserk)."""
        sk = p.session.skills
        extra = p.session.stat(f"{t.monster.type.family}DamagePct") + p.buff_total("buffDamagePct")
        if t.state == "stunned":
            extra += sk.stat_total("damageVsStunnedPct")
        if p.riposte_ready:  # Riposte: the swing after a block
            extra += sk.effect("riposte", "damageAfterBlockPct")
        missing = 1 - p.life / max(1.0, p.max_life)  # Colossus: harder the more life is missing
        extra += math.floor(missing * 10 + 1e-9) * sk.stat_total("damagePerMissingLife10Pct")
        if extra:
            atk = with_bonus(atk, extra)
        low = sk.effect("killing_blow", "lowLifeThreshold")
        if low > 0 and t.monster.hp < t.monster.max_hp * low:  # Killing Blow: crits on the nearly dead
            atk = replace(atk, crit_chance=min(0.95, atk.crit_chance + sk.effect("killing_blow", "critChanceLowLife")))
        return atk

    def _weapon_stun(self, p: PlayerActor, t: MonsterActor, weapon) -> None:
        """Passives that stun with a kind of weapon (Concussive: maceStunChance / maceStunSeconds)."""
        if weapon is None or not t.alive:
            return
        sk = p.session.skills
        for tag in weapon.base.tags:
            chance, secs = sk.stat_total(f"{tag}StunChance"), sk.stat_total(f"{tag}StunSeconds")
            if chance > 0 and secs > 0 and t.state != "stunned" and p.session.combat_rng.chance(chance):
                self._stun_monster(t, secs)
                return

    def _skill_on_hit(self, p: PlayerActor, t: MonsterActor, skill: str) -> None:
        """What a skill's stats do to whoever it hit: knockbackTiles pushes away, stunSeconds stuns (always, or with
        stunChance when the skill has that stat)."""
        sk = p.session.skills
        push = sk.effect(skill, "knockbackTiles")
        if push > 0:
            self._knock_back(t, p.x, p.y, push)
        secs = sk.effect(skill, "stunSeconds")
        if secs > 0:
            chance = sk.effect(skill, "stunChance") if "stunChance" in sk.stats_of(skill) else 1.0
            if p.session.combat_rng.chance(chance):
                self._stun_monster(t, secs)

    def _damage_monster(self, p: PlayerActor, t: MonsterActor, amount: float, prof: str, crit: bool = False, recover: bool = True,
                        skill: str | None = None) -> None:
        """Every hit on a monster goes through here: XP to ``prof`` and to the active ``skill`` that dealt it."""
        eff = t.monster.take_damage(amount)
        self.bus.publish(DamageDealt(prof, t.monster.xp_info, eff, p.player_id, skill or ""))
        leech = p.session.gear.stat_total("lifeLeechPct") + (p.session.skills.effect("bloodlust", "lifeLeechPct") if p.session.gear.dual_wielding else 0.0)
        if eff > 0 and leech > 0:  # items, and Bloodlust with two weapons
            p.life = min(p.max_life, p.life + eff * leech)
        self._float(t.x, t.y, f"{amount:.0f}{'!' if crit else ''}", "crit" if crit else "damage")
        self._alert(t)
        if not t.alive:
            self._kill(t, p)
            return
        self.brains.on_hurt(t)
        rec = monster_hit_recovery(self.timing, amount, t.monster.max_hp, t.monster.rank.id) if recover else 0.0
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

    def _stun_monster(self, t: MonsterActor, seconds: float, word: str = "stunned") -> None:
        """Stunned (or frozen: the same state, another word): it neither moves nor attacks."""
        seconds *= self._cc_mult(t)
        t.state, t.state_left = "stunned", seconds
        t.attack_elapsed, t.attack_hit_done = 0.0, False
        self._float(t.x, t.y, word, "info")

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
        self.brains.on_death(t)
        self.bus.publish(MonsterKilled(m.xp_info, m.rank.id, killer.player_id))
        s = killer.session
        if m.boss is not None:  # every player in the area has beaten it (acts it unlocks open for all)
            for other in self.sessions.values():
                for town in other.defeat_boss(m.boss.id):
                    self.bus.publish(ActOpened(other.player_id, town))
        if not m.no_reward:
            for drop in s.loot.roll_drops(m.rank.id, m.level, s.stat("magicFind"), s.loot_rng):
                self.drop_at(drop, t.x, t.y, s.world_rng)
        del self.monsters[t.id]
        t.dots, t.debuffs, t.rush, t.winding = {}, {}, None, None
        self.fallen.append(t)

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
        if isinstance(g.drop, ChestDrop):  # a chest opens: what it holds falls around it
            s = p.session
            del self.ground[g.id]
            for drop in s.loot.roll_drops(self.data.areas.prefabs.chest_loot_as, g.drop.level, s.stat("magicFind"), s.loot_rng):
                self.drop_at(drop, g.x, g.y, s.world_rng)
            self.bus.publish(LootPicked(p.player_id, g.drop, True))
            return True
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
                self._step_towards(p, (g.x, g.y), self.data.balance.movement.player_tiles_per_second, dt)
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
            if not lead.alive:
                continue
            auras = [lead.aura] if lead.aura is not None else []
            auras += [mod.aura for mod in lead.monster.modifiers if mod.aura is not None]  # Fury Aura on a unique
            for aura in auras:
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
        if m.state in ("hit_recovery", "stunned", "winding"):
            m.state_left -= dt
            m.attack_elapsed += dt
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
                m.attack_cooldown = m.attack_interval - m.attack_duration  # the pause before the next swing
            return

        if m.alert:
            self._face(m, target.x - m.x, target.y - m.y)
            if self.brains.think(m, target, dist, dt):
                return
            if m.ranged and dist < self.data.balance.combat.ranged_kite_tiles and self._step_back(m, target, dt):
                return  # an archer caught up close backs off before shooting again
            sees = not m.ranged or line_of_sight(self.layout, (m.x, m.y), (target.x, target.y))
            if dist > m.reach or not sees:  # archers and casters get closer until they have a clear shot
                self._step_towards(m, (target.x, target.y), m.speed, dt)
                self._separate(m, dt)
            elif m.attack_cooldown <= 0:
                m.state, m.attack_elapsed, m.attack_hit_done = "attacking", 0.0, False
        elif m.leader_id is not None:
            leader = self.monsters.get(m.leader_id)
            if leader and math.hypot(leader.x - m.x, leader.y - m.y) > 4:
                self._face(m, leader.x - m.x, leader.y - m.y)
                self._step_towards(m, (leader.x, leader.y), m.speed * 0.6, dt)

    def _step_back(self, m: MonsterActor, threat: PlayerActor, dt: float) -> bool:
        """Moves away from ``threat``, or slantwise when straight back is blocked. False when cornered."""
        dx, dy = m.x - threat.x, m.y - threat.y
        n = math.hypot(dx, dy) or 1.0
        step = m.speed * dt
        for ang in (0.0, 0.6, -0.6, 1.2, -1.2):
            c, s = math.cos(ang), math.sin(ang)
            ux, uy = (dx * c - dy * s) / n, (dx * s + dy * c) / n
            if self._try_move(m, m.x + ux * step, m.y + uy * step):
                return True
        return False

    def _separate(self, m: MonsterActor, dt: float) -> None:
        """Gently pushes overlapping monsters apart (packs spawn close together)."""
        if not m.collides:
            return
        push_x = push_y = 0.0
        for o in self._near_bodies(m.x, m.y):
            if o is m or not o.collides or o in self.players.values():
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
        atk = melee_edge(m.attack, self.data.balance.combat) if p.session.gear.has_bow else m.attack  # an archer caught in melee
        dealt = self._hit_player(p, atk, m.monster.xp_info, m.x, m.y, magic=False, melee=m, extra=self.brains.elemental_extra(m))
        self.brains.on_hit_player(m, p, dealt)

    def stun_player(self, p: PlayerActor, seconds: float) -> None:
        """Stunned by a monster (a boar's charge): Steadfast (ccReduction) shortens it."""
        seconds *= max(0.0, 1 - p.session.stat("ccReduction"))
        if seconds <= 0 or p.state == "dead":
            return
        self._lower_guard(p, start_cooldown=True)
        self._cancel_portal(p)
        p.dash = p.channel = None
        p.state, p.state_left = "stunned", seconds
        p.path.clear()
        self._float(p.x, p.y, "stunned", "hurt")

    def slow_player(self, p: PlayerActor, pct: float, seconds: float) -> None:
        """Chilled by a monster: moves and swings slower for a while (the strongest slow wins; ccReduction shortens it)."""
        seconds *= max(0.0, 1 - p.session.stat("ccReduction"))
        if seconds > 0 and (pct >= p.slow_pct or p.slow_left <= 0):
            p.slow_pct, p.slow_left = min(0.9, pct), seconds

    def spawn_monster(self, type_id: str, level: int, x: float, y: float, no_reward: bool = False) -> MonsterActor | None:
        """A new monster in the area (a boss's summons), at the nearest free spot."""
        host = next(iter(self.sessions.values()))
        inst = host.monster_factory.create(self.data.monster_type(type_id), level, "normal", host.spawn_rng)
        inst.no_reward = no_reward
        a = MonsterActor(inst, x, y, base_speed=self.data.balance.movement.monster_base_tiles_per_second,
                         swing_seconds=self.timing.monster_swing_seconds)
        for r in (0.0, 0.6, 1.2, 1.8):
            for k in range(8):
                cx, cy = x + math.cos(k * math.pi / 4) * r, y + math.sin(k * math.pi / 4) * r
                if self.clear_of_walls(a, cx, cy) and not self._overlaps_any(a, cx, cy):
                    a.x, a.y = cx, cy
                    a.pack_ids = [inst.instance_id]
                    self.monsters[inst.instance_id] = a
                    return a
        return None

    @property
    def _host_rng(self):
        return next(iter(self.sessions.values())).world_rng

    @staticmethod
    def _in_arc(fx: float, fy: float, dx: float, dy: float, arc: float) -> bool:
        from .combat import CombatMath

        return CombatMath.in_arc(fx, fy, dx, dy, arc)

    def player_resist(self, p: PlayerActor, element: str) -> float:
        """Share of an element's damage the player ignores: Elemental Resistance and items (elementalResist, or
        <element>Resist), Bastion with a shield; capped at combat.resistCap."""
        s = p.session
        r = s.stat("elementalResist") + s.stat(f"{element}Resist") + p.buff_total("buffResist")
        if s.gear.has_shield:
            r += s.skills.stat_total("allResistWithShield")
        return min(r, self.data.balance.combat.resist_cap)

    def _hit_player(self, p: PlayerActor, attack, info, from_x: float, from_y: float, magic: bool, melee: MonsterActor | None,
                    element: str | None = None, extra: dict[str, float] | None = None) -> float:
        """A swing or a shot reaches the player. (from_x, from_y) is where it comes from, for the guard arc.
        Magic gets past the guard unless Bulwark is up; only melee hits can be counterattacked.
        ``element``: the whole hit is of that element (a shaman's bolt): armor does nothing, resistance takes its
        share. ``extra``: element → share of the hit added on top as that element (Fire Enchanted)."""
        s = p.session
        deflect = s.skills.effect("blade_guard", "deflectChance")
        if melee is not None and s.gear.dual_wielding and deflect > 0 and s.combat_rng.chance(deflect):
            self._float(p.x, p.y, "deflected", "block")  # Blade Guard: the blades turn the blow aside
            return
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
        buff = p.buff_total("buffDefensePct")  # Berserk lowers it
        if buff:
            defense = replace(defense, defense=defense.defense * max(0.0, 1 + buff), armor=defense.armor * max(0.0, 1 + buff))
        r = s.combat.resolve_physical(attack, defense, s.combat_rng, guard)
        if not r.hit:
            self._float(p.x, p.y, "dodged", "miss")
            return 0.0
        p.energy = max(0.0, p.energy - r.guard_energy_cost)
        final, mitigated = r.final, r.mitigated
        if element is not None:  # all of it elemental: past the guard as usual, then resistance instead of armor
            final, mitigated = r.reached_armor * (1 - self.player_resist(p, element)), 0.0
        for el, share in (extra or {}).items():
            final += r.reached_armor * share * (1 - self.player_resist(p, el))
        final *= max(0.0, 1 + p.buff_total("buffDamageTakenPct"))  # Sanctuary
        thorns = p.buff_total("buffThornsPct")
        if melee is not None and thorns > 0 and melee.alive:  # Thorn Ward: the attacker takes part of it back
            self._damage_monster(p, melee, final * thorns, self.data.node("thorn_ward").tree, recover=False, skill="thorn_ward")
        if p.barrier > 0 and final > 0:  # the barrier first; what it takes teaches Warding
            absorbed = min(p.barrier, final)
            p.barrier -= absorbed
            self.bus.publish(DamageAbsorbed(info, final, absorbed, p.player_id, p.barrier_skill))
            final -= absorbed
            if p.barrier <= 0:
                p.buffs.pop(p.barrier_skill, None)
                self._float(p.x, p.y, "barrier broken", "hurt")
        shield = s.stat("manaShieldPct")
        if shield > 0 and final > 0 and p.energy > 0:  # Mana Shield: part of it out of Energy instead
            from_energy = min(p.energy, final * shield)
            p.energy -= from_energy
            final -= from_energy

        if r.blocked:  # the guard stops most of the hit, never all of it (GDD 7.4)
            perfect = r.block == BlockOutcome.PERFECT
            self.bus.publish(HitBlocked(info, perfect, p.player_id))
            p.riposte_ready = s.skills.points_in("riposte") > 0
            word = "parried!" if perfect else "blocked"
            self._float(p.x, p.y, f"{word} -{final:.0f}", "perfect" if perfect else "block")
            if perfect and melee is not None:
                self._counter(p, melee)
            else:
                fbr = s.stat("fasterBlockRecovery")
                p.state, p.state_left = "block_recovery", player_block_recovery(self.timing, fbr)
                p.path.clear()
        elif r.block == BlockOutcome.GUARD_BROKEN:
            self._lower_guard(p, start_cooldown=True)
            self._cancel_portal(p)
            p.state, p.state_left = "stunned", self.data.balance.guard.guard_break_stun_seconds
            p.path.clear()
            self._float(p.x, p.y, "guard broken", "hurt")
        if mitigated > 0:  # armor works on what got past the guard
            self.bus.publish(DamageMitigated(info, r.reached_armor, mitigated, p.player_id))
        if not r.blocked:
            self._float(p.x, p.y, f"-{final:.0f}", "hurt")
        defer = s.skills.stat_total("deferredDamagePct")  # Unshakable: part of it arrives over a few seconds
        if defer > 0:
            secs = max(0.1, s.skills.effect("unshakable", "deferSeconds"))
            p.deferred.append([final * defer / secs, secs])
        if self._lose_life(p, final * (1 - defer)):
            return final
        if not r.blocked and p.state != "stunned":
            fhr = s.stat("fasterHitRecovery")
            rec = player_hit_recovery(self.timing, final, p.max_life, fhr)
            if rec > 0:
                p.state, p.state_left = "hit_recovery", rec  # interrupts attack, movement and an opening portal
                p.attack_hit_done = True
                self._cancel_portal(p)
        return final

    def _lose_life(self, p: PlayerActor, amount: float) -> bool:
        """Takes life (not in god mode). Second Wind heals once when it falls low. True: the player died."""
        if p.god_mode or amount <= 0:
            return False
        p.life -= amount
        sk = p.session.skills
        low = sk.effect("second_wind", "threshold")
        if 0 < p.life < p.max_life * low and p.cooldowns.ready("second_wind"):
            heal = p.max_life * sk.effect("second_wind", "healPctOfMax")
            p.life = min(p.max_life, p.life + heal)
            p.cooldowns.start("second_wind", sk.effect("second_wind", "cooldownSeconds"))
            self._float(p.x, p.y, f"Second Wind +{heal:.0f}", "heal")
        if p.life <= 0:
            p.life = 0
            p.state = "dead"
            p.path.clear()
            p.dash = p.channel = None
            p.deferred.clear()
            p.barrier = 0.0
            p.session.leave_corpse(self.area_id, self.floor, p.x, p.y)  # the gear stays on the body
            self.bus.publish(PlayerDied(p.player_id))
            return True
        return False

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
                            owner="monster", owner_id=m.id, magic=d.magic, attack=m.attack, info=m.monster.xp_info, element=d.element)
        proj.aim(target.x, target.y)

    def _shoot_arrows(self, p: PlayerActor, skill: str | None) -> None:
        """A bow's shot: toward the target where it is now (or where the player faces), in straight lines."""
        s, b = p.session, self.data.balance.bow
        t = self.monsters.get(p.attack_target) if p.attack_target is not None else None
        if t is not None and t.alive:
            self._face(p, t.x - p.x, t.y - p.y)
        base = math.atan2(p.face_y, p.face_x)
        count, spread = 1, 0.0
        if skill == "multishot":
            count = max(1, math.floor(s.skills.effect("multishot", "arrows") + 1e-9))
            if t is not None and t.alive:
                dist = math.hypot(t.x - p.x, t.y - p.y)
            elif p.aim_x is not None:
                dist = math.hypot(p.aim_x - p.x, p.aim_y - p.y)
            else:
                dist = self.player_reach(p) / 2
            spread = math.radians(self.multishot_spread(p, dist))
        reach = self.player_reach(p) + 1.0
        for k in range(count):
            a = base + (spread * (k / (count - 1) - 0.5) if count > 1 else 0.0)
            proj = self._launch(x=p.x, y=p.y, speed=b.arrow_speed, radius=b.arrow_radius, max_range=reach, look="arrow",
                                owner="player", owner_id=p.player_id, skill=skill, prof=self.data.node(skill).tree if skill else "bows",
                                pierce=skill == "piercing_arrow")
            proj.dir_x, proj.dir_y = math.cos(a), math.sin(a)

    def multishot_spread(self, p: PlayerActor, distance: float) -> float:
        """Fan angle in degrees: it covers about spreadTiles across at the target's distance, so it opens
        wide up close and closes far away (clamped between the narrowest and widest fan)."""
        e = p.session.skills.effect
        width = e("multishot", "spreadTiles")
        angle = math.degrees(2 * math.atan2(width / 2, max(0.5, distance)))
        return max(e("multishot", "minSpreadDegrees"), min(e("multishot", "maxSpreadDegrees"), angle))

    def _throw_shield(self, p: PlayerActor, cmd: UseSkill) -> str | None:
        s = p.session
        if p.busy or p.state == "attacking" or not p.cooldowns.ready("global"):
            return None
        t = self.monsters.get(cmd.target_id) if cmd.target_id is not None else None
        tx, ty = (t.x, t.y) if t else (cmd.x, cmd.y)
        if tx is None or ty is None:
            return "no target"
        e = s.skills.effect
        self._commit_skill(p, "shield_throw")
        proj = self._launch(x=p.x, y=p.y, speed=e("shield_throw", "projectileSpeed"), radius=0.25, max_range=e("shield_throw", "rangeTiles"),
                            look="shield", owner="player", owner_id=p.player_id, skill="shield_throw", prof="shield",
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
                if proj.element is not None:
                    self.spells.projectile_end(proj)  # a Fireball bursts on walls and at the end of its flight

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
                        self._hit_player(p, proj.attack, proj.info, x - proj.dir_x, y - proj.dir_y, proj.magic, melee=None,
                                         element=proj.element)
                        return False
            else:
                hit = next((m for m in self.monsters.values() if m.alive and m.id not in proj.hit_ids
                            and math.hypot(m.x - x, m.y - y) < m.radius + proj.radius), None)
                if hit is not None:
                    return self._player_projectile_hit(proj, hit)
        return not proj.spent

    def _player_projectile_hit(self, proj: Projectile, t: MonsterActor) -> bool:
        if proj.element is not None:
            return self.spells.projectile_hit(proj, t)
        p = self.players.get(proj.owner_id)
        if p is None:
            return False
        s = p.session
        proj.hit_ids.add(t.id)
        atk = self._hit_bonuses(p, t, player_attack(s.character, s.skills, s.gear, proj.skill))
        r = s.combat.resolve_physical(atk, t.defense, s.combat_rng)
        if r.hit:
            self._damage_monster(p, t, r.final, proj.prof or "bows", crit=r.crit, skill=proj.skill)
        else:
            self._float(t.x, t.y, "missed", "miss")
        if proj.pierce:
            return True  # goes on through the line; hit_ids keeps it from hitting the same one twice
        return self.bounce(proj)

    def bounce(self, proj: Projectile) -> bool:
        """Sends a bouncing shot (Shield Throw, Chain Lightning) on to the nearest enemy it has not hit yet, in sight
        and within its bounce range. False when there is none (or no bounce left): the shot ends."""
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

    def _on_skill_level_up(self, e: SkillLeveledUp) -> None:
        p = self.players.get(e.player_id)
        if p is not None:
            self._float(p.x, p.y, f"{self.data.node(e.skill_id).name} {e.level}!", "levelup")

    def _float(self, x: float, y: float, text: str, kind: str) -> None:
        self.bus.publish(FloatingText(x, y, text, kind))

    @staticmethod
    def _face(p: PlayerActor | MonsterActor, dx: float, dy: float) -> None:
        n = math.hypot(dx, dy)
        if n > 1e-6:
            p.face_x, p.face_y = dx / n, dy / n

    # ------------------------------------------------------------ movement and collision

    def _bodies(self):
        yield from self.players.values()
        yield from self.monsters.values()

    def _rebuild_grid(self) -> None:
        """Bodies by cell of BODY_CELL tiles: collision looks only at the cells around instead of every body.
        Built once a step; bodies move a fraction of a tile per step, so the 3 × 3 cells around still hold them."""
        grid: dict[tuple[int, int], list] = {}
        for b in self._bodies():
            if b.collides:
                grid.setdefault((math.floor(b.x / BODY_CELL), math.floor(b.y / BODY_CELL)), []).append(b)
        self._grid = grid

    def _near_bodies(self, x: float, y: float):
        """Bodies that may touch a point: from the grid when there is one (inside a step), else all of them."""
        if self._grid is None:
            yield from self._bodies()
            return
        cx, cy = math.floor(x / BODY_CELL), math.floor(y / BODY_CELL)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                yield from self._grid.get((gx, gy), ())

    def blocked_by_body(self, a, x: float, y: float):
        """The body ``a`` would overlap at (x, y), or None. Moving away from an overlap is always allowed,
        so actors that start on top of each other (spawns) can get apart."""
        if not a.collides:
            return None
        ar = a.radius
        for o in self._near_bodies(x, y):
            if o is a or not o.collides:
                continue
            touch = ar + o.radius
            dx, dy = x - o.x, y - o.y
            if abs(dx) >= touch or abs(dy) >= touch:
                continue
            if dx * dx + dy * dy < touch * touch and math.hypot(dx, dy) < math.hypot(a.x - o.x, a.y - o.y):
                return o
        return None

    def _overlaps_any(self, a, x: float, y: float) -> bool:
        return a.collides and any(
            o is not a and o.collides and math.hypot(x - o.x, y - o.y) < a.radius + o.radius for o in self._near_bodies(x, y)
        )

    def _hits_wall(self, x: float, y: float, r: float) -> bool:
        """The body's box (±r) at (x, y) reaches a wall cell: at most the 2×2 cells it straddles."""
        L = self.layout
        x0, x1 = math.floor(x - r + 0.5), math.floor(x + r + 0.5)
        y0, y1 = math.floor(y - r + 0.5), math.floor(y + r + 0.5)
        if x0 < 0 or y0 < 0 or x1 >= L.width or y1 >= L.height:
            return True
        tiles = L.tiles
        return any(tiles[cy, cx] != FLOOR for cy in range(y0, y1 + 1) for cx in range(x0, x1 + 1))

    def clear_of_walls(self, a, x: float, y: float) -> bool:
        """Center on the floor and the body's edge off the walls (up to collision.wallMarginMax, so a one-cell
        corridor fits anyone). A body that already touches a wall (a spawn) may still move: never stuck."""
        if not self.layout.is_floor(cell_of(x, y)):
            return False
        r = min(getattr(a, "radius", 0.0), self.data.balance.collision.wall_margin_max)
        return r <= 0 or not self._hits_wall(x, y, r) or self._hits_wall(a.x, a.y, r)

    def _free(self, a, x: float, y: float) -> bool:
        return self.clear_of_walls(a, x, y) and self.blocked_by_body(a, x, y) is None

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
            return self._try_move(a, float(tx), float(ty)) or not self.clear_of_walls(a, float(tx), float(ty))
        ux, uy = dx / dist, dy / dist
        nx, ny = a.x + ux * step, a.y + uy * step
        if self._try_move(a, nx, ny):
            return False
        body = self.blocked_by_body(a, nx, ny) if self.clear_of_walls(a, nx, ny) else None
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
        """The exit whose zone (balance.json → exits.touchRadiusTiles around its cell) contains (x, y)."""
        if not self.layout:
            return None
        r = self.data.balance.exits.touch_radius_tiles
        return next((e for e in self.layout.exits if math.hypot(e.pos[0] - x, e.pos[1] - y) <= r), None)
