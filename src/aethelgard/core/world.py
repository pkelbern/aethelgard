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
from dataclasses import dataclass, field

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
from .monsters import MonsterInstance, PackKind
from .pathfinding import find_path
from .session import GameSession
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
IMPLEMENTED_SKILLS = TARGET_SKILLS | AREA_SKILLS | SELF_SKILLS

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


Command = MoveTo | AttackTarget | UseSkill | SetGuard | Stop


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

    @property
    def id(self) -> int:
        return self.monster.instance_id

    @property
    def alive(self) -> bool:
        return self.monster.alive

    @property
    def speed(self) -> float:
        return self.monster.type.move_speed * 2.6 * (1 + self.monster.stat("moveSpeedPct"))

    @property
    def attack_duration(self) -> float:
        return 1.4 / (self.monster.type.attack_rate * (1 + self.monster.stat("attackSpeedPct")))

    @property
    def reach(self) -> float:
        return max(1.2, self.monster.type.range)


# ---------------------------------------------------------------- world


class World:
    def __init__(self, sessions: list[GameSession]):
        self.sessions = {s.player_id: s for s in sessions}
        self.bus = sessions[0].bus
        self.data = sessions[0].data
        self.timing = self.data.balance.timing
        self.players: dict[int, PlayerActor] = {s.player_id: PlayerActor(s, 0, 0) for s in sessions}
        self.monsters: dict[int, MonsterActor] = {}
        self.layout: AreaLayout | None = None
        self.area_id = ""
        self.floor = 0
        self.time = 0.0
        for p in self.players.values():
            p.reset_vitals()
        self.bus.subscribe(ProficiencyLeveledUp, self._on_level_up)

    # ------------------------------------------------------------ areas

    def enter_area(self, area_id: str, floor: int = 0, came_from: str | None = None, arrive_at: ExitKind | None = None) -> None:
        host = next(iter(self.sessions.values()))
        layout, packs = host.enter_area(area_id, floor)
        self.layout, self.area_id, self.floor = layout, area_id, floor
        self.monsters.clear()

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
        for p in self.players.values():
            p.x, p.y = float(start[0]), float(start[1])
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
                pos = anchor
                for _ in range(12):
                    c = (anchor[0] + rng.randint(-2, 2), anchor[1] + rng.randint(-2, 2))
                    if layout.is_floor(c):
                        pos = c
                        break
                self.monsters[m.instance_id] = MonsterActor(m, float(pos[0]), float(pos[1]), leader_id=m.leader_id)
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
        if p is None or p.state == "dead":
            return None
        if isinstance(cmd, SetGuard):
            p.guard_held = cmd.held
            if abs(cmd.face_x) + abs(cmd.face_y) > 1e-6:
                n = math.hypot(cmd.face_x, cmd.face_y)
                p.face_x, p.face_y = cmd.face_x / n, cmd.face_y / n
            return None
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
            secs = 3 + p.session.skills.effect("bulwark", "durationSeconds")
            p.bulwark_left = secs
            self._float(p.x, p.y, "Bulwark!", "info")
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
        for m in list(self.monsters.values()):
            if m.alive:
                self._step_monster(m, dt)

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

        # exits
        if p.exit_cooldown <= 0 and self.layout:
            c = cell_of(p.x, p.y)
            for e in self.layout.exits:
                if e.pos == c:
                    p.exit_cooldown = EXIT_COOLDOWN
                    self.bus.publish(ExitReached(p.player_id, e.kind.value, e.target_area))
                    break

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
            arc = self.data.node("wide_slash").effects.get("arcDegrees", 120)  # fixed arc; points raise the damage
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
            r = s.combat.resolve_physical(atk, monster_defense(t.monster), s.combat_rng)
            if not r.hit:
                self._float(t.x, t.y, "miss", "miss")
                continue
            self._damage_monster(p, t, r.final, prof, crit=r.crit)
            if skill == "shield_bash" and t.alive:
                self._stun_monster(t, self.data.node("shield_bash").effects.get("stunSeconds", 0.6))

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

    def _stun_monster(self, t: MonsterActor, seconds: float) -> None:
        if t.monster.rank.id in ("unique", "boss"):
            seconds *= 1 - self.data.balance.combat.cc_reduction_unique_and_boss
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
        """Loot goes straight to the killer until items on the ground exist (backlog M6-02)."""
        m = t.monster
        self.bus.publish(MonsterKilled(m.xp_info, m.rank.id, killer.player_id))
        s = killer.session
        for drop in s.loot.roll_drops(m.rank.id, m.level, 0.0, s.loot_rng):
            self.bus.publish(LootPicked(killer.player_id, drop, s.collect(drop)))
        del self.monsters[t.id]

    # ------------------------------------------------------------ monsters

    def _nearest_player(self, m: MonsterActor) -> PlayerActor | None:
        alive = [p for p in self.players.values() if p.state != "dead"]
        return min(alive, key=lambda p: math.hypot(p.x - m.x, p.y - m.y)) if alive else None

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
                if math.hypot(target.x - m.x, target.y - m.y) <= m.reach + 0.6:
                    self._monster_hit(m, target)
            if m.attack_elapsed >= m.attack_duration:
                m.state = "idle"
                m.attack_cooldown = m.attack_duration * 0.3
            return

        if m.alert:
            if dist > m.reach:
                self._step_towards(m, (target.x, target.y), m.speed, dt)
                self._separate(m, dt)
            elif m.attack_cooldown <= 0:
                m.state, m.attack_elapsed, m.attack_hit_done = "attacking", 0.0, False
        elif m.leader_id is not None:
            leader = self.monsters.get(m.leader_id)
            if leader and math.hypot(leader.x - m.x, leader.y - m.y) > 4:
                self._step_towards(m, (leader.x, leader.y), m.speed * 0.6, dt)

    def _separate(self, m: MonsterActor, dt: float) -> None:
        """Gently pushes overlapping monsters apart (avoids piles on the same spot)."""
        push_x = push_y = 0.0
        for o in self.monsters.values():
            if o is m or not o.alive:
                continue
            dx, dy = m.x - o.x, m.y - o.y
            d = math.hypot(dx, dy)
            if 1e-6 < d < 0.7:
                push_x += dx / d * (0.7 - d)
                push_y += dy / d * (0.7 - d)
        if push_x or push_y:
            nx, ny = m.x + push_x * min(1.0, 6 * dt), m.y + push_y * min(1.0, 6 * dt)
            if self.layout.is_floor(cell_of(nx, ny)):
                m.x, m.y = nx, ny

    def _monster_hit(self, m: MonsterActor, p: PlayerActor) -> None:
        s = p.session
        prof = player_guard(s.skills, s.gear, self.data.balance.guard)
        bulwark = p.bulwark_left > 0
        guard = GuardState(
            raised=p.guarding and prof.can_guard,
            in_arc=bulwark or s.combat.in_arc(p.face_x, p.face_y, m.x - p.x, m.y - p.y, prof.arc_degrees),
            perfect=p.guarding and p.guard_time <= prof.perfect_window,
            efficiency=prof.efficiency,
            energy=p.energy,
            free_block=bulwark,
        )
        defense = player_defense(s.character, s.skills, s.gear)
        r = s.combat.resolve_physical(monster_attack(m.monster), defense, s.combat_rng, guard)
        if not r.hit:
            self._float(p.x, p.y, "dodged", "miss")
            return
        p.energy = max(0.0, p.energy - r.guard_energy_cost)

        if r.blocked:
            perfect = r.block == BlockOutcome.PERFECT
            self.bus.publish(HitBlocked(m.monster.xp_info, perfect, p.player_id))
            self._float(p.x, p.y, "parried!" if perfect else "blocked", "perfect" if perfect else "block")
            if perfect:
                self._counter(p, m)
            else:
                fbr = s.skills.stat_total("fasterBlockRecovery")
                p.state, p.state_left = "block_recovery", player_block_recovery(self.timing, fbr)
                p.path.clear()
            return

        if r.block == BlockOutcome.GUARD_BROKEN:
            self._lower_guard(p, start_cooldown=True)
            p.state, p.state_left = "stunned", self.data.balance.guard.guard_break_stun_seconds
            p.path.clear()
            self._float(p.x, p.y, "guard broken", "hurt")
        if r.mitigated > 0:
            self.bus.publish(DamageMitigated(m.monster.xp_info, r.raw, r.mitigated, p.player_id))
        if not p.god_mode:
            p.life -= r.final
        self._float(p.x, p.y, f"-{r.final:.0f}", "hurt")
        if p.life <= 0:
            p.life = 0
            p.state = "dead"
            self.bus.publish(PlayerDied(p.player_id))
            return
        if p.state != "stunned":
            fhr = s.skills.stat_total("fasterHitRecovery")
            rec = player_hit_recovery(self.timing, r.final, p.max_life, fhr)
            if rec > 0:
                p.state, p.state_left = "hit_recovery", rec  # interrupts attack and movement
                p.attack_hit_done = True

    def _counter(self, p: PlayerActor, m: MonsterActor) -> None:
        s = p.session
        bonus = s.skills.effect("parry", "counterDamagePct")
        if bonus <= 0 or not m.alive:
            return
        atk = with_bonus(player_attack(s.character, s.skills, s.gear), bonus, always_hit=True)
        r = s.combat.resolve_physical(atk, monster_defense(m.monster), s.combat_rng)
        self._damage_monster(p, m, r.final, "shield", r.crit)

    # ------------------------------------------------------------ helpers

    def _on_level_up(self, e: ProficiencyLeveledUp) -> None:
        p = self.players.get(e.player_id)
        if p and p.state != "dead":
            p.life = min(p.max_life, p.life + p.max_life * 0.25)
            name = p.session.proficiencies[e.proficiency_id].name
            self._float(p.x, p.y, f"{name} {e.new_level}!", "levelup")

    def _float(self, x: float, y: float, text: str, kind: str) -> None:
        self.bus.publish(FloatingText(x, y, text, kind))

    @staticmethod
    def _face(p: PlayerActor, dx: float, dy: float) -> None:
        n = math.hypot(dx, dy)
        if n > 1e-6:
            p.face_x, p.face_y = dx / n, dy / n

    def _step_towards(self, a, target: tuple[float, float], speed: float, dt: float) -> bool:
        """Walks toward the target, sliding along walls. True on arrival."""
        L = self.layout
        tx, ty = target
        dx, dy = tx - a.x, ty - a.y
        dist = math.hypot(dx, dy)
        step = speed * dt
        if dist <= step:
            if L.is_floor(cell_of(tx, ty)):
                a.x, a.y = float(tx), float(ty)
            return True
        nx, ny = a.x + dx / dist * step, a.y + dy / dist * step
        if L.is_floor(cell_of(nx, ny)):
            a.x, a.y = nx, ny
        elif L.is_floor(cell_of(nx, a.y)):
            a.x = nx
        elif L.is_floor(cell_of(a.x, ny)):
            a.y = ny
        return False

    def exit_at(self, x: float, y: float) -> Exit | None:
        c = cell_of(x, y)
        return next((e for e in self.layout.exits if e.pos == c), None) if self.layout else None
