"""Animated sprites for the player and the monsters (pygame side of ``animation.py``).

- ``SpriteLibrary`` reads ``data/ui/sprites.json`` and loads sheets from the art folder on demand. The art is
  not in the repository: ``ArtDownload`` fetches it in the background on first run (``tools/fetch_art.py``).
  A sheet that is not there yet is drawn as a magenta checkerboard, so a missing sprite is obvious.
- ``ActorSprites`` keeps what the client needs to animate each actor (how far it walked, since when it
  is in its state) and the corpses on the floor, and draws them. It only reads the world's actors;
  the core never knows sprites exist, and actors without a look keep the placeholder drawing.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from ..core.saves import default_art_dir
from ..tools.fetch_art import ArtError, fetch_art, http_fetch, load_config, missing_sheets, sheets_in_use, sprites_dir
from .animation import DIRECTION_NAMES, Pose, choose_pose, direction8, pose_frame
from .iso import Camera

FADE_SECONDS = 1.5
MOVE_EPSILON = 0.002  # tiles per frame below which an actor is standing still
MISSING_SIZE = (22, 46)  # the "no sprite" stand-in, about the size of a person
MISSING_COLORS = ((255, 0, 220), (20, 0, 20))


def missing_surface(scale: float = 1.0, lying: bool = False) -> pygame.Surface:
    """Magenta/black checkerboard with a white frame and a "?" — impossible to mistake for real art."""
    w, h = max(6, round(MISSING_SIZE[0] * scale)), max(6, round(MISSING_SIZE[1] * scale))
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    cell = max(3, w // 4)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            surf.fill(MISSING_COLORS[(x // cell + y // cell) % 2], (x, y, cell, cell))
    pygame.draw.rect(surf, (255, 255, 255), surf.get_rect(), 1)
    cx, cy, k = w / 2, h * 0.38, max(2, w // 6)  # "?" drawn with lines: no font needed
    pts = [(cx - k, cy - k), (cx - k / 2, cy - 2 * k), (cx + k / 2, cy - 2 * k), (cx + k, cy - k), (cx, cy), (cx, cy + k)]
    pygame.draw.lines(surf, (255, 255, 255), False, pts, 2)
    pygame.draw.circle(surf, (255, 255, 255), (round(cx), round(cy + 2 * k)), 2)
    return pygame.transform.rotate(surf, 90) if lying else surf


class SpriteSheet:
    """One atlas and its animations. Frames are cut (and scaled) once, on first use."""

    def __init__(self, meta: dict, image: pygame.Surface):
        self.image = image
        self.anims: dict[str, dict] = meta["anims"]
        self._cache: dict[tuple, tuple[pygame.Surface | None, int, int]] = {}

    def frame(self, anim: str, index: int, direction: int, scale: float = 1.0, flash: int = 0):
        """(surface, ox, oy) — ox, oy = the point that sits on the actor's feet; surface None = empty frame."""
        key = (anim, index, direction, round(scale, 3), flash)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        x, y, w, h, ox, oy = self.anims[anim]["rects"][index][direction]
        if w <= 0 or h <= 0:
            out = (None, 0, 0)
        else:
            surf = self.image.subsurface((x, y, w, h))
            if abs(scale - 1.0) > 1e-3:
                surf = pygame.transform.smoothscale(surf, (max(1, round(w * scale)), max(1, round(h * scale))))
                ox, oy = round(ox * scale), round(oy * scale)
            if flash:
                surf = surf.copy()
                surf.fill((flash, flash, flash, 0), special_flags=pygame.BLEND_RGBA_ADD)
            out = (surf, ox, oy)
        self._cache[key] = out
        return out


