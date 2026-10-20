"""Inventory screen (I): equipment on the left, backpack grid on the right (GDD 12.2).

Equip by dragging a backpack item onto its slot, or with a double-click / Ctrl+click; the same on a
worn item takes it off. Holding Shift over a backpack item compares it with the worn one. Every
change is an ``EquipItem``/``UnequipItem`` command to the world, so the rules (requirements, swaps,
a full backpack) live in the core. The tooltip text comes from ``describe``.

For now each item takes one cell; items spanning several cells (``size`` in items.json) are M6-03.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from ..core.describe import Line, compare_items, describe_item
from ..core.world import EquipItem, UnequipItem
from . import palette as C
from .tooltip import draw_panel, draw_tooltip, wrap

if TYPE_CHECKING:
    from .app import Game

CELL = 48
SLOT = 56
PAD = 14
GEAR_W = 3 * SLOT + 4 * 12
DOUBLE_CLICK_MS = 350
# where each equipment slot sits in the gear column: (column, row) in a 3 × 4 layout
GEAR_LAYOUT = {
    "head": (1, 0), "neck": (2, 0),
    "mainhand": (0, 1), "body": (1, 1), "offhand": (2, 1),
    "hands": (0, 2), "waist": (1, 2), "ring": (2, 2),
    "feet": (1, 3),
}


class InventoryView:
    def __init__(self, game: Game):
        self.g = game
        self.open = False
        self.drag: tuple[str, object] | None = None  # ("bag", index) or ("gear", slot)
        self._last_click: tuple[tuple, int] | None = None

    # ------------------------------------------------------------ layout

    @property
    def cols(self) -> int:
        return self.g.data.items.inventory.columns

    @property
    def rows(self) -> int:
        return self.g.data.items.inventory.rows

    @property
    def rect(self) -> pygame.Rect:
        W, _ = self.g.screen.get_size()
        w = PAD * 3 + GEAR_W + self.cols * CELL
        h = 46 + max(4 * (SLOT + 12), self.rows * CELL) + PAD
        return pygame.Rect(W - w - 16, 70, w, h)

    def gear_rect(self, slot: str) -> pygame.Rect:
        c, r = GEAR_LAYOUT[slot]
        x0, y0 = self.rect.x + PAD, self.rect.y + 46
        return pygame.Rect(x0 + 12 + c * (SLOT + 12), y0 + r * (SLOT + 12), SLOT, SLOT)

    def grid_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x + PAD * 2 + GEAR_W, self.rect.y + 46, self.cols * CELL, self.rows * CELL)

    def cell_rect(self, index: int) -> pygame.Rect:
        g = self.grid_rect()
        return pygame.Rect(g.x + (index % self.cols) * CELL, g.y + (index // self.cols) * CELL, CELL, CELL)

    def contains(self, pos) -> bool:
        return self.open and self.rect.collidepoint(pos)

    def hit(self, pos) -> tuple[str, object] | None:
        """What is under the mouse: a backpack item, an equipment slot (worn or empty), or the grid."""
        for slot in GEAR_LAYOUT:
            if self.gear_rect(slot).collidepoint(pos):
                return ("gear", slot)
        items = self.g.session.inventory.items
        for i in range(len(items)):
            if self.cell_rect(i).collidepoint(pos):
                return ("bag", i)
        if self.grid_rect().collidepoint(pos):
            return ("grid", None)
        return None

    # ------------------------------------------------------------ input

    def toggle(self) -> None:
        self.open = not self.open
        self.drag = None

    def handle(self, ev: pygame.event.Event) -> bool:
        """True when the event was meant for the inventory (the world must not see it)."""
        if not self.open:
            return False
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.drag is not None:
            self._drop(self.hit(ev.pos))
            return True
        if ev.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) or not self.rect.collidepoint(ev.pos):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            h = self.hit(ev.pos)
            if h is None or h[0] == "grid" or (h[0] == "gear" and self.g.session.gear.get(h[1]) is None):
                return True
            now = pygame.time.get_ticks()
            double = self._last_click is not None and self._last_click[0] == h and now - self._last_click[1] <= DOUBLE_CLICK_MS
            self._last_click = (h, now)
            if double or pygame.key.get_mods() & pygame.KMOD_CTRL:
                self._quick(h)
                self._last_click = None
            else:
                self.drag = h
        return True

    def _quick(self, h) -> None:
        """Double-click or Ctrl+click: wear a backpack item, take off a worn one."""
        self.drag = None
        if h[0] == "bag":
            self._send(EquipItem(self.g.session.player_id, h[1]))
        elif h[0] == "gear":
            self._send(UnequipItem(self.g.session.player_id, h[1]))

    def _drop(self, target) -> None:
        src, self.drag = self.drag, None
        if target is None or target == src:
            return
        s = self.g.session
        if src[0] == "bag" and target[0] == "gear":
            item = s.inventory.items[src[1]]
            if item.slot != target[1]:
                names = self.g.data.items.slot_names
                self.g.hud.add(f"{item.name} goes in the {names.get(item.slot, item.slot)} slot, not {names.get(target[1], target[1])}.", C.MUTED)
                return
            self._send(EquipItem(s.player_id, src[1]))
        elif src[0] == "gear" and target[0] in ("bag", "grid"):
            self._send(UnequipItem(s.player_id, src[1]))

    def _send(self, cmd) -> None:
        msg = self.g.world.apply(cmd)
        if msg:
            self.g.hud.add(msg, C.WARN)

    # ------------------------------------------------------------ drawing

    def draw(self, screen: pygame.Surface) -> None:
        if not self.open:
            return
        f, s = self.g.fonts, self.g.session
        r = self.rect
        draw_panel(screen, r)
        keys = self.g.keys
        title = f"Inventory  ·  {len(s.inventory)}/{s.inventory.capacity}  ·  Gold {s.gold}"
        screen.blit(f["body"].render(title, True, C.GOLD), (r.x + PAD, r.y + 12))
        hint = f"drag to equip · double-click or Ctrl+click · {keys.action_label('inventory')} closes"
        img = f["tiny"].render(hint, True, C.MUTED)
        screen.blit(img, (r.right - img.get_width() - PAD, r.y + 18))

        names = self.g.data.items.slot_names
        for slot in GEAR_LAYOUT:
            box = self.gear_rect(slot)
            pygame.draw.rect(screen, C.PANEL, box)
            item = s.gear.get(slot)
            dragging = self.drag == ("gear", slot)
            if item is not None and not dragging:
                self._item_box(screen, item, box)
            else:
                pygame.draw.rect(screen, C.PANEL_EDGE, box, 1)
                lab = f["tiny"].render(names.get(slot, slot), True, C.PANEL_EDGE)
                screen.blit(lab, lab.get_rect(center=box.center))

        grid = self.grid_rect()
        for i in range(self.cols * self.rows):
            pygame.draw.rect(screen, (24, 25, 31), self.cell_rect(i))
            pygame.draw.rect(screen, (34, 35, 42), self.cell_rect(i), 1)
        for i, item in enumerate(s.inventory.items):
            if self.drag != ("bag", i):
                self._item_box(screen, item, self.cell_rect(i).inflate(-4, -4))
        pygame.draw.rect(screen, C.PANEL_EDGE, grid, 1)

        mouse = pygame.mouse.get_pos()
        if self.drag is not None:  # the item follows the mouse; valid slots light up
            item = self._item(self.drag)
            if item is not None:
                if self.drag[0] == "bag":
                    pygame.draw.rect(screen, C.GOOD, self.gear_rect(item.slot), 2)
                self._item_box(screen, item, pygame.Rect(mouse[0] - CELL // 2, mouse[1] - CELL // 2, CELL - 4, CELL - 4))
            return
        h = self.hit(mouse)
        item = self._item(h) if h else None
        if item is None:
            return
        d = self.g.data
        lines = describe_item(d, item, s.missing_requirements(item))
        worn = s.gear.get(item.slot)
        comparing = h[0] == "bag" and self.g.keys.held("compare")
        if comparing:
            lines += compare_items(d, item, worn)
        elif h[0] == "bag":
            lines.append(Line(f"Hold {self.g.keys.action_label('compare')} to compare", "hint"))
        box = draw_tooltip(screen, f, lines, mouse, edge=C.RARITY[int(item.rarity)])
        if comparing and worn is not None:  # the worn one beside it, for a side-by-side read
            worn_lines = [Line("Equipped", "hint"), *describe_item(d, worn, s.missing_requirements(worn))]
            draw_tooltip(screen, f, worn_lines, (box.x, box.y), edge=C.RARITY[int(worn.rarity)], right_edge=box.x, top=box.y)

    def _item(self, h):
        s = self.g.session
        if h[0] == "bag":
            return s.inventory.items[h[1]] if h[1] < len(s.inventory.items) else None
        if h[0] == "gear":
            return s.gear.get(h[1])
        return None

    def _item_box(self, screen, item, box: pygame.Rect) -> None:
        color = C.RARITY[int(item.rarity)]
        usable = not self.g.session.missing_requirements(item)
        pygame.draw.rect(screen, (34, 30, 30) if not usable else (30, 32, 40), box)
        pygame.draw.rect(screen, color if usable else C.LIFE, box, 2)
        font = self.g.fonts["tiny"]
        lines = wrap(item.base.name, font, box.w - 6)[:3]
        y = box.centery - len(lines) * font.get_height() // 2
        for line in lines:
            img = font.render(line, True, color)
            screen.blit(img, img.get_rect(midtop=(box.centerx, y)))
            y += font.get_height()
