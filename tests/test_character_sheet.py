"""Character sheet (C): values match what the fight uses, every row explains itself."""

import pytest

from aethelgard.core.combat import player_attack
from aethelgard.core.items import Item
from aethelgard.core.session import GameSession
from aethelgard.core.sheet import character_sheet
from aethelgard.core.world import World


def sheet_of(data, setup=None):
    s = GameSession(data, 1)
    if setup:
        setup(s)
    w = World([s])
    w.enter_area("plains")
    p = w.players[0]
    rows = {r.label: r for sec in character_sheet(w, p) for r in sec.rows}
    return s, w, p, rows


def test_every_row_has_a_value_and_an_explanation(data):
    s, w, p, rows = sheet_of(data)
    assert {"Level", "Life", "Energy", "Damage", "Accuracy", "Armor", "Block", "Defense", "Strength", "Archery"} <= set(rows)
    for r in rows.values():
        assert r.value and r.help and r.help[0].style == "title", r.label


def test_numbers_come_from_the_same_rules_as_the_fight(data):
    s, w, p, rows = sheet_of(data)
    atk = player_attack(s.character, s.skills, s.gear)
    assert rows["Damage"].value == f"{atk.dmg_min * (1 + atk.damage_bonus_pct):.0f}–{atk.dmg_max * (1 + atk.damage_bonus_pct):.0f}"
    assert rows["Life"].value.endswith(f"/ {s.character.max_life:.0f}")
    assert rows["Block"].value == "70%"
    assert any("Chance to hit a level" in x.text for x in rows["Accuracy"].help)


def test_sheet_explains_no_guard_with_a_bow_and_shows_the_off_hand_when_dual(data):
    def bow(s):
        s.gear.slots.pop("offhand")
        s.gear.equip(Item.plain(data.base("short_bow")), "mainhand")

    s, w, p, rows = sheet_of(data, bow)
    assert rows["Block"].value == "—" and any("bow takes both hands" in x.text for x in rows["Block"].help)
    assert rows["Reach"].value == "14.0 tiles"

    def dual(s):
        s.gear.slots.pop("offhand")
        s.gear.equip(Item.plain(data.base("long_dagger")), "offhand")

    s, w, p, rows = sheet_of(data, dual)
    assert "Off-hand damage" in rows and any("70%" in x.text for x in rows["Off-hand damage"].help)


def test_level_row_explains_the_formula(data):
    s, w, p, rows = sheet_of(data, lambda s: s.proficiencies.set_xp("blades", data.xp_total_by_level[4]))
    assert rows["Level"].value == "5"
    assert any("Blades 5" in x.text for x in rows["Level"].help)


@pytest.mark.parametrize("prof", ["blades", "shield", "armor", "dual_wield", "bows"])
def test_each_proficiency_says_how_it_gains_xp(data, prof):
    assert data.proficiency(prof).description
