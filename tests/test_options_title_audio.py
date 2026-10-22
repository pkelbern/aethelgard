"""Options, title screen, credits, sounds (M7-04, M7-05, M7-08, M1-05)."""

import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from aethelgard.client.keys import KeyMap  # noqa: E402
from aethelgard.client.settings import Settings  # noqa: E402
from aethelgard.core.items import SharedStash  # noqa: E402
from aethelgard.core.saves import SaveStore  # noqa: E402
from aethelgard.core.session import GameSession  # noqa: E402
from aethelgard.tools.fetch_art import load_config  # noqa: E402
from aethelgard.tools.fetch_sounds import fetch_sounds, load_sound_config, missing_sounds, sounds_dir  # noqa: E402


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    from aethelgard.client.app import Game

    g = Game(seed=3, new_game=True, download_art=False, art_dir=tmp_path / "art", title=True)
    yield g
    pygame.quit()


def click(g, rect, button=1):
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=rect.center))
    g.tick()


# ---------------------------------------------------------------- settings and keys


def test_settings_round_trip_and_broken_files(tmp_path):
    s = Settings.load(tmp_path)
    assert s.music_volume == 0.5 and s.resolution == [1280, 720]
    s.sfx_volume, s.keys = 0.3, {"inventory": "b"}
    s.save(tmp_path)
    assert Settings.load(tmp_path).sfx_volume == 0.3 and Settings.load(tmp_path).keys == {"inventory": "b"}
    Settings.path(tmp_path).write_text("{not json", encoding="utf-8")
    assert Settings.load(tmp_path) == Settings()
    Settings.path(tmp_path).write_text(json.dumps({"music_volume": 9, "resolution": [1, 1], "junk": 1}), encoding="utf-8")
    s = Settings.load(tmp_path)
    assert s.music_volume == 1.0 and s.resolution == [1280, 720]


def test_rebinding_swaps_with_the_old_owner():
    pygame.init()
    k = KeyMap.load(6)
    over = k.rebind("inventory", pygame.K_t)  # t was the skill trees
    k2 = KeyMap.load(6, over)
    assert k2.action_for(pygame.K_t) == "inventory" and k2.action_for(pygame.K_i) == "skillTrees"
    over = k2.rebind("skillBar", pygame.K_q, slot=0)  # q was the life potion
    k3 = KeyMap.load(6, over)
    assert k3.slot_for(pygame.K_q) == 0 and k3.action_for(pygame.K_1) == "lifePotion"
    assert KeyMap.load(6, {"skillBar": ["1"]}).slot_keys == KeyMap.load(6).slot_keys, "a broken change is ignored"


def test_characters_are_listed_newest_first(data, tmp_path):
    for i, name in enumerate(("Ana", "Bo")):
        s = GameSession(data, 1, name=name)
        SaveStore(tmp_path, slot=name.lower()).save_session(s)
        os.utime(tmp_path / f"{name.lower()}.json", (100 + i, 100 + i))
    SaveStore.for_stash(tmp_path).save_stash(SharedStash())
    assert [(slot, name) for slot, name, _ in SaveStore.characters(tmp_path)] == [("bo", "Bo"), ("ana", "Ana")]


# ---------------------------------------------------------------- sounds


def test_fetching_sounds_writes_the_files_and_the_credits(tmp_path):
    cfg = load_sound_config()
    asked = []

    def fake(url):
        asked.append(url)
        if url.endswith(cfg["files"]["thunder"]):
            raise OSError("gone")
        return b"OggS"

    failed = fetch_sounds(cfg, load_config(), tmp_path, fetch=fake)
    assert failed == ["thunder"] and missing_sounds(cfg, tmp_path) == ["thunder"]
    assert all(u.startswith(load_config()["import"]["sources"]["flare/"]["base"]) for u in asked)
    assert "CC-BY-SA" in (sounds_dir(tmp_path) / "ATTRIBUTION.md").read_text(encoding="utf-8")


def test_every_effect_and_theme_names_a_known_file():
    cfg = load_sound_config()
    for names in cfg["effects"].values():
        assert set(names) <= set(cfg["files"])
    assert set(cfg["music"].values()) <= set(cfg["files"])


# ---------------------------------------------------------------- screens


def test_the_title_screen_pauses_and_makes_a_new_character(game):
    g = game
    assert g.title.open and g.paused
    t = g.world.time
    g.tick()
    assert g.world.time == t
    g.title.press("new")
    for ch in "Ana Lee":
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=0, mod=0, unicode=ch, scancode=0))
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="", scancode=0))
    g.tick()
    assert not g.title.open and g.session.name == "Ana Lee" and g.store.slot == "ana_lee"
    assert (g.store.directory / "ana_lee.json").is_file()
    g.tick()
    assert g.world.time > t


def test_the_title_lists_saves_and_continues_one(game, data):
    g = game
    SaveStore(g.store.directory, slot="bo").save_session(GameSession(data, 1, name="Bo"))
    ids = [bid for bid, _, _ in g.title.items()]
    assert ids[0] == "play:bo" and {"new", "options", "credits", "quit"} <= set(ids)
    g.title.layout()
    click(g, g.title._buttons["play:bo"])
    assert g.session.name == "Bo" and not g.title.open


def test_options_change_volume_size_and_keys_and_save_them(game):
    g = game
    g.title.open = False
    g.menu.open = True
    g.menu.press("options")
    assert g.options.open
    g.options.layout()
    music = g.settings.music_volume
    click(g, g.options._buttons["music"])
    assert g.settings.music_volume == pytest.approx(music + 0.1)
    click(g, g.options._buttons["music"], button=3)
    assert g.settings.music_volume == pytest.approx(music)
    g.options.press("size")
    assert g.screen.get_size() == (1600, 900) and g.cam.w == 1600
    g.options.press("key:inventory:")
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_b, mod=0, unicode="b", scancode=0))
    g.tick()
    assert g.keys.action_for(pygame.K_b) == "inventory" and g.hud.keys is g.keys
    saved = Settings.load(g.store.directory)
    assert saved.keys == {"inventory": "b"} and saved.resolution == [1600, 900]
    g.options.press("reset_keys")
    assert g.keys.action_for(pygame.K_i) == "inventory"
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode="", scancode=0))
    g.tick()
    assert not g.options.open and g.menu.open, "Esc leaves the options, back to the menu"


def test_credits_name_every_art_source(game):
    g = game
    text = " ".join(t for t, _ in g.credits.lines())
    for src in g.sprites.lib.cfg["import"]["sources"].values():
        assert src["license"] in text and src["title"] in text
    g.title.open = False
    g.credits.open = True
    g.tick()  # draws without errors


def test_a_critical_hit_shakes_the_screen(game):
    from aethelgard.core.events import FloatingText

    g = game
    g.title.open = False
    g.session.bus.publish(FloatingText(1.0, 1.0, "99", "crit"))
    assert g.shake > 0
    g.tick()
