"""Character sheet window (C): the rows of ``core.sheet`` in a panel on the left; hovering a row shows
where its number comes from. It only reads; nothing here changes the character."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from ..core.sheet import character_sheet
from . import palette as C
from .tooltip import draw_panel, draw_tooltip

if TYPE_CHECKING:
    from .app import Game

WIDTH = 340
ROW_H = 16
SECTION_GAP = 6
PAD = 14


class CharacterWindow:
    def __init__(self, game: Game):
        self.g = game
        self.open = False
        self._rows: list[tuple[pygame.Rect, list]] = []  # (row box, help lines) from the last draw

    def toggle(self) -> None:
        self.open = not self.open

    @property
    def rect(self) -> pygame.Rect:
        _, H = self.g.screen.get_size()
        return pygame.Rect(16, 56, WIDTH, H - 56 - 92)

    def contains(self, pos) -> bool:
        return self.open and self.rect.collidepoint(pos)

    def handle(self, ev: pygame.event.Event) -> bool:
        """Clicks on the sheet do nothing, but they must not walk the character either."""
        return self.open and ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and self.rect.collidepoint(ev.pos)

    def draw(self, screen: pygame.Surface) -> None:
        if not self.open:
            return
        f = self.g.fonts
        r = self.rect
        draw_panel(screen, r, opaque=True)
        keys = self.g.keys
        screen.blit(f["body"].render("Character", True, C.GOLD), (r.x + PAD, r.y + 10))
        hint = f["tiny"].render(f"hover a line for details · {keys.action_label('character')} closes", True, C.MUTED)
        screen.blit(hint, (r.right - hint.get_width() - PAD, r.y + 16))
        y = r.y + 36
        mouse = pygame.mouse.get_pos()
        self._rows = []
        hovered = None
        for sec in character_sheet(self.g.world, self.g.player):
            screen.blit(f["small"].render(sec.title, True, C.GOLD), (r.x + PAD, y))
            y += ROW_H
            for row in sec.rows:
                box = pygame.Rect(r.x + 6, y - 1, r.w - 12, ROW_H)
                if box.collidepoint(mouse):
                    pygame.draw.rect(screen, (38, 40, 52), box, border_radius=3)
                    hovered = row, box
                screen.blit(f["small"].render(row.label, True, C.MUTED), (r.x + PAD + 6, y))
                val = f["small"].render(row.value, True, C.TEXT)
                screen.blit(val, (r.right - PAD - val.get_width(), y))
                self._rows.append((box, row.help))
                y += ROW_H
            y += SECTION_GAP
        if hovered is not None and hovered[0].help:
            # beside the panel, level with the row, so it never hides the numbers
            draw_tooltip(screen, f, hovered[0].help, (r.right + 4, hovered[1].y))
