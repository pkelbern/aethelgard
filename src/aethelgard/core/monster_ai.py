"""Monster behaviours beyond "walk up and hit" (GDD 8): archetypes, modifier effects and the boss's pattern.

* ``ai`` (monsters.json): ``flanker`` comes at you from the side; ``charger`` rushes in a straight line and
  stuns on contact; ``caster_revive`` raises a dead follower; ``support_heal`` heals a wounded ally.
  ``melee`` and ``ranged`` are the world's default chase (archers already back off from melee).
* Modifiers (monster_modifiers.json): elemental damage on its hits, life leech, a slowing touch, a burst on
  death, sparks when hurt, teleporting next to you, auras.
* Bosses (monsters.json bosses): telegraphed specials — the area shows on the ground for ``windupSeconds``,
  then hits whoever is still inside — and a second phase below ``phaseTwoAtPct`` of its life.

Numbers live in balance.json (monsterAi) and the monster files. The world calls in through ``think`` (each
step of an alert monster), ``on_hurt``, ``on_hit_player`` and ``on_death``; everything that lands goes
through the world's own helpers (``_hit_player``, ``_launch``…), so blocks, armor and resistances apply.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .combat import AttackerStats, with_bonus
from .data import ELEMENTS
from .events import SpellBurst

if TYPE_CHECKING:
    from .world import MonsterActor, PlayerActor, World

ALWAYS = 1e9  # accuracy of effects that cannot be dodged (the guard still blocks them)


@dataclass
class Telegraph:
    """A boss special on its way: drawn on the ground until it lands."""

    id: int
    owner_id: int
    name: str
    x: float
    y: float
    radius: float
    arc: float  # degrees; 360 = circle
    dir_x: float
    dir_y: float
    left: float
    total: float
    damage_mult: float

    def covers(self, x: float, y: float, r: float, in_arc) -> bool:
        dx, dy = x - self.x, y - self.y
        if math.hypot(dx, dy) > self.radius + r:
            return False
        return self.arc >= 360 or in_arc(self.dir_x, self.dir_y, dx, dy, self.arc)


class MonsterBrains:
    def __init__(self, world: World):
        self.w = world
        self.telegraphs: dict[int, Telegraph] = {}
        self._next = 1

    @property
    def p(self):
        return self.w.data.balance.monster_ai

    def clear(self) -> None:
        self.telegraphs.clear()

    # ------------------------------------------------------------ every step of an alert monster

    def think(self, m: MonsterActor, target: PlayerActor, dist: float, dt: float) -> bool:
        """True when the monster's behaviour took over this step (the world skips its default chase)."""
        for k in list(m.ai_cooldowns):
            m.ai_cooldowns[k] -= dt
        if m.rush is not None:
            self._step_rush(m, target, dt)
            return True
        for mod in m.monster.modifiers:
            if mod.ability == "teleport" and self._teleport(m, target, dist, mod):
                return True
        if m.monster.boss is not None and self._boss(m, target, dist):
            return True
        ai = m.monster.type.ai
        if ai == "charger":
            return self._charge(m, target, dist)
        if ai == "flanker":
            return self._flank(m, target, dist, dt)
        if ai == "caster_revive":
            self._revive(m)
        elif ai == "support_heal":
            return self._heal(m)
        return False

    def _ready(self, m: MonsterActor, key: str, cooldown: float) -> bool:
        if m.ai_cooldowns.get(key, 0.0) > 0:
            return False
        m.ai_cooldowns[key] = cooldown
        return True

    # ---- flanker: from the side

    def _flank(self, m: MonsterActor, t: PlayerActor, dist: float, dt: float) -> bool:
        if dist <= m.reach or dist > 6.0:
            return False
        nx, ny = (t.x - m.x) / dist, (t.y - m.y) / dist
        side = 1 if m.id % 2 else -1
        off = self.p.flank_offset_tiles * min(1.0, (dist - m.reach) / 3)
        goal = (t.x - ny * side * off, t.y + nx * side * off)
        self.w._step_towards(m, goal, m.speed, dt)
        self.w._separate(m, dt)
        return True

    # ---- charger: a straight rush that stuns

    def _charge(self, m: MonsterActor, t: PlayerActor, dist: float) -> bool:
        p = self.p
        if not (p.charge_min_tiles <= dist <= p.charge_max_tiles) or m.ai_cooldowns.get("charge", 0) > 0:
            return False
        from .projectiles import line_of_sight

        if not line_of_sight(self.w.layout, (m.x, m.y), (t.x, t.y)) or not self._ready(m, "charge", p.charge_cooldown):
            return False
        m.rush = [(t.x - m.x) / dist, (t.y - m.y) / dist, dist + 2.0]
        self.w._float(m.x, m.y, "charges!", "info")
        return True

    def _step_rush(self, m: MonsterActor, t: PlayerActor, dt: float) -> None:
        dx, dy, left = m.rush
        step = min(left, m.speed * self.p.charge_speed_mult * dt)
        if math.hypot(t.x - m.x, t.y - m.y) <= m.radius + t.radius + 0.25:  # hit the player
            m.rush = None
            atk = with_bonus(m.attack, self.p.charge_damage_pct)
            self.w._hit_player(t, atk, m.monster.xp_info, m.x, m.y, magic=False, melee=m)
            if t.state not in ("dead", "stunned") and not (t.guarding and t.bulwark_left > 0):
                self.w.stun_player(t, self.p.charge_stun_seconds)
            m.attack_cooldown = m.attack_interval
            return
        if not self.w._try_move(m, m.x + dx * step, m.y + dy * step):
            m.rush = None  # a wall or a body stops it
            return
        m.rush[2] = left - step
        if m.rush[2] <= 0:
            m.rush = None

    # ---- caster_revive: raise a dead follower

    def _revive(self, m: MonsterActor) -> None:
        lead = m.monster.type.leader
        kinds = set(lead.followers) if lead else {m.monster.type.id}
        near = [c for c in self.w.fallen if c.monster.type.id in kinds
                and math.hypot(c.x - m.x, c.y - m.y) <= self.p.revive_radius_tiles]
        if not near or not self._ready(m, "revive", self.p.revive_cooldown):
            return
        c = min(near, key=lambda c: math.hypot(c.x - m.x, c.y - m.y))
        self.w.fallen.remove(c)
        c.monster.hp = c.monster.max_hp * self.p.revive_hp_pct
        c.monster.no_reward = True  # raised: no XP or loot a second time
        c.state, c.alert, c.dots, c.debuffs = "idle", True, {}, {}
        self.w.monsters[c.id] = c
        self.w.bus.publish(SpellBurst(c.x, c.y, 0.8, "raise"))
        self.w._float(c.x, c.y, "raised", "info")

    # ---- support_heal: heal a wounded ally instead of attacking

    def _heal(self, m: MonsterActor) -> bool:
        p = self.p
        hurt = [o for o in self.w.monsters.values() if o is not m and o.alive and o.monster.hp < o.monster.max_hp * p.heal_below_pct
                and math.hypot(o.x - m.x, o.y - m.y) <= p.heal_radius_tiles]
        if not hurt or not self._ready(m, "heal", p.heal_cooldown):
            return False
        o = min(hurt, key=lambda o: o.monster.hp / o.monster.max_hp)
        amount = min(o.monster.max_hp - o.monster.hp, o.monster.max_hp * p.heal_pct)
        o.monster.hp += amount
        self.w.bus.publish(SpellBurst(o.x, o.y, 0.7, "monster_heal"))
        self.w._float(o.x, o.y, f"+{amount:.0f}", "heal")
        m.attack_cooldown = max(m.attack_cooldown, m.attack_interval)  # that was its turn
        return True

    # ---- teleport (modifier)

    def _teleport(self, m: MonsterActor, t: PlayerActor, dist: float, mod) -> bool:
        if dist <= float(mod.params.get("teleportMinTiles", 4)) or not self._ready(m, "teleport", float(mod.params.get("teleportCooldown", 5))):
            return False
        for a in range(8):
            ang = a * math.tau / 8 + m.id
            x, y = t.x + math.cos(ang) * (m.radius + t.radius + 0.3), t.y + math.sin(ang) * (m.radius + t.radius + 0.3)
            if self.w._free(m, x, y):
                self.w.bus.publish(SpellBurst(m.x, m.y, 0.6, "teleport"))
                m.x, m.y = x, y
                self.w.bus.publish(SpellBurst(x, y, 0.6, "teleport"))
                return True
        return False

    # ------------------------------------------------------------ modifier effects

    @staticmethod
    def elemental_extra(m: MonsterActor) -> dict[str, float]:
        """Share of each of its hits added as an element (Fire Enchanted: fireDamagePct)."""
        return {e: m.stat(f"{e}DamagePct") for e in ELEMENTS if m.stat(f"{e}DamagePct") > 0}

    def on_hit_player(self, m: MonsterActor, p: PlayerActor, dealt: float) -> None:
        """After its hit landed: life leech, and a slowing touch (Cold Enchanted)."""
        leech = m.stat("lifeLeech")
        if leech > 0 and dealt > 0:
            m.monster.hp = min(m.monster.max_hp, m.monster.hp + dealt * leech)
        for mod in m.monster.modifiers:
            if mod.on_hit == "slow" and dealt > 0:
                self.w.slow_player(p, float(mod.params.get("slowPct", 0.3)), float(mod.params.get("slowSeconds", 2)))

    def on_hurt(self, m: MonsterActor) -> None:
        """When hit: Lightning Enchanted sends sparks in every direction."""
        for mod in m.monster.modifiers:
            if mod.on_hurt != "charged_bolt":
                continue
            rng = self.w._host_rng
            if not rng.chance(float(mod.params.get("chargedChance", 0.3))):
                continue
            n = int(mod.params.get("chargedBolts", 3))
            atk = self._scaled(m, float(mod.params.get("chargedDamagePct", 0.3)))
            for k in range(n):
                a = k * math.tau / n + rng.uniform(0, 1)
                proj = self.w._launch(x=m.x, y=m.y, speed=7.0, radius=0.15, max_range=6.0, look="bolt", owner="monster",
                                      owner_id=m.id, magic=True, attack=atk, info=m.monster.xp_info, element="lightning")
                proj.dir_x, proj.dir_y = math.cos(a), math.sin(a)

    def on_death(self, m: MonsterActor) -> None:
        """Fire and Cold Enchanted burst when they die: whoever stands close takes it (cold also slows)."""
        for mod in m.monster.modifiers:
            if mod.on_death != "burst":
                continue
            radius = float(mod.params.get("burstRadiusTiles", 2))
            element = str(mod.params.get("burstElement", "fire"))
            self.w.bus.publish(SpellBurst(m.x, m.y, radius, f"monster_{element}"))
            atk = self._scaled(m, float(mod.params.get("burstDamagePct", 1.0)))
            for p in self.w.players.values():
                if p.state != "dead" and math.hypot(p.x - m.x, p.y - m.y) <= radius + p.radius:
                    self.w._hit_player(p, atk, m.monster.xp_info, m.x, m.y, magic=True, melee=None, element=element)
                    if element == "cold" and p.state != "dead":
                        self.w.slow_player(p, float(mod.params.get("slowPct", 0.3)), float(mod.params.get("slowSeconds", 2)))

    @staticmethod
    def _scaled(m: MonsterActor, share: float) -> AttackerStats:
        a = m.attack
        return AttackerStats(a.level, ALWAYS, a.dmg_min * share, a.dmg_max * share, a.damage_bonus_pct)

    # ------------------------------------------------------------ the boss

    def _boss(self, m: MonsterActor, t: PlayerActor, dist: float) -> bool:
        boss = m.monster.boss
        if m.phase == 1 and m.monster.hp < m.monster.max_hp * boss.phase_two_at_pct:
            m.phase = 2
            m.boost = dict(boss.phase_two_stats)
            self.w.bus.publish(SpellBurst(m.x, m.y, 2.0, "boss_phase"))
            self.w._float(m.x, m.y, "enraged!", "levelup")
        if m.state != "idle":
            return False
        for ab in boss.abilities:
            if ab.phase > m.phase or m.ai_cooldowns.get(ab.id, 0.0) > 0:
                continue
            if ab.summon is None and dist > ab.radius_tiles + t.radius:
                continue
            m.ai_cooldowns[ab.id] = ab.cooldown
            self._wind_up(m, t, ab)
            return True
        return False

    def _wind_up(self, m: MonsterActor, t: PlayerActor, ab) -> None:
        """The boss stops and the area shows on the ground; ``step`` makes it land."""
        d = math.hypot(t.x - m.x, t.y - m.y) or 1.0
        g = Telegraph(self._next, m.id, ab.id, m.x, m.y, ab.radius_tiles, ab.arc_degrees, (t.x - m.x) / d, (t.y - m.y) / d,
                      ab.windup_seconds, ab.windup_seconds, ab.damage_mult)
        self._next += 1
        self.telegraphs[g.id] = g
        m.state, m.state_left = "winding", ab.windup_seconds
        m.attack_elapsed, m.attack_duration_override = 0.0, ab.windup_seconds
        m.winding = ab
        self.w._float(m.x, m.y, ab.name, "info")

    def step(self, dt: float) -> None:
        """Telegraphs count down and land."""
        for g in list(self.telegraphs.values()):
            g.left -= dt
            m = self.w.monsters.get(g.owner_id)
            if m is None or not m.alive or m.state == "stunned":  # killed or stunned while winding up: it fizzles
                del self.telegraphs[g.id]
                continue
            if g.left > 0:
                continue
            del self.telegraphs[g.id]
            ab = m.winding
            m.winding = None
            if ab is not None and ab.summon:
                self._summon(m, ab)
            else:
                self.w.bus.publish(SpellBurst(g.x, g.y, g.radius, "boss_strike"))
                atk = replace_accuracy(with_bonus(m.attack, g.damage_mult - 1.0))
                for p in self.w.players.values():
                    if p.state != "dead" and g.covers(p.x, p.y, p.radius, self.w._in_arc):
                        self.w._hit_player(p, atk, m.monster.xp_info, m.x, m.y, magic=False, melee=None)

    def _summon(self, m: MonsterActor, ab) -> None:
        for k in range(ab.count):
            a = k * math.tau / max(1, ab.count)
            x, y = m.x + math.cos(a) * (m.radius + 1.0), m.y + math.sin(a) * (m.radius + 1.0)
            minion = self.w.spawn_monster(ab.summon, m.monster.level, x, y, no_reward=True)
            if minion is not None:
                minion.alert = True
        self.w.bus.publish(SpellBurst(m.x, m.y, m.radius + 1.0, "raise"))


def replace_accuracy(a: AttackerStats) -> AttackerStats:
    from dataclasses import replace

    return replace(a, accuracy=ALWAYS)
