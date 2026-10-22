"""Item windows: the inventory (I), the shared stash and the merchant (GDD 10.5, 12.2).

All three share one base: item grids, dragging an item from one place to another, a quick action
(double-click, Ctrl+click or Shift+click) and the item tooltip, which compares with the worn item
while Shift is held. Each window only says what a drop
or a quick action means, as a command to the world, so the rules (requirements, gold, room, being
next to the NPC) stay in the core.

For now each item takes one cell; items spanning several cells (``size`` in items.json) are M6-03.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from ..core.describe import Line, compare_items, describe_item
from ..core.items import ItemContainer, SharedStash
from ..core.town import item_price, sell_price
from ..core.world import Buy, BuyConsumable, EquipItem, MoveItem, Sell, StashGold, StashPut, StashTake, UnequipItem
from . import palette as C
from .tooltip import draw_button, draw_panel, draw_tooltip, wrap

if TYPE_CHECKING:
    from .app import Game

CELL = 48
SLOT = 56
PAD = 14
HEADER = 46
DOUBLE_CLICK_MS = 350
GEAR_W = 3 * SLOT + 4 * 12
# where each equipment slot sits in the gear column: (column, row) in a 3 × 4 layout
GEAR_LAYOUT = {
    "head": (1, 0), "neck": (2, 0),
    "mainhand": (0, 1), "body": (1, 1), "offhand": (2, 1),
    "hands": (0, 2), "waist": (1, 2), "ring1": (2, 2),
    "ring2": (0, 3), "feet": (1, 3),
}
Hit = tuple  # ("grid", name, index) · ("gear", slot) · ("button", id)
EMPTY_STASH = SharedStash()  # drawn while the real one is not loaded


class ItemGrid:
    """A core ``ItemContainer`` on screen from ``origin``: every item covers its size in cells."""

    def __init__(self, name: str, container):
        self.name, self._container = name, container
        self.origin = (0, 0)

    @property
    def container(self):
        return self._container()

    @property
    def cols(self) -> int:
        return self.container.cols

    @property
    def rows(self) -> int:
        return self.container.rows

    @property
    def items(self) -> list:
        return self.container.items

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(self.origin, (self.cols * CELL, self.rows * CELL))

    def square(self, cx: int, cy: int) -> pygame.Rect:
        x, y = self.origin
        return pygame.Rect(x + cx * CELL, y + cy * CELL, CELL, CELL)

    def cell(self, i: int) -> pygame.Rect:
        """The area item ``i`` covers."""
        c = self.container
        (x, y), (w, h) = c.pos[i], c.size_of(c.items[i])
        return pygame.Rect(self.origin[0] + x * CELL, self.origin[1] + y * CELL, w * CELL, h * CELL)

    def cell_at(self, pos) -> tuple[int, int] | None:
        if not self.rect.collidepoint(pos):
            return None
        return ((pos[0] - self.origin[0]) // CELL, (pos[1] - self.origin[1]) // CELL)

    def hit(self, pos) -> int | None:
        """Index of the item under ``pos``; -1 on an empty part of the grid; None outside."""
        c = self.cell_at(pos)
        if c is None:
            return None
        i = self.container.item_at(*c)
        return -1 if i is None else i

    def drop_spot(self, pos, grab: tuple[int, int]) -> tuple[int, int] | None:
        """Top-left cell for an item let go at ``pos``, held by its ``grab`` cell."""
        c = self.cell_at(pos)
        return None if c is None else (c[0] - grab[0], c[1] - grab[1])


class ItemWindow:
    """Base: a panel with grids (and maybe more), drag and drop, quick actions and tooltips."""

    title = ""
    quick_hint = ""

    def __init__(self, game: Game):
        self.g = game
        self.open = False
        self.drag: Hit | None = None
        self.grab = (0, 0)  # which cell of the dragged item the mouse holds
        self.drop_at: tuple[int, int] | None = None  # top-left cell where the dragged item was let go
        self._last_click: tuple[Hit, int] | None = None
        self.grids: dict[str, ItemGrid] = {}

    # ---- layout, overridden by each window

    @property
    def rect(self) -> pygame.Rect:
        raise NotImplementedError

    def layout(self) -> None:
        """Places the grids inside ``rect`` (called before drawing and hit-testing)."""

    def extra_hit(self, pos) -> Hit | None:
        return None

    # ---- what things mean, overridden by each window

    def on_quick(self, h: Hit) -> None: ...

    def on_drop(self, src: Hit, dst: Hit | None) -> None: ...

    def moved_within(self, src: Hit, dst: Hit | None) -> bool:
        """An item let go on its own grid (backpack or stash) moves there. True when that is what happened."""
        if src[0] != "grid" or dst is None or dst[0] != "grid" or dst[1] != src[1] or self.drop_at is None:
            return False
        if src[1] in ("bag", "stash"):
            self.send(MoveItem(self.g.session.player_id, src[1], src[2], *self.drop_at))
        return True

    def tooltip_extra(self, h: Hit, item) -> list[Line]:
        return []

    def draw_extra(self, screen) -> None: ...

    def press_button(self, button_id: str) -> None: ...

    # ---- shared behaviour

    def toggle(self) -> None:
        self.open = not self.open
        self.drag = None

    def contains(self, pos) -> bool:
        return self.open and self.rect.collidepoint(pos)

    def hit(self, pos) -> Hit | None:
        self.layout()
        h = self.extra_hit(pos)
        if h is not None:
            return h
        for g in self.grids.values():
            i = g.hit(pos)
            if i is not None:
                return ("grid", g.name, i)
        return None

    def item_at(self, h: Hit | None):
        if h is None:
            return None
        if h[0] == "grid":
            items = self.grids[h[1]].items
            return items[h[2]] if 0 <= h[2] < len(items) else None
        if h[0] == "gear":
            return self.g.session.gear.get(h[1])
        return None

    def send(self, cmd) -> None:
        msg = self.g.world.apply(cmd)
        if msg:
            self.g.hud.add(msg, C.WARN)

    def quick_pressed(self) -> bool:
        """Ctrl or Shift held with the click (double-click is detected separately)."""
        return bool(pygame.key.get_mods() & (pygame.KMOD_CTRL | pygame.KMOD_SHIFT))

    def handle(self, ev: pygame.event.Event) -> bool:
        """True when the event was meant for this window (the world must not see it)."""
        if not self.open:
            return False
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.drag is not None:
            src, self.drag = self.drag, None
            dst = self.hit(ev.pos)
            g = self.grids.get(dst[1]) if dst is not None and dst[0] == "grid" else None
            self.drop_at = g.drop_spot(ev.pos, self.grab) if g is not None else None
            if dst != src or self.drop_at is not None:
                self.on_drop(src, dst)
            return True
        if ev.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) or not self.rect.collidepoint(ev.pos):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            h = self.hit(ev.pos)
            if h is not None and h[0] == "button":
                self.press_button(h[1])
                return True
            if self.item_at(h) is None:
                return True
            now = pygame.time.get_ticks()
            double = self._last_click is not None and self._last_click[0] == h and now - self._last_click[1] <= DOUBLE_CLICK_MS
            self._last_click = (h, now)
            if double or self.quick_pressed():
                self._last_click = None
                self.on_quick(h)
            else:
                self.drag = h
                if h[0] == "grid":  # remember where the item was grabbed, so it lands the way it is held
                    g, (cx, cy) = self.grids[h[1]], self.grids[h[1]].cell_at(ev.pos)
                    x0, y0 = g.container.pos[h[2]]
                    self.grab = (cx - x0, cy - y0)
                else:
                    self.grab = (0, 0)
        return True

    def draw(self, screen: pygame.Surface) -> None:
        if not self.open:
            return
        self.layout()
        f = self.g.fonts
        r = self.rect
        draw_panel(screen, r)
        screen.blit(f["body"].render(self.title, True, C.GOLD), (r.x + PAD, r.y + 12))
        img = f["tiny"].render(self.quick_hint, True, C.MUTED)
        screen.blit(img, (r.right - img.get_width() - PAD, r.y + 18))
        mouse = pygame.mouse.get_pos()
        for g in self.grids.values():
            for cy in range(g.rows):
                for cx in range(g.cols):
                    pygame.draw.rect(screen, (24, 25, 31), g.square(cx, cy))
                    pygame.draw.rect(screen, (34, 35, 42), g.square(cx, cy), 1)
            if self.drag is not None and self.drag[0] == "grid":  # where the dragged item would land
                item = self.item_at(self.drag)
                spot = g.drop_spot(mouse, self.grab)
                if item is not None and spot is not None:
                    ignore = self.drag[2] if self.drag[1] == g.name else None
                    w, h = g.container.size_of(item)
                    ok = g.container.fits_at(item, *spot, ignore=ignore)
                    area = pygame.Rect(g.origin[0] + spot[0] * CELL, g.origin[1] + spot[1] * CELL, w * CELL, h * CELL).clip(g.rect)
                    pygame.draw.rect(screen, (40, 70, 40) if ok else (80, 36, 36), area)
            for i, item in enumerate(g.items):
                if self.drag != ("grid", g.name, i):
                    self.item_box(screen, item, g.cell(i).inflate(-4, -4))
            pygame.draw.rect(screen, C.PANEL_EDGE, g.rect, 1)
        self.draw_extra(screen)
        if self.drag is not None:  # the item follows the mouse, held where it was grabbed
            item = self.item_at(self.drag)
            if item is not None:
                w, h = ItemContainer.size_of(item)
                box = pygame.Rect(mouse[0] - self.grab[0] * CELL - CELL // 2, mouse[1] - self.grab[1] * CELL - CELL // 2, w * CELL - 4, h * CELL - 4)
                self.item_box(screen, item, box)
            return
        h = self.hit(mouse)
        item = self.item_at(h)
        if item is not None:
            s = self.g.session
            lines = describe_item(self.g.data, item, s.missing_requirements(item), s.gear) + self.tooltip_extra(h, item)
            self.draw_item_tooltip(screen, lines, item, mouse, h)

    # ---- compare (hold Shift over an item in a grid: backpack, stash or merchant)

    def worn_for(self, item):
        s = self.g.session
        return next((s.gear.get(k) for k in s.gear.native_slots(item) if s.gear.get(k)), None)

    def comparing(self, h: Hit) -> bool:
        return h[0] == "grid" and self.g.keys.held("compare")

    def compare_lines(self, h: Hit, item) -> list[Line]:
        if h[0] != "grid":
            return []
        if self.comparing(h):
            return compare_items(self.g.data, item, self.worn_for(item))
        return [Line(f"Hold {self.g.keys.action_label('compare')} to compare", "hint")]

    def draw_item_tooltip(self, screen, lines, item, mouse, h) -> pygame.Rect:
        box = draw_tooltip(screen, self.g.fonts, lines, mouse, edge=C.RARITY[int(item.rarity)])
        worn = self.worn_for(item) if self.comparing(h) else None
        if worn is not None:  # the worn one beside it, for a side-by-side read
            s = self.g.session
            worn_lines = [Line("Equipped", "hint"), *describe_item(self.g.data, worn, s.missing_requirements(worn), s.gear)]
            draw_tooltip(screen, self.g.fonts, worn_lines, (box.x, box.y), edge=C.RARITY[int(worn.rarity)], right_edge=box.x, top=box.y)
        return box

    def item_box(self, screen, item, box: pygame.Rect) -> None:
        color = C.RARITY[int(item.rarity)]
        usable = not self.g.session.missing_requirements(item)
        pygame.draw.rect(screen, (34, 30, 30) if not usable else (30, 32, 40), box)
        pygame.draw.rect(screen, color if usable else C.LIFE, box, 2)
        icon = self.g.icons.item(item.base.id, max(8, min(box.w, box.h) - 8))
        if icon is not None:
            screen.blit(icon, icon.get_rect(center=box.center))
            return
        font = self.g.fonts["tiny"]
        lines = wrap(item.base.name, font, box.w - 6)[:3]
        y = box.centery - len(lines) * font.get_height() // 2
        for line in lines:
            img = font.render(line, True, color)
            screen.blit(img, img.get_rect(midtop=(box.centerx, y)))
            y += font.get_height()

    def button(self, screen, rect: pygame.Rect, text: str, enabled: bool = True) -> None:
        draw_button(screen, self.g.fonts["tiny"], rect, text, enabled)


class BagMixin:
    """The backpack grid, as the right-hand side of a window."""

    def bag_grid(self) -> ItemGrid:
        return ItemGrid("bag", lambda: self.g.session.inventory)


# ---------------------------------------------------------------- inventory


class InventoryWindow(BagMixin, ItemWindow):
    """Equipment on the left, backpack on the right. Drag to equip, double-click or Ctrl+click to
    wear or take off, hold Shift over a backpack item to compare it with the worn one."""

    def __init__(self, game: Game):
        super().__init__(game)
        self.grids = {"bag": self.bag_grid()}

    @property
    def title(self) -> str:
        s = self.g.session
        return f"Inventory  ·  {s.inventory.used_cells}/{s.inventory.capacity} cells  ·  Gold {s.gold}"

    @property
    def quick_hint(self) -> str:
        k = self.g.keys
        return f"drag to equip · double-click or Ctrl+click · hold {k.action_label('compare')} to compare · {k.action_label('inventory')} closes"

    def quick_pressed(self) -> bool:
        return bool(pygame.key.get_mods() & pygame.KMOD_CTRL)  # Shift here means "compare"

    @property
    def rect(self) -> pygame.Rect:
        W, _ = self.g.screen.get_size()
        g = self.grids["bag"]
        w = PAD * 3 + GEAR_W + g.cols * CELL
        h = HEADER + max(4 * (SLOT + 12), g.rows * CELL) + PAD
        return pygame.Rect(W - w - 16, 70, w, h)

    def layout(self) -> None:
        self.grids["bag"].origin = (self.rect.x + PAD * 2 + GEAR_W, self.rect.y + HEADER)

    def gear_rect(self, slot: str) -> pygame.Rect:
        c, r = GEAR_LAYOUT[slot]
        x0, y0 = self.rect.x + PAD, self.rect.y + HEADER
        return pygame.Rect(x0 + 12 + c * (SLOT + 12), y0 + r * (SLOT + 12), SLOT, SLOT)

    def cell_rect(self, index: int) -> pygame.Rect:
        self.layout()
        return self.grids["bag"].cell(index)

    def extra_hit(self, pos) -> Hit | None:
        return next((("gear", slot) for slot in GEAR_LAYOUT if self.gear_rect(slot).collidepoint(pos)), None)

    def on_quick(self, h: Hit) -> None:
        pid = self.g.session.player_id
        if h[0] == "grid":
            self.send(EquipItem(pid, h[2]))
        elif h[0] == "gear":
            self.send(UnequipItem(pid, h[1]))

    def on_drop(self, src: Hit, dst: Hit | None) -> None:
        pid = self.g.session.player_id
        if self.moved_within(src, dst):
            return
        if src[0] == "grid" and dst is not None and dst[0] == "gear":
            self.send(EquipItem(pid, src[2], dst[1]))  # the core says when it does not fit
        elif src[0] == "gear" and dst is not None and dst[0] == "grid":
            self.send(UnequipItem(pid, src[1]))

    def draw_extra(self, screen) -> None:
        s = self.g.session
        names = {k: v.name for k, v in self.g.data.items.equipment.items()}
        for slot in GEAR_LAYOUT:
            box = self.gear_rect(slot)
            pygame.draw.rect(screen, C.PANEL, box)
            item = s.gear.get(slot)
            if item is not None and self.drag != ("gear", slot):
                self.item_box(screen, item, box)
            else:
                pygame.draw.rect(screen, C.PANEL_EDGE, box, 1)
                lab = self.g.fonts["tiny"].render(names.get(slot, slot), True, C.PANEL_EDGE)
                screen.blit(lab, lab.get_rect(center=box.center))
        dragged = self.item_at(self.drag) if self.drag and self.drag[0] == "grid" else None
        if dragged is not None:  # where it fits lights up
            for slot in s.gear.slots_for(dragged):
                pygame.draw.rect(screen, C.GOOD, self.gear_rect(slot), 2)

    def tooltip_extra(self, h: Hit, item) -> list[Line]:
        return self.compare_lines(h, item)


# ---------------------------------------------------------------- two-sided windows: stash and merchant


class TwoSided(BagMixin, ItemWindow):
    """Left grid (stash or merchant) and the backpack on the right."""

    left_rows = 6

    def __init__(self, game: Game, left: ItemGrid):
        super().__init__(game)
        self.grids = {left.name: left, "bag": self.bag_grid()}
        self.left = left

    @property
    def rect(self) -> pygame.Rect:
        W, _ = self.g.screen.get_size()
        lg, bg = self.left, self.grids["bag"]
        w = PAD * 3 + lg.cols * CELL + bg.cols * CELL
        h = HEADER + max(lg.rows, bg.rows) * CELL + 60
        return pygame.Rect((W - w) // 2, 64, w, h)

    def layout(self) -> None:
        r = self.rect
        self.left.origin = (r.x + PAD, r.y + HEADER)
        self.grids["bag"].origin = (r.x + PAD * 2 + self.left.cols * CELL, r.y + HEADER)

    def footer(self) -> pygame.Rect:
        r = self.rect
        return pygame.Rect(r.x + PAD, r.bottom - 46, r.w - 2 * PAD, 34)


class StashWindow(TwoSided):
    """Shared stash (every character sees the same one). Drag either way, or Shift/Ctrl/double-click."""

    def __init__(self, game: Game):
        super().__init__(game, ItemGrid("stash", lambda: game.world.stash or EMPTY_STASH))

    @property
    def title(self) -> str:
        st, s = self.g.world.stash, self.g.session
        st = st or EMPTY_STASH
        return f"Shared Stash  ·  {st.used_cells}/{st.capacity} cells  ·  gold {st.gold}   |   Backpack · gold {s.gold}"

    quick_hint = "drag, Shift+click or double-click to move · hold Shift to compare · Esc closes"

    def _buttons(self) -> dict[str, pygame.Rect]:
        f = self.footer()
        return {"deposit": pygame.Rect(f.x, f.y, 180, f.h), "withdraw": pygame.Rect(f.x + 190, f.y, 180, f.h)}

    def extra_hit(self, pos) -> Hit | None:
        return next((("button", k) for k, r in self._buttons().items() if r.collidepoint(pos)), None)

    def press_button(self, button_id: str) -> None:
        pid, s, st = self.g.session.player_id, self.g.session, self.g.world.stash
        self.send(StashGold(pid, s.gold if button_id == "deposit" else -(st.gold if st else 0)))

    def draw_extra(self, screen) -> None:
        b = self._buttons()
        st, s = self.g.world.stash, self.g.session
        self.button(screen, b["deposit"], f"Put all gold in ({s.gold})", s.gold > 0)
        self.button(screen, b["withdraw"], f"Take all gold ({st.gold if st else 0})", bool(st and st.gold))

    def on_quick(self, h: Hit, at: tuple[int, int] | None = None) -> None:
        pid = self.g.session.player_id
        self.send(StashPut(pid, h[2], at) if h[1] == "bag" else StashTake(pid, h[2], at))

    def on_drop(self, src: Hit, dst: Hit | None) -> None:
        if self.moved_within(src, dst):
            return
        if dst is not None and dst[0] == "grid" and dst[1] != src[1]:
            self.on_quick(src, self.drop_at)  # where it was let go

    def tooltip_extra(self, h: Hit, item) -> list[Line]:
        return self.compare_lines(h, item)


class ShopWindow(TwoSided):
    """Merchant: buy by dragging into the backpack or Shift+click; sell the same way the other way.
    Potions and scrolls are always in stock (click to buy one)."""

    def __init__(self, game: Game):
        super().__init__(game, ItemGrid("shop", lambda: game.world.vendor.stock))

    @property
    def title(self) -> str:
        return f"Merchant   |   Backpack  ·  gold {self.g.session.gold}"

    quick_hint = "drag or Shift+click to buy and sell · hold Shift to compare · Esc closes"

    def _consumable_buttons(self) -> dict[str, pygame.Rect]:
        f = self.footer()
        w = (f.w - 20) // 3
        sold = [cid for cid, c in self.g.data.items.consumables.items() if c.price > 0]
        return {cid: pygame.Rect(f.x + i * (w + 10), f.y, w, f.h) for i, cid in enumerate(sold)}

    def extra_hit(self, pos) -> Hit | None:
        return next((("button", k) for k, r in self._consumable_buttons().items() if r.collidepoint(pos)), None)

    def press_button(self, button_id: str) -> None:
        self.send(BuyConsumable(self.g.session.player_id, button_id))

    def draw_extra(self, screen) -> None:
        gold = self.g.session.gold
        for cid, r in self._consumable_buttons().items():
            c = self.g.data.items.consumables[cid]
            self.button(screen, r, f"{c.name}  ·  {c.price} gold  ·  have {self.g.session.consumables[cid]}", gold >= c.price)

    def on_quick(self, h: Hit) -> None:
        pid = self.g.session.player_id
        self.send(Buy(pid, h[2]) if h[1] == "shop" else Sell(pid, h[2]))

    def on_drop(self, src: Hit, dst: Hit | None) -> None:
        if self.moved_within(src, dst):
            return
        if dst is not None and dst[0] == "grid" and dst[1] != src[1]:
            self.on_quick(src)

    def tooltip_extra(self, h: Hit, item) -> list[Line]:
        d = self.g.data
        if h[1] == "shop":
            price = item_price(d, item)
            trade = Line(f"Buy: {price} gold", "good" if self.g.session.gold >= price else "warn")
        else:
            trade = Line(f"Sell: {sell_price(d, item)} gold", "good")
        return [trade, *self.compare_lines(h, item)]
