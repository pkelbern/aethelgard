"""Map art: which tile goes on each cell, walls with the right face, trees and rocks, and map objects.

Reads ``data/ui/sprites.json`` → ``tiles``. Floors are painted into the map chunks; everything that stands
up (walls, trees, stairs, a chest, the merchant) becomes a :class:`Prop` that the client sorts with the
actors by depth, so a wall in front of the hero covers him, as in Diablo 2. Without the art (not downloaded
yet) :func:`MapArt.build` returns None and the map keeps its placeholder shapes.

Choices are cosmetic, so they come from a hash of the cell and the map's seed (no game RNG): the same map
always looks the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.world_gen import FLOOR, AreaLayout, ExitKind
from .iso import HALF_H, HALF_W
from .sprites import SpriteLibrary, TileSheet


def cell_hash(x: int, y: int, seed: int, salt: int = 0) -> int:
    h = (x * 73856093) ^ (y * 19349663) ^ (seed * 83492791) ^ (salt * 2654435761)
    h = (h ^ (h >> 13)) * 1274126177
    return (h ^ (h >> 16)) & 0x7FFFFFFF


def weighted_pick(weights: list[tuple[int, int]], h: int) -> int:
    total = sum(w for _, w in weights)
    r = h % total
    for tid, w in weights:
        if r < w:
            return tid
        r -= w
    return weights[-1][0]


def _weights(table: dict | None, sheet: TileSheet) -> list[tuple[int, int]]:
    """``{"64": 10, ...}`` → [(64, 10), ...], keeping only tiles the sheet has."""
    return [(int(k), int(v)) for k, v in (table or {}).items() if sheet.has(int(k)) and v > 0]


@dataclass
class Prop:
    """Something on a cell, drawn from a tile: a wall, a tree, stairs. ``depth`` sorts it with the actors."""

    x: int
    y: int
    sheet: TileSheet
    tile: int
    scale: float = 1.0
    flat: bool = False  # lies on the floor: drawn before every actor
    wall: bool = False  # may turn see-through when the hero is behind it
    kind: str = ""  # map object name (waypoint, stairsDown…) or "" for walls and props
    alpha: int = 255

    def __post_init__(self) -> None:
        self.depth = float(self.x + self.y)
        self.wx, self.wy = (self.x - self.y) * HALF_W, (self.x + self.y) * HALF_H  # cell center in world px
        self._img: dict[int, tuple] = {}

    def image(self, alpha: int = 255) -> tuple:
        """(surface, ox, oy), cut once per opacity."""
        hit = self._img.get(alpha)
        if hit is None:
            hit = self._img[alpha] = self.sheet.tile(self.tile, self.scale, alpha)
        return hit


@dataclass
class Npc:
    """A map NPC drawn with a look (the merchant)."""

    name: str
    x: float
    y: float
    look: str
    role: str


class MapArt:
    def __init__(self, lib: SpriteLibrary, layout: AreaLayout, theme: dict, sheet: TileSheet, objects: dict, area_kind: str):
        self.lib, self.layout, self.theme, self.sheet = lib, layout, theme, sheet
        self.objects_cfg, self.area_kind = objects, area_kind
        self.seed = layout.seed & 0xFFFFFFFF
        self.floor_weights = _weights(theme.get("floor"), sheet)
        self.wall_e = _weights(theme.get("wallE"), sheet)
        self.wall_s = _weights(theme.get("wallS"), sheet)
        self.wall_back = _weights(theme.get("wallBack"), sheet)
        self.props_weights = _weights(theme.get("props"), sheet)
        self.under = bool(theme.get("under"))
        self.scale = lib.cfg.get("tiles", {}).get("objectScale", 1.0)
        self._props: dict[tuple[int, int], list[Prop]] = {}
        self.objects, self.npcs = self._place_objects()

    # ---- building

    @classmethod
    def build(cls, lib: SpriteLibrary, layout: AreaLayout, area_id: str, area_kind: str) -> MapArt | None:
        cfg = lib.cfg.get("tiles")
        if not cfg:
            return None
        name = cfg.get("areaThemes", {}).get(area_id) or cfg.get("kindThemes", {}).get(area_kind)
        theme = cfg.get("themes", {}).get(name or "")
        if theme is None:
            return None
        sheet = lib.tilesheet(theme["sheet"])
        if sheet is None:
            return None
        objects = {**cfg.get("objects", {}), **theme.get("objects", {})}
        return cls(lib, layout, theme, sheet, objects, area_kind)

    # ---- floors

    def floor_tile(self, x: int, y: int) -> tuple | None:
        """(surface, ox, oy) for the floor of this cell, or None (a wall with nothing under it)."""
        L = self.layout
        if not self.floor_weights:
            return None
        if L.tiles[y, x] != FLOOR and not (self.under or self._touches_floor(x, y)):
            return None
        return self.sheet.tile(weighted_pick(self.floor_weights, cell_hash(x, y, self.seed)))

    def _touches_floor(self, x: int, y: int) -> bool:
        L = self.layout
        return any(L.is_floor((x + dx, y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    # ---- walls and props, per chunk

    def props_in_chunk(self, cx: int, cy: int, size: int) -> list[Prop]:
        key = (cx, cy)
        if key not in self._props:
            out = []
            L = self.layout
            for y in range(cy * size, min(L.height, (cy + 1) * size)):
                for x in range(cx * size, min(L.width, (cx + 1) * size)):
                    prop = self._prop_at(x, y)
                    if prop is not None:
                        out.append(prop)
            self._props[key] = out
        return self._props[key]

    def _prop_at(self, x: int, y: int) -> Prop | None:
        L = self.layout
        if L.tiles[y, x] == FLOOR:
            return None
        h = cell_hash(x, y, self.seed, 1)
        if self.wall_e or self.wall_s:  # dungeon-like: a wall facing the room
            if L.is_floor((x + 1, y)) and self.wall_e:
                return Prop(x, y, self.sheet, weighted_pick(self.wall_e, h), wall=True)
            if L.is_floor((x, y + 1)) and self.wall_s:
                return Prop(x, y, self.sheet, weighted_pick(self.wall_s, h), wall=True)
            if self._touches_floor(x, y) and self.wall_back:  # between the camera and the room
                return Prop(x, y, self.sheet, weighted_pick(self.wall_back, h), wall=True, alpha=self.theme.get("backAlpha", 255))
            return None
        if self.props_weights:  # open ground: trees and rocks, dense at the edge of the clearings
            chances = self.theme.get("propChance", [0.5])
            chances = chances if isinstance(chances, list) else [chances]
            for r, chance in enumerate(chances, 1):
                if self._near_floor(x, y, r):
                    if (h % 1000) / 1000 < chance:
                        return Prop(x, y, self.sheet, weighted_pick(self.props_weights, h >> 10), scale=self.scale, wall=True)
                    break
        return None

    def _near_floor(self, x: int, y: int, r: int) -> bool:
        L = self.layout
        return any(L.is_floor((x + dx, y + dy)) for dx in range(-r, r + 1) for dy in range(-r, r + 1))

    # ---- map objects

    def _object(self, name: str, x: int, y: int, state: int = 0) -> Prop | None:
        o = self.objects_cfg.get(name)
        if not o or "sheet" not in o:
            return None
        sheet = self.lib.tilesheet(o["sheet"])
        tiles = o.get("tiles") or [o.get("tile")]
        tid = tiles[min(state, len(tiles) - 1)]
        if sheet is None or tid is None or not sheet.has(tid):
            return None
        return Prop(x, y, sheet, tid, scale=o.get("scale", 1.0) * self.scale, flat=bool(o.get("flat")), kind=name)

    def _place_objects(self) -> tuple[list[Prop], list[Npc]]:
        L, objs, npcs = self.layout, [], []
        for e in L.exits:
            if e.kind == ExitKind.STAIRS_DOWN:
                name = "stairsDown"
            elif e.kind == ExitKind.STAIRS_UP or (e.kind == ExitKind.ENTRANCE and self.area_kind == "dungeon"):
                name = "stairsUp"
            elif e.kind == ExitKind.ENTRANCE:
                name = "entrance"
            else:
                name = "exit"
            prop = self._object(name, *e.pos)
            if prop is not None:
                objs.append(prop)
        for label, (cell, props) in L.markers.items():
            role = props.get("role", "")
            o = self.objects_cfg.get(role, {})  # object named after the NPC's role (stash, vendor)
            if "look" in o:
                npcs.append(Npc(label, float(cell[0]), float(cell[1]), o["look"], role))
            elif (prop := self._object(role, *cell)) is not None:
                objs.append(prop)
        return objs, npcs

    def waypoint(self, active: bool) -> Prop | None:
        wp = self.layout.waypoint
        return self._object("waypoint", wp[0], wp[1], 1 if active else 0) if wp else None

    def has_art(self, cell: tuple[int, int]) -> bool:
        """Something with art stands on this cell (its placeholder shape is not drawn, only its label)."""
        wp = self.layout.waypoint
        return any((p.x, p.y) == tuple(cell) for p in self.objects) or any((round(n.x), round(n.y)) == tuple(cell) for n in self.npcs) or (
            wp is not None and tuple(cell) == tuple(wp) and self.waypoint(False) is not None)
