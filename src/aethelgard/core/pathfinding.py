"""Busca de caminho com o A* do tcod (implementado em C), em 8 direções."""

from __future__ import annotations

import tcod

from .world_gen import AreaLayout, Cell

DIAGONAL_COST = 1.41


def nearest_walkable(L: AreaLayout, c: Cell, radius: int = 6) -> Cell | None:
    if L.is_floor(c):
        return c
    for r in range(1, radius + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                n = (c[0] + dx, c[1] + dy)
                if L.is_floor(n):
                    return n
    return None


def find_path(L: AreaLayout, start: Cell, goal: Cell) -> list[Cell]:
    """Caminho de ``start`` (exclusive) até ``goal``; se o destino é parede, vai até o chão mais próximo."""
    if not L.is_floor(start):
        return []
    g = nearest_walkable(L, goal)
    if g is None or g == start:
        return []
    # tcod usa índices (linha, coluna) = (y, x); custo 0 = parede
    astar = tcod.path.AStar(L.tiles, diagonal=DIAGONAL_COST)
    path = astar.get_path(start[1], start[0], g[1], g[0])
    return [(int(x), int(y)) for y, x in path]
