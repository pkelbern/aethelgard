"""pygame_gui windows: skill trees (T), the skill picker of an action-bar slot and the debug console (F1).

Skill tooltips are not pygame_gui tooltips: the game paints them (``client.tooltip``) from the same
core description used by the action bar, so both places always say the same thing.
"""

from __future__ import annotations

from html import escape
from importlib import resources
from typing import TYPE_CHECKING

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UILabel, UITextBox, UITextEntryLine, UIWindow

from ..core.progression import Allocate

if TYPE_CHECKING:
    from .app import Game

TREE_ORDER = ["blades", "shield", "armor"]
TREE_SIZE = (960, 540)
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
        self.tree: UIWindow | None = None
        self.picker: UIWindow | None = None
        self.picker_slot: int | None = None
        self.console: UIWindow | None = None
        self._console_out: UITextBox | None = None
        self._console_in: UITextEntryLine | None = None
        self._console_log: list[str] = ["Type <b>help</b> to list the commands."]
        self._node_buttons: dict[UIButton, str] = {}
        self._node_rows: list[tuple[tuple, str]] = []  # (elements of the row, node id) for mouse hover
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
        return any(self._open(w) for w in (self.tree, self.picker, self.console))

    @property
    def over_ui(self) -> bool:
        """Mouse over any window: the world ignores hover and clicks there."""
        return self.manager.get_hovering_any_element()

    def close_all(self) -> None:
        for w in (self.tree, self.picker, self.console):
            if self._open(w):
                w.kill()
        self.tree = self.picker = self.console = None
        self.picker_slot = None

    def hovered_node(self, pos: tuple[int, int]) -> str | None:
        """Skill-tree node under the mouse (its + button or its two labels), if the tree window is open."""
        if not self._open(self.tree) or not self.tree.rect.collidepoint(pos):
            return None
        for elements, node_id in self._node_rows:
            if any(e.rect.collidepoint(pos) for e in elements):
                return node_id
        return None

    # ------------------------------------------------------------ events

    def process(self, event: pygame.event.Event) -> bool:
        """Hands the event to pygame_gui. True if the interface consumed it."""
        consumed = self.manager.process_events(event)
        if event.type == pygame_gui.UI_BUTTON_PRESSED and event.ui_element in self._node_buttons:
            node = self._node_buttons[event.ui_element]
            r = self.g.session.allocate_skill(node)
            if r is not Allocate.OK:
                self.g.hud.add(f"{self.g.data.node(node).name}: {r.value}")
            self.refresh_tree()
            return True
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
            if event.ui_element is self.tree:
                self.tree = None
            elif event.ui_element is self.picker:
                self.picker, self.picker_slot = None, None
            elif event.ui_element is self.console:
                self.console = None
        return consumed

    def update(self, dt: float) -> None:
        self.manager.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        self.manager.draw_ui(screen)

    # ------------------------------------------------------------ skill trees

    def toggle_tree(self) -> None:
        if self._open(self.tree):
            self.tree.kill()
            self.tree = None
        else:
            self.refresh_tree()

    def refresh_tree_if_open(self) -> None:
        """After points or keys change elsewhere (console, action bar), so the window never shows stale data."""
        if self._open(self.tree):
            self.refresh_tree()

    def refresh_tree(self) -> None:
        """Builds the window again in the same place (pygame_gui labels are rebuilt rather than patched)."""
        if self._open(self.tree):
            pos = self.tree.rect.topleft
            self.tree.kill()
        else:
            W, H = self.manager.window_resolution
            pos = ((W - TREE_SIZE[0]) // 2, (H - TREE_SIZE[1]) // 2)
        self._node_buttons.clear()
        self._node_rows.clear()
        s, keys = self.g.session, self.g.keys
        title = f"Skill trees  ({keys.action_label('skillTrees')} closes · hover an active skill and press {keys.bar_labels} to put it on the bar)"
        win = UIWindow(pygame.Rect(pos, TREE_SIZE), self.manager, window_display_title=title, object_id="#tree_window")
        col_w = 310
        for i, tree in enumerate(TREE_ORDER):
            prof = s.proficiencies[tree]
            x = 10 + i * col_w
            UILabel(pygame.Rect(x, 6, col_w - 10, 26), f"{prof.name} {prof.level}  ·  {s.skills.available(tree)} point(s)",
                    self.manager, container=win, object_id="#tree_header")
            for j, node in enumerate(sorted(s.skills.nodes_of(tree), key=lambda n: (n.layer, n.kind != "active", n.name))):
                y = 40 + j * 56
                pts = s.skills.points_in(node.id)
                btn = UIButton(pygame.Rect(x, y + 4, 30, 30), "+", self.manager, container=win)
                if s.skills.can_allocate(node.id) is not Allocate.OK:
                    btn.disable()
                self._node_buttons[btn] = node.id
                locked = prof.level < node.layer
                slot = s.skill_bar.slot_of(node.id)
                line2 = f"level {node.layer} · {node.kind}" + (f" · cooldown {node.cooldown:g}s" if node.kind == "active" else "")
                if slot is not None:
                    line2 += f" · key {keys.slot_label(slot)}"
                a = UILabel(pygame.Rect(x + 36, y, col_w - 50, 22), f"{node.name}  {pts}/{node.max_points}", self.manager, container=win,
                            object_id="#node_locked" if locked else ("#node_on" if pts else "#node"))
                b = UILabel(pygame.Rect(x + 36, y + 20, col_w - 50, 20), line2, self.manager, container=win, object_id="#node_info")
                self._node_rows.append(((btn, a, b), node.id))
        self.tree = win

    # ------------------------------------------------------------ skill picker for one bar slot

    def open_picker(self, slot: int) -> None:
        """Lists the learned actives; choosing one puts it in ``slot`` (swapping if it was elsewhere)."""
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
            UILabel(pygame.Rect(8, y, PICKER_W - 48, 22), "No active skill learned yet.", self.manager, container=win, object_id="#node_info")
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
