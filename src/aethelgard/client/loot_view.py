"""Drops on the ground: a small shape per drop, its name label (always while Alt is held, otherwise
only under the mouse) and which drop the mouse points at, so a click can send ``PickUp``."""

from __future__ import annotations

import pygame

from ..core.items import GoldDrop, GroundItem, ItemDrop, PotionDrop, drop_name
from . import palette as C
from .iso import Camera

PICK_RADIUS_PX = 14  # how close the mouse must be to a drop's shape (labels are hit-tested by their box)


def drop_color(g: GroundItem) -> tuple[int, int, int]:
    d = g.drop
    if isinstance(d, ItemDrop):
        return C.RARITY[int(d.item.rarity)]
    if isinstance(d, GoldDrop):
        return C.GOLD
    return C.RARITY[0]


class GroundView:
    def __init__(self, font: pygame.font.Font, art=None):
        """``art`` = :class:`sprites.EffectArt` (Flare loot pictures) or None for the placeholder shapes."""
        self.font = font
        self.art = art
        self.labels: list[tuple[pygame.Rect, int]] = []  # last frame's labels: (box on screen, ground id)
        self.boxes: dict[int, pygame.Rect] = {}  # last frame's pictures, for the mouse

    def draw_shapes(self, screen: pygame.Surface, cam: Camera, ground: list[GroundItem]) -> None:
        """Under the actors."""
        self.boxes = {}
        for g in ground:
            sx, sy = cam.to_screen(g.x, g.y)
            box = self.art.draw_drop(screen, (sx, sy), g.id, g.drop) if self.art is not None else None
            if box is None:
                self._shape(screen, g, sx, sy)
            else:
                self.boxes[g.id] = box

    def draw_labels(self, screen: pygame.Surface, cam: Camera, ground: list[GroundItem], show_all: bool, under: int | None, mouse) -> None:
        """Over the actors: every label while Alt is held, otherwise only the drop under the mouse."""
        shown = [(g, *cam.to_screen(g.x, g.y)) for g in ground]
        self.labels = []
        placed: list[pygame.Rect] = []
        # labels from the bottom of the screen up, nudged so they never cover each other (like D2)
        for g, sx, sy in sorted(shown, key=lambda t: -t[2]):
            if not show_all and g.id != under:
                continue
            img = self.font.render(drop_name(g.drop), True, drop_color(g))
            box = img.get_rect(midbottom=(sx, sy - 10)).inflate(8, 4)
            while any(box.colliderect(r) for r in placed):
                box.move_ip(0, -(box.h + 1))
            placed.append(box)
            hot = box.collidepoint(mouse) or g.id == under
            bg = pygame.Surface(box.size, pygame.SRCALPHA)
            bg.fill((40, 40, 60, 220) if hot else (0, 0, 0, 170))
            screen.blit(bg, box.topleft)
            screen.blit(img, img.get_rect(center=box.center))
            self.labels.append((box, g.id))

    @staticmethod
    def _shape(screen, g: GroundItem, sx: int, sy: int) -> None:
        d, color = g.drop, drop_color(g)
        if isinstance(d, GoldDrop):
            pygame.draw.circle(screen, (90, 70, 20), (sx, sy - 3), 5)
            pygame.draw.circle(screen, color, (sx, sy - 4), 4)
        elif isinstance(d, PotionDrop):
            liquid = C.LIFE if d.kind == "life" else C.ENERGY
            pygame.draw.rect(screen, liquid, (sx - 3, sy - 10, 6, 8), border_radius=2)
            pygame.draw.rect(screen, C.TEXT, (sx - 1, sy - 13, 2, 3))
        elif isinstance(d, ItemDrop):
            pygame.draw.polygon(screen, color, [(sx, sy - 9), (sx + 7, sy - 3), (sx, sy + 3), (sx - 7, sy - 3)])
            pygame.draw.polygon(screen, (0, 0, 0), [(sx, sy - 9), (sx + 7, sy - 3), (sx, sy + 3), (sx - 7, sy - 3)], 1)
        else:  # scroll
            pygame.draw.rect(screen, (214, 198, 160), (sx - 6, sy - 6, 12, 5), border_radius=2)

    def at(self, mouse: tuple[int, int], cam: Camera, ground: list[GroundItem]) -> int | None:
        """The drop under the mouse: its label first (labels sit above the crowd), then the closest shape."""
        for box, gid in reversed(self.labels):
            if box.collidepoint(mouse):
                return gid
        for gid, box in self.boxes.items():  # a picture: its box, a little bigger
            if box.inflate(6, 6).collidepoint(mouse):
                return gid
        best, best_d = None, PICK_RADIUS_PX
        for g in ground:
            sx, sy = cam.to_screen(g.x, g.y)
            d = ((sx - mouse[0]) ** 2 + (sy - 4 - mouse[1]) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = g.id, d
        return best
