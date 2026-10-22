"""Deterministic generation of open areas and dungeon floors (GDD 9).

Same master seed + same area + same floor = same map, whatever the order of visits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import tcod

from .data import AreaDef, GameData, PrefabDef
from .rng import Rng

WALL, FLOOR = 0, 1
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
CHUNK = 16
MAX_ATTEMPTS = 20


class ExitKind(Enum):
    EDGE = "edge"
    ENTRANCE = "entrance"
    STAIRS_DOWN = "stairs_down"
    STAIRS_UP = "stairs_up"
    PORTAL_TO_TOWN = "portal_to_town"  # walking into your town portal out in the world
    PORTAL_BACK = "portal_back"  # walking into it in town: back to where it was opened


Cell = tuple[int, int]


@dataclass(frozen=True)
class Exit:
    pos: Cell
    kind: ExitKind
    target_area: str | None  # None + stairs = next/previous floor of the same dungeon


@dataclass
class AreaLayout:
    width: int
    height: int
    tiles: np.ndarray  # uint8, tiles[y, x] (tiles[y][x] also works)
    entry: Cell = (0, 0)
    exits: list[Exit] = field(default_factory=list)
    spawn_points: list[Cell] = field(default_factory=list)
    waypoint: Cell | None = None
    boss_spot: Cell | None = None
    seed: int = 0
    attempts: int = 1
    markers: dict[str, tuple[Cell, dict]] = field(default_factory=dict)  # NPCs and named points (Tiled maps)
    chests: list[Cell] = field(default_factory=list)  # closed chests (prefabs, M5-03)
    connectors: list[Cell] = field(default_factory=list)  # a prefab room's doors (M5-04)
    mask: np.ndarray | None = None  # a prefab: the cells it sets (bool [y, x]); the others keep the generated map

    @classmethod
    def filled(cls, w: int, h: int, tile: int = WALL) -> AreaLayout:
        return cls(w, h, np.full((h, w), tile, dtype=np.uint8))

    def in_bounds(self, c: Cell) -> bool:
        return 0 <= c[0] < self.width and 0 <= c[1] < self.height

    def is_floor(self, c: Cell) -> bool:
        x, y = c
        return 0 <= x < self.width and 0 <= y < self.height and self.tiles[y, x] == FLOOR

    def set(self, c: Cell, tile: int) -> None:
        if self.in_bounds(c):
            self.tiles[c[1], c[0]] = tile

    def distances_from(self, start: Cell) -> np.ndarray:
        """Steps (BFS, 4 directions) from ``start``; -1 = unreachable. Array [y, x], computed in C by tcod."""
        dist = tcod.path.maxarray((self.height, self.width), dtype=np.int32, order="C")
        if self.is_floor(start):
            dist[start[1], start[0]] = 0
            tcod.path.dijkstra2d(dist, self.tiles, cardinal=1, diagonal=0, out=dist)
        dist[dist == np.iinfo(np.int32).max] = -1
        return dist

    def validate(self) -> str | None:
        """Is everything reachable from the entry? Returns the reason for the failure, or None."""
        if not self.is_floor(self.entry):
            return "entry is not floor"
        dist = self.distances_from(self.entry)
        for e in self.exits:
            if not self.is_floor(e.pos) or dist[e.pos[1], e.pos[0]] < 0:
                return f"exit {e.kind.value} at {e.pos} unreachable"
        for s in self.spawn_points:
            if dist[s[1], s[0]] < 0:
                return f"spawn {s} unreachable"
        for label, c in (("waypoint", self.waypoint), ("boss", self.boss_spot)):
            if c is not None and dist[c[1], c[0]] < 0:
                return f"{label} unreachable"
        return None

    def floor_count(self) -> int:
        return int(np.count_nonzero(self.tiles == FLOOR))


class AreaGenerator:
    def __init__(self, data: GameData):
        self._d = data
        self._tiled_cache: dict[str, AreaLayout] = {}

    def generate(self, area: AreaDef, floor: int, master_seed: int, spawn_count: int) -> AreaLayout:
        if area.map:
            layout = load_tiled_map(area.map)
            reason = layout.validate()
            if reason:
                raise RuntimeError(f"map '{area.map}' is invalid: {reason}")
            return layout
        base = Rng.for_label(master_seed, f"area:{area.id}:{floor}")
        reason = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            rng = base.fork(f"try:{attempt}")
            if area.kind == "dungeon":
                layout = self._dungeon(area, floor, rng, spawn_count)
            else:
                layout = self._open(area, rng, spawn_count, master_seed)
            layout.attempts = attempt
            reason = layout.validate() or ""
            if not reason:
                return layout
        raise RuntimeError(f"failed to generate '{area.id}' floor {floor}: {reason}")

    # ------------------------------------------------------------ open area

    def edge_fraction(self, a: AreaDef, b: AreaDef, master_seed: int) -> float:
        """Where along the shared edge the crossing is (0..1), the same seen from both areas.
        A hand-made neighbour fixes it with its own exit; otherwise the seed picks it for the pair."""
        for here, there in ((b, a), (a, b)):
            if here.map:
                tl = self._tiled(here.map)
                e = next((x for x in tl.exits if x.target_area == there.id), None)
                if e is not None:
                    side = side_toward(here, there)
                    along, length = (e.pos[1], tl.height) if side in (EAST, WEST) else (e.pos[0], tl.width)
                    return along / max(1, length - 1)
        pair = "|".join(sorted((a.id, b.id)))
        return Rng.for_label(master_seed, f"edge:{pair}").uniform(0.25, 0.75)

    def _tiled(self, path: str) -> AreaLayout:
        if path not in self._tiled_cache:
            self._tiled_cache[path] = load_tiled_map(path)
        return self._tiled_cache[path]

    def _open(self, area: AreaDef, rng: Rng, spawn_count: int, master_seed: int = 0) -> AreaLayout:
        cw, ch = area.size_chunks or (6, 6)
        w, h = cw * CHUNK, ch * CHUNK
        L = AreaLayout.filled(w, h)
        L.seed = rng.seed
        noise = np.array([rng.random() for _ in range(w * h)], dtype=np.float64).reshape(h, w)
        L.tiles[:] = np.where(noise < 0.42, WALL, FLOOR)
        L.tiles[0, :] = L.tiles[-1, :] = WALL
        L.tiles[:, 0] = L.tiles[:, -1] = WALL
        for _ in range(4):
            _smooth(L)
        prefab_rng = rng.fork("prefabs")  # its own stream: adding a piece does not reshuffle the rest of the map

        center = (w // 2, h // 2)
        L.entry = center
        edges = [0, 1, 2, 3]
        rng.shuffle(edges)
        k = 0
        for link in area.links:
            other = self._d.area(link)
            if other.kind == "dungeon":
                pos, kind = (rng.randint(12, w - 13), rng.randint(12, h - 13)), ExitKind.ENTRANCE
            elif area.map_pos is not None and other.map_pos is not None:  # the side facing the neighbour (continuity)
                side = side_toward(area, other)
                pos, kind = _edge_cell_at(L, side, self.edge_fraction(area, other, master_seed)), ExitKind.EDGE
            else:  # no place on the act map: any free side
                pos, kind = _edge_cell(L, edges[k % 4], rng), ExitKind.EDGE
                k += 1
            L.exits.append(Exit(pos, kind, link))

        joins = self._place_open_prefabs(L, area, center, prefab_rng)
        _carve_room(L, center, 4)
        for e in L.exits:
            _carve_path(L, center, e.pos, 1, rng)
            _carve_room(L, e.pos, 3 if e.kind == ExitKind.ENTRANCE else 2)  # room around it: its touch zone is open ground
        for c in joins:  # each piece's way in, joined to the middle of the map
            _carve_path(L, c, center, 0, prefab_rng)
        L.tiles[0, :] = L.tiles[-1, :] = WALL
        L.tiles[:, 0] = L.tiles[:, -1] = WALL
        for e in L.exits:
            L.set(e.pos, FLOOR)

        _remove_unreachable(L)
        if area.waypoint:
            L.waypoint = _waypoint_spot(L, (center[0] + 3, center[1]))
        _place_spawns(L, spawn_count, 14, 7, rng)
        return L

    # ------------------------------------------------------------ dungeon

    def _dungeon(self, area: AreaDef, floor: int, rng: Rng, spawn_count: int) -> AreaLayout:
        L = AreaLayout.filled(72, 72)
        L.seed = rng.seed
        rooms: list[tuple[int, int, int, int]] = []
        target = rng.randint(9, 13)
        prefab_rng = rng.fork("prefabs")
        pf = self._d.areas.prefabs
        wanted = [self._pick(self._prefabs_for(pf.rooms, area), prefab_rng) for _ in range(prefab_rng.randint(*pf.rooms_per_floor))]
        wanted = [p for p in wanted if p is not None]
        made: dict[int, AreaLayout] = {}  # room index → the prefab stamped there
        for _ in range(300):
            if len(rooms) >= target:
                break
            piece = wanted[0] if rooms and wanted else None  # room 0 (the arrival) stays a plain room
            rw, rh = (piece.width, piece.height) if piece else (rng.randint(6, 11), rng.randint(6, 11))
            x, y = rng.randint(2, L.width - rw - 3), rng.randint(2, L.height - rh - 3)
            if any(x < r[0] + r[2] + 2 and x + rw + 2 > r[0] and y < r[1] + r[3] + 2 and y + rh + 2 > r[1] for r in rooms):
                continue
            if piece:
                made[len(rooms)] = wanted.pop(0)
            rooms.append((x, y, rw, rh))
        for i, (x0, y0, rw, rh) in enumerate(rooms):
            if i in made:
                stamp(L, made[i], x0, y0)
            else:
                L.tiles[y0 : y0 + rh, x0 : x0 + rw] = FLOOR

        def center(i: int) -> Cell:
            r = rooms[i]
            mid = (r[0] + r[2] // 2, r[1] + r[3] // 2)
            return _nearest_floor(L, mid) if i in made else mid

        def door(i: int, toward: Cell) -> Cell:
            """Where a corridor meets room i: a prefab's connector nearest the other room, else the middle."""
            if i not in made or not made[i].connectors:
                return center(i)
            x0, y0 = rooms[i][:2]
            return min(((x0 + cx, y0 + cy) for cx, cy in made[i].connectors), key=lambda c: manhattan(c, toward))

        def manhattan(a: Cell, b: Cell) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        def join(i: int, j: int) -> None:
            _carve_l(L, door(i, center(j)), door(j, center(i)), rng)

        connected = [0]
        for i in range(1, len(rooms)):
            j = min(connected, key=lambda c: manhattan(center(c), center(i)))
            join(i, j)
            connected.append(i)
        if len(rooms) > 3:
            for _ in range(rng.randint(1, 2)):
                join(rng.randint(0, len(rooms) - 1), rng.randint(0, len(rooms) - 1))

        L.entry = center(0)
        up_kind = ExitKind.ENTRANCE if floor == 0 else ExitKind.STAIRS_UP
        L.exits.append(Exit(L.entry, up_kind, area.links[0] if floor == 0 and area.links else None))

        dist = L.distances_from(L.entry)
        reachable = [center(i) for i in range(1, len(rooms)) if dist[center(i)[1], center(i)[0]] >= 0]
        far = max(reachable, key=lambda c: dist[c[1], c[0]]) if reachable else L.entry
        if floor >= area.floors - 1:
            if area.boss:
                L.boss_spot = far
        else:
            L.exits.append(Exit(far, ExitKind.STAIRS_DOWN, None))
        if area.waypoint and floor == 0:
            L.waypoint = _waypoint_spot(L, (L.entry[0] + WAYPOINT_EXIT_GAP, L.entry[1]))
        _place_spawns(L, spawn_count, 10, 5, rng)
        return L

    # ------------------------------------------------------------ hand-made pieces (M5-03, M5-04)

    def _prefabs_for(self, defs: list[PrefabDef], area: AreaDef) -> list[tuple[AreaLayout, int]]:
        return [(self._tiled(p.map), p.weight) for p in defs if not p.areas or area.id in p.areas]

    @staticmethod
    def _pick(options: list[tuple[AreaLayout, int]], rng: Rng) -> AreaLayout | None:
        if not options:
            return None
        return options[rng.weighted({i: w for i, (_, w) in enumerate(options)})][0]

    def _place_open_prefabs(self, L: AreaLayout, area: AreaDef, center: Cell, rng: Rng) -> list[Cell]:
        """Stamps hand-made pieces away from the arrival point and the exits; returns each one's way in."""
        pf = self._d.areas.prefabs
        options = self._prefabs_for(pf.open, area)
        placed: list[tuple[int, int, int, int]] = []
        joins: list[Cell] = []
        for _ in range(rng.randint(*pf.open_per_area) if options else 0):
            piece = self._pick(options, rng)
            pw, ph = piece.width, piece.height
            for _ in range(60):
                x, y = rng.randint(2, L.width - pw - 3), rng.randint(2, L.height - ph - 3)
                mid = (x + pw // 2, y + ph // 2)
                if math.hypot(mid[0] - center[0], mid[1] - center[1]) < pf.min_tiles_from_entry + max(pw, ph) / 2:
                    continue
                if any(math.hypot(mid[0] - e.pos[0], mid[1] - e.pos[1]) < max(pw, ph) / 2 + 5 for e in L.exits):
                    continue
                if any(x < r[0] + r[2] + 2 and x + pw + 2 > r[0] and y < r[1] + r[3] + 2 and y + ph + 2 > r[1] for r in placed):
                    continue
                stamp(L, piece, x, y)
                placed.append((x, y, pw, ph))
                joins.append((x + piece.entry[0], y + piece.entry[1]))
                break
        return joins


# ---------------------------------------------------------------- helpers


def _smooth(L: AreaLayout) -> None:
    """Cellular automaton (8-neighborhood), vectorized with numpy."""
    t = L.tiles
    walls = (t == WALL).astype(np.int8)
    n = np.zeros_like(walls)
    n[1:-1, 1:-1] = sum(
        walls[1 + dy : walls.shape[0] - 1 + dy, 1 + dx : walls.shape[1] - 1 + dx]
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if dx or dy
    )
    inner = (slice(1, -1), slice(1, -1))
    new = t.copy()
    new[inner] = np.where(n[inner] >= 5, WALL, np.where(n[inner] <= 3, FLOOR, t[inner]))
    L.tiles[:] = new


NORTH, EAST, SOUTH, WEST = 0, 1, 2, 3  # same numbers as _edge_cell's edges: y=0, x=w-1, y=h-1, x=0


def side_toward(a: AreaDef, b: AreaDef) -> int:
    """The edge of ``a`` that faces ``b`` on the act map (mapPos)."""
    dx, dy = b.map_pos[0] - a.map_pos[0], b.map_pos[1] - a.map_pos[1]
    if abs(dx) >= abs(dy):
        return EAST if dx > 0 else WEST
    return SOUTH if dy > 0 else NORTH


def _edge_cell_at(L: AreaLayout, side: int, frac: float) -> Cell:
    """A cell on ``side`` at ``frac`` (0..1) of the way along it, kept 8 cells from the corners."""
    length = L.height if side in (EAST, WEST) else L.width
    along = max(8, min(length - 9, round(frac * (length - 1))))
    return {NORTH: (along, 0), EAST: (L.width - 1, along), SOUTH: (along, L.height - 1), WEST: (0, along)}[side]


def _edge_cell(L: AreaLayout, edge: int, rng: Rng) -> Cell:
    w, h = L.width, L.height
    if edge == 0:
        return (rng.randint(8, w - 9), 0)
    if edge == 1:
        return (w - 1, rng.randint(8, h - 9))
    if edge == 2:
        return (rng.randint(8, w - 9), h - 1)
    return (0, rng.randint(8, h - 9))


def _carve_room(L: AreaLayout, c: Cell, radius: int) -> None:
    x0, x1 = max(1, c[0] - radius), min(L.width - 1, c[0] + radius + 1)
    y0, y1 = max(1, c[1] - radius), min(L.height - 1, c[1] + radius + 1)
    if x0 < x1 and y0 < y1:
        L.tiles[y0:y1, x0:x1] = FLOOR


def _carve_path(L: AreaLayout, start: Cell, goal: Cell, half: int, rng: Rng) -> None:
    """Winding path toward the goal, with random detours."""
    x, y = start
    guard = (L.width + L.height) * 8
    while (x, y) != goal and guard > 0:
        guard -= 1
        _carve_room(L, (x, y), half)
        dx = (goal[0] > x) - (goal[0] < x)
        dy = (goal[1] > y) - (goal[1] < y)
        if rng.chance(0.25):
            if dx != 0:
                dy = rng.choice([1, -1])
            else:
                dx = rng.choice([1, -1])
            if rng.chance(0.5):
                dx = 0
            else:
                dy = 0
        elif dx != 0 and dy != 0:
            if rng.chance(0.5):
                dx = 0
            else:
                dy = 0
        x = max(0, min(L.width - 1, x + dx))
        y = max(0, min(L.height - 1, y + dy))
    while (x, y) != goal:
        if x != goal[0]:
            x += 1 if goal[0] > x else -1
        else:
            y += 1 if goal[1] > y else -1
        _carve_room(L, (x, y), 0)


def _carve_l(L: AreaLayout, a: Cell, b: Cell, rng: Rng) -> None:
    corner = (b[0], a[1]) if rng.chance(0.5) else (a[0], b[1])
    _carve_line(L, a, corner)
    _carve_line(L, corner, b)


def _carve_line(L: AreaLayout, a: Cell, b: Cell) -> None:
    dx = (b[0] > a[0]) - (b[0] < a[0])
    dy = (b[1] > a[1]) - (b[1] < a[1])
    x, y = a
    L.set((x, y), FLOOR)
    while (x, y) != b:
        x, y = x + dx, y + dy
        L.set((x, y), FLOOR)
        L.set((x + (1 if dy else 0), y + (1 if dx else 0)), FLOOR)  # corridor 2 tiles wide


def _remove_unreachable(L: AreaLayout) -> None:
    dist = L.distances_from(L.entry)
    L.tiles[(L.tiles == FLOOR) & (dist < 0)] = WALL


WAYPOINT_EXIT_GAP = 4  # tiles between a waypoint and any exit (stairs, entrance, edge), so neither covers the other


def _waypoint_spot(L: AreaLayout, near: Cell) -> Cell:
    """The floor cell closest to ``near`` that is reachable and at least WAYPOINT_EXIT_GAP from every exit."""
    dist = L.distances_from(L.entry)
    for r in range(max(L.width, L.height)):
        ring = [(near[0] + dx, near[1] + dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if max(abs(dx), abs(dy)) == r]
        for c in ring:
            if (L.is_floor(c) and dist[c[1], c[0]] >= 0
                    and all(math.hypot(c[0] - e.pos[0], c[1] - e.pos[1]) >= WAYPOINT_EXIT_GAP for e in L.exits)):
                return c
    return _nearest_floor(L, near)


def _nearest_floor(L: AreaLayout, c: Cell) -> Cell:
    for r in range(max(L.width, L.height)):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                n = (c[0] + dx, c[1] + dy)
                if L.is_floor(n):
                    return n
    return L.entry


def _place_spawns(L: AreaLayout, count: int, min_dist: int, spacing: int, rng: Rng) -> None:
    dist = L.distances_from(L.entry)
    ys, xs = np.nonzero(dist >= min_dist)
    cands = [(int(x), int(y)) for y, x in zip(ys, xs, strict=True)]
    rng.shuffle(cands)
    for sp in (spacing, spacing // 2, 1):
        for c in cands:
            if len(L.spawn_points) >= count:
                return
            if c not in L.spawn_points and all(abs(s[0] - c[0]) + abs(s[1] - c[1]) >= sp for s in L.spawn_points):
                L.spawn_points.append(c)


# ---------------------------------------------------------------- hand-made maps (Tiled)


def stamp(L: AreaLayout, piece: AreaLayout, x0: int, y0: int) -> None:
    """Copies a prefab onto the map at (x0, y0): the cells it sets, its chests and its pack spots."""
    h, w = piece.tiles.shape
    region = L.tiles[y0 : y0 + h, x0 : x0 + w]
    mask = piece.mask if piece.mask is not None else np.ones_like(piece.tiles, dtype=bool)
    region[mask] = piece.tiles[mask]
    for cx, cy in piece.chests:
        L.chests.append((x0 + cx, y0 + cy))
        L.set((x0 + cx, y0 + cy), FLOOR)
    for cx, cy in piece.spawn_points:
        L.spawn_points.append((x0 + cx, y0 + cy))
        L.set((x0 + cx, y0 + cy), FLOOR)


def load_tiled_map(relative_path: str) -> AreaLayout:
    """Loads a map from the Tiled editor (https://www.mapeditor.org) with pytmx.

    Convention: orthogonal map where each tile is one logic cell; the tile's ``walkable``
    property says whether it is floor. Objects: ``entry`` (arrival), ``exit`` (property
    ``target`` = area), ``waypoint`` and ``npc`` (property ``role``); prefabs also use ``chest``, ``spawn``
    and ``connector``, and cells left empty (no tile) are not part of the piece (``mask``). The game draws it isometric.
    """
    from importlib import resources

    import pytmx

    path = resources.files("aethelgard") / "data" / relative_path
    with resources.as_file(path) as real:
        tmx = pytmx.TiledMap(str(real))
    layer = next(lay for lay in tmx.visible_layers if isinstance(lay, pytmx.TiledTileLayer))
    L = AreaLayout.filled(tmx.width, tmx.height, WALL)
    L.mask = np.zeros((tmx.height, tmx.width), dtype=bool)
    for x, y, gid in layer:
        L.mask[y, x] = gid != 0
        props = tmx.get_tile_properties_by_gid(gid) or {}
        if props.get("walkable"):
            L.tiles[y, x] = FLOOR
    tw, th = tmx.tilewidth, tmx.tileheight
    for obj in tmx.objects:
        cell = (int(obj.x // tw), int(obj.y // th))
        kind = (obj.type or getattr(obj, "class", "") or "").lower()
        if kind == "entry":
            L.entry = cell
        elif kind == "exit":
            edge = cell[0] in (0, L.width - 1) or cell[1] in (0, L.height - 1)
            L.exits.append(Exit(cell, ExitKind.EDGE if edge else ExitKind.ENTRANCE, obj.properties.get("target")))
        elif kind == "waypoint":
            L.waypoint = cell
        elif kind == "npc":
            L.markers[obj.name] = (cell, dict(obj.properties))
        elif kind == "chest":
            L.chests.append(cell)
        elif kind == "spawn":
            L.spawn_points.append(cell)
        elif kind == "connector":
            L.connectors.append(cell)
    return L
