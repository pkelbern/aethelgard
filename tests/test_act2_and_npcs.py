"""Act 2 (the Ashen Reach): opened by the Act 1 boss, its own town for portals and deaths; town people (lore)."""

import os

import pytest

from aethelgard.core.session import GameSession
from aethelgard.core.world import OpenPortal, World
from test_monster_ai import boss_arena
from test_world_timing import run

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def test_beating_the_crypt_warden_opens_kharos(data):
    s, w, p, m = boss_arena(data)
    assert "kharos" not in s.waypoints
    w._damage_monster(p, m, 1e9, "blades")
    assert "act1_boss" in s.bosses_killed and "kharos" in s.waypoints
    back = GameSession.load(data, s.save())
    assert back.home_town == "kharos" and "act1_boss" in back.bosses_killed, "a loaded game starts in the furthest town"
    assert GameSession(data, 1).home_town == data.town_id


def test_each_act_has_its_own_town(data):
    assert data.town_of("dune_sea") == "kharos" and data.town_of("crypt") == data.town_id
    assert data.is_town("kharos") and not data.is_town("ash_road")
    assert data.act_of("sun_tomb").unlocked_by == "act1_boss"


def test_a_portal_in_act_two_goes_to_kharos(data):
    s = GameSession(data, 3)
    w = World([s])
    w.enter_area("ash_road")
    w.monsters.clear()
    p = w.players[0]
    w.apply(OpenPortal(0))
    run(w, data.balance.town_portal.cast_seconds + 0.2)
    w.take_portal(0)
    assert w.area_id == "kharos"
    w.take_portal(0)
    assert w.area_id == "ash_road" and p.state != "dead"


def test_act_two_areas_generate_with_their_monsters(data):
    s = GameSession(data, 5)
    w = World([s])
    for area in ("ash_road", "ember_mines", "dune_sea", "antlion_hollows", "dry_oasis", "valley_of_kings"):
        w.enter_area(area)
        fams = {m.monster.type.family for m in w.monsters.values()}
        assert fams and fams <= set(data.area(area).families), area
        assert min(m.monster.level for m in w.monsters.values()) >= data.area(area).level_for_floor(0)
    w.enter_area("sun_tomb", 2)
    assert any(m.monster.boss is not None and m.monster.boss.id == "act2_boss" for m in w.monsters.values())


def test_town_people_say_different_things_after_the_boss(data):
    dara = data.npcs.npcs["dara_caravan"]
    before = [t.text for t in dara.topics_for(set())]
    after = [t.text for t in dara.topics_for({"act1_boss"})]
    assert len(before) == len(after) == 1 and before != after
    for town in ("village", "kharos"):
        from aethelgard.core.world_gen import load_tiled_map

        L = load_tiled_map(data.area(town).map)
        ids = [props["npc"] for _, props in L.markers.values() if "npc" in props]
        assert ids and set(ids) <= set(data.npcs.npcs), town


@pytest.fixture
def game(tmp_path, monkeypatch):
    import pygame

    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    from aethelgard.client.app import Game

    g = Game(seed=3, new_game=True, download_art=False)
    yield g
    pygame.quit()


def test_walking_up_to_a_town_person_opens_the_talk(game):
    g = game
    g.load_area("village")
    g.talk_to = "Elder Bram"
    cell, _ = g.world.layout.markers["Elder Bram"]
    g.player.x, g.player.y = cell[0] + 1.0, float(cell[1])
    g.tick()
    assert g.dialogue.open and g.dialogue.npc.name == "Elder Bram" and g.paused
    first = g.dialogue.text
    g.dialogue.press("topic:0")
    assert g.dialogue.text != first
    g.dialogue.press("bye")
    assert not g.dialogue.open
