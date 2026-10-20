"""HUD: vida, Energia, proficiências, barra de habilidades com contagem regressiva e log."""

from __future__ import annotations

import math
from collections import deque

import pygame

from ..core.world import IMPLEMENTED_SKILLS, PlayerActor, World
from . import palette as C

SKILL_BAR = [("Q", "golpe_preciso"), ("W", "golpe_escudo"), ("E", "corte_amplo"), ("R", "muralha")]
SHORT = {"golpe_preciso": "Preciso", "golpe_escudo": "G. Esc.", "corte_amplo": "Corte", "muralha": "Muralha"}
STATE_LABEL = {
    "hit_recovery": "Recuperando do golpe",
    "block_recovery": "Recuperando do bloqueio",
    "stunned": "Atordoado",
    "attacking": "Atacando",
}


class Hud:
    def __init__(self, fonts: dict[str, pygame.font.Font]):
        self.f = fonts
        self.log: deque[tuple[str, tuple[int, int, int]]] = deque(maxlen=7)
        self.area_title = ""

    def add(self, text: str, color=C.TEXT) -> None:
        self.log.append((text, color))

    # ------------------------------------------------------------ desenho

    def draw(self, screen: pygame.Surface, world: World, p: PlayerActor) -> None:
        s = p.session
        W, H = screen.get_size()
        self._text(screen, self.area_title, (16, 12), self.f["title"], C.TEXT)
        self._text(screen, f"{s.character.title}  ·  Nível {s.character.level}  ·  Ouro {s.gold}", (16, 44), self.f["small"], C.MUTED)

        self._bar(screen, (16, 68, 260, 14), p.life / max(1, p.max_life), C.LIFE, f"Vida {p.life:.0f}/{p.max_life:.0f}")
        self._bar(screen, (16, 86, 260, 14), p.energy / max(1, p.max_energy), C.ENERGY, f"Energia {p.energy:.0f}/{p.max_energy:.0f}")

        y = 110
        for prof in s.proficiencies:
            free = s.skills.available(prof.id)
            label = f"{prof.name} {prof.level}" + (f"   [{free} ponto(s)]" if free else "")
            self._text(screen, label, (16, y), self.f["small"], C.GOLD if free else C.TEXT)
            self._bar(screen, (16, y + 18, 200, 5), prof.progress, C.XP, None)
            y += 30

        # estado atual (recuperação, bloqueio, atordoado) com tempo restante
        if p.state in STATE_LABEL and p.state != "attacking":
            t = f"{STATE_LABEL[p.state]} {p.state_left:.2f}s"
            self._text(screen, t, (W // 2 - self.f["body"].size(t)[0] // 2, H - 150), self.f["body"], C.FLOAT["info"])
        if p.guarding:
            g = "Guarda erguida" + ("  ·  Muralha" if p.muralha_left > 0 else "")
            self._text(screen, g, (W // 2 - self.f["small"].size(g)[0] // 2, H - 128), self.f["small"], C.GUARD)
        elif not p.cooldowns.ready("guard_raise"):
            g = f"Guarda em recarga {p.cooldowns.remaining('guard_raise'):.1f}s"
            self._text(screen, g, (W // 2 - self.f["small"].size(g)[0] // 2, H - 128), self.f["small"], C.MUTED)

        self._skill_bar(screen, p, W, H)

        ly = H - 24 - 18 * len(self.log)
        for text, color in self.log:
            self._text(screen, text, (16, ly), self.f["small"], color)
            ly += 18
        help_ = "Clique: andar/atacar · Espaço: guarda · Q W E R: habilidades · T: árvores · Tab: mapa · F1: console"
        self._text(screen, help_, (W - self.f["tiny"].size(help_)[0] - 16, H - 20), self.f["tiny"], C.MUTED)

    def _skill_bar(self, screen, p: PlayerActor, W: int, H: int) -> None:
        s = p.session
        size, gap = 56, 8
        total = len(SKILL_BAR) * size + (len(SKILL_BAR) - 1) * gap
        x0, y0 = W // 2 - total // 2, H - size - 20
        for i, (key, skill) in enumerate(SKILL_BAR):
            node = s.data.node(skill)
            r = pygame.Rect(x0 + i * (size + gap), y0, size, size)
            has = s.skills.points_in(skill) > 0
            pygame.draw.rect(screen, C.PANEL, r, border_radius=4)
            pygame.draw.rect(screen, C.GOLD if has else C.PANEL_EDGE, r, 2, border_radius=4)
            name = SHORT.get(skill, node.name) if has else "—"
            self._text(screen, name, (r.x + 5, r.y + 6), self.f["tiny"], C.TEXT if has else C.MUTED)
            self._text(screen, key, (r.right - 14, r.bottom - 18), self.f["small"], C.MUTED)
            if has and skill not in IMPLEMENTED_SKILLS:
                self._text(screen, "em breve", (r.x + 5, r.y + 22), self.f["tiny"], C.MUTED)
            # contagem regressiva: setor escuro que esvazia + segundos restantes
            frac = max(p.cooldowns.fraction(skill), p.cooldowns.fraction("global") if has else 0)
            if has and frac > 0:
                self._sweep(screen, r, frac)
                left = p.cooldowns.remaining(skill)
                if left > 0:
                    t = f"{left:.1f}" if left < 10 else f"{left:.0f}"
                    img = self.f["body"].render(t, True, C.TEXT)
                    screen.blit(img, img.get_rect(center=r.center))
            if has and p.energy < node.energy_cost:
                pygame.draw.rect(screen, (40, 60, 140), r, 3, border_radius=4)

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
