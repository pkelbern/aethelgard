"""Animated sprites for the player and the monsters (pygame side of ``animation.py``).

- ``SpriteLibrary`` reads ``data/ui/sprites.json`` and loads sheets from ``assets/sprites`` on demand.
- ``ActorSprites`` keeps what the client needs to animate each actor (how far it walked, since when it
  is in its state) and the corpses on the floor, and draws them. It only reads the world's actors;
  the core never knows sprites exist, and actors without a look keep the placeholder drawing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources

import pygame

from .animation import DIRECTION_NAMES, Pose, choose_pose, direction8, pose_frame
from .iso import Camera

FADE_SECONDS = 1.5
MOVE_EPSILON = 0.002  # tiles per frame below which an actor is standing still


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
    def __init__(self, config: dict | None = None):
        if config is None:
            path = resources.files("aethelgard") / "data" / "ui" / "sprites.json"
            config = json.loads(path.read_text(encoding="utf-8"))
        self.cfg = config
        self.looks: dict[str, dict] = config["looks"]
        self.gear: dict[str, dict[str, str]] = config["gear"]
        self.order = {DIRECTION_NAMES.index(name): layers for name, layers in config["layerOrder"].items()}
        self._sheets: dict[str, SpriteSheet] = {}
        self._root = resources.files("aethelgard") / "assets" / "sprites"

    def sheet(self, sheet_id: str) -> SpriteSheet:
        if sheet_id not in self._sheets:
            meta = json.loads((self._root / f"{sheet_id}.json").read_text(encoding="utf-8"))
            with resources.as_file(self._root / f"{sheet_id}.png") as png:
                image = pygame.image.load(str(png))
            if pygame.display.get_surface() is not None:
                image = image.convert_alpha()
            self._sheets[sheet_id] = SpriteSheet(meta, image)
        return self._sheets[sheet_id]

    def layers(self, look_id: str, gear_ids: list[str] = ()) -> dict[str, str] | None:
        """Slot → sheet for a look, with equipped gear on top (in the order given). None = no look."""
        look = self.looks.get(look_id)
        if look is None:
            return None
        out = dict(look["layers"])
        for base_id in gear_ids:
            out.update(self.gear.get(base_id, {}))
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

    def _blit(self, screen, pos, sheets: list[str], pose: Pose, direction: int, scale: float, flash: int, alpha: int = 255) -> pygame.Rect:
        sx, sy = pos
        box = pygame.Rect(sx, sy, 0, 0)
        for sheet_id in sheets:
            sheet = self.lib.sheet(sheet_id)
            anim = pose.anim if pose.anim in sheet.anims else "stance"
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
