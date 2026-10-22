"""Game menu (Esc): resume, save, options, credits, save and quit. The game is paused while it is open.

Single player only pauses the local world; in co-op (M10) the host keeps running and this becomes
a plain overlay. It is a :class:`screens.Panel`, like the title, options and credits screens.
"""

from __future__ import annotations

import pygame

from . import palette as C
from .screens import Panel


class GameMenu(Panel):
    title = "Paused"
    width = 280

    def items(self) -> list[tuple[str, str, bool]]:
        return [("resume", "Resume", True), ("save", "Save game", True), ("characters", "Change character", True),
                ("options", "Options", True), ("credits", "Credits", True), ("quit", "Save and quit", True)]

    def height(self) -> int:
        return super().height() + 16

    def toggle(self) -> None:
        self.open = not self.open

    def handle(self, ev: pygame.event.Event) -> bool:
        """While open it takes every click and key (the world is paused). True: the event was used."""
        if self.open and ev.type == pygame.KEYDOWN and self.g.keys.action_for(ev.key) == "cancel":
            self.open = False
            return True
        return super().handle(ev)

    def press(self, bid: str) -> None:
        if bid == "resume":
            self.open = False
        elif bid == "save":
            self.g.save_game()
        elif bid == "characters":
            self.g.save_game()
            self.g.select.open_list()
        elif bid == "options":
            self.g.options.open = True
        elif bid == "credits":
            self.g.credits.open = True
        elif bid == "quit":
            self.g.running = False  # run() saves on the way out

    def draw_extra(self, screen: pygame.Surface) -> None:
        r = self.rect
        hint = self.g.fonts["tiny"].render(f"{self.g.keys.action_label('cancel')} resumes", True, C.MUTED)
        screen.blit(hint, hint.get_rect(midbottom=(r.centerx, r.bottom - 8)))
