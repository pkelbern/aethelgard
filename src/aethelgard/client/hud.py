"""HUD: life, Energy, proficiencies, action bar with countdowns, target frame and the message log."""

from __future__ import annotations

import math
from collections import deque

import pygame

from ..core.describe import stat_line
from ..core.world import IMPLEMENTED_SKILLS, MonsterActor, PlayerActor, World
from . import palette as C
from .keys import CONSUMABLE_KEYS, KeyMap
from .tooltip import draw_panel

STATE_LABEL = {
    "hit_recovery": "Recovering from hit",
    "block_recovery": "Recovering from block",
    "stunned": "Stunned",
    "attacking": "Attacking",
}
SLOT_SIZE, SLOT_GAP = 56, 8
TARGET_W = 420


class Hud:
    def __init__(self, fonts: dict[str, pygame.font.Font], keys: KeyMap):
        self.f = fonts
        self.keys = keys
        self.log: deque[tuple[str, tuple[int, int, int]]] = deque(maxlen=7)
        self.area_title = ""

    def add(self, text: str, color=C.TEXT) -> None:
        self.log.append((text, color))

    # ------------------------------------------------------------ layout (shared by drawing and the mouse)

    def slot_rects(self, slots: int, size: tuple[int, int]) -> list[pygame.Rect]:
        W, H = size
        total = slots * SLOT_SIZE + (slots - 1) * SLOT_GAP
        x0, y0 = W // 2 - total // 2, H - SLOT_SIZE - 20
        return [pygame.Rect(x0 + i * (SLOT_SIZE + SLOT_GAP), y0, SLOT_SIZE, SLOT_SIZE) for i in range(slots)]

    def slot_at(self, pos: tuple[int, int], slots: int, size: tuple[int, int]) -> int | None:
        return next((i for i, r in enumerate(self.slot_rects(slots, size)) if r.collidepoint(pos)), None)

    # ------------------------------------------------------------ drawing

    def draw(self, screen: pygame.Surface, world: World, p: PlayerActor, target: MonsterActor | None = None) -> None:
        s = p.session
        W, H = screen.get_size()
        self._text(screen, self.area_title, (16, 12), self.f["title"], C.TEXT)
        info = f"{s.name}  ·  {s.character.title}  ·  Level {s.character.level}  ·  Gold {s.gold}  ·  Backpack {len(s.inventory)}/{s.inventory.capacity}"
        self._text(screen, info, (16, 44), self.f["small"], C.MUTED)

        self._bar(screen, (16, 68, 260, 14), p.life / max(1, p.max_life), C.LIFE, f"Life {p.life:.0f}/{p.max_life:.0f}")
        self._bar(screen, (16, 86, 260, 14), p.energy / max(1, p.max_energy), C.ENERGY, f"Energy {p.energy:.0f}/{p.max_energy:.0f}")

        y = 110  # only the proficiencies in use; the others are summed up in one line (all of them are in the trees, T)
        unused = 0
        for prof in s.proficiencies:
            free = s.skills.available(prof.id)
            if prof.xp <= 0:
                unused += free
                continue
            label = f"{prof.name} {prof.level}" + (f"   [{free} point{'s' if free > 1 else ''}]" if free else "")
            self._text(screen, label, (16, y), self.f["small"], C.GOLD if free else C.TEXT)
            self._bar(screen, (16, y + 18, 200, 5), prof.progress, C.XP, None)
            y += 30
        if unused:
            self._text(screen, f"+{unused} points in unused trees", (16, y), self.f["tiny"], C.MUTED)

        # current state (hit recovery, block recovery, stunned) with time left
        if p.state in STATE_LABEL and p.state != "attacking":
            t = f"{STATE_LABEL[p.state]} {p.state_left:.2f}s"
            self._text(screen, t, (W // 2 - self.f["body"].size(t)[0] // 2, H - 150), self.f["body"], C.FLOAT["info"])
        if p.guarding:
            g = "Guard up" + ("  ·  Bulwark" if p.bulwark_left > 0 else "")
            self._text(screen, g, (W // 2 - self.f["small"].size(g)[0] // 2, H - 128), self.f["small"], C.GUARD)
        elif not p.cooldowns.ready("guard_raise"):
            g = f"Guard cooldown {p.cooldowns.remaining('guard_raise'):.1f}s"
            self._text(screen, g, (W // 2 - self.f["small"].size(g)[0] // 2, H - 128), self.f["small"], C.MUTED)

        self._skill_bar(screen, p)
        self._consumables(screen, p)
        if target is not None:
            self._target_frame(screen, target, world.data)

        ly = H - 24 - 18 * len(self.log)
        for text, color in self.log:
            self._text(screen, text, (16, ly), self.f["small"], color)
            ly += 18
        k = self.keys
        parts = [("guard", "guard"), ("skillTrees", "trees"), ("inventory", "inventory"), ("character", "character"),
                 ("showItems", "items"), ("map", "map"), ("save", "save")]
        potions = f"{k.action_label('lifePotion')}/{k.action_label('energyPotion')}: potions · {k.action_label('townPortal')}: town portal"
        help_ = f"Click: move/attack · {k.bar_labels}: skills · {potions} · " + " · ".join(f"{k.action_label(a)}: {label}" for a, label in parts)
        self._text(screen, help_, (W - self.f["tiny"].size(help_)[0] - 16, H - 20), self.f["tiny"], C.MUTED)

    def _consumables(self, screen, p: PlayerActor) -> None:
        """Town portal and potions left of the action bar: key, name, how many are left and the cooldown."""
        s = p.session
        first = self.slot_rects(len(s.skill_bar), screen.get_size())[0]
        x = first.x - 12
        entries = [("townPortal", "town_portal", "Portal", None)]  # always available: no count
        entries += [(a, cid, s.data.items.consumables[cid].name.split()[0], s.consumables[cid]) for a, cid in reversed(list(CONSUMABLE_KEYS.items()))]
        for action, cid, short, n in entries:
            r = pygame.Rect(0, first.y + 12, 70, 32)
            r.right = x
            pygame.draw.rect(screen, C.PANEL, r, border_radius=4)
            usable = n is None or n > 0
            pygame.draw.rect(screen, C.PANEL_EDGE if usable else (40, 40, 44), r, 1, border_radius=4)
            color = {"life_potion": C.LIFE, "energy_potion": C.ENERGY}.get(cid, (150, 190, 255)) if usable else C.MUTED
            self._text(screen, short if n is None else f"{short} ×{n}", (r.x + 6, r.y + 3), self.f["tiny"], color)
            self._text(screen, self.keys.action_label(action), (r.x + 6, r.y + 17), self.f["tiny"], C.MUTED)
            self._countdown(screen, r, p.cooldowns.fraction(cid), p.cooldowns.remaining(cid), "tiny")
            if cid == "town_portal" and p.portal_cast_left > 0:  # opening: the bar fills up
                k = 1 - p.portal_cast_left / max(1e-6, s.data.balance.town_portal.cast_seconds)
                pygame.draw.rect(screen, (90, 150, 255), (r.x + 2, r.bottom - 5, (r.w - 4) * k, 3))
            x = r.x - 6

    def _countdown(self, screen, r: pygame.Rect, frac: float, left: float, font: str) -> None:
        """Cooldown on a bar button (skills, potions, the portal): a dark sector that empties, and the seconds left."""
        if frac <= 0:
            return
        self._sweep(screen, r, frac)
        if left > 0:
            t = f"{left:.1f}" if left < 10 else f"{left:.0f}"
            img = self.f[font].render(t, True, C.TEXT)
            screen.blit(img, img.get_rect(midright=(r.right - 5, r.centery)) if font == "tiny" else img.get_rect(center=r.center))

    def _skill_bar(self, screen, p: PlayerActor) -> None:
        s = p.session
        bar = s.skill_bar
        mouse = pygame.mouse.get_pos()
        for i, r in enumerate(self.slot_rects(len(bar), screen.get_size())):
            skill = bar.skill_at(i)
            has = skill is not None and s.skills.points_in(skill) > 0
            hover = r.collidepoint(mouse)
            pygame.draw.rect(screen, C.PANEL, r, border_radius=4)
            edge = C.TEXT if hover else (C.GOLD if has else C.PANEL_EDGE)
            pygame.draw.rect(screen, edge, r, 2, border_radius=4)
            self._text(screen, self.keys.slot_label(i), (r.right - 14, r.bottom - 18), self.f["small"], C.MUTED)
            if skill is None:
                self._text(screen, "empty", (r.x + 5, r.y + 6), self.f["tiny"], C.PANEL_EDGE)
                continue
            node = s.data.node(skill)
            self._text(screen, node.short_name or node.name, (r.x + 5, r.y + 6), self.f["tiny"], C.TEXT if has else C.MUTED)
            if not has:
                continue
            if skill not in IMPLEMENTED_SKILLS:
                self._text(screen, "soon", (r.x + 5, r.y + 22), self.f["tiny"], C.MUTED)
            frac = max(p.cooldowns.fraction(skill), p.cooldowns.fraction("global"))
            self._countdown(screen, r, frac, p.cooldowns.remaining(skill), "body")
            if p.energy < node.energy_cost:
                pygame.draw.rect(screen, (40, 60, 140), r, 3, border_radius=4)

    def _target_frame(self, screen, m: MonsterActor, data) -> None:
        """Monster under the mouse (GDD 12.1): name in its rank color, life bar, level, rank, modifiers,
        and plainly what is special about it (leader aura, bonuses it is getting, no body)."""
        mi = m.monster
        color = C.rank_name_color(mi.rank.id)
        W = screen.get_width()
        details = f"Level {mi.level}" + (f"  ·  {mi.rank.name}" if mi.rank.id != "normal" else "")
        if mi.type.name not in mi.name:  # uniques and bosses have their own names: say what they are
            details += f"  ·  {mi.type.name}"
        extra: list[tuple[str, tuple]] = []
        if mi.modifiers:
            extra.append((", ".join(x.name for x in mi.modifiers), color))
        if m.aura is not None:
            gives = ", ".join(stat_line(data.stat(k), v) for k, v in m.aura.stats.items())
            extra.append((f"Leader: allies within {m.aura.radius_tiles:g} tiles get {gives}", C.GOLD))
        if m.buffs:
            extra.append(("Empowered: " + ", ".join(stat_line(data.stat(k), v) for k, v in m.buffs.items()), C.WARN))
        if not m.collides:  # explicit: this one has no body (monsters.json collides=false)
            extra.append(("Ethereal: passes through bodies", C.NEXT))
        h = 74 + 18 * len(extra)
        rect = pygame.Rect(W // 2 - TARGET_W // 2, 10, TARGET_W, h)
        draw_panel(screen, rect, edge=color)
        name = self.f["body"].render(mi.name, True, color)
        screen.blit(name, name.get_rect(midtop=(rect.centerx, rect.y + 8)))
        bar = pygame.Rect(rect.x + 16, rect.y + 32, TARGET_W - 32, 10)
        pygame.draw.rect(screen, (0, 0, 0), bar)
        pygame.draw.rect(screen, C.LIFE, (bar.x, bar.y, bar.w * max(0.0, mi.hp / mi.max_hp), bar.h))
        pygame.draw.rect(screen, C.PANEL_EDGE, bar, 1)
        info = self.f["tiny"].render(details, True, C.MUTED)
        screen.blit(info, info.get_rect(midtop=(rect.centerx, rect.y + 50)))
        for k, (text, col) in enumerate(extra):
            img = self.f["tiny"].render(text, True, col)
            screen.blit(img, img.get_rect(midtop=(rect.centerx, rect.y + 68 + 18 * k)))

    @staticmethod
    def _sweep(screen, r: pygame.Rect, frac: float) -> None:
        overlay = pygame.Surface(r.size, pygame.SRCALPHA)
        cx, cy = r.w / 2, r.h / 2
        rad = math.hypot(cx, cy) + 2
        pts = [(cx, cy)]
        steps = max(2, int(36 * frac))
        for k in range(steps + 1):
            a = -math.pi / 2 + 2 * math.pi * frac * k / steps
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        pygame.draw.polygon(overlay, C.COOLDOWN, pts)
        screen.blit(overlay, r.topleft)

    def _bar(self, screen, rect, frac: float, color, label: str | None) -> None:
        x, y, w, h = rect
        pygame.draw.rect(screen, (0, 0, 0), rect)
        pygame.draw.rect(screen, color, (x, y, max(0, min(1, frac)) * w, h))
        if label:
            self._text(screen, label, (x + 6, y - 1), self.f["tiny"], C.TEXT)

    @staticmethod
    def _text(screen, text: str, pos, font, color) -> None:
        screen.blit(font.render(text, True, color), pos)
