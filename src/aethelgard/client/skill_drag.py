"""Dragging skills onto the action bar (M3-03): from the skill trees, or from one bar slot to another.

A press on a learned active (in the trees) or on a bar slot only *arms* a drag; moving the mouse a few pixels
with the button held makes it a drag. Let go over a bar slot to put it there (two bar slots swap), over the
right-click slot to make it the right-click skill, or anywhere else to take a bar skill off the bar. A press
that never moved is a plain click: the trees spend a point, a bar slot opens the skill picker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from . import palette as C
from .tooltip import draw_panel

if TYPE_CHECKING:
    from .app import Game

DRAG_PIXELS = 6
RIGHT = -1  # the right-click slot, as a "slot number"


class SkillDrag:
    def __init__(self, game: Game):
        self.g = game
        self.armed: tuple[str, int | None, tuple[int, int]] | None = None  # (skill, bar slot or None = trees, where pressed)
        self.active = False

    @property
    def skill(self) -> str | None:
        return self.armed[0] if self.armed and self.active else None

    def slot_at(self, pos) -> int | None:
        """A bar slot, RIGHT for the right-click slot, or None."""
        g = self.g
        size, n = g.screen.get_size(), len(g.session.skill_bar)
        if g.hud.right_rect(n, size).collidepoint(pos):
            return RIGHT
        return g.hud.slot_at(pos, n, size)

    def handle(self, ev: pygame.event.Event) -> bool:
        """True when the event was used here (the bar's own clicks and every drop)."""
        g = self.g
        bar = g.session.skill_bar
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            slot = self.slot_at(ev.pos)
            if slot is not None:
                skill = bar.right if slot == RIGHT else bar.skill_at(slot)
                self.armed, self.active = (skill or "", slot, ev.pos), False
                return True
            node_id = g.tree_view.node_at(ev.pos)
            if node_id is not None and g.data.node(node_id).kind == "active" and g.session.skills.points_in(node_id) > 0:
                self.armed, self.active = (node_id, None, ev.pos), False  # the trees still see the press
            return False
        if ev.type == pygame.MOUSEMOTION and self.armed and not self.active and self.armed[0]:
            x0, y0 = self.armed[2]
            if abs(ev.pos[0] - x0) + abs(ev.pos[1] - y0) >= DRAG_PIXELS:
                self.active = True
            return False
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.armed:
            skill, source, _ = self.armed
            was_drag, self.armed, self.active = self.active, None, False
            if was_drag:
                self.drop(skill, source, self.slot_at(ev.pos))
                return True
            if source is not None:  # a click on the bar: choose a skill for that key
                if source == RIGHT:
                    g.hud.add(g.tr("bar.rightHint"), C.MUTED)
                else:
                    g.ui.open_picker(source)
                return True
        return False

    def drop(self, skill: str, source: int | None, target: int | None) -> None:
        g, bar = self.g, self.g.session.skill_bar
        if target == RIGHT:
            g.set_right_skill(skill)
        elif target is not None and source is not None and source != RIGHT:
            bar.swap(source, target)
        elif target is not None:
            g.bind_slot(target, skill)
        elif source == RIGHT:
            g.set_right_skill(None)
        elif source is not None:  # dragged off the bar
            g.bind_slot(source, None)

    def draw(self, screen: pygame.Surface) -> None:
        if not self.skill:
            return
        node = self.g.data.node(self.skill)
        x, y = pygame.mouse.get_pos()
        icon = self.g.icons.skill(self.skill, 44)
        if icon is not None:
            screen.blit(icon, icon.get_rect(center=(x, y)))
            return
        img = self.g.fonts["small"].render(node.short_name or node.name, True, C.TEXT)
        r = img.get_rect(center=(x, y - 4)).inflate(14, 10)
        draw_panel(screen, r, edge=C.GOLD, opaque=True)
        screen.blit(img, img.get_rect(center=r.center))
