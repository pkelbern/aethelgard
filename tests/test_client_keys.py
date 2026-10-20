"""keys.json must match the action bar: one key per slot, no key used twice."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from aethelgard.client.keys import KeyMap  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _pygame():
    pygame.init()
    yield
    pygame.quit()


def test_default_keys_cover_every_bar_slot(data):
    k = KeyMap.load(data.skills.bar_slots)
    assert [k.slot_label(i) for i in range(data.skills.bar_slots)] == ["1", "2", "3", "4", "5", "6"], "GDD 12.3"
    assert k.bar_labels == "1–6"
    assert k.slot_for(pygame.K_3) == 2 and k.slot_for(pygame.K_t) is None
    assert k.action_for(pygame.K_SPACE) == "guard" and k.action_label("map") == "Tab" and k.action_label("save") == "F5"
    assert k.action_for(pygame.K_LALT) == k.action_for(pygame.K_RALT) == "showItems" and k.action_label("showItems") == "Alt"


def test_bad_key_maps_are_refused():
    raw = {"skillBar": ["q", "w"], "guard": "space", "skillTrees": "t", "inventory": "i", "map": "tab", "console": "f1", "save": "f5", "cancel": "escape",
           "showItems": ["left alt", "right alt"], "compare": ["left shift", "right shift"],
           "lifePotion": "z", "energyPotion": "x", "portalScroll": "c"}
    with pytest.raises(ValueError, match="slots"):
        KeyMap(raw, 6)
    with pytest.raises(ValueError, match="twice"):
        KeyMap({**raw, "skillBar": ["q", "t"]}, 2)
    assert KeyMap(raw, 2).bar_labels == "Q W"


def test_inventory_screen_draws_every_equipment_slot(data):
    from aethelgard.client.item_windows import GEAR_LAYOUT

    assert set(GEAR_LAYOUT) == set(data.items.equipment), "a slot in items.json without a place on the screen (or the reverse)"
