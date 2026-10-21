"""2:1 isometric projection, camera and map drawing in pre-rendered chunks."""

from __future__ import annotations

import math

import pygame

from ..core.world_gen import FLOOR, AreaLayout, ExitKind
from . import palette as C

TILE_W, TILE_H = 64, 32
HALF_W, HALF_H = TILE_W // 2, TILE_H // 2
WALL_H = 18
CHUNK = 16


def grid_to_world(gx: float, gy: float) -> tuple[float, float]:
    return (gx - gy) * HALF_W, (gx + gy) * HALF_H


def world_to_grid(wx: float, wy: float) -> tuple[float, float]:
    return wx / TILE_W + wy / TILE_H, wy / TILE_H - wx / TILE_W


class Camera:
    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.x = self.y = 0.0  # center, in world coordinates

    def follow(self, gx: float, gy: float, dt: float, snap: bool = False) -> None:
        tx, ty = grid_to_world(gx, gy)
        k = 1.0 if snap else 1 - math.exp(-10 * dt)
        self.x += (tx - self.x) * k
        self.y += (ty - self.y) * k

    def to_screen(self, gx: float, gy: float) -> tuple[int, int]:
        wx, wy = grid_to_world(gx, gy)
        return round(wx - self.x + self.w / 2), round(wy - self.y + self.h / 2)

    def to_grid(self, sx: float, sy: float) -> tuple[float, float]:
        return world_to_grid(sx + self.x - self.w / 2, sy + self.y - self.h / 2)


def diamond(cx: float, cy: float, scale: float = 1.0) -> list[tuple[float, float]]:
    hw, hh = HALF_W * scale, HALF_H * scale
    return [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]