class SpriteLibrary:
    def __init__(self, config: dict | None = None, art_dir: Path | None = None):
        self.cfg = config if config is not None else load_config()
        self.art_dir = Path(art_dir) if art_dir is not None else default_art_dir()
        self.looks: dict[str, dict] = self.cfg["looks"]
        self.gear: dict[str, dict[str, str]] = self.cfg["gear"]
        self.order = {DIRECTION_NAMES.index(name): layers for name, layers in self.cfg["layerOrder"].items()}
        self._sheets: dict[str, SpriteSheet | None] = {}  # None = not on disk (tried once; see forget_missing)
        self._root = sprites_dir(self.art_dir)

    def sheet(self, sheet_id: str) -> SpriteSheet | None:
        """The sheet, or None when its files are missing or broken (the actor is drawn as "missing")."""
        if sheet_id not in self._sheets:
            self._sheets[sheet_id] = self._load(sheet_id)
        return self._sheets[sheet_id]

    def _load(self, sheet_id: str) -> SpriteSheet | None:
        try:
            meta = json.loads((self._root / f"{sheet_id}.json").read_text(encoding="utf-8"))
            image = pygame.image.load(str(self._root / f"{sheet_id}.png"))
        except (OSError, ValueError, pygame.error):
            return None
        if pygame.display.get_surface() is not None:
            image = image.convert_alpha()
        return SpriteSheet(meta, image)

    def forget_missing(self) -> None:
        """Try missing sheets again (after a download)."""
        self._sheets = {k: v for k, v in self._sheets.items() if v is not None}

    def missing(self) -> list[str]:
        return missing_sheets(self.cfg, self.art_dir)

    def total(self) -> int:
        return len(sheets_in_use(self.cfg))

    def without_look(self, monster_type_ids) -> list[str]:
        """Monster types that have no art defined at all (drawn as the magenta stand-in)."""
        return [t for t in monster_type_ids if t not in self.looks]

    def anim_for(self, sheet: SpriteSheet, wanted: str) -> str:
        """``wanted`` if the sheet has it, else the first of its fallbacks that it has (packs often lack hit/block)."""
        for name in (wanted, *self.cfg.get("fallbacks", {}).get(wanted, ()), "stance"):
            if name in sheet.anims:
                return name
        return next(iter(sheet.anims))

    def layers(self, look_id: str, equipped: list[tuple[str, str]] = ()) -> dict[str, str] | None:
        """Layer → sheet for a look, with the equipped items on top: ``equipped`` = (equipment slot, base id) pairs.
        ``slotLayers`` says which layers an item may draw from each slot. None = no look (placeholder)."""
        look = self.looks.get(look_id)
        if look is None:
            return None
        out = dict(look["layers"])
        allowed = self.cfg.get("slotLayers", {})
        for slot, base_id in equipped:
            layers = self.gear.get(base_id, {})
            keep = allowed.get(slot)
            out.update({k: v for k, v in layers.items() if keep is None or k in keep})
        return out

    def ordered(self, layers: dict[str, str], direction: int) -> list[str]:
        """Sheets in drawing order (back to front) for this facing."""
        order = self.order.get(direction, [])
        named = [layers[slot] for slot in order if slot in layers]
        return named + [s for slot, s in layers.items() if slot not in order]

    def attack_anim(self, look_id: str) -> str:
        return self.looks.get(look_id, {}).get("attack", "swing")

    def draw_scale(self, look_id: str) -> float:
        return self.looks.get(look_id, {}).get("drawScale", 1.0)


# ---------------------------------------------------------------- actors on screen


@dataclass
class Track:
    """What the client remembers about one actor between frames."""

    x: float
    y: float
    stride: float = 0.0  # tiles walked, for the run cycle
    moving: bool = False
    state: str = ""
    since: float = 0.0
    direction: int = 6
    seen: float = 0.0


@dataclass
class Corpse:
    sheets: list[str]
    x: float
    y: float
    direction: int
    scale: float
    born: float


