"""Skill and item icons (M7-02) cut from the icon sheet of ``data/ui/icons.json``.

Every lookup returns a scaled Surface, or None when the sheet is not downloaded yet or the id has no icon:
the caller then draws the name, as before.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pygame

from ..tools.fetch_sounds import file_path


class Icons:
    def __init__(self, art_dir: Path, cfg: dict):
        self.cfg = cfg
        self.path = file_path(cfg, art_dir, "icons")
        self._sheet: pygame.Surface | None = None
        self._tried = False
        self._cache: dict[tuple[int, int, bool], pygame.Surface] = {}

    def reload(self) -> None:
        """After a download: try the sheet again."""
        self._sheet, self._tried = None, False
        self._cache.clear()

    def sheet(self) -> pygame.Surface | None:
        if not self._tried:
            self._tried = True
            try:
                img = pygame.image.load(str(self.path))
                self._sheet = img.convert_alpha() if pygame.display.get_surface() is not None else img
            except (pygame.error, FileNotFoundError):
                self._sheet = None
        return self._sheet

    def get(self, index: int | None, size: int, cutout: bool = False) -> pygame.Surface | None:
        """``cutout``: without the icon's black square (an item lying on the ground)."""
        if index is None:
            return None
        key = (index, size, cutout)
        if key not in self._cache:
            sheet = self.sheet()
            n, cols = self.cfg["size"], self.cfg["columns"]
            rect = pygame.Rect(index % cols * n, index // cols * n, n, n)
            if sheet is None or not sheet.get_rect().contains(rect):
                return None
            img = sheet.subsurface(rect).copy()
            if cutout:
                img = _cut_black(img)
            self._cache[key] = pygame.transform.smoothscale(img, (size, size))
        return self._cache[key]

    def skill(self, skill_id: str, size: int) -> pygame.Surface | None:
        return self.get(self.cfg["skills"].get(skill_id), size)

    def item(self, base_id: str, size: int, cutout: bool = False) -> pygame.Surface | None:
        return self.get(self.cfg["items"].get(base_id), size, cutout)

    def consumable(self, consumable_id: str, size: int) -> pygame.Surface | None:
        return self.get(self.cfg["consumables"].get(consumable_id), size)


def _cut_black(img: pygame.Surface, darkest: int = 18) -> pygame.Surface:
    """Makes the near-black background of a Flare item icon see-through."""
    out = img.convert_alpha() if pygame.display.get_surface() is not None else img.copy()
    rgb = pygame.surfarray.pixels3d(out)
    alpha = pygame.surfarray.pixels_alpha(out)
    alpha[np.asarray(rgb).max(axis=2) <= darkest] = 0
    del rgb, alpha  # release the pixel locks
    return out
