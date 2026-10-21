"""Game menu (Esc): resume, save, save and quit. The game is paused while it is open.

Single player only pauses the local world; in co-op (M10) the host keeps running and this becomes
a plain overlay. Options (volume, resolution, keys) join it with M7-05.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from . import palette as C
from .tooltip import draw_button, draw_panel

if TYPE_CHECKING:
    from .app import Game

BUTTONS = (("resume", "Resume"), ("save", "Save game"), ("quit", "Save and quit"))
W, BTN_H, GAP = 280, 40, 12


class GameMenu:
    def __init__(self, game: Game):
        self.g = game
        self.open = False

    def toggle(self) -> None:
        self.open = not self.open

    @property
    def rect(self) -> pygame.Rect:
        sw, sh = self.g.screen.get_size()
        h = 70 + len(BUTTONS) * (BTN_H + GAP) + 20
        return pygame.Rect((sw - W) // 2, (sh - h) // 2, W, h)

    def buttons(self) -> dict[str, pygame.Rect]:
        r = self.rect
        return {bid: pygame.Rect(r.x + 30, r.y + 60 + i * (BTN_H + GAP), W - 60, BTN_H) for i, (bid, _) in enumerate(BUTTONS)}

    def handle(self, ev: pygame.event.Event) -> bool:
        """While open it takes every click and key (the world is paused). True: the event was used."""
        if not self.open:
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for bid, r in self.buttons().items():
                if r.collidepoint(ev.pos):
                    self.press(bid)
                    break
        elif ev.type == pygame.KEYDOWN and self.g.keys.action_for(ev.key) == "cancel":
            self.open = False
        return ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.KEYDOWN, pygame.KEYUP)

    def press(self, bid: str) -> None:
        if bid == "resume":
            self.open = False
        elif bid == "save":
            self.g.save_game()
        elif bid == "quit":
            self.g.running = False  # run() saves on the way out

    def draw(self, screen: pygame.Surface) -> None:
        if not self.open:
            return
        shade = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 120))
        screen.blit(shade, (0, 0))
        r = self.rect
        draw_panel(screen, r, opaque=True)
        f = self.g.fonts
        title = f["body"].render("Paused", True, C.GOLD)
        screen.blit(title, title.get_rect(midtop=(r.centerx, r.y + 16)))
        labels = dict(BUTTONS)
        for bid, br in self.buttons().items():
            draw_button(screen, f["small"], br, labels[bid])
        hint = f["tiny"].render(f"{self.g.keys.action_label('cancel')} resumes", True, C.MUTED)
        screen.blit(hint, hint.get_rect(midbottom=(r.centerx, r.bottom - 8)))
