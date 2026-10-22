"""pygame_gui windows: the skill picker of an action-bar slot and the debug console (F1).

The skill trees are painted by ``client.skill_tree_view``.
"""

from __future__ import annotations

from html import escape
from importlib import resources
from typing import TYPE_CHECKING

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UILabel, UITextBox, UITextEntryLine, UIWindow

if TYPE_CHECKING:
    from .app import Game

PICKER_W = 300


class GameUI:
    def __init__(self, game: Game, size: tuple[int, int]):
        self.g = game
        theme = resources.files("aethelgard") / "data" / "ui" / "theme.json"
        with resources.as_file(theme) as path:
            self.manager = pygame_gui.UIManager(size, str(path))
        self.manager.preload_fonts([
            {"name": "noto_sans", "point_size": 14, "style": "bold", "antialiased": "1"},
            {"name": "noto_sans", "point_size": 14, "style": "italic", "antialiased": "1"},
        ])
        self.picker: UIWindow | None = None
        self.picker_slot: int | None = None
        self.console: UIWindow | None = None
        self._console_out: UITextBox | None = None
        self._console_in: UITextEntryLine | None = None
        self._console_log: list[str] = ["Type <b>help</b> to list the commands."]
        self._picker_buttons: dict[UIButton, str | None] = {}  # None = clear the slot

    # ------------------------------------------------------------ state

    @property
    def typing(self) -> bool:
        return self._console_in is not None and self._console_in.alive() and self._console_in.is_focused

    @staticmethod
    def _open(win: UIWindow | None) -> bool:
        return bool(win and win.alive())

    @property
    def any_open(self) -> bool:
        return any(self._open(w) for w in (self.picker, self.console))

    @property
    def over_ui(self) -> bool:
        """Mouse over any window: the world ignores hover and clicks there."""
        return self.manager.get_hovering_any_element()

    def resize(self, size: tuple[int, int]) -> None:
        """The window changed size (options): the interface fills the new one."""
        self.manager.set_window_resolution(size)

    def close_all(self) -> None:
        for w in (self.picker, self.console):
            if self._open(w):
                w.kill()
        self.picker = self.console = None
        self.picker_slot = None

    # ------------------------------------------------------------ events

    def process(self, event: pygame.event.Event) -> bool:
        """Hands the event to pygame_gui. True if the interface consumed it."""
        consumed = self.manager.process_events(event)
        if event.type == pygame_gui.UI_BUTTON_PRESSED and event.ui_element in self._picker_buttons:
            self.g.bind_slot(self.picker_slot, self._picker_buttons[event.ui_element])
            self.close_picker()
            return True
        if event.type == pygame_gui.UI_TEXT_ENTRY_FINISHED and event.ui_element is self._console_in:
            text = event.text.strip()
            self._console_in.set_text("")
            if text:
                parts = text.split()
                try:
                    out = self.g.command(parts[0].lower(), parts[1:])
                except Exception as ex:  # the console never crashes the game
                    out = f"error: {ex}"
                self._console_log.append(f"<font color='#e8c452'>&gt; {escape(text)}</font><br>{escape(out).replace(chr(10), '<br>')}")
                self._console_out.set_text("<br>".join(self._console_log[-30:]))
            self._console_in.focus()
            return True
        if event.type == pygame_gui.UI_WINDOW_CLOSE:
            if event.ui_element is self.picker:
                self.picker, self.picker_slot = None, None
            elif event.ui_element is self.console:
                self.console = None
        return consumed

    def update(self, dt: float) -> None:
        self.manager.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        self.manager.draw_ui(screen)

    # ------------------------------------------------------------ skill picker for one bar slot

    def open_picker(self, slot: int) -> None:
        """Lists the actives with a level; choosing one puts it in ``slot`` (swapping if it was elsewhere)."""
        self.close_picker()
        s, keys = self.g.session, self.g.keys
        actives = s.skills.learned_actives()
        rows = len(actives) + 1
        h = 44 + 38 * rows + (26 if not actives else 0)  # title bar + one 38 px row per button
        rect = self.g.hud.slot_rects(len(s.skill_bar), self.manager.window_resolution)[slot]
        x = max(8, min(rect.centerx - PICKER_W // 2, self.manager.window_resolution[0] - PICKER_W - 8))
        win = UIWindow(pygame.Rect(x, rect.top - h - 8, PICKER_W, h), self.manager,
                       window_display_title=f"Skill for key {keys.slot_label(slot)}", object_id="#picker_window")
        self._picker_buttons.clear()
        y = 6
        if not actives:
            UILabel(pygame.Rect(8, y, PICKER_W - 48, 22), "No active skill unlocked yet.", self.manager, container=win, object_id="#node_info")
            y += 26
        for node_id in actives:
            node = s.data.node(node_id)
            other = s.skill_bar.slot_of(node_id)
            label = node.name + (f"   (on {keys.slot_label(other)})" if other is not None and other != slot else "")
            btn = UIButton(pygame.Rect(8, y, PICKER_W - 48, 32), label, self.manager, container=win)
            if other == slot:
                btn.select()
            self._picker_buttons[btn] = node_id
            y += 38
        self._picker_buttons[UIButton(pygame.Rect(8, y, PICKER_W - 48, 32), "Clear this key", self.manager, container=win)] = None
        self.picker, self.picker_slot = win, slot

    def close_picker(self) -> None:
        if self._open(self.picker):
            self.picker.kill()
        self.picker, self.picker_slot = None, None

    # ------------------------------------------------------------ console

    def toggle_console(self) -> None:
        if self._open(self.console):
            self.console.kill()
            self.console = None
            return
        W, H = self.manager.window_resolution
        win = UIWindow(pygame.Rect(20, H - 300, 700, 280), self.manager, window_display_title="Debug console  (F1 closes)",
                       object_id="#console_window")
        self._console_out = UITextBox("<br>".join(self._console_log[-30:]), pygame.Rect(0, 0, 664, 190), self.manager, container=win)
        self._console_in = UITextEntryLine(pygame.Rect(0, 196, 664, 32), self.manager, container=win)
        self._console_in.focus()
        self.console = win
