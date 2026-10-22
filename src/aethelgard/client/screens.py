"""Full-screen menus (M7-05, M7-08): the title screen, options and credits.

They share one base, :class:`Panel`: a centered box of buttons drawn over a dimmed screen. Each screen only
changes client things (settings, which save to play); the game itself starts through ``Game.start_character``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from ..core.saves import SaveStore, slot_name
from . import palette as C
from .keys import ACTIONS
from .settings import RESOLUTIONS
from .strings import languages
from .tooltip import draw_button, draw_panel, wrap

if TYPE_CHECKING:
    from .app import Game

BTN_H, GAP = 36, 10
KEY_COLS = 3  # key bindings in the options, side by side
ACTION_NAMES = {
    "guard": "Guard", "skillTrees": "Skill trees", "inventory": "Inventory", "character": "Character", "map": "Map",
    "console": "Console", "save": "Save", "showItems": "Show items", "compare": "Compare", "lifePotion": "Life potion",
    "energyPotion": "Energy potion", "townPortal": "Town portal", "standStill": "Attack in place", "fps": "FPS counter",
}
REBINDABLE = [a for a in ACTIONS if a in ACTION_NAMES]


class Panel:
    """A centered box with a title and buttons (id → rect), drawn over the dimmed screen."""

    title = ""
    width = 360

    def __init__(self, game: Game):
        self.g = game
        self.open = False
        self._buttons: dict[str, pygame.Rect] = {}
        self.focus: str | None = None  # button chosen with the arrows / d-pad

    def items(self) -> list[tuple[str, str, bool]]:
        """(id, label, enabled) of each button, top to bottom."""
        return []

    def height(self) -> int:
        return 80 + len(self.items()) * (BTN_H + GAP)

    @property
    def rect(self) -> pygame.Rect:
        W, H = self.g.screen.get_size()
        h = min(H - 20, self.height())
        return pygame.Rect((W - self.width) // 2, (H - h) // 2, self.width, h)

    def layout(self) -> None:
        r = self.rect
        self._buttons = {bid: pygame.Rect(r.x + 30, r.y + 60 + i * (BTN_H + GAP), r.w - 60, BTN_H)
                         for i, (bid, _, _) in enumerate(self.items())}

    def handle(self, ev: pygame.event.Event) -> bool:
        if not self.open:
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for bid in self.enabled():
                if self._buttons[bid].collidepoint(ev.pos):
                    self.press(bid)
                    break
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            self.back()
        elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_UP, pygame.K_DOWN):
            self.move_focus(-1 if ev.key == pygame.K_UP else 1)
        elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.press_focus()
        return ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.KEYDOWN, pygame.KEYUP)

    # ---- the keyboard's arrows and the gamepad's d-pad walk through the buttons

    def enabled(self) -> list[str]:
        """The buttons that can be pressed now, top to bottom."""
        self.layout()
        off = {bid for bid, _, ok in self.items() if not ok}
        return [bid for bid in self._buttons if bid not in off]

    def move_focus(self, step: int) -> None:
        ids = self.enabled()
        if ids:
            i = ids.index(self.focus) if self.focus in ids else (-1 if step > 0 else 0)
            self.focus = ids[(i + step) % len(ids)]

    def press_focus(self) -> None:
        if self.focus in self.enabled():
            self.press(self.focus)

    def press(self, bid: str) -> None: ...

    def back(self) -> None:
        self.open = False

    def draw(self, screen: pygame.Surface, dim: bool = True) -> None:
        if not self.open:
            return
        if dim:
            shade = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 140))
            screen.blit(shade, (0, 0))
        self.layout()
        r = self.rect
        draw_panel(screen, r, opaque=True)
        img = self.g.fonts["body"].render(self.title, True, C.GOLD)
        screen.blit(img, img.get_rect(midtop=(r.centerx, r.y + 18)))
        for bid, label, ok in self.items():
            draw_button(screen, self.g.fonts["small"], self._buttons[bid], label, ok)
        self.draw_extra(screen)
        if self.focus in self._buttons:
            pygame.draw.rect(screen, C.GOLD, self._buttons[self.focus].inflate(6, 6), 2, border_radius=5)

    def draw_extra(self, screen: pygame.Surface) -> None: ...


# ---------------------------------------------------------------- options


class OptionsScreen(Panel):
    """Volumes, window size, full screen and keys. Every change is saved at once (settings.json)."""

    title = "Options"
    width = 780

    def __init__(self, game: Game):
        super().__init__(game)
        self.waiting: tuple[str, int | None] | None = None  # (action, bar slot) waiting for a key

    def height(self) -> int:
        return 80 + len(self.items()) * (BTN_H + GAP) + -(-(len(REBINDABLE) + 6) // KEY_COLS) * 30 + BTN_H + GAP + 40

    def items(self) -> list[tuple[str, str, bool]]:
        st = self.g.settings
        w, h = st.resolution
        return [
            ("music", f"Music volume: {st.music_volume * 100:.0f}%   (click: +10%, right click: −10%)", True),
            ("sfx", f"Effects volume: {st.sfx_volume * 100:.0f}%   (click: +10%, right click: −10%)", True),
            ("size", f"Window: {w} × {h}   (click for the next size)", True),
            ("fullscreen", f"Full screen: {'on' if st.fullscreen else 'off'}", True),
            ("language", f"Language: {st.language}", len(languages()) > 1),
        ]

    def layout(self) -> None:
        super().layout()
        r = self.rect
        y0 = r.y + 60 + len(self.items()) * (BTN_H + GAP)
        col_w = (r.w - 60) // KEY_COLS
        rows = [("skillBar", i) for i in range(len(self.g.keys.slot_keys))] + [(a, None) for a in REBINDABLE]
        for i, (action, slot) in enumerate(rows):
            col, row = i % KEY_COLS, i // KEY_COLS
            self._buttons[f"key:{action}:{'' if slot is None else slot}"] = pygame.Rect(r.x + 30 + col * col_w, y0 + row * 30, col_w - 8, 26)
        bottom = y0 + -(-len(rows) // KEY_COLS) * 30 + 12
        self._buttons["reset_keys"] = pygame.Rect(r.x + 30, bottom, (r.w - 70) // 2, BTN_H)
        self._buttons["back"] = pygame.Rect(r.centerx + 5, bottom, (r.w - 70) // 2, BTN_H)

    def handle(self, ev: pygame.event.Event) -> bool:
        if not self.open:
            return False
        if self.waiting is not None and ev.type == pygame.KEYDOWN:
            action, slot = self.waiting
            self.waiting = None
            if ev.key != pygame.K_ESCAPE:
                self.g.settings.keys = self.g.keys.rebind(action, ev.key, slot)
                self.g.apply_settings()
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:  # right click lowers a volume
            self.layout()
            for bid in ("music", "sfx"):
                if self._buttons[bid].collidepoint(ev.pos):
                    self._volume(bid, -0.1)
            return True
        return super().handle(ev)

    def _volume(self, which: str, delta: float) -> None:
        st = self.g.settings
        if which == "music":
            st.music_volume = round(min(1.0, max(0.0, st.music_volume + delta)), 2)
        else:
            st.sfx_volume = round(min(1.0, max(0.0, st.sfx_volume + delta)), 2)
        self.g.apply_settings()

    def press(self, bid: str) -> None:
        st = self.g.settings
        if bid in ("music", "sfx"):
            cur = st.music_volume if bid == "music" else st.sfx_volume
            self._volume(bid, 0.1 if cur < 0.99 else -1.0)  # past 100% it wraps to silence
        elif bid == "size":
            i = RESOLUTIONS.index(tuple(st.resolution)) if tuple(st.resolution) in RESOLUTIONS else 0
            st.resolution = list(RESOLUTIONS[(i + 1) % len(RESOLUTIONS)])
            self.g.apply_settings(display=True)
        elif bid == "fullscreen":
            st.fullscreen = not st.fullscreen
            self.g.apply_settings(display=True)
        elif bid == "language":
            langs = languages()
            st.language = langs[(langs.index(st.language) + 1) % len(langs)] if st.language in langs else langs[0]
            self.g.apply_settings()
        elif bid == "reset_keys":
            st.keys = {}
            self.g.apply_settings()
        elif bid == "back":
            self.back()
        elif bid.startswith("key:"):
            _, action, slot = bid.split(":")
            self.waiting = (action, int(slot) if slot else None)

    def draw_extra(self, screen: pygame.Surface) -> None:
        f, k = self.g.fonts, self.g.keys
        for bid, r in self._buttons.items():
            if bid.startswith("key:"):
                _, action, slot = bid.split(":")
                name = f"Skill {int(slot) + 1}" if action == "skillBar" else ACTION_NAMES[action]
                key = k.slot_label(int(slot)) if action == "skillBar" else k.action_label(action)
                waiting = self.waiting == (action, int(slot) if slot else None)
                draw_button(screen, f["tiny"], r, f"{name}:  {'press a key… (Esc: cancel)' if waiting else key}")
        draw_button(screen, f["small"], self._buttons["reset_keys"], "Default keys")
        draw_button(screen, f["small"], self._buttons["back"], "Back")
        r = self.rect
        img = f["tiny"].render("Click a key to change it; a key already in use swaps places.", True, C.MUTED)
        screen.blit(img, img.get_rect(midtop=(r.centerx, r.y + 44 + len(self.items()) * (BTN_H + GAP))))


# ---------------------------------------------------------------- credits


class CreditsScreen(Panel):
    """Who made what (M7-08): the code, and every downloaded art and sound source with its license."""

    title = "Credits"
    width = 760

    def lines(self) -> list[tuple[str, str]]:
        out = [("Aethelgard", "title"), ("Code and design: Peter (pkelbern) · GPL-3.0 · github.com/pkelbern/aethelgard", "body"), ("", "body")]
        for src in self.g.sprites.lib.cfg["import"]["sources"].values():
            out.append((src.get("title", "Art"), "title"))
            out.append((f"{src.get('credit', '')}", "body"))
            out.append((f"{src.get('license', '')} · {src.get('page', src.get('url', ''))}", "muted"))
        out += [("Sounds, music and icons", "title"), ("From the Flare project (same source and license as the Flare art above).", "body"),
                ("", "body"), ("Libraries: pygame-ce, pygame_gui, numpy, python-tcod, pytmx, py7zr.", "muted"),
                ("Full list of files and authors: ATTRIBUTION.md in the art folder and docs/licencas_assets.csv.", "muted")]
        return out

    def height(self) -> int:
        return 120 + len(self.lines()) * 20

    def items(self) -> list[tuple[str, str, bool]]:
        return []

    def layout(self) -> None:
        r = self.rect
        self._buttons = {"back": pygame.Rect(r.centerx - 80, r.bottom - BTN_H - 14, 160, BTN_H)}

    def press(self, bid: str) -> None:
        self.back()

    def draw_extra(self, screen: pygame.Surface) -> None:
        f, r = self.g.fonts, self.rect
        y = r.y + 54
        for text, style in self.lines():
            font = f["small"] if style != "title" else f["body"]
            color = C.GOLD if style == "title" else C.MUTED if style == "muted" else C.TEXT
            img = font.render(text, True, color)
            screen.blit(img, img.get_rect(midtop=(r.centerx, y)))
            y += 22 if style == "title" else 19
        draw_button(screen, f["small"], self._buttons["back"], "Back")


# ---------------------------------------------------------------- title


class CharacterSelectScreen(Panel):
    """Every saved character (most recent first): pick one and play, make a new one, or delete one (M7-05).

    Opened from the title and from the game menu ("Change character": the one being played is saved first).
    A click picks, a second click (or Play, or Enter) plays; Delete asks for a second click."""

    title = "Characters"
    width = 520
    MAX_SHOWN = 10

    def __init__(self, game: Game):
        super().__init__(game)
        self.selected: str | None = None
        self.naming: str | None = None  # the new character's name while it is typed
        self.confirm_delete: str | None = None
        self._cache: list | None = None

    def open_list(self, naming: bool = False) -> None:
        self.open, self._cache, self.confirm_delete = True, None, None
        self.naming = "" if naming else None
        chars = self.characters()
        if self.selected not in {c.slot for c in chars}:
            self.selected = chars[0].slot if chars else None

    def characters(self):
        if self._cache is None:
            self._cache = SaveStore.summaries(self.g.data, self.g.store.directory)[: self.MAX_SHOWN]
        return self._cache

    def items(self) -> list[tuple[str, str, bool]]:
        if self.naming is not None:
            return [("create", "Start", True), ("cancel", "Back", True)]
        out = []
        for c in self.characters():
            out.append((f"pick:{c.slot}", f"{c.name}  ·  level {c.level} {c.title}  ·  {c.gold} gold", True))
        chosen = next((c for c in self.characters() if c.slot == self.selected), None)
        delete = f"Delete {chosen.name}? Click again" if chosen and self.confirm_delete == chosen.slot else "Delete"
        return out + [("play", f"Play {chosen.name}" if chosen else "Play", chosen is not None), ("new", "New character", True),
                      ("delete", delete, chosen is not None), ("back", "Back", True)]

    def height(self) -> int:
        return super().height() + (40 if self.naming is not None else 0)

    def layout(self) -> None:
        super().layout()
        if self.naming is not None:  # the name field above the buttons
            self._buttons = {k: r.move(0, 40) for k, r in self._buttons.items()}

    def handle(self, ev: pygame.event.Event) -> bool:
        if not self.open:
            return False
        if self.naming is not None and ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.press("create")
            elif ev.key == pygame.K_ESCAPE:
                self.naming = None
            elif ev.key == pygame.K_BACKSPACE:
                self.naming = self.naming[:-1]
            elif ev.unicode and ev.unicode.isprintable() and len(self.naming) < 20:
                self.naming += ev.unicode
            return True
        return super().handle(ev)

    def back(self) -> None:
        if self.naming is not None:
            self.naming = None
        else:
            self.open = False

    def press(self, bid: str) -> None:
        g = self.g
        if bid.startswith("pick:"):
            slot = bid.split(":", 1)[1]
            if slot == self.selected:  # a second click plays
                return self.press("play")
            self.selected, self.confirm_delete = slot, None
        elif bid == "play" and self.selected:
            self._play(self.selected)
        elif bid == "new":
            self.naming = ""
        elif bid == "create":
            name = self.naming.strip() or "Hero"  # (a gamepad has no letters)
            taken = {slot for slot, _, _ in SaveStore.characters(g.store.directory)}
            slot, n = slot_name(name), 2
            while slot in taken:  # never over another character's save
                slot, n = f"{slot_name(name)}_{n}", n + 1
            self._play(slot, new_name=name)
        elif bid == "cancel":
            self.naming = None
        elif bid == "delete" and self.selected:
            if self.confirm_delete != self.selected:
                self.confirm_delete = self.selected
                return
            if self.selected == g.store.slot and not g.title.open:
                g.hud.add(g.tr("characters.cannotDeletePlaying"), C.MUTED)
                return
            SaveStore(g.store.directory, self.selected).delete()
            self.selected, self.confirm_delete, self._cache = None, None, None
            self.open_list()
        elif bid == "back":
            self.back()

    def _play(self, slot: str, new_name: str | None = None) -> None:
        g = self.g
        if not g.title.open and (slot != g.store.slot or new_name is not None):
            g.save_game()  # leaving a character in the middle of a game: keep it as it is now
        g.start_character(slot, new_name=new_name)
        self.naming, self.open, g.title.open, g.menu.open = None, False, False, False

    def draw_extra(self, screen: pygame.Surface) -> None:
        f, r = self.g.fonts, self.rect
        chosen = self._buttons.get(f"pick:{self.selected}")
        if chosen is not None and self.naming is None:
            pygame.draw.rect(screen, C.GOLD, chosen, 2, border_radius=4)
        if not self.characters() and self.naming is None:
            img = f["small"].render("No characters yet: make one.", True, C.MUTED)
            screen.blit(img, img.get_rect(midtop=(r.centerx, r.y + 40)))
        if self.naming is not None:
            box = pygame.Rect(r.x + 30, r.y + 62, r.w - 60, 32)
            pygame.draw.rect(screen, (20, 20, 26), box, border_radius=4)
            pygame.draw.rect(screen, C.GOLD, box, 1, border_radius=4)
            caret = "|" if (pygame.time.get_ticks() // 500) % 2 else ""
            text = f["body"].render((self.naming or "") + caret, True, C.TEXT)
            screen.blit(text, (box.x + 8, box.y + 6))
            hint = f["tiny"].render("Name your character, then Enter (empty: Hero)", True, C.MUTED)
            screen.blit(hint, hint.get_rect(midbottom=(r.centerx, box.y - 4)))


class TitleScreen(Panel):
    """Continue with the last character, choose another, make a new one; options, credits, quit."""

    title = "Aethelgard"
    width = 420

    def items(self) -> list[tuple[str, str, bool]]:
        chars = SaveStore.characters(self.g.store.directory)
        first = [("continue", f"Continue: {chars[0][1]}", True)] if chars else []
        return first + [("characters", "Characters", bool(chars)), ("new", "New character", True), ("options", "Options", True),
                        ("credits", "Credits", True), ("quit", "Quit", True)]

    def back(self) -> None:
        pass  # the title has nothing behind it

    def press(self, bid: str) -> None:
        g = self.g
        if bid == "continue":
            chars = SaveStore.characters(g.store.directory)
            g.start_character(chars[0][0])
            self.open = False
        elif bid == "characters":
            g.select.open_list()
        elif bid == "new":
            g.select.open_list(naming=True)
        elif bid == "options":
            g.options.open = True
        elif bid == "credits":
            g.credits.open = True
        elif bid == "quit":
            g.running = False

    def draw_extra(self, screen: pygame.Surface) -> None:
        f, r = self.g.fonts, self.rect
        img = f["tiny"].render("an isometric action RPG", True, C.MUTED)
        screen.blit(img, img.get_rect(midtop=(r.centerx, r.y + 40)))


# ---------------------------------------------------------------- town people


class DialogueScreen(Panel):
    """Talking to a town NPC (npcs.json): what they say, and the topics you can ask about. Topics tied to a boss
    appear before or after it is beaten. Pauses the game like the other panels (towns hold no monsters)."""

    width = 640
    LINE_H = 20

    def __init__(self, game: Game):
        super().__init__(game)
        self.npc_id: str | None = None
        self.text = ""

    @property
    def npc(self):
        return self.g.data.npcs.npcs.get(self.npc_id or "")

    @property
    def title(self) -> str:  # noqa: D401 - the Panel heading
        return self.npc.name if self.npc else ""

    def talk(self, npc_id: str) -> None:
        self.npc_id, self.open, self.focus = npc_id, True, None
        self.text = self.npc.greeting

    def topics(self):
        return self.npc.topics_for(self.g.session.bosses_killed) if self.npc else []

    def items(self) -> list[tuple[str, str, bool]]:
        return [(f"topic:{i}", t.title, True) for i, t in enumerate(self.topics())] + [("bye", "Goodbye", True)]

    def lines(self) -> list[str]:
        return wrap(self.text, self.g.fonts["small"], self.width - 60)

    def height(self) -> int:
        return super().height() + len(self.lines()) * self.LINE_H + 10

    def layout(self) -> None:
        super().layout()
        shift = len(self.lines()) * self.LINE_H + 10
        self._buttons = {k: r.move(0, shift) for k, r in self._buttons.items()}

    def press(self, bid: str) -> None:
        if bid.startswith("topic:"):
            self.text = self.topics()[int(bid.split(":")[1])].text
        elif bid == "bye":
            self.open = False

    def draw_extra(self, screen: pygame.Surface) -> None:
        r, font = self.rect, self.g.fonts["small"]
        for i, line in enumerate(self.lines()):
            screen.blit(font.render(line, True, C.TEXT), (r.x + 30, r.y + 52 + i * self.LINE_H))
