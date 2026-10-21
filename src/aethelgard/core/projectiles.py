"""Projectiles: arrows, magic bolts, spells and the thrown shield (GDD 8 and 6.3).

They fly in a straight line from where they were fired toward where the target WAS, and never
steer: stepping out of the line dodges them. Walls stop them. The world moves them and decides
what a hit does (``World._step_projectiles``); here is only the data and the geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import tcod

from .combat import AttackerStats
from .events import MonsterXpInfo
from .world_gen import AreaLayout

MAX_SUBSTEP = 0.1  # tiles per collision check, so a fast projectile cannot skip through a body or a wall
RANGE_EPS = 1e-6  # a leftover range this small is spent (float sums like 8.999999999999998 of 9 must not keep it alive)


@dataclass
class Projectile:
    id: int
    x: float
    y: float
    dir_x: float
    dir_y: float
    speed: float
    radius: float
    max_range: float
    look: str  # how the client draws it: arrow | bolt | shield
    owner: str  # "monster" (hits players) or "player" (hits monsters)
    owner_id: int  # monster instance id or player id
    magic: bool = False  # magic cannot be blocked by the guard, except with Bulwark (GDD 19.2, question 3)
    attack: AttackerStats | None = None  # a monster's shot keeps its numbers even if the shooter dies
    info: MonsterXpInfo | None = None
    skill: str | None = None  # the player's skill that fired it
    prof: str | None = None  # proficiency that gets the XP of a player's shot (Archery for arrows)
    pierce: bool = False  # goes on through every enemy in its line (Piercing Arrow)
    element: str | None = None  # a spell (fire): always hits, resisted instead of armored (core/spells.py)
    burst_radius: float = 0.0  # Fireball: bursts over this radius on the first enemy, a wall or the end of its flight
    bounces_left: int = 0
    bounce_range: float = 0.0
    traveled: float = 0.0
    hit_ids: set[int] = field(default_factory=set)

    def aim(self, tx: float, ty: float) -> None:
        dx, dy = tx - self.x, ty - self.y
        n = math.hypot(dx, dy)
        if n > 1e-9:
            self.dir_x, self.dir_y = dx / n, dy / n

    @property
    def spent(self) -> bool:
        return self.traveled >= self.max_range - RANGE_EPS

    def substeps(self, dt: float) -> list[tuple[float, float]]:
        """Positions along this frame's flight, at most MAX_SUBSTEP apart (the last one is the new position)."""
        dist = min(self.speed * dt, self.max_range - self.traveled)
        n = max(1, math.ceil(dist / MAX_SUBSTEP))
        return [(self.x + self.dir_x * dist * k / n, self.y + self.dir_y * dist * k / n) for k in range(1, n + 1)]


def line_of_sight(layout: AreaLayout, a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True when no wall cell lies on the straight line between the two points (Bresenham, from tcod)."""
    (x0, y0), (x1, y1) = (round(a[0]), round(a[1])), (round(b[0]), round(b[1]))
    return all(layout.is_floor((int(x), int(y))) for x, y in tcod.los.bresenham((x0, y0), (x1, y1)).tolist())
