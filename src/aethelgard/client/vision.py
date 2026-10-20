"""Campo de visão, névoa de guerra e mapa automático (GDD 9.5), com o FOV do tcod."""

from __future__ import annotations

import numpy as np
import pygame
import tcod

from ..core.world_gen import FLOOR, AreaLayout
from . import palette as C

VIEW_RADIUS = 14


class Vision:
    """Guarda o que já foi explorado em cada área/andar e o que está visível agora."""

    def __init__(self) -> None:
        self._explored: dict[tuple[str, int], np.ndarray] = {}
        self.visible: np.ndarray | None = None
        self.explored: np.ndarray | None = None
        self._layout: AreaLayout | None = None

    def enter(self, key: tuple[str, int], layout: AreaLayout, reveal_all: bool = False) -> None:
        """Ao trocar de área. O mundo é regenerado a cada jogo, então a memória vale só para esta sessão."""
        self._layout = layout
        if key not in self._explored or self._explored[key].shape != layout.tiles.shape:
            self._explored[key] = np.zeros(layout.tiles.shape, dtype=bool)
        self.explored = self._explored[key]
        if reveal_all:
            self.explored[:] = True
        self.visible = np.zeros(layout.tiles.shape, dtype=bool)

    def update(self, x: float, y: float) -> None:
        L = self._layout
        cx, cy = int(round(x)), int(round(y))
        if not L.in_bounds((cx, cy)):
            return
        transparency = L.tiles == FLOOR
        self.visible = tcod.map.compute_fov(
            transparency, (cy, cx), radius=VIEW_RADIUS, light_walls=True, algorithm=tcod.libtcodpy.FOV_SYMMETRIC_SHADOWCAST
        )
        self.explored |= self.visible

    def sees(self, x: float, y: float) -> bool:
        cx, cy = int(round(x)), int(round(y))
        v = self.visible
        return v is not None and 0 <= cy < v.shape[0] and 0 <= cx < v.shape[1] and bool(v[cy, cx])

    def draw_automap(self, screen: pygame.Surface, layout: AreaLayout, px: float, py: float, big: bool) -> None:
        """Mapa automático: pequeno no canto, ou grande sobre a tela (Tab)."""
        h, w = layout.tiles.shape
        scale = max(1, min(6, (min(screen.get_width(), screen.get_height()) - 80) // max(w, h))) if big else 2
        # imagem do mapa: só o que já foi explorado
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        floor = (layout.tiles == FLOOR) & self.explored
        wall = (layout.tiles != FLOOR) & self.explored
        rgb[floor] = (70, 72, 64)
        rgb[wall] = (150, 140, 120)
        rgb[floor & self.visible] = (110, 112, 96)
        surf = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
        surf = pygame.transform.scale(surf, (w * scale, h * scale))
        surf.set_colorkey((0, 0, 0))
        if big:
            surf.set_alpha(220)
            pos = ((screen.get_width() - surf.get_width()) // 2, (screen.get_height() - surf.get_height()) // 2)
        else:
            surf.set_alpha(170)
            pos = (screen.get_width() - surf.get_width() - 12, 12)
        screen.blit(surf, pos)
        for e in layout.exits:
            if self.explored[e.pos[1], e.pos[0]]:
                pygame.draw.rect(screen, C.EXIT, (pos[0] + e.pos[0] * scale - 1, pos[1] + e.pos[1] * scale - 1, scale + 2, scale + 2))
        if layout.waypoint and self.explored[layout.waypoint[1], layout.waypoint[0]]:
            wx, wy = layout.waypoint
            pygame.draw.rect(screen, C.WAYPOINT, (pos[0] + wx * scale - 1, pos[1] + wy * scale - 1, scale + 2, scale + 2))
        pygame.draw.circle(screen, C.PLAYER_BODY, (pos[0] + int(px * scale), pos[1] + int(py * scale)), max(2, scale))