@dataclass
class ActorSprites:
    lib: SpriteLibrary
    clock: float = 0.0
    tracks: dict[tuple[str, int], Track] = field(default_factory=dict)
    corpses: list[Corpse] = field(default_factory=list)
    rects: dict[tuple[str, int], pygame.Rect] = field(default_factory=dict)  # last drawn box, for the mouse
    _missing: dict[tuple, pygame.Surface] = field(default_factory=dict)

    # ---- per frame

    def tick(self, dt: float) -> None:
        self.clock += dt
        ttl = self.lib.cfg.get("corpseSeconds", 8)
        self.corpses = [c for c in self.corpses if self.clock - c.born < ttl]
        stale = [k for k, t in self.tracks.items() if self.clock - t.seen > 1.0]
        for k in stale:
            del self.tracks[k]
            self.rects.pop(k, None)

    def clear(self) -> None:
        """New area: nothing carries over."""
        self.tracks.clear()
        self.corpses.clear()
        self.rects.clear()

    def _track(self, key, actor) -> Track:
        t = self.tracks.get(key)
        if t is None:
            t = self.tracks[key] = Track(actor.x, actor.y, since=self.clock, state=actor.state)
        step = math.hypot(actor.x - t.x, actor.y - t.y)
        t.moving = step > MOVE_EPSILON
        t.stride += step
        t.x, t.y = actor.x, actor.y
        if actor.state != t.state:
            t.state, t.since = actor.state, self.clock
        if abs(actor.face_x) + abs(actor.face_y) > 1e-6:
            t.direction = direction8(actor.face_x, actor.face_y)
        t.seen = self.clock
        return t

    # ---- drawing

    def draw_actor(self, screen: pygame.Surface, cam: Camera, key: tuple[str, int], actor, layers: dict[str, str],
                   *, attack_anim: str = "swing", scale: float = 1.0, guarding: bool = False, hit_flash: bool = True) -> pygame.Rect:
        """Draws one actor and returns the screen box it covers (also kept in ``rects``)."""
        t = self._track(key, actor)
        duration = getattr(actor, "attack_duration", 0.0) or 0.0
        pose = choose_pose(
            actor.state,
            moving=t.moving,
            guarding=guarding,
            attack_progress=(actor.attack_elapsed / duration) if duration > 0 else 1.0,
            attack_anim=attack_anim,
            state_time=self.clock - t.since,
            clock=self.clock + (key[1] % 7) * 0.13,  # idle monsters do not breathe in sync
            stride_cycles=t.stride / self.lib.cfg.get("strideTiles", 1.7),
        )
        flash = self.lib.cfg.get("hitFlash", 0) if hit_flash and actor.state in ("hit_recovery", "stunned") else 0
        rect = self._blit(screen, cam.to_screen(actor.x, actor.y), self.lib.ordered(layers, t.direction), pose, t.direction, scale, flash)
        self.rects[key] = rect
        return rect

    def add_corpse(self, actor, layers: dict[str, str], scale: float) -> None:
        d = direction8(actor.face_x, actor.face_y) if abs(actor.face_x) + abs(actor.face_y) > 1e-6 else 6
        self.corpses.append(Corpse(self.lib.ordered(layers, d), actor.x, actor.y, d, scale, self.clock))

    def draw_corpses(self, screen: pygame.Surface, cam: Camera, visible) -> None:
        ttl = self.lib.cfg.get("corpseSeconds", 8)
        for c in self.corpses:
            if not visible(c.x, c.y):
                continue
            age = self.clock - c.born
            alpha = 255 if age < ttl - FADE_SECONDS else max(0, int(255 * (ttl - age) / FADE_SECONDS))
            self._blit(screen, cam.to_screen(c.x, c.y), c.sheets, Pose("die", "clock", age), c.direction, c.scale, 0, alpha)

    def draw_missing(self, screen: pygame.Surface, cam: Camera, key: tuple[str, int], actor, scale: float = 1.0) -> pygame.Rect:
        """An actor with no art at all: the magenta stand-in (lying down when dead)."""
        self._track(key, actor)
        pose = Pose("die" if actor.state == "dead" else "stance", "clock", 0.0)
        rect = self._blit(screen, cam.to_screen(actor.x, actor.y), [], pose, 0, scale, 0)
        self.rects[key] = rect
        return rect

    def _blit(self, screen, pos, sheets: list[str], pose: Pose, direction: int, scale: float, flash: int, alpha: int = 255) -> pygame.Rect:
        sx, sy = pos
        loaded = [self.lib.sheet(s) for s in sheets]
        if not loaded or any(s is None for s in loaded):  # one missing layer is enough: the stand-in shows something is wrong
            lying = pose.anim == "die"
            key = (round(scale, 3), lying)
            surf = self._missing.get(key)
            if surf is None:
                surf = self._missing[key] = missing_surface(scale, lying)
            if alpha < 255:
                surf = surf.copy()
                surf.set_alpha(alpha)
            return screen.blit(surf, (sx - surf.get_width() // 2, sy - surf.get_height()))
        box = pygame.Rect(sx, sy, 0, 0)
        for sheet in loaded:
            anim = self.lib.anim_for(sheet, pose.anim)
            a = sheet.anims[anim]
            index = pose_frame(pose, a["frames"], a["seconds"], a["loop"])
            surf, ox, oy = sheet.frame(anim, index, direction, scale, flash)
            if surf is None:
                continue
            if alpha < 255:
                surf = surf.copy()
                surf.set_alpha(alpha)
            r = screen.blit(surf, (sx - ox, sy - oy))
            box.union_ip(r)
        return box


# ---------------------------------------------------------------- first-run download


class ArtDownload:
    """Fetches the missing art in a background thread; the game keeps running with stand-ins meanwhile.

    The main loop calls ``poll()`` every frame: it returns a message once, when the download ends,
    and then the library is told to load the new sheets."""

    def __init__(self, lib: SpriteLibrary, fetch=None):
        self.lib = lib
        self._fetch = fetch
        self.state = "idle"  # idle | running | done | failed
        self.stage, self.done, self.total = "", 0, 0
        self.error = ""
        self.problems: list[str] = []
        self._reported = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Starts only if something is missing. True if a download began."""
        if self.state == "running" or not self.lib.missing():
            return False
        self.state, self._reported = "running", False
        self._thread = threading.Thread(target=self._run, name="art-download", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        self.problems = []
        try:
            fetch_art(self.lib.cfg, self.lib.art_dir, self._fetch or http_fetch(), progress=self._progress)
            self.state = "done"
        except ArtError as ex:  # some sources worked: those sheets are in place
            self.problems = ex.problems
            self.error = f"{len(ex.problems)} problem(s): " + "; ".join(ex.problems)
            self.state = "failed"
        except Exception as ex:  # no network, proxy, disk full: the game goes on with stand-ins
            self.problems = [f"{type(ex).__name__}: {ex}"]
            self.error = self.problems[0]
            self.state = "failed"

    def _progress(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total

    @property
    def fraction(self) -> float:
        """0..1 for a progress bar: downloads are the first 70%, conversion the rest."""
        if self.state in ("done", "failed"):
            return 1.0
        part = self.done / self.total if self.total else 0.0
        return 0.7 * part if self.stage != "convert" else 0.7 + 0.3 * part

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def status_text(self) -> str:
        if self.state != "running":
            return ""
        what = "Downloading art" if self.stage != "convert" else "Preparing sprites"
        pct = f" {100 * self.done // self.total}%" if self.total else ""
        return f"{what}{pct}… (missing sprites show as magenta squares)"

    def poll(self) -> str | None:
        if self._reported or self.state not in ("done", "failed"):
            return None
        self._reported = True
        self.lib.forget_missing()
        if self.state == "done":
            return "Art downloaded."
        return f"Some art could not be downloaded ({self.error}). Magenta squares = missing sprites. Retry: python -m aethelgard.tools.fetch_art"
