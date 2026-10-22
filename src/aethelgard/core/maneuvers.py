"""Skills that move the fighter (GDD 6.2–6.4): dashes (Charge, Blade Dance) and channels (Whirlwind).

A dash moves the player much faster than walking along a straight line for a few frames:
- Charge runs at one target (bodies stop it) and swings the moment it is in reach;
- Blade Dance goes through enemies along the aim and strikes each one it passes with both weapons.

A channel lasts while its key is held (``World`` gets ``ReleaseSkill`` on key up): Whirlwind drains
Energy every second and, every swing's worth of time, hits everyone around while the player keeps
walking. The world owns the actors; this only drives them with the world's own helpers, so a hit
here gives XP, loot, leech and on-hit effects exactly like a normal swing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .events import SkillUsed

if TYPE_CHECKING:
    from .world import PlayerActor, World

MELEE_REACH_AROUND = 1.8  # same as world.MELEE_REACH_AROUND (kept here to avoid a circular import)

DASH_SPEED_MULT = 3.0  # times the walking speed
DASH_HIT_SLACK = 0.4  # tiles beyond touching bodies that a dash still strikes


@dataclass
class Dash:
    skill: str
    left: float  # tiles still to travel
    speed: float  # tiles per second
    target_id: int | None = None  # Charge: strike this one when in reach
    dir_x: float = 0.0  # Blade Dance: straight along the aim, through bodies
    dir_y: float = 0.0
    hit_ids: set[int] = field(default_factory=set)


class Maneuvers:
    def __init__(self, world: World):
        self.w = world

    # ------------------------------------------------------------ starting

    def start_charge(self, p: PlayerActor, skill: str, target_id: int | None) -> str | None:
        w = self.w
        t = w.monsters.get(target_id) if target_id is not None else None
        if t is None or not t.alive:
            return "no target"
        reach = p.session.skills.effect(skill, "rangeTiles")
        if w.melee_gap(p, t) > reach:
            return f"{w.data.node(skill).name}: too far"
        if p.busy or p.state == "attacking" or not p.cooldowns.ready("global"):
            return None
        w._commit_skill(p, skill)
        p.path.clear()
        p.target_id, p.pending_skill = t.id, None
        p.dash = Dash(skill, reach + 1.0, w.player_speed(p) * DASH_SPEED_MULT, target_id=t.id)
        return None

    def start_blade_dance(self, p: PlayerActor, skill: str, x: float | None, y: float | None, target_id: int | None) -> str | None:
        w = self.w
        t = w.monsters.get(target_id) if target_id is not None else None
        ax, ay = (t.x, t.y) if t is not None else (x, y)
        if ax is None or ay is None:
            return "no target"
        if p.busy or p.state == "attacking" or not p.cooldowns.ready("global"):
            return None
        dx, dy = ax - p.x, ay - p.y
        n = math.hypot(dx, dy)
        ux, uy = (dx / n, dy / n) if n > 1e-6 else (p.face_x, p.face_y)
        w._commit_skill(p, skill)
        w._face(p, ux, uy)
        p.path.clear()
        p.target_id = p.pending_skill = None
        p.dash = Dash(skill, p.session.skills.effect(skill, "rangeTiles"), w.player_speed(p) * DASH_SPEED_MULT, dir_x=ux, dir_y=uy)
        return None

    def start_channel(self, p: PlayerActor, skill: str) -> str | None:
        if p.busy or p.state == "attacking":
            return None
        p.channel, p.channel_tick = skill, 0.0
        p.target_id = p.pending_skill = None
        self.w.bus.publish(SkillUsed(p.player_id, skill))
        return None

    def stop_channel(self, p: PlayerActor) -> None:
        p.channel = None

    # ------------------------------------------------------------ every step

    def step(self, p: PlayerActor, dt: float) -> bool:
        """Moves a dashing player (True: the step was spent on it) and spins a channel (False: the player may walk)."""
        if p.dash is not None:
            self._step_dash(p, dt)
            return True
        if p.channel is not None:
            self._step_channel(p, dt)
        return False

    def _step_dash(self, p: PlayerActor, dt: float) -> None:
        w, d = self.w, p.dash
        step = min(d.left, d.speed * dt)
        if d.target_id is not None:  # Charge: run at the target, strike once in reach
            t = w.monsters.get(d.target_id)
            if t is None or not t.alive:
                p.dash = None
                return
            if w.in_reach(p, t):
                p.dash = None
                w._face(p, t.x - p.x, t.y - p.y)
                w._begin_attack(p, t.id, d.skill, commit=False)
                return
            before = (p.x, p.y)
            w._step_towards(p, (t.x, t.y), d.speed, dt)
            moved = math.hypot(p.x - before[0], p.y - before[1])
            d.left -= max(moved, 1e-3)
            if moved < 1e-4 or d.left <= 0:
                p.dash = None
            return
        # Blade Dance: straight on, through bodies, striking whoever it passes
        nx, ny = p.x + d.dir_x * step, p.y + d.dir_y * step
        if not w.clear_of_walls(p, nx, ny):
            p.dash = None
            return
        p.x, p.y = nx, ny
        d.left -= step
        for m in list(w.monsters.values()):
            if m.alive and m.id not in d.hit_ids and math.hypot(m.x - p.x, m.y - p.y) <= p.radius + m.radius + DASH_HIT_SLACK:
                d.hit_ids.add(m.id)
                w.strike(p, m, d.skill, ("main", "off"))
        if d.left <= 1e-6:
            p.dash = None

    def _step_channel(self, p: PlayerActor, dt: float) -> None:
        w = self.w
        node = w.data.node(p.channel)
        if w.skill_block_reason(p, p.channel, ignore_energy=True):  # a hard hit only pauses it (the step is skipped)
            p.channel = None
            return
        p.energy -= node.energy_cost * dt
        if p.energy <= 0:
            p.energy = 0.0
            p.channel = None
            return
        p.channel_tick -= dt
        if p.channel_tick > 0:
            return
        p.channel_tick = w.timing.attack_seconds / (1 + w.attack_speed(p))  # one spin per swing's worth of time
        hand = ("off" if p.attack_hand == "main" else "main") if p.session.gear.dual_wielding else "main"
        p.attack_hand = hand
        for m in list(w.monsters.values()):
            if m.alive and w.melee_gap(p, m) <= MELEE_REACH_AROUND:
                w.strike(p, m, p.channel, (hand,))
