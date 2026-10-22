"""Sprite animation math with no pygame: directions, frame choice, poses and the sheet format.

Kept apart from ``sprites.py`` (which loads and draws with pygame) so all of it can be tested
without a screen. The sheet format is the game's own (``assets/sprites/<id>.json``), so any art
source can feed it: today the importer converts Flare sheets, later a Blender render can write it.

Sheet JSON::

    {"image": "<id>.png", "anims": {"run": {"frames": 8, "seconds": 0.533, "loop": "looped",
                                            "rects": [[[x, y, w, h, ox, oy] × 8 directions] × frames]}}}

``ox, oy`` is the point of the frame that sits on the actor's feet. Directions follow the
art: index 0..7 = map SW, W, NW, N, NE, E, SE, S (``direction8`` turns a grid vector into one).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DIRECTIONS = 8
# compass names used by the art, in index order (map axes: +x = E, +y = S; on screen E is down-right)
DIRECTION_NAMES = ("SW", "W", "NW", "N", "NE", "E", "SE", "S")
LOOPS = ("looped", "back_forth", "play_once")


def direction8(dx: float, dy: float, default: int = 6) -> int:
    """Direction index for a vector in grid coordinates (same rule as the art's renderer)."""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return default
    octant = round(math.atan2(dy, dx) / (math.pi / 4))  # E = 0, S = 2, W = ±4, N = -2
    return (octant + 5) % DIRECTIONS


def frame_at(frames: int, seconds: float, loop: str, t: float) -> int:
    """Frame for an animation playing for ``t`` seconds. ``back_forth`` goes 0→n−1→1 and repeats."""
    if frames <= 1 or seconds <= 0:
        return 0
    k = int(max(0.0, t) / seconds * frames)
    if loop == "looped":
        return k % frames
    if loop == "back_forth":
        cycle = 2 * frames - 2
        k %= cycle
        return k if k < frames else cycle - k
    return min(k, frames - 1)  # play_once: holds the last frame


def frame_by_progress(frames: int, progress: float) -> int:
    """Frame for an action that is ``progress`` (0..1) done, e.g. an attack synced with the core timing."""
    return max(0, min(frames - 1, int(progress * frames)))


# ---------------------------------------------------------------- poses


@dataclass(frozen=True)
class Pose:
    """Which animation to show and how to pick its frame.

    ``mode``: ``clock`` = by time (``value`` = seconds playing); ``progress`` = 0..1 of the action;
    ``stride`` = by distance walked (``value`` = cycles, so feet do not slide at any speed).
    """

    anim: str
    mode: str
    value: float


def choose_pose(
    state: str,
    *,
    moving: bool,
    guarding: bool,
    attack_progress: float,
    attack_anim: str,
    state_time: float,
    clock: float,
    stride_cycles: float,
) -> Pose:
    """Animation for an actor of the core (player or monster) from its state.

    The core decides what happens and when (attack timing, recoveries); this only picks the picture.
    """
    if state == "dead":
        return Pose("die", "clock", state_time)
    if state == "attacking":
        return Pose(attack_anim, "progress", attack_progress)
    if state == "winding":  # a boss special: the swing is raised and held until the area lands
        return Pose(attack_anim, "progress", min(1.0, attack_progress) * 0.4)
    if state in ("hit_recovery", "stunned"):
        return Pose("hit", "clock", state_time)
    if state == "block_recovery" or guarding:
        return Pose("block", "clock", state_time if state == "block_recovery" else 1e9)  # guard up: last frame
    if moving:
        return Pose("run", "stride", stride_cycles)
    return Pose("stance", "clock", clock)


def pose_frame(pose: Pose, frames: int, seconds: float, loop: str) -> int:
    if pose.mode == "progress":
        return frame_by_progress(frames, pose.value)
    if pose.mode == "stride":
        return int(pose.value * frames) % max(1, frames)
    return frame_at(frames, seconds, loop, pose.value)


# ---------------------------------------------------------------- Flare's text format (importer)


@dataclass
class FlareAnim:
    frames: int
    seconds: float
    loop: str
    rects: dict[tuple[int, int], tuple[int, int, int, int, int, int]]  # (frame, direction) → x, y, w, h, ox, oy
    images: dict[tuple[int, int], str] = field(default_factory=dict)  # (frame, direction) → image key ("" = the first image)


def flare_images(text: str) -> dict[str, str]:
    """Images of an animation file by key: ``image=path`` is key "", ``image=path,name`` is key "name" (bigger
    monsters keep one image per animation and name it on every frame line)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        key, _, val = raw.strip().partition("=")
        if key.strip() == "image" and val.strip():
            path, _, name = val.strip().partition(",")
            out.setdefault(name.strip(), path.strip())
    return out


def parse_flare_animation(text: str) -> tuple[str, dict[str, FlareAnim]]:
    """Reads a Flare animation file: returns the image path and the animations.

    Both Flare formats: *compressed* (one ``frame=`` line per frame and direction, as in flare-game) and the
    older *uncompressed* grid (``render_size``/``render_offset`` once, then per animation ``position`` = first
    column; frame i of direction d is the cell at column position+i, row d), used by many community packs."""
    image, anims, current = "", {}, None
    size, offset = (0, 0), (0, 0)  # uncompressed: carried over from where they were set
    grids: dict[str, tuple[int, tuple[int, int], tuple[int, int]]] = {}  # anim → position, size, offset
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = FlareAnim(1, 0.0, "looped", {})
            anims[line[1:-1]] = current
            grids[line[1:-1]] = (0, size, offset)
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key == "image":
            image = val.split(",")[0].strip()
        elif key in ("render_size", "render_offset"):
            a, b = (int(v) for v in val.split(",")[:2])
            if key == "render_size":
                size = (a, b)
            else:
                offset = (a, b)
            if current is not None:
                name = next(reversed(anims))
                pos, s, o = grids[name]
                grids[name] = (pos, size, offset)
        elif current is None:
            continue
        elif key == "position":
            name = next(reversed(anims))
            grids[name] = (int(val), size, offset)
        elif key == "frames":
            current.frames = int(val)
        elif key == "duration":
            current.seconds = _seconds(val)
        elif key == "type":
            current.loop = val if val in LOOPS else "looped"
        elif key == "frame":
            parts = [p.strip() for p in val.split(",")]
            index = int(parts[0])
            direction = DIRECTION_NAMES.index(parts[1]) if parts[1] in DIRECTION_NAMES else int(parts[1])
            x, y, w, h, ox, oy = (int(p) for p in parts[2:8])
            current.rects[(index, direction)] = (x, y, w, h, ox, oy)
            if len(parts) > 8 and parts[8]:  # files with one image per animation name the image of each frame
                current.images[(index, direction)] = parts[8]
    for name, a in anims.items():  # uncompressed animations: every cell of the grid
        pos, (w, h), (ox, oy) = grids[name]
        if not a.rects and w > 0 and h > 0:
            a.rects = {(i, d): ((pos + i) * w, d * h, w, h, ox, oy) for i in range(a.frames) for d in range(DIRECTIONS)}
    return image, anims


def _seconds(val: str) -> float:
    if val.endswith("ms"):
        return float(val[:-2]) / 1000
    if val.endswith("s"):
        return float(val[:-1])
    return float(val) / 1000  # Flare: a bare number is milliseconds


# ---------------------------------------------------------------- atlas packing


def pack_shelves(sizes: list[tuple[int, int]], width: int, pad: int = 1) -> tuple[list[tuple[int, int]], int]:
    """Places rectangles in rows (tallest first). Returns the top-left of each, in input order, and the atlas height."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    pos: list[tuple[int, int]] = [(0, 0)] * len(sizes)
    x = y = row_h = 0
    for i in order:
        w, h = sizes[i]
        if w > width:
            raise ValueError(f"frame {w}px wider than the atlas ({width}px)")
        if x + w > width:
            x, y, row_h = 0, y + row_h + pad, 0
        pos[i] = (x, y)
        x += w + pad
        row_h = max(row_h, h)
    return pos, y + row_h


def parse_flare_tileset(text: str) -> tuple[list[str], dict[int, tuple[int, int, int, int, int, int, int]]]:
    """Reads a Flare ``tilesetdefs/*.txt``: its images (a tileset may have several ``[tileset]`` sections, one
    image each) and ``tile id → image index, x, y, w, h, ox, oy``. ``(ox, oy)`` is the point that sits on the
    center of the cell the tile is placed on."""
    images, tiles = [], {}
    for raw in text.splitlines():
        key, _, val = raw.strip().partition("=")
        if key == "img":
            images.append(val.strip())
        elif key == "tile" and images:
            parts = [int(p) for p in val.split(",")[:7]]
            if len(parts) == 7:
                tiles[parts[0]] = (len(images) - 1, *parts[1:])
    return images, tiles


def is_flare_tileset(text: str) -> bool:
    return "[tileset]" in text or any(line.startswith("tile=") for line in text.splitlines()[:20])
