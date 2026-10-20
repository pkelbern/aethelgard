"""Panels and tooltips painted with pygame: one look for the skill bar, the skill trees and the target frame.

The content comes from the core as ``describe.Line`` (text + style); here we only pick font and
color per style, wrap long lines and keep the box inside the screen.
"""

from __future__ import annotations

import pygame

from ..core.describe import Line
from . import palette as C

STYLES = {
    "title": ("body", C.GOLD),
    "subtitle": ("tiny", C.MUTED),
    "body": ("small", C.TEXT),
    "header": ("small", C.GOLD),
    "stat": ("small", C.TEXT),
    "next": ("small", C.NEXT),
    "good": ("small", C.GOOD),
    "warn": ("small", C.WARN),
    "muted": ("tiny", C.MUTED),
    "hint": ("tiny", C.HINT),
}
SPACE_BEFORE = {"header": 6, "body": 4, "hint": 6}
PAD = 10
WIDTH = 360


def wrap(text: str, font: pygame.font.Font, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split(" "):
        trial = f"{line} {word}".strip() if line else word
        if font.size(trial)[0] <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    return [*out, line]


def draw_panel(screen: pygame.Surface, rect: pygame.Rect, edge=C.PANEL_EDGE) -> None:
    box = pygame.Surface(rect.size, pygame.SRCALPHA)
    box.fill(C.TOOLTIP_BG)
    screen.blit(box, rect.topleft)
    pygame.draw.rect(screen, edge, rect, 1, border_radius=3)


def draw_tooltip(screen: pygame.Surface, fonts: dict, lines: list[Line], anchor: tuple[int, int], width: int = WIDTH, edge=C.PANEL_EDGE) -> pygame.Rect:
    """Box with the lines next to ``anchor`` (usually the mouse), flipped and clamped to stay on screen."""
    inner = width - 2 * PAD
    rows: list[tuple[pygame.Surface, int]] = []
    height = PAD
    for i, ln in enumerate(lines):
        font_key, color = STYLES.get(ln.style, STYLES["body"])
        font = fonts[font_key]
        gap = (SPACE_BEFORE.get(ln.style, 0) or (4 if lines[i - 1].style == "body" else 0)) if i else 0
        for j, part in enumerate(wrap(ln.text, font, inner)):
            img = font.render(part, True, color)
            rows.append((img, gap if j == 0 else 0))
            height += img.get_height() + (gap if j == 0 else 0) + 1
    height += PAD - 1
    W, H = screen.get_size()
    x, y = anchor[0] + 18, anchor[1] - height - 8
    if x + width > W - 4:
        x = anchor[0] - width - 18
    rect = pygame.Rect(max(4, x), max(4, min(y, H - height - 4)), width, height)
    draw_panel(screen, rect, edge)
    cy = rect.y + PAD
    for img, gap in rows:
        cy += gap
        screen.blit(img, (rect.x + PAD, cy))
        cy += img.get_height() + 1
    return rect
