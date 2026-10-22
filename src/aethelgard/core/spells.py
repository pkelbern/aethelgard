"""Spells (GDD 6.7): how a cast is delivered and how elemental damage lands.

A spell is a skills.json node with ``spell``. It is cast in place toward a point (the target, or the
mouse), takes ``magic.castSeconds`` and leaves the hand partway through, like a swing. Spell damage
always hits and ignores armor; the target's resistance to the element takes its share.

Deliveries (``spell.cast``):
- ``bolt``: a straight projectile that hits the first enemy it meets.
- ``ball``: a straight projectile that bursts (area) on the first enemy, on a wall or where its flight ends.
- ``chain``: a bolt that jumps on to the nearest enemies it has not hit (``chains`` times, ``bounceRangeTiles``).
- ``nova``: bursts around the caster at once (``radiusTiles``).
- ``wall``: a line across the aim, at the aimed point; enemies standing in it take damage every tick.
- ``field``: a circle on the aimed point that hurts every tick (Blizzard).
- ``storm``: a circle on the aimed point that strikes one enemy in it every ``intervalSeconds`` (Thunderstorm).
- ``meteor``: falls on the aimed point after a delay, bursts, then leaves the ground burning.
- ``self``: on the caster (Heal, blessings): see ``World.apply_self_skill``.

What a hit does after its damage comes from stats, for every element: ``chillPct``/``chillSeconds`` slow
the target, ``stunSeconds``/``knockbackTiles`` work as for any skill, Ignite burns on fire hits, and the
passives ``<element>CritChance`` (double damage), ``<element>FreezeChance``/``FreezeSeconds`` and
``<element>ResistPierce`` work for whichever element names them.

The world owns the actors and the projectiles; :class:`SpellCaster` adds ground fire and burning
monsters to it and uses the world's own helpers to launch, damage and float numbers, so a spell
kill gives XP and loot exactly like a sword kill.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .combat import AttackerStats
from .events import SpellBurst

if TYPE_CHECKING:
    from .world import MonsterActor, PlayerActor, World

SPELL_ACCURACY = 1e9  # spells never miss


def spell_attack(session, node_id: str) -> AttackerStats:
    """Damage of one hit of the spell now: its range, raised by Intelligence, the element's bonus and
    the node's own damage bonus (synergies). Kept on the projectile, like a monster's shot."""
    s, d = session, session.data
    node = d.node(node_id)
    e = s.skills.effect
    bonus = (s.character.attributes.int * d.balance.attributes.spell_damage_per_int
             + s.stat(f"{node.spell.element}DamagePct") + e(node_id, "damagePct"))
    return AttackerStats(s.character.level, SPELL_ACCURACY, e(node_id, "damageMin"), e(node_id, "damageMax"), bonus)


def per_second_damage(session, node_id: str) -> float:
    """Fire on the ground: damage per second with the same bonuses as a hit."""
    a = spell_attack(session, node_id)
    return session.skills.effect(node_id, "damagePerSecond") * (1 + a.damage_bonus_pct)


def effective_resist(resist: float, pierce: float, cap: float) -> float:
    """Resistance after the caster's pierce: pierce lowers a positive resistance down to zero, never
    below; a weakness (negative) stays. Capped like the player's (balance.json combat.resistCap)."""
    r = min(resist, cap)
    return r if r <= 0 else max(0.0, r - pierce)


def segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point P to the segment AB."""
    dx, dy = bx - ax, by - ay
    n2 = dx * dx + dy * dy
    t = 0.0 if n2 < 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


@dataclass
class GroundFire:
    """Fire on the floor: a wall (segment a→b) or a burning circle (a = b, radius). A meteor is one
    too: it waits ``delay`` seconds, bursts for ``burst`` in its radius, then the ground burns."""

    id: int
    owner_id: int
    skill: str
    prof: str
    element: str
    look: str
    ax: float
    ay: float
    bx: float
    by: float
    radius: float  # half-width of a wall, or the circle's radius
    dps: float
    left: float  # seconds of burning left
    delay: float = 0.0  # meteor: seconds until it lands
    burst: AttackerStats | None = None  # meteor: the hit when it lands
    tick: float = 0.0  # seconds until the next damage step

    @property
    def landed(self) -> bool:
        return self.delay <= 0

    strike: AttackerStats | None = None  # storm: one enemy in it takes this hit every ``interval`` seconds
    interval: float = 0.0
    strike_all: bool = False  # rain: every enemy in it takes the hit, not just one

    def touches(self, m: MonsterActor) -> bool:
        return segment_distance(m.x, m.y, self.ax, self.ay, self.bx, self.by) <= self.radius + m.radius


class SpellCaster:
    """The spell half of the world: releasing casts, projectile bursts, ground fire and burning."""

    def __init__(self, world: World):
        self.w = world
        self.fires: dict[int, GroundFire] = {}
        self._next = 1

    def clear(self) -> None:
        self.fires.clear()

    # ------------------------------------------------------------ casting

    def cast_seconds(self, p: PlayerActor) -> float:
        return self.w.data.balance.magic.cast_seconds / (1 + p.session.stat("fasterCastPct"))

    def aim_point(self, p: PlayerActor, node_id: str, at: tuple[float, float] | None = None,
                  default_range: float = 14.0) -> tuple[float, float]:
        """Where the spell goes: the aimed point (``at``, or the player's aim), pulled back to the spell's range
        (its rangeTiles, else ``default_range``: a bow's reach for Rain of Arrows)."""
        rng = p.session.skills.effect(node_id, "rangeTiles") or default_range
        if at is not None:
            ax, ay = at
        else:
            ax, ay = (p.aim_x, p.aim_y) if p.aim_x is not None else (p.x + p.face_x, p.y + p.face_y)
        dx, dy = ax - p.x, ay - p.y
        dist = math.hypot(dx, dy)
        if dist > rng:
            ax, ay = p.x + dx / dist * rng, p.y + dy / dist * rng
        return ax, ay

    def release(self, p: PlayerActor, node_id: str) -> None:
        """The moment the spell leaves the hand (partway through the cast)."""
        s, w = p.session, self.w
        node = w.data.node(node_id)
        e = s.skills.effect
        ax, ay = self.aim_point(p, node_id)
        cast, element, look = node.spell.cast, node.spell.element, node.spell.look
        if cast == "self":
            w.apply_self_skill(p, node_id)
        elif cast == "nova":
            self.burst(p, p.x, p.y, e(node_id, "radiusTiles"), spell_attack(s, node_id), element, node_id, look)
        elif cast in ("bolt", "ball", "chain"):
            proj = w._launch(x=p.x, y=p.y, speed=e(node_id, "projectileSpeed") or 10.0, radius=0.2, max_range=e(node_id, "rangeTiles") or 14.0,
                             look=look, owner="player", owner_id=p.player_id, skill=node_id, prof=node.tree, magic=True,
                             attack=spell_attack(s, node_id), element=element,
                             burst_radius=e(node_id, "radiusTiles") if cast == "ball" else 0.0,
                             bounces_left=math.floor(e(node_id, "chains") + 1e-9) if cast == "chain" else 0,
                             bounce_range=e(node_id, "bounceRangeTiles"))
            if math.hypot(ax - p.x, ay - p.y) > 1e-6:
                proj.aim(ax, ay)
            else:
                proj.dir_x, proj.dir_y = p.face_x, p.face_y
        elif cast == "wall":
            fx, fy = ax - p.x, ay - p.y
            n = math.hypot(fx, fy) or 1.0
            ux, uy = -fy / n, fx / n  # across the aim
            half = e(node_id, "lengthTiles") / 2
            self._add(GroundFire(0, p.player_id, node_id, node.tree, node.spell.element, node.spell.look,
                                 ax - ux * half, ay - uy * half, ax + ux * half, ay + uy * half, 0.45,
                                 per_second_damage(s, node_id), e(node_id, "durationSeconds")))
        elif cast == "meteor":
            self._add(GroundFire(0, p.player_id, node_id, node.tree, node.spell.element, node.spell.look, ax, ay, ax, ay,
                                 e(node_id, "radiusTiles"), per_second_damage(s, node_id), e(node_id, "durationSeconds"),
                                 delay=e(node_id, "delaySeconds"), burst=spell_attack(s, node_id)))
        elif cast == "field":
            self._add(GroundFire(0, p.player_id, node_id, node.tree, element, look, ax, ay, ax, ay,
                                 e(node_id, "radiusTiles"), per_second_damage(s, node_id), e(node_id, "durationSeconds")))
        elif cast == "storm":
            self._add(GroundFire(0, p.player_id, node_id, node.tree, element, look, ax, ay, ax, ay,
                                 e(node_id, "radiusTiles"), 0.0, e(node_id, "durationSeconds"),
                                 strike=spell_attack(s, node_id), interval=e(node_id, "intervalSeconds") or 1.0))

    def _add(self, f: GroundFire) -> GroundFire:
        f.id = self._next
        self._next += 1
        self.fires[f.id] = f
        return f

    # ------------------------------------------------------------ damage

    def deal(self, p: PlayerActor, t: MonsterActor, amount: float, skill: str, recover: bool = True, crit: bool = False) -> None:
        """Damage from a spell: XP to its tree and to the spell itself."""
        self.w._damage_monster(p, t, amount, self.w.data.node(skill).tree, crit=crit, recover=recover, skill=skill)

    def hit(self, p: PlayerActor, t: MonsterActor, attack: AttackerStats, element: str, skill: str, can_ignite: bool = True) -> float:
        """One spell hit on a monster: rolled (with the target bonuses of any hit), maybe critical (lightning), resisted,
        dealt (XP and loot as for any hit), then its after-effects. Returns the damage. ``physical`` (Rain of
        Arrows): an ordinary blow instead, with accuracy against defense and armor."""
        s = p.session
        attack = self.w._hit_bonuses(p, t, attack)
        if element == "physical":
            r = s.combat.resolve_physical(attack, t.defense, s.combat_rng)
            if not r.hit:
                self.w._float(t.x, t.y, "missed", "miss")
                return 0.0
            self.deal(p, t, r.final, skill, crit=r.crit)
            return r.final
        raw = s.combat_rng.uniform(attack.dmg_min, attack.dmg_max) * (1 + attack.damage_bonus_pct)
        crit = s.combat_rng.chance(s.stat(f"{element}CritChance"))
        amount = self.resisted(p, t, raw * (2 if crit else 1), element)
        if amount <= 0:
            return 0.0
        self.deal(p, t, amount, skill, crit=crit)
        if t.alive:
            self.after_hit(p, t, amount, element, skill, can_ignite)
        return amount

    def after_hit(self, p: PlayerActor, t: MonsterActor, amount: float, element: str, skill: str, can_ignite: bool = True) -> None:
        """What lands with the damage: Ignite (fire), chill and freeze (cold), and the skill's own stun or push."""
        if can_ignite and element == "fire":
            self.ignite(p, t, amount, skill)
        self.chill(p, t, element, skill)
        self.w._skill_on_hit(p, t, skill)

    def chill(self, p: PlayerActor, t: MonsterActor, element: str, skill: str) -> None:
        """chillPct/chillSeconds of the spell slow the target (the strongest chill wins); <element>FreezeChance freezes it."""
        sk = p.session.skills
        pct, secs = sk.effect(skill, "chillPct"), sk.effect(skill, "chillSeconds")
        if pct > 0 and secs > 0 and pct >= t.chill_pct * (t.chill_left > 0):
            t.chill_pct, t.chill_left = min(pct, 0.9), secs
        chance, frozen = sk.stat_total(f"{element}FreezeChance"), sk.stat_total(f"{element}FreezeSeconds")
        if chance > 0 and frozen > 0 and t.alive and t.state != "stunned" and p.session.combat_rng.chance(chance):
            self.w._stun_monster(t, frozen, "frozen")

    def resisted(self, p: PlayerActor, t: MonsterActor, raw: float, element: str) -> float:
        cap = self.w.data.balance.combat.resist_cap
        pierce = p.session.stat(f"{element}ResistPierce")
        return raw * (1 - effective_resist(t.resist(element), pierce, cap))

    def ignite(self, p: PlayerActor, t: MonsterActor, amount: float, skill: str) -> None:
        """Ignite: the hit burns on for a while (the strongest burn wins; it does not stack)."""
        e = p.session.skills.effect
        share, secs = e("ignite", "burnPctOfHit"), e("ignite", "burnSeconds")
        if share <= 0 or secs <= 0:
            return
        from .world import Dot

        t.add_dot("burn", Dot(amount * share / secs, secs, p.player_id, skill, self.w.data.node(skill).tree))

    def burst(self, p: PlayerActor, x: float, y: float, radius: float, attack: AttackerStats, element: str, skill: str, look: str) -> None:
        """Every monster within ``radius`` of (x, y) takes the hit (its body counts: a wide wolf is caught sooner)."""
        self.w.bus.publish(SpellBurst(x, y, radius, look))
        for m in list(self.w.monsters.values()):
            if m.alive and math.hypot(m.x - x, m.y - y) <= radius + m.radius:
                self.hit(p, m, attack, element, skill)

    def projectile_hit(self, proj, t: MonsterActor) -> bool:
        """A bolt, ball or chain reached a monster. False: the projectile is gone (a chain jumps on while it can)."""
        p = self.w.players.get(proj.owner_id)
        if p is None:
            return False
        proj.hit_ids.add(t.id)
        if proj.burst_radius > 0:
            self.burst(p, proj.x, proj.y, proj.burst_radius, proj.attack, proj.element, proj.skill, proj.look)
            proj.burst_radius = 0.0  # already burst: it does not go off again where it ends
            return False
        self.hit(p, t, proj.attack, proj.element, proj.skill)
        return self.w.bounce(proj)

    def projectile_end(self, proj) -> None:
        """A ball that hit a wall or flew its whole range bursts where it is."""
        p = self.w.players.get(proj.owner_id)
        if p is not None and proj.burst_radius > 0:
            self.burst(p, proj.x, proj.y, proj.burst_radius, proj.attack, proj.element, proj.skill, proj.look)

    # ------------------------------------------------------------ every step

    def step(self, dt: float) -> None:
        tick = self.w.data.balance.magic.tick_seconds
        for f in list(self.fires.values()):
            p = self.w.players.get(f.owner_id)
            if p is None:
                del self.fires[f.id]
                continue
            if not f.landed:
                f.delay -= dt
                if f.landed and f.burst is not None:  # the meteor hits the ground
                    self.burst(p, f.ax, f.ay, f.radius, f.burst, f.element, f.skill, f.look)
                    f.tick = tick
                continue
            f.left -= dt
            f.tick -= dt
            if f.strike is not None:  # a storm: one enemy inside, picked at random, every interval (rain: all of them)
                if f.tick <= 0:
                    f.tick += f.interval
                    inside = [m for m in self.w.monsters.values() if m.alive and f.touches(m)]
                    if inside and f.strike_all:
                        for m in inside:
                            self.hit(p, m, f.strike, f.element, f.skill)
                    elif inside:
                        m = inside[p.session.combat_rng.randint(0, len(inside) - 1)]
                        self.w.bus.publish(SpellBurst(m.x, m.y, 0.6, f.look))
                        self.hit(p, m, f.strike, f.element, f.skill)
            elif f.tick <= 0:
                f.tick += tick
                for m in list(self.w.monsters.values()):
                    if m.alive and f.touches(m):
                        amount = self.resisted(p, m, f.dps * tick, f.element)
                        if amount > 0:  # magic on the ground hurts but never staggers
                            self.deal(p, m, amount, f.skill, recover=False)
                            if m.alive:
                                self.chill(p, m, f.element, f.skill)
            if f.left <= 0:
                del self.fires[f.id]
        for m in list(self.w.monsters.values()):
            if m.chill_left > 0:
                m.chill_left = max(0.0, m.chill_left - dt)

    def rain(self, p: PlayerActor, skill: str) -> None:
        """Rain of Arrows: arrows fall on the aimed circle every tick for durationSeconds; each enemy inside takes a
        bow shot with the skill's bonus (accuracy, armor and crits as for any arrow)."""
        from .combat import player_attack

        s, e = p.session, p.session.skills.effect
        ax, ay = self.aim_point(p, skill, default_range=self.w.player_reach(p))
        node = self.w.data.node(skill)
        self._add(GroundFire(0, p.player_id, skill, node.tree, "physical", "rain", ax, ay, ax, ay, e(skill, "radiusTiles"), 0.0,
                             e(skill, "durationSeconds"), strike=player_attack(s.character, s.skills, s.gear, skill),
                             interval=self.w.data.balance.magic.tick_seconds, strike_all=True))