class MapRenderer:
    """Pre-renders the map in 16×16-tile chunks and only draws the visible chunks."""

    def __init__(self, layout: AreaLayout, theme: str, art=None):
        """``art`` = :class:`tilemap.MapArt` (tiles, walls as props) or None for the placeholder shapes."""
        self.layout = layout
        self.art = art
        self.theme = C.THEMES.get(theme, C.THEMES["open"])
        self._chunks: dict[tuple[int, int], tuple[pygame.Surface, tuple[float, float]]] = {}
        self._fog: dict[tuple[int, int], tuple[int, pygame.Surface]] = {}

    def _chunk(self, cx: int, cy: int):
        key = (cx, cy)
        if key in self._chunks:
            return self._chunks[key]
        L, th = self.layout, self.theme
        x0, y0 = cx * CHUNK, cy * CHUNK
        # world bounding box: corner (x0, y0+CHUNK) is the leftmost; (x0+CHUNK, y0) the rightmost
        left = grid_to_world(x0, y0 + CHUNK)[0] - HALF_W
        top = grid_to_world(x0, y0)[1] - HALF_H - WALL_H
        width = CHUNK * TILE_W + TILE_W
        height = CHUNK * TILE_H + TILE_H + WALL_H
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        for s in range(2 * CHUNK - 1):
            for dx in range(max(0, s - CHUNK + 1), min(s, CHUNK - 1) + 1):
                x, y = x0 + dx, y0 + (s - dx)
                if x >= L.width or y >= L.height:
                    continue
                wx, wy = grid_to_world(x, y)
                px, py = wx - left, wy - top
                if self.art is not None:  # tiles: floors here; walls and trees are props, sorted with the actors
                    tile = self.art.floor_tile(x, y)
                    if tile is not None:
                        surf.blit(tile[0], (px - tile[1], py - tile[2]))
                    continue
                if L.tiles[y][x] == FLOOR:
                    col = th["floor_a"] if (x + y) % 2 == 0 else th["floor_b"]
                    pygame.draw.polygon(surf, col, diamond(px, py))
                elif th.get("solid_walls") or self._near_floor(x, y):
                    self._wall(surf, px, py, th)
        self._chunks[key] = (surf, (left, top))
        return self._chunks[key]

    def _near_floor(self, x: int, y: int) -> bool:
        L = self.layout
        return any(L.is_floor((x + dx, y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    @staticmethod
    def _wall(surf, cx, cy, th) -> None:
        h = WALL_H
        top = [(cx, cy - HALF_H - h), (cx + HALF_W, cy - h), (cx, cy + HALF_H - h), (cx - HALF_W, cy - h)]
        left = [(cx - HALF_W, cy - h), (cx, cy + HALF_H - h), (cx, cy + HALF_H), (cx - HALF_W, cy)]
        right = [(cx + HALF_W, cy - h), (cx, cy + HALF_H - h), (cx, cy + HALF_H), (cx + HALF_W, cy)]
        pygame.draw.polygon(surf, th["wall_left"], left)
        pygame.draw.polygon(surf, th["wall_right"], right)
        pygame.draw.polygon(surf, th["wall_top"], top)

    def draw(self, screen: pygame.Surface, cam: Camera, explored=None) -> None:
        L = self.layout
        # screen corners in grid space → range of visible chunks
        corners = [cam.to_grid(0, 0), cam.to_grid(cam.w, 0), cam.to_grid(0, cam.h), cam.to_grid(cam.w, cam.h)]
        gx0 = max(0, int(min(c[0] for c in corners)) - 2) // CHUNK
        gx1 = min(L.width - 1, int(max(c[0] for c in corners)) + 2) // CHUNK
        gy0 = max(0, int(min(c[1] for c in corners)) - 2) // CHUNK
        gy1 = min(L.height - 1, int(max(c[1] for c in corners)) + 2) // CHUNK
        for cy in range(gy0, gy1 + 1):
            for cx in range(gx0, gx1 + 1):
                surf, (left, top) = self._chunk(cx, cy)
                pos = (round(left - cam.x + cam.w / 2), round(top - cam.y + cam.h / 2))
                screen.blit(surf, pos)
                if explored is not None:
                    fog = self._fog_chunk(cx, cy, explored, surf.get_size(), left, top)
                    if fog is not None:
                        screen.blit(fog, pos)

    def visible_chunks(self, cam: Camera, margin: int = 2) -> list[tuple[int, int]]:
        L = self.layout
        corners = [cam.to_grid(0, 0), cam.to_grid(cam.w, 0), cam.to_grid(0, cam.h), cam.to_grid(cam.w, cam.h)]
        gx0 = max(0, int(min(c[0] for c in corners)) - margin) // CHUNK
        gx1 = min(L.width - 1, int(max(c[0] for c in corners)) + margin) // CHUNK
        gy0 = max(0, int(min(c[1] for c in corners)) - margin) // CHUNK
        gy1 = min(L.height - 1, int(max(c[1] for c in corners)) + margin) // CHUNK
        return [(cx, cy) for cy in range(gy0, gy1 + 1) for cx in range(gx0, gx1 + 1)]

    def props(self, cam: Camera, explored=None) -> list:
        """Walls, trees and map objects on screen and already seen (tall art reaches a few cells beyond the screen)."""
        if self.art is None:
            return []
        out = []
        # tall art reaches up to ~4 cells above its foot and ~2 sideways: keep what can touch the screen
        left, top = cam.x - cam.w / 2, cam.y - cam.h / 2  # screen corner in world px
        x0, x1, y0, y1 = left - 3 * TILE_W, left + cam.w + 3 * TILE_W, top - TILE_H, top + cam.h + 8 * TILE_H
        for cx, cy in self.visible_chunks(cam, margin=6):
            for prop in self.art.props_in_chunk(cx, cy, CHUNK):
                if x0 <= prop.wx <= x1 and y0 <= prop.wy <= y1 and (explored is None or explored[prop.y, prop.x]):
                    out.append(prop)
        out += [p for p in self.art.objects if explored is None or explored[p.y, p.x]]
        return out

    @staticmethod
    def prop_rect(cam: Camera, prop) -> pygame.Rect:
        surf, ox, oy = prop.image()
        sx, sy = round(prop.wx - cam.x + cam.w / 2), round(prop.wy - cam.y + cam.h / 2)
        return pygame.Rect(sx - ox, sy - oy, surf.get_width(), surf.get_height())

    @staticmethod
    def draw_prop(screen: pygame.Surface, cam: Camera, prop, alpha: int = 255) -> None:
        surf, ox, oy = prop.image(alpha)
        screen.blit(surf, (round(prop.wx - cam.x + cam.w / 2) - ox, round(prop.wy - cam.y + cam.h / 2) - oy))

    def _fog_chunk(self, cx: int, cy: int, explored, size, left: float, top: float):
        """Fog of war: covers in black what was never seen. Rebuilt only when the chunk changes."""
        x0, y0 = cx * CHUNK, cy * CHUNK
        part = explored[y0 : y0 + CHUNK, x0 : x0 + CHUNK]
        seen = int(part.sum())
        if seen == part.size:
            return None
        cached = self._fog.get((cx, cy))
        if cached and cached[0] == seen:
            return cached[1]
        fog = pygame.Surface(size, pygame.SRCALPHA)
        for (dy, dx) in zip(*(~part).nonzero(), strict=True):
            wx, wy = grid_to_world(x0 + dx, y0 + dy)
            px, py = wx - left, wy - top
            pygame.draw.polygon(fog, C.BG, diamond(px, py, 1.08))
            pygame.draw.rect(fog, C.BG, (px - HALF_W, py - HALF_H - WALL_H, TILE_W, WALL_H + HALF_H))
        self._fog[(cx, cy)] = (seen, fog)
        return fog

    def draw_markers(self, screen: pygame.Surface, cam: Camera, font: pygame.font.Font, area_names: dict[str, str], explored=None) -> None:
        """Exits, waypoint and NPCs, but only where the fog of war has been lifted (same rule as the automap)."""
        L = self.layout

        def seen(c) -> bool:
            return explored is None or bool(explored[round(c[1]), round(c[0])])

        art = self.art
        for e in L.exits:
            if not seen(e.pos):
                continue
            sx, sy = cam.to_screen(*e.pos)
            if art is None or not art.has_art(e.pos):
                pygame.draw.polygon(screen, C.EXIT, diamond(sx, sy, 0.8))
            label = {ExitKind.STAIRS_DOWN: "Down", ExitKind.STAIRS_UP: "Up"}.get(e.kind) or area_names.get(e.target_area or "", "")
            img = font.render(label, True, C.EXIT)
            lift = 30 if art is None or not art.has_art(e.pos) else 95  # above the signpost or the stairs
            screen.blit(img, (sx - img.get_width() // 2, sy - lift))
        if L.waypoint and seen(L.waypoint) and (art is None or not art.has_art(L.waypoint)):
            sx, sy = cam.to_screen(*L.waypoint)
            pygame.draw.polygon(screen, C.WAYPOINT, diamond(sx, sy, 0.7))
        for name, (cell, props) in L.markers.items():
            if not seen(cell):
                continue
            sx, sy = cam.to_screen(*cell)
            color = C.NPC.get(props.get("role", ""), C.TEXT)
            lift = 50
            if art is None or not art.has_art(cell):
                pygame.draw.circle(screen, (0, 0, 0), (sx, sy), 10)
                pygame.draw.rect(screen, color, (sx - 8, sy - 34, 16, 30), border_radius=4)
            else:
                lift = 110  # above the art (a person, a chest)
            img = font.render(name, True, color)
            screen.blit(img, (sx - img.get_width() // 2, sy - lift))
