"""How spells look on screen: fire on the ground, bursts, falling meteors, burning monsters and the
fire bolts when their art is missing. Only reads the core (``World.spells``, ``SpellBurst`` events)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

from ..core.events import SpellBurst
from .iso import HALF_H, HALF_W

if TYPE_CHECKING:
    from .app import Game

FIRE_DARK = (150, 40, 10)
FIRE = (240, 120, 30)
FIRE_HOT = (255, 220, 120)
BURST_SECONDS = 0.35
ROOT2 = math.sqrt(2)  # a circle on the grid is an ellipse on screen, sqrt(2) half-tiles across per tile of radius


@dataclass
class Burst:
    x: float
    y: float
    radius: float
    left: float = BURST_SECONDS


class SpellView:
    def __init__(self, game: Game):
        self.g = game
        self.bursts: list[Burst] = []
        self.clock = 0.0

    def listen(self, bus) -> None:
        bus.subscribe(SpellBurst, lambda e: self.bursts.append(Burst(e.x, e.y, e.radius)))

    def update(self, dt: float) -> None:
        self.clock += dt
        for b in self.bursts:
            b.left -= dt
        self.bursts = [b for b in self.bursts if b.left > 0]

    # ---- under the actors

    def _ellipse(self, screen, x: float, y: float, radius: float, color, width: int = 0, alpha: int = 255) -> None:
        sx, sy = self.g.cam.to_screen(x, y)
        w, h = radius * HALF_W * ROOT2, radius * HALF_H * ROOT2
        if alpha >= 255:
            pygame.draw.ellipse(screen, color, (sx - w, sy - h, 2 * w, 2 * h), width)
            return
        surf = pygame.Surface((int(2 * w) + 2, int(2 * h) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(surf, (*color, alpha), surf.get_rect(), width)
        screen.blit(surf, (sx - w - 1, sy - h - 1))

    def _flame(self, screen, sx: float, sy: float, size: float, phase: float) -> None:
        """A small flickering flame standing on (sx, sy)."""
        k = 0.75 + 0.25 * math.sin(self.clock * 14 + phase)
        h = size * k
        pygame.draw.polygon(screen, FIRE_DARK, [(sx - size * 0.45, sy), (sx, sy - h * 1.6), (sx + size * 0.45, sy)])
        pygame.draw.polygon(screen, FIRE, [(sx - size * 0.3, sy), (sx, sy - h * 1.2), (sx + size * 0.3, sy)])
        pygame.draw.polygon(screen, FIRE_HOT, [(sx - size * 0.12, sy), (sx, sy - h * 0.6), (sx + size * 0.12, sy)])

    def draw_ground(self, screen) -> None:
        """Fire walls, burning ground and where a meteor is about to land."""
        sees = self.g.vision.sees
        for f in self.g.world.spells.fires.values():
            if not sees((f.ax + f.bx) / 2, (f.ay + f.by) / 2):
                continue
            if not f.landed:  # a meteor on its way: a closing ring where it will hit
                total = max(1e-6, self.g.data.node(f.skill).base.get("delaySeconds", 1.0))
                k = max(0.0, min(1.0, f.delay / total))
                self._ellipse(screen, f.ax, f.ay, f.radius, FIRE_DARK, 0, 70)
                self._ellipse(screen, f.ax, f.ay, f.radius * (0.3 + 0.7 * k), FIRE, 2)
                sx, sy = self.g.cam.to_screen(f.ax, f.ay)
                drop = 420 * k  # the rock falls from above the screen
                pygame.draw.circle(screen, FIRE, (sx + drop * 0.35, sy - drop - 10), 14)
                pygame.draw.circle(screen, FIRE_HOT, (sx + drop * 0.35, sy - drop - 10), 7)
                continue
            length = math.hypot(f.bx - f.ax, f.by - f.ay)
            if length < 1e-6:  # burning ground
                self._ellipse(screen, f.ax, f.ay, f.radius, FIRE_DARK, 0, 90)
                n = max(3, int(f.radius * 5))
                for i in range(n):
                    a = i / n * math.tau
                    sx, sy = self.g.cam.to_screen(f.ax + math.cos(a) * f.radius * 0.6, f.ay + math.sin(a) * f.radius * 0.6)
                    self._flame(screen, sx, sy, 8, i * 1.7)
                continue
            n = max(2, int(length * 2.5))  # a wall: flames along the line
            for i in range(n + 1):
                t = i / n
                sx, sy = self.g.cam.to_screen(f.ax + (f.bx - f.ax) * t, f.ay + (f.by - f.ay) * t)
                self._flame(screen, sx, sy, 18, i * 2.3)

    # ---- over the actors

    def draw_bursts(self, screen) -> None:
        for b in self.bursts:
            k = 1 - b.left / BURST_SECONDS
            alpha = int(220 * (1 - k))
            self._ellipse(screen, b.x, b.y, b.radius * (0.4 + 0.6 * k), FIRE, 0, alpha // 2)
            self._ellipse(screen, b.x, b.y, b.radius * (0.4 + 0.6 * k), FIRE_HOT, 3, alpha)

    def draw_burning(self, screen, m, sx: int, sy: int) -> None:
        """Ignite: little flames on a burning monster."""
        if m.burn_left > 0 and m.alive:
            for i, dx in enumerate((-8, 6)):
                self._flame(screen, sx + dx, sy - 14 - 8 * i, 7, i * 3.1 + m.id)

    def draw_projectile(self, screen, proj, head: tuple[float, float], tail: tuple[float, float]) -> bool:
        """Fire bolts and balls without their art: a glowing ball with a trail. False for other looks."""
        if proj.element != "fire":
            return False
        big = proj.burst_radius > 0
        pygame.draw.line(screen, FIRE_DARK, tail, head, 7 if big else 5)
        pygame.draw.circle(screen, FIRE, head, 9 if big else 6)
        pygame.draw.circle(screen, FIRE_HOT, head, 4 if big else 3)
        return True
