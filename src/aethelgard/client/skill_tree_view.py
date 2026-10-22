"""Skill trees window (T): one tab per proficiency, actives on the left and passives on the right, one row per
layer, lines from each skill to the ones it requires. Click a node to spend a point; hover it for the tooltip
(the same ``core.describe`` text as the action bar) and press a bar key to put an active on that key.

It reads the session every frame, so it never shows stale numbers (XP by use, a wand put on, the console).
The layout (``tree_layout``) is plain arithmetic with no pygame, so it is tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

from ..core.progression import Allocate
from ..core.world import ForgetTree
from . import palette as C
from .tooltip import draw_button, draw_panel

if TYPE_CHECKING:
    from .app import Game

NODE_W, NODE_H = 184, 58
ICON = 34  # an active skill's icon, left of its name
GAP_X, ROW_GAP = 18, 30
LABEL_W = 64  # "Level 12" at the left of each row
PAD = 16
TAB_H = 34
TABS_PER_ROW = 6  # more proficiencies wrap to a second row of tabs
HEAD_H = 86  # one row of tabs + the line under them + the column titles
MAX_W = 940

LOCKED = (90, 88, 82)
AVAILABLE = (120, 210, 140)
LINK_OFF = (70, 68, 62)


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int = NODE_W
    h: int = NODE_H

    @property
    def top(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y

    @property
    def bottom(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h


def tree_layout(nodes, width: int) -> tuple[dict[str, Box], list[int]]:
    """Positions inside the tree area (0, 0 = its top-left): actives in the left half, passives in the right,
    one row per layer. In a row, nodes sit under the node they require, so the lines stay short and straight.
    Returns the boxes and the y of each row (for its label)."""
    layers = sorted({n.layer for n in nodes})
    half = (width - LABEL_W) // 2
    boxes: dict[str, Box] = {}
    rows = [i * (NODE_H + ROW_GAP) for i in range(len(layers))]
    for side, kind in enumerate(("active", "passive")):
        left = LABEL_W + side * half
        for row, layer in enumerate(layers):
            here = [n for n in nodes if n.kind == kind and n.layer == layer]

            here.sort(key=lambda n, left=left: (min([boxes[r].x for r in n.requires if r in boxes], default=left), n.name))
            total = len(here) * NODE_W + (len(here) - 1) * GAP_X
            x = left + max(0, (half - total) // 2)
            for n in here:
                boxes[n.id] = Box(x, rows[row])
                x += NODE_W + GAP_X
    return boxes, rows


def fit(font: pygame.font.Font, text: str, width: int) -> str:
    """The text, cut with an ellipsis if it is wider than ``width``."""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "…")[0] > width:
        text = text[:-1]
    return text.rstrip(" ·") + "…"


class SkillTreeWindow:
    def __init__(self, game: Game):
        self.g = game
        self.open = False
        self.tab = game.data.proficiencies.proficiencies[0].id
        self._tabs: list[tuple[pygame.Rect, str]] = []
        self._nodes: list[tuple[pygame.Rect, str]] = []
        self._forget: pygame.Rect | None = None  # "Forget this tree" (with a Scroll of Forgetting)
        self._pressed: str | None = None  # node under the last press

    # ---- window protocol (same as the item windows)

    def toggle(self) -> None:
        self.open = not self.open
        if self.open:
            self.tab = self._best_tab()

    def _best_tab(self) -> str:
        """Opens on the tree with points to spend (the highest level first), else where it was."""
        s = self.g.session
        free = [p for p in s.proficiencies if s.skills.available(p.id) > 0]
        return max(free, key=lambda p: p.level).id if free else self.tab

    @property
    def rect(self) -> pygame.Rect:
        W, H = self.g.screen.get_size()
        layers = len({n.layer for n in self.g.data.skills.nodes})
        h = self.head_h + layers * (NODE_H + ROW_GAP) + 40
        w = min(W - 32, MAX_W)
        return pygame.Rect((W - w) // 2, max(8, (H - 92 - h) // 2), w, h)

    @property
    def tab_rows(self) -> int:
        return -(-len(self.g.data.proficiencies.proficiencies) // TABS_PER_ROW)

    @property
    def head_h(self) -> int:
        return HEAD_H + (self.tab_rows - 1) * TAB_H

    def contains(self, pos) -> bool:
        return self.open and self.rect.collidepoint(pos)

    def node_at(self, pos) -> str | None:
        if not self.open:
            return None
        return next((node_id for box, node_id in self._nodes if box.collidepoint(pos)), None)

    def handle(self, ev: pygame.event.Event) -> bool:
        if not self.open or ev.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL):
            return False
        if ev.type == pygame.MOUSEWHEEL:  # the wheel over the window flips through the trees
            if not self.rect.collidepoint(pygame.mouse.get_pos()):
                return False
            ids = [t for _, t in self._tabs] or [self.tab]
            self.tab = ids[(ids.index(self.tab) - ev.y) % len(ids)]
            return True
        if not self.rect.collidepoint(ev.pos):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for box, tree in self._tabs:
                if box.collidepoint(ev.pos):
                    self.tab = tree
                    return True
            if self._forget is not None and self._forget.collidepoint(ev.pos):
                msg = self.g.world.apply(ForgetTree(self.g.session.player_id, self.tab))
                self.g.hud.add(msg or self.g.tr("tree.forgotten", tree=self.g.data.proficiency(self.tab).name), C.MUTED if msg else C.GOLD)
                return True
            self._pressed = self.node_at(ev.pos)  # a point is spent when the button comes up (a drag spends none)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            node_id, pressed, self._pressed = self.node_at(ev.pos), self._pressed, None
            if node_id is not None and node_id == pressed:
                r = self.g.session.allocate_skill(node_id)
                if r not in (Allocate.OK, Allocate.BY_USE):
                    self.g.hud.add(self.g.tr("skill.cannot", name=self.g.data.node(node_id).name, reason=r.value), C.MUTED)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:  # right click: the right-click skill
            node_id = self.node_at(ev.pos)
            if node_id is not None and self.g.data.node(node_id).kind == "active":
                self.g.set_right_skill(node_id)
        return True

    # ---- drawing

    def draw(self, screen: pygame.Surface) -> None:
        if not self.open:
            return
        r = self.rect
        draw_panel(screen, r, opaque=True)
        self._draw_tabs(screen, r)
        s, f = self.g.session, self.g.fonts
        prof = s.proficiencies[self.tab]
        free = s.skills.available(self.tab)
        info = f"{prof.name} level {prof.level}  ·  {free} free point{'s' if free != 1 else ''}"
        info_img = f["body"].render(info, True, C.GOLD)
        screen.blit(info_img, (r.x + PAD, r.y + self.tab_rows * TAB_H + 10))
        scrolls = s.consumables["respec_scroll"]
        self._forget = None
        if scrolls > 0 and s.skills.spent(self.tab) > 0:  # a Scroll of Forgetting gives this tree's points back
            self._forget = pygame.Rect(r.x + PAD + info_img.get_width() + 16, r.y + self.tab_rows * TAB_H + 8, 210, 24)
            draw_button(screen, f["tiny"], self._forget, f"Forget this tree (Scroll ×{scrolls})")
        keys = self.g.keys
        hint = (f"click: spend a point  ·  drag an active to the bar (or hover + {keys.bar_labels})  ·  "
                f"right-click: right-click skill  ·  {keys.action_label('skillTrees')} closes")
        img = f["tiny"].render(hint, True, C.MUTED)
        screen.blit(img, (r.right - PAD - img.get_width(), r.y + self.tab_rows * TAB_H + 14))

        area = pygame.Rect(r.x + PAD, r.y + self.head_h, r.w - 2 * PAD, r.h - self.head_h - 12)
        nodes = s.skills.nodes_of(self.tab)
        boxes, rows = tree_layout(nodes, area.w)
        half = (area.w - LABEL_W) // 2
        for col, title in enumerate(("Active skills  ·  1 point to unlock, then they grow by use", "Passives  ·  1 point per level")):
            img = f["small"].render(title, True, C.HINT)
            screen.blit(img, (area.x + LABEL_W + col * half + (half - img.get_width()) // 2, area.y - 20))
        pygame.draw.line(screen, C.PANEL_EDGE, (area.x + LABEL_W + half - GAP_X // 2, area.y - 4),
                         (area.x + LABEL_W + half - GAP_X // 2, area.bottom - 4))
        layers = sorted({n.layer for n in nodes})
        for layer, y in zip(layers, rows, strict=True):
            color = C.TEXT if prof.level >= layer else LOCKED
            screen.blit(f["small"].render(f"Level {layer}", True, color), (area.x, area.y + y + NODE_H // 2 - 7))

        def at(b: Box) -> pygame.Rect:
            return pygame.Rect(area.x + b.x, area.y + b.y, b.w, b.h)

        for n in nodes:  # lines under the boxes
            for req in n.requires:
                if req in boxes:
                    a, b = at(boxes[req]).midbottom, at(boxes[n.id]).midtop
                    on = s.skills.taken(req)
                    mid = (a[1] + b[1]) // 2
                    pts = [a, (a[0], mid), (b[0], mid), b]
                    pygame.draw.lines(screen, C.GOLD if on else LINK_OFF, False, pts, 2 if on else 1)
        self._nodes = []
        hovered = pygame.mouse.get_pos()
        for n in nodes:
            box = at(boxes[n.id])
            self._draw_node(screen, box, n, box.collidepoint(hovered))
            self._nodes.append((box, n.id))

    def _draw_tabs(self, screen: pygame.Surface, r: pygame.Rect) -> None:
        s, f = self.g.session, self.g.fonts
        profs = list(s.proficiencies)
        per_row = -(-len(profs) // self.tab_rows)
        w = (r.w - 2 * PAD) // per_row
        self._tabs = []
        for i, p in enumerate(profs):
            row, col = divmod(i, per_row)
            box = pygame.Rect(r.x + PAD + col * w, r.y + 6 + row * TAB_H, w - 4, TAB_H - 6)
            active = p.id == self.tab
            hot = box.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(screen, (46, 42, 30) if active else (34, 35, 44) if hot else C.PANEL, box, border_radius=4)
            pygame.draw.rect(screen, C.GOLD if active else C.PANEL_EDGE, box, 1, border_radius=4)
            free = s.skills.available(p.id)
            room = box.w - 12
            if free:  # a badge: points waiting in this tree (the name keeps clear of it)
                badge = f["tiny"].render(str(free), True, C.BG)
                dot = pygame.Rect(0, 0, max(16, badge.get_width() + 8), 16)
                dot.midright = (box.right - 4, box.centery)
                pygame.draw.rect(screen, AVAILABLE, dot, border_radius=8)
                screen.blit(badge, badge.get_rect(center=dot.center))
                room -= dot.w + 4
            text = fit(f["small"], f"{p.name} {p.level}", room)
            label = f["small"].render(text, True, C.GOLD if active else C.TEXT)
            screen.blit(label, label.get_rect(center=(box.x + 6 + room // 2, box.centery)))
            self._tabs.append((box, p.id))

    def _draw_node(self, screen: pygame.Surface, box: pygame.Rect, n, hot: bool) -> None:
        s, f = self.g.session, self.g.fonts
        t = s.skills
        r = t.can_allocate(n.id)
        level = t.points_in(n.id)
        taken = t.taken(n.id)
        locked = r in (Allocate.LAYER_LOCKED, Allocate.REQUIRES) and level == 0
        edge = C.GOLD if taken else AVAILABLE if r is Allocate.OK else LOCKED if locked else C.PANEL_EDGE
        pygame.draw.rect(screen, (40, 38, 30) if taken else (30, 31, 38) if hot else C.PANEL, box, border_radius=5)
        pygame.draw.rect(screen, C.TEXT if hot else edge, box, 2 if taken or r is Allocate.OK else 1, border_radius=5)
        name_color = LOCKED if locked else C.GOLD if taken else C.TEXT
        slot = s.skill_bar.slot_of(n.id)
        icon = self.g.icons.skill(n.id, ICON) if n.kind == "active" else None
        tx = box.x + 8
        if icon is not None:
            icon.set_alpha(110 if locked else 255)
            screen.blit(icon, (box.x + 6, box.y + 6))
            icon.set_alpha(255)
            tx += ICON + 4
        screen.blit(f["small"].render(fit(f["small"], n.name, box.right - tx - (32 if slot is not None else 8)), True, name_color), (tx, box.y + 6))
        if slot is not None:
            key = f["tiny"].render(self.g.keys.slot_label(slot), True, C.BG)
            chip = pygame.Rect(0, 0, key.get_width() + 8, 15)
            chip.topright = (box.right - 6, box.y + 6)
            pygame.draw.rect(screen, C.GOLD, chip, border_radius=3)
            screen.blit(key, key.get_rect(center=chip.center))
        status, color = self._status(n, r, level)
        screen.blit(f["tiny"].render(fit(f["tiny"], status, box.right - tx - 8), True, color), (tx, box.y + 25))
        bar = pygame.Rect(box.x + 8, box.bottom - 12, box.w - 16, 5)
        if n.kind == "active":
            if n.id in t.unlocked:  # XP by use toward the next level
                pygame.draw.rect(screen, (40, 40, 48), bar)
                fill = bar.copy()
                fill.w = round(bar.w * t.xp_progress(n.id))
                pygame.draw.rect(screen, LOCKED if t.gate_holding(n.id) else C.XP, fill)
        else:  # one pip per point
            pips = n.max_points
            step = bar.w / pips
            for i in range(pips):
                pip = pygame.Rect(round(bar.x + i * step), bar.y, max(2, round(step) - 2), bar.h)
                pygame.draw.rect(screen, C.GOLD if i < t.points.get(n.id, 0) else (40, 40, 48), pip)

    def _status(self, n, r: Allocate, level: int) -> tuple[str, tuple]:
        t, name = self.g.session.skills, self.g.data.proficiency(n.tree).name
        if n.kind == "active":
            wand = t.granted(n.id)
            plus = f" (+{wand} wand)" if wand else ""
            if n.id in t.unlocked:
                held = t.gate_holding(n.id)
                if held is not None:
                    return f"Level {t.own_level(n.id)}{plus} · needs {self.g.data.node(held.node).name} {held.points}", C.WARN
                return f"Level {t.own_level(n.id)}{plus} · {t.xp_progress(n.id):.0%} to next", C.TEXT
            if r is Allocate.OK:
                return f"Click to unlock{plus}", AVAILABLE
        else:
            if r is Allocate.OK:
                return f"{level}/{n.max_points} · click for +1", AVAILABLE
            if level:
                return f"{level}/{n.max_points}" + ("  · maxed" if r is Allocate.MAXED_OUT else ""), C.TEXT
        if r is Allocate.LAYER_LOCKED:
            return f"Needs {name} level {n.layer}", LOCKED
        if r is Allocate.REQUIRES:
            return "Needs " + ", ".join(self.g.data.node(x).name for x in t.missing_requirements(n.id)), LOCKED
        if r is Allocate.NO_POINTS:
            return f"No free {name} points" + (f" · +{level} wand" if level else ""), C.MUTED
        return r.value, C.MUTED
