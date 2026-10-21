"""Esc with nothing open shows the game menu (paused) instead of closing the game."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    from aethelgard.client.app import Game

    g = Game(seed=3, new_game=True, download_art=False)
    yield g
    pygame.quit()


def press(g, key):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="", scancode=0))
    g.tick()


def test_escape_opens_the_menu_and_pauses(game):
    g = game
    g.load_area("plains")
    press(g, pygame.K_ESCAPE)
    assert g.menu.open and g.running, "Esc no longer quits"
    t = g.world.time
    g.tick()
    assert g.world.time == t, "paused while the menu is open"
    press(g, pygame.K_ESCAPE)
    assert not g.menu.open
    g.tick()
    assert g.world.time > t


def test_escape_closes_windows_first(game):
    g = game
    g.open_only(g.inventory_view)
    press(g, pygame.K_ESCAPE)
    assert not g.inventory_view.open and not g.menu.open


def test_menu_buttons(game):
    g = game
    g.menu.open = True
    g.menu.press("save")
    assert g.store.exists()
    g.menu.press("resume")
    assert not g.menu.open
    g.menu.press("quit")
    assert not g.running
