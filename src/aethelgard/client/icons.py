"""Skill and item icons (M7-02) cut from the icon sheet of ``data/ui/icons.json``.

Every lookup returns a scaled Surface, or None when the sheet is not downloaded yet or the id has no icon:
the caller then draws the name, as before.
"""

from __future__ import annotations

from pathlib import Path

import pygame

from ..tools.fetch_sounds import file_path


class Icons:
    def __init__(self, art_dir: Path, cfg: dict):
        self.cfg = cfg
        self.path = file_path(cfg, art_dir, "icons")
        self._sheet: pygame.Surface | None = None
        self._tried = False
        self._cache: dict[tuple[int, int], pygame.Surface] = {}

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

    def get(self, index: int | None, size: int) -> pygame.Surface | None:
        if index is None:
            return None
        key = (index, size)
        if key not in self._cache:
            sheet = self.sheet()
            n, cols = self.cfg["size"], self.cfg["columns"]
            rect = pygame.Rect(index % cols * n, index // cols * n, n, n)
            if sheet is None or not sheet.get_rect().contains(rect):
                return None
            self._cache[key] = pygame.transform.smoothscale(sheet.subsurface(rect), (size, size))
        return self._cache[key]

    def skill(self, skill_id: str, size: int) -> pygame.Surface | None:
        return self.get(self.cfg["skills"].get(skill_id), size)

    def item(self, base_id: str, size: int) -> pygame.Surface | None:
        return self.get(self.cfg["items"].get(base_id), size)

    def consumable(self, consumable_id: str, size: int) -> pygame.Surface | None:
        return self.get(self.cfg["consumables"].get(consumable_id), size)
