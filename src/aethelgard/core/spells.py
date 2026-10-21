"""Spells (GDD 6.7): how a cast is delivered and how elemental damage lands.

A spell is a skills.json node with ``spell``. It is cast in place toward a point (the target, or the
mouse), takes ``magic.castSeconds`` and leaves the hand partway through, like a swing. Spell damage
always hits and ignores armor; the target's resistance to the element takes its share.

Deliveries (``spell.cast``):
- ``bolt``: a straight projectile that burns the first enemy it meets.
- ``ball``: a straight projectile that bursts (area) on the first enemy, on a wall or where its flight ends.
- ``wall``: a line of fire across the aim, at the aimed point; enemies standing in it burn every tick.
- ``meteor``: falls on the aimed point after a delay, bursts, then leaves the ground burning.

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
             + s.skills.stat_total(f"{node.spell.element}DamagePct") + e(node_id, "damagePct"))
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
        return self.w.data.balance.magic.cast_seconds / (1 + p.session.skills.stat_total("fasterCastPct"))

    def aim_point(self, p: PlayerActor, node_id: str, at: tuple[float, float] | None = None) -> tuple[float, float]:
        """Where the spell goes: the aimed point (``at``, or the player's aim), pulled back to the spell's range."""
        rng = p.session.skills.effect(node_id, "rangeTiles") or 14.0
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
        if node_id not in s.skills.learned:
            s.skills.practice(node_id)  # cast from a wand: the next level of its tree teaches it
        ax, ay = self.aim_point(p, node_id)
        if node.spell.cast in ("bolt", "ball"):
            proj = w._launch(x=p.x, y=p.y, speed=e(node_id, "projectileSpeed") or 10.0, radius=0.2, max_range=e(node_id, "rangeTiles") or 14.0,
                             look=node.spell.look, owner="player", owner_id=p.player_id, skill=node_id, prof=node.tree, magic=True,
                             attack=spell_attack(s, node_id), element=node.spell.element,
                             burst_radius=e(node_id, "radiusTiles") if node.spell.cast == "ball" else 0.0)
            if math.hypot(ax - p.x, ay - p.y) > 1e-6:
                proj.aim(ax, ay)
            else:
                proj.dir_x, proj.dir_y = p.face_x, p.face_y
        elif node.spell.cast == "wall":
            fx, fy = ax - p.x, ay - p.y
            n = math.hypot(fx, fy) or 1.0
            ux, uy = -fy / n, fx / n  # across the aim
            half = e(node_id, "lengthTiles") / 2
            self._add(GroundFire(0, p.player_id, node_id, node.tree, node.spell.element, node.spell.look,
                                 ax - ux * half, ay - uy * half, ax + ux * half, ay + uy * half, 0.45,
                                 per_second_damage(s, node_id), e(node_id, "durationSeconds")))
        elif node.spell.cast == "meteor":
            self._add(GroundFire(0, p.player_id, node_id, node.tree, node.spell.element, node.spell.look, ax, ay, ax, ay,
                                 e(node_id, "radiusTiles"), per_second_damage(s, node_id), e(node_id, "durationSeconds"),
                                 delay=e(node_id, "delaySeconds"), burst=spell_attack(s, node_id)))

    def _add(self, f: GroundFire) -> GroundFire:
        f.id = self._next
        self._next += 1
        self.fires[f.id] = f
        return f

    # ------------------------------------------------------------ damage

    def hit(self, p: PlayerActor, t: MonsterActor, attack: AttackerStats, element: str, prof: str, can_ignite: bool = True) -> float:
        """One spell hit on a monster: rolled, resisted, dealt (XP and loot as for any hit). Returns the damage."""
        s = p.session
        raw = s.combat_rng.uniform(attack.dmg_min, attack.dmg_max) * (1 + attack.damage_bonus_pct)
        amount = self.resisted(p, t, raw, element)
        if amount <= 0:
            return 0.0
        self.w._damage_monster(p, t, amount, prof)
        if can_ignite and t.alive:
            self.ignite(p, t, amount, prof)
        return amount

    def resisted(self, p: PlayerActor, t: MonsterActor, raw: float, element: str) -> float:
        cap = self.w.data.balance.combat.resist_cap
        pierce = p.session.skills.stat_total(f"{element}ResistPierce")
        return raw * (1 - effective_resist(t.resist(element), pierce, cap))

    def ignite(self, p: PlayerActor, t: MonsterActor, amount: float, prof: str) -> None:
        """Ignite: the hit burns on for a while (the strongest burn wins; it does not stack)."""
        e = p.session.skills.effect
        share, secs = e("ignite", "burnPctOfHit"), e("ignite", "burnSeconds")
        if share <= 0 or secs <= 0:
            return
        dps = amount * share / secs
        if dps * secs >= t.burn_dps * t.burn_left:
            t.burn_dps, t.burn_left, t.burn_owner, t.burn_prof = dps, secs, p.player_id, prof

    def burst(self, p: PlayerActor, x: float, y: float, radius: float, attack: AttackerStats, element: str, prof: str, look: str) -> None:
        """Every monster within ``radius`` of (x, y) takes the hit (its body counts: a wide wolf is caught sooner)."""
        self.w.bus.publish(SpellBurst(x, y, radius, look))
        for m in list(self.w.monsters.values()):
            if m.alive and math.hypot(m.x - x, m.y - y) <= radius + m.radius:
                self.hit(p, m, attack, element, prof)

    def projectile_hit(self, proj, t: MonsterActor) -> bool:
        """A bolt or a ball reached a monster. False: the projectile is gone."""
        p = self.w.players.get(proj.owner_id)
        if p is None:
            return False
        if proj.burst_radius > 0:
            self.burst(p, proj.x, proj.y, proj.burst_radius, proj.attack, proj.element, proj.prof, proj.look)
            proj.burst_radius = 0.0  # already burst: it does not go off again where it ends
        else:
            self.hit(p, t, proj.attack, proj.element, proj.prof)
        return False

    def projectile_end(self, proj) -> None:
        """A ball that hit a wall or flew its whole range bursts where it is."""
        p = self.w.players.get(proj.owner_id)
        if p is not None and proj.burst_radius > 0:
            self.burst(p, proj.x, proj.y, proj.burst_radius, proj.attack, proj.element, proj.prof, proj.look)

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
                    self.burst(p, f.ax, f.ay, f.radius, f.burst, f.element, f.prof, f.look)
                    f.tick = tick
                continue
            f.left -= dt
            f.tick -= dt
            if f.tick <= 0:
                f.tick += tick
                for m in list(self.w.monsters.values()):
                    if m.alive and f.touches(m):
                        amount = self.resisted(p, m, f.dps * tick, f.element)
                        if amount > 0:  # fire on the ground hurts but never staggers
                            self.w._damage_monster(p, m, amount, f.prof, recover=False)
            if f.left <= 0:
                del self.fires[f.id]
        for m in list(self.w.monsters.values()):
            if m.burn_left > 0 and m.alive:
                self._step_burn(m, dt, tick)

    def _step_burn(self, m: MonsterActor, dt: float, tick: float) -> None:
        m.burn_left -= dt
        m.burn_tick -= dt
        if m.burn_tick <= 0:
            m.burn_tick += tick
            p = self.w.players.get(m.burn_owner)
            if p is not None:
                self.w._damage_monster(p, m, m.burn_dps * tick, m.burn_prof or "pyromancy", recover=False)
        if m.burn_left <= 0:
            m.burn_left = m.burn_dps = 0.0
