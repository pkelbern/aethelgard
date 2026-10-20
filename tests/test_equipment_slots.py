"""Equipment slots from items.json: two rings, and a one-hand weapon in the off hand (no guard, no Shield skills)."""

import json

import pytest

from aethelgard.core.combat import player_attack, player_defense
from aethelgard.core.items import Item
from aethelgard.core.session import GameSession
from aethelgard.core.world import EquipItem, SetGuard, UseSkill, World

DT = 1 / 60


def town(data):
    s = GameSession(data, 1)
    w = World([s])
    w.enter_area(data.town_id)
    return s, w, w.players[0]


def bag_index(s, base_id):
    return next(i for i, it in enumerate(s.inventory.items) if it.base.id == base_id)


def test_two_rings_fill_both_slots_then_swap_the_first(data):
    s, w, p = town(data)
    for _ in range(3):
        s.inventory.add(Item.plain(data.base("ring")))
    for _ in range(3):
        assert w.apply(EquipItem(0, bag_index(s, "ring"))) is None
    assert s.gear.get("ring1") and s.gear.get("ring2")
    assert sum(1 for it in s.inventory.items if it.base.id == "ring") == 1, "the third ring swapped with ring 1"


def test_double_click_puts_a_sword_in_the_main_hand_even_with_a_free_off_hand(data):
    s, w, p = town(data)
    s.gear.slots.pop("offhand")
    s.inventory.add(Item.plain(data.base("long_dagger")))
    assert w.apply(EquipItem(0, bag_index(s, "long_dagger"))) is None
    assert s.gear.main_hand.base.id == "long_dagger" and s.gear.off_hand is None


def test_a_sword_dragged_to_the_off_hand_means_two_weapons(data):
    s, w, p = town(data)
    s.inventory.add(Item.plain(data.base("long_dagger")))
    assert w.apply(EquipItem(0, bag_index(s, "long_dagger"), "offhand")) is None
    assert s.gear.dual_wielding and not s.gear.has_shield
    assert s.inventory.items[-1].base.id == "buckler", "the shield went back to the backpack"
    sword, dagger = data.base("short_sword"), data.base("long_dagger")
    a_main = player_attack(s.character, s.skills, s.gear, None, "main")
    a_off = player_attack(s.character, s.skills, s.gear, None, "off")
    assert (a_main.dmg_min, a_main.dmg_max) == (sword.dmg_min, sword.dmg_max)
    m = data.balance.dual_wield.off_hand_damage_mult
    assert (a_off.dmg_min, a_off.dmg_max) == pytest.approx((dagger.dmg_min * m, dagger.dmg_max * m)), "the weaker hand"


def test_two_weapons_alternate_swings_and_cannot_guard_or_bash(data):
    s, w, p = town(data)
    s.inventory.add(Item.plain(data.base("long_dagger")))
    w.apply(EquipItem(0, bag_index(s, "long_dagger"), "offhand"))
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert not p.guarding, "no shield, no guard"
    s.allocate_skill("shield_bash")
    assert w.apply(UseSkill(0, "shield_bash", 1)) == "Shield Bash: needs a shield"
    hands = []
    for _ in range(4):
        w._begin_attack(p, None, None)
        hands.append(p.attack_hand)
    assert hands == ["off", "main", "off", "main"]


def test_shields_and_weapons_only_go_where_they_fit(data):
    s, w, p = town(data)
    s.inventory.add(Item.plain(data.base("kite_shield")))
    msg = w.apply(EquipItem(0, bag_index(s, "kite_shield"), "mainhand"))
    assert msg == "Kite Shield goes in Off Hand, not Weapon"


def test_old_saves_with_one_ring_slot_still_load(data):
    s = GameSession(data, 1)
    s.gear.equip(Item.plain(data.base("ring")))
    raw = json.loads(s.save())
    raw["gear"]["ring"] = raw["gear"].pop("ring1")
    back = GameSession.load(data, json.dumps(raw))
    assert back.gear.get("ring1").base.id == "ring"


def test_heavy_shield_only_strengthens_the_shield(data):
    s = GameSession(data, 1)
    s.proficiencies.set_xp("shield", data.xp_total_by_level[5])
    s.skills.points["heavy_shield"] = 5
    base = player_defense(s.character, s.skills, s.gear).armor
    assert base == pytest.approx(13 + 3 * 0.08 * 5), "leather 10 + buckler 3, and +40% of the buckler only"
    s.gear.slots.pop("offhand")
    assert player_defense(s.character, s.skills, s.gear).armor == pytest.approx(10)
