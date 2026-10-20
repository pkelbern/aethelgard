"""pygame_gui windows: skill trees (T) and debug console (F1)."""

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
EFFECT_LABEL = {
    "damagePct": "damage",
    "accuracyPct": "accuracy",
    "swordDamagePct": "sword damage",
    "guardEfficiency": "guard efficiency",
    "fasterBlockRecovery": "faster block recovery",
    "fasterHitRecovery": "faster hit recovery",
    "armorPct": "armor",
    "lifeRegenPerSecond": "life/s",
    "stunSeconds": "stun (s)",
    "perfectWindowSeconds": "perfect window (s)",
    "counterDamagePct": "counterattack",
    "guardArcDegrees": "guard arc (°)",
    "shieldArmorPct": "shield armor",
}


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
        self.console: UIWindow | None = None
        self._console_out: UITextBox | None = None
        self._console_in: UITextEntryLine | None = None
        self._console_log: list[str] = ["Type <b>help</b> to list the commands."]
        self._node_buttons: dict[UIButton, str] = {}

    # ------------------------------------------------------------ state

    @property
    def typing(self) -> bool:
        return self._console_in is not None and self._console_in.alive() and self._console_in.is_focused

    @property
    def any_open(self) -> bool:
        return bool((self.tree and self.tree.alive()) or (self.console and self.console.alive()))

    # ------------------------------------------------------------ events

    def process(self, event: pygame.event.Event) -> bool:
        """Hands the event to pygame_gui. True if the interface consumed it."""
        consumed = self.manager.process_events(event)
        if event.type == pygame_gui.UI_BUTTON_PRESSED and event.ui_element in self._node_buttons:
            node = self._node_buttons[event.ui_element]
            r = self.g.session.skills.allocate(node)
            if r is not Allocate.OK:
                self.g.hud.add(f"{self.g.data.node(node).name}: {r.value}")
            self.refresh_tree()
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
            elif event.ui_element is self.console:
                self.console = None
        return consumed

    def update(self, dt: float) -> None:
        self.manager.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        self.manager.draw_ui(screen)

    # ------------------------------------------------------------ skill trees

    def toggle_tree(self) -> None:
        if self.tree and self.tree.alive():
            self.tree.kill()
            self.tree = None
        else:
            self.refresh_tree()

    def refresh_tree(self) -> None:
        if self.tree and self.tree.alive():
            pos = self.tree.rect.topleft
            self.tree.kill()
        else:
            W, H = self.manager.window_resolution
            pos = ((W - 960) // 2, (H - 520) // 2)
        self._node_buttons.clear()
        s = self.g.session
        win = UIWindow(pygame.Rect(pos, (960, 520)), self.manager, window_display_title="Skill trees  (T closes)",
                       object_id="#tree_window")
        col_w = 310
        for i, tree in enumerate(TREE_ORDER):
            prof = s.proficiencies[tree]
            x = 10 + i * col_w
            UILabel(pygame.Rect(x, 6, col_w - 10, 26), f"{prof.name} {prof.level}  ·  {s.skills.available(tree)} point(s)",
                    self.manager, container=win, object_id="#tree_header")
            for j, node in enumerate(sorted(s.skills.nodes_of(tree), key=lambda n: (n.layer, n.kind != "active", n.name))):
                y = 40 + j * 54
                pts = s.skills.points_in(node.id)
                btn = UIButton(pygame.Rect(x, y + 4, 30, 30), "+", self.manager, container=win,
                               tool_tip_text=self._tooltip(node))
                if s.skills.can_allocate(node.id) is not Allocate.OK:
                    btn.disable()
                self._node_buttons[btn] = node.id
                kind = "active" if node.kind == "active" else "passive"
                line1 = f"{node.name}  {pts}/{node.max_points}"
                locked = prof.level < node.layer
                line2 = f"level {node.layer} · {kind}" + (f" · cooldown {node.cooldown:g}s" if node.kind == "active" else "")
                UILabel(pygame.Rect(x + 36, y, col_w - 50, 22), line1, self.manager, container=win,
                        object_id="#node_locked" if locked else ("#node_on" if pts else "#node"))
                UILabel(pygame.Rect(x + 36, y + 20, col_w - 50, 20), line2, self.manager, container=win, object_id="#node_info")
        self.tree = win

    def _tooltip(self, node) -> str:
        s = self.g.session
        parts = []
        for stat, per_point in node.effects.items():
            label = EFFECT_LABEL.get(stat, stat)
            now = s.skills.effect(node.id, stat)
            parts.append(f"{label}: {per_point:g} per point (now {now:.2f})")
        syn = [f"+{x.per_point:g} {EFFECT_LABEL.get(x.stat, x.stat)} per point in {self.g.data.node(x.from_).name}"
               for x in self.g.data.skills.synergies if x.target == node.id]
        if syn:
            parts.append("Synergies: " + "; ".join(syn))
        if node.kind == "active":
            parts.append(f"Energy {node.energy_cost:g} · cooldown {node.cooldown:g}s")
        return "<br>".join(escape(p) for p in parts) or escape(node.name)

    # ------------------------------------------------------------ console

    def toggle_console(self) -> None:
        if self.console and self.console.alive():
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
