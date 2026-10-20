"""Potions, the town portal and areas that stay as they were left."""

import pytest

from aethelgard.core.events import ConsumableUsed, ExitReached
from aethelgard.core.items import GoldDrop
from aethelgard.core.session import GameSession
from aethelgard.core.world import OpenPortal, UseConsumable, World
from aethelgard.core.world_gen import ExitKind

DT = 1 / 60


def world_in(data, area="plains", floor=0):
    s = GameSession(data, 21)
    w = World([s])
    w.enter_area(area, floor)
    return s, w, w.players[0]


def run(w, seconds):
    for _ in range(round(seconds / DT)):
        w.step(DT)


def test_life_potion_heals_a_share_over_time_and_is_used_up(data):
    s, w, p = world_in(data)
    w.monsters.clear()
    s.consumables["life_potion"] = 2
    p.life = 10
    used = []
    s.bus.subscribe(ConsumableUsed, used.append)
    assert w.apply(UseConsumable(0, "life_potion")) is None
    w.step(DT)
    assert 10 < p.life < 10 + p.max_life * 0.4, "it heals over the potion's seconds, not at once"
    run(w, 2.5)
    assert p.life == pytest.approx(min(p.max_life, 10 + p.max_life * 0.4), abs=0.5)
    assert s.consumables["life_potion"] == 1 and used


def test_potions_have_a_short_cooldown_and_run_out(data):
    s, w, p = world_in(data)
    s.consumables["energy_potion"] = 1
    assert w.apply(UseConsumable(0, "energy_potion")) is None
    assert w.apply(UseConsumable(0, "energy_potion")) == "No Energy Potion left"
    s.consumables["energy_potion"] = 1
    assert w.apply(UseConsumable(0, "energy_potion")) is None and s.consumables["energy_potion"] == 1, "still on cooldown"


def test_no_portal_from_town(data):
    s, w, p = world_in(data, data.town_id)
    assert w.apply(OpenPortal(0)) == "You are already in town"


def test_the_town_portal_needs_no_scroll_but_has_a_cooldown(data):
    s, w, p = world_in(data, "plains")
    assert "portal_scroll" not in data.items.consumables
    assert w.apply(OpenPortal(0)) is None and 0 in w.portals
    assert w.apply(OpenPortal(0)).startswith("Town Portal:"), "cooldown"
    run(w, data.balance.town_portal.cooldown_seconds + 0.1)
    p.x += 1
    assert w.apply(OpenPortal(0)) is None and w.portals[0].x == pytest.approx(p.x), "a new portal replaces the old one"


def test_town_portal_round_trip_brings_you_back_to_the_same_spot(data):
    s, w, p = world_in(data, "crypt", 1)
    w.monsters.clear()
    here = (p.x, p.y)
    assert w.apply(OpenPortal(0)) is None
    exits = []
    s.bus.subscribe(ExitReached, exits.append)
    run(w, 1.0)  # standing on it: it only takes you once the short arrival cooldown is over
    assert exits and exits[-1].kind == ExitKind.PORTAL_TO_TOWN.value
    w.take_portal(0)
    assert w.area_id == data.town_id
    spot = w.portal_spot(0)
    assert spot is not None and 0 < abs(p.x - spot[0]) + abs(p.y - spot[1]) <= 2, "arrives beside the town portal"
    p.x, p.y = spot
    exits.clear()
    run(w, 1.0)
    assert exits and exits[-1].kind == ExitKind.PORTAL_BACK.value and exits[-1].target_area == "crypt"
    w.take_portal(0)
    assert (w.area_id, w.floor) == ("crypt", 1)
    assert abs(p.x - here[0]) + abs(p.y - here[1]) <= 2


def test_areas_stay_as_they_were_left(data):
    s, w, p = world_in(data, "plains")
    first = sorted(w.monsters)
    victim = first[0]
    w._kill(w.monsters[victim], p)
    gold = w.drop_at(GoldDrop(7), 1.0, 1.0, s.world_rng)
    w.enter_area(data.town_id)
    w.enter_area("plains", came_from=data.town_id)
    assert victim not in w.monsters and sorted(w.monsters) == first[1:], "the dead stay dead, nobody respawns"
    assert gold.id in w.ground, "drops wait on the floor"
