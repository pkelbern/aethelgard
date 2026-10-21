"""Character save: what comes back after closing and reopening the game."""

import json

import pytest

from aethelgard.core.describe import describe_item
from aethelgard.core.events import LootPicked
from aethelgard.core.items import GoldDrop, Item, ItemDrop, LootGenerator, PotionDrop, Rarity, ScrollDrop
from aethelgard.core.rng import Rng
from aethelgard.core.saves import SaveStore, default_save_dir, slot_name
from aethelgard.core.session import SAVE_VERSION, GameSession
from aethelgard.core.world import EquipItem, PickUp, SetGuard, UnequipItem, World


def veteran(data) -> GameSession:
    """A character with XP, points, gold, a new shield, a full-ish backpack and potions."""
    s = GameSession(data, 2024, name="Ana")
    s.gold = 321
    s.proficiencies.set_xp("blades", data.xp_total_by_level[5])  # level 6
    s.proficiencies.set_xp("shield", 5000)
    s.skills.allocate("sharp_blade")
    s.skills.allocate("sharp_blade")
    s.skills.allocate("wide_slash")
    s.gear.equip(Item.plain(data.base("kite_shield")))
    loot, rng = LootGenerator(data), Rng(8)
    for _ in range(5):
        s.collect(ItemDrop(loot.roll_item(9, 3, 1, Rarity.MAGIC, rng)))
    s.collect(PotionDrop("life"))
    s.collect(PotionDrop("life"))
    s.collect(ScrollDrop("portal"))
    return s


def assert_same_character(a: GameSession, b: GameSession) -> None:
    assert b.name == a.name and b.gold == a.gold
    for p in a.proficiencies:
        assert b.proficiencies[p.id].xp == pytest.approx(p.xp)
        assert b.proficiencies[p.id].level == p.level
    assert b.skills.points == a.skills.points
    assert b.gear.to_dict() == a.gear.to_dict()
    assert b.inventory.to_list() == a.inventory.to_list()
    assert b.consumables == a.consumables


def test_save_and_load_keep_levels_skills_and_items(data):
    s = veteran(data)
    loaded = GameSession.load(data, s.save(), new_world_seed=77)
    assert loaded.master_seed == 77, "the world is regenerated every game"
    assert_same_character(s, loaded)
    assert loaded.gear.off_hand.base.id == "kite_shield"
    assert loaded.character.max_life == pytest.approx(s.character.max_life)


def test_version_1_saves_still_load(data):
    old = {"version": 1, "seed": 5, "gold": 40, "proficiencyXp": {"blades": 900}, "skillPoints": {"sharp_blade": 1}}
    s = GameSession.load(data, json.dumps(old))
    assert s.gold == 40 and s.skills.points_in("sharp_blade") == 1
    assert [i.base.id for i in s.gear.slots.values()] == data.items.starting_gear
    assert len(s.inventory) == 0


def test_newer_saves_are_refused(data):
    with pytest.raises(ValueError, match="newer"):
        GameSession.load(data, json.dumps({"version": SAVE_VERSION + 1, "seed": 1}))


def test_collect_puts_each_drop_in_its_place(data):
    s = GameSession(data, 1)
    s.inventory.items.clear()  # start from an empty backpack (new characters carry a bow and a sword)
    s.inventory.capacity = 1
    item = Item.plain(data.base("helm"))
    assert s.collect(GoldDrop(12)) and s.gold == 12
    assert s.collect(PotionDrop("energy")) and s.consumables["energy_potion"] == 1
    assert s.collect(ItemDrop(item)) and s.inventory.items == [item]
    assert not s.collect(ItemDrop(item)), "backpack full"


def test_kills_drop_loot_on_the_ground_and_picking_it_up_fills_the_backpack(data):
    s = GameSession(data, 9)
    s.inventory.items.clear()  # start from an empty backpack (new characters carry a bow and a sword)
    w = World([s])
    w.enter_area("crypt", 2)  # the boss always drops magic items
    picked = []
    s.bus.subscribe(LootPicked, picked.append)
    p = w.players[0]
    w.kill_all(p)
    assert w.ground and not picked and not s.inventory.items, "drops wait on the floor"
    for g in list(w.ground.values()):
        p.x, p.y = g.x + 0.5, g.y
        w.apply(PickUp(0, g.id))
        for _ in range(30):
            w.step(1 / 60)
    items = [e.drop.item for e in picked if isinstance(e.drop, ItemDrop) and e.stored]
    assert items and all(e.player_id == 0 for e in picked)
    assert s.inventory.items == items and not w.ground


def test_full_backpack_leaves_the_drop_on_the_ground(data):
    s = GameSession(data, 9)
    w = World([s])
    w.enter_area("crypt", 2)
    s.inventory.capacity = 0
    p = w.players[0]
    g = w.drop_at(ItemDrop(Item.plain(data.base("kite_shield"))), p.x + 0.5, p.y, s.world_rng)
    picked = []
    s.bus.subscribe(LootPicked, picked.append)
    w.apply(PickUp(0, g.id))
    for _ in range(30):
        w.step(1 / 60)
    assert picked and not picked[0].stored and g.id in w.ground


def test_gold_is_picked_up_by_walking_over_it(data):
    s = GameSession(data, 9)
    w = World([s])
    w.enter_area("crypt", 2)
    w.monsters.clear()
    p = w.players[0]
    w.ground.clear()
    w.drop_at(GoldDrop(40), p.x, p.y, s.world_rng)
    g = next(iter(w.ground.values()))
    p.x, p.y = g.x, g.y
    w.step(1 / 60)
    assert s.gold == 40 and not w.ground


def test_store_writes_reads_and_keeps_a_backup(data, tmp_path):
    store = SaveStore(tmp_path, slot_name("Ana"))
    assert not store.exists() and store.load_session(data) is None
    s = veteran(data)
    store.save_session(s)
    s.gold = 999
    store.save_session(s)
    assert store.path.exists() and store.backup_path.exists()
    assert json.loads(store.backup_path.read_text())["gold"] == 321
    assert_same_character(s, store.load_session(data, new_world_seed=3))


def test_store_falls_back_to_the_backup_when_the_save_is_broken(data, tmp_path):
    store = SaveStore(tmp_path)
    s = veteran(data)
    store.save_session(s)
    store.save_session(s)
    store.path.write_text("{ not json", encoding="utf-8")
    assert_same_character(s, store.load_session(data))
    store.backup_path.write_text("also broken", encoding="utf-8")
    with pytest.raises(ValueError, match="No readable save"):
        store.load_session(data)


def test_save_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    assert default_save_dir() == tmp_path
    assert slot_name("Sir Ana!") == "sir_ana" and slot_name("???") == "character"


# ---------------------------------------------------------------- equipment (inventory screen)


def test_equipping_swaps_in_place_and_checks_requirements(data):
    s = GameSession(data, 1)
    s.inventory.items.clear()  # start from an empty backpack (new characters carry a bow and a sword)
    w = World([s])
    w.enter_area(data.town_id)
    round_shield = Item.plain(data.base("round_shield"))  # needs 20 Strength
    s.inventory.add(Item.plain(data.base("long_dagger")))
    s.inventory.add(round_shield)
    assert "needs 20 Strength" in w.apply(EquipItem(0, 1))
    s.proficiencies.set_xp("blades", data.xp_total_by_level[5])  # +10 Strength
    assert w.apply(EquipItem(0, 1)) is None
    assert s.gear.off_hand is round_shield and s.inventory.items[1].base.id == "buckler", "the buckler took its cell"
    assert len(s.inventory) == 2


def test_unequipping_needs_room_and_drops_the_guard(data):
    s = GameSession(data, 1)
    w = World([s])
    w.enter_area(data.town_id)
    p = w.players[0]
    w.apply(SetGuard(0, True, 1, 0))
    w.step(1 / 60)
    assert p.guarding
    assert w.apply(UnequipItem(0, "offhand")) is None
    assert not s.gear.has_shield and not p.guarding and s.inventory.items[-1].base.id == "buckler"
    s.inventory.capacity = len(s.inventory)
    assert w.apply(UnequipItem(0, "body")) == "Backpack full"


def test_item_tooltip_lists_numbers_affixes_and_requirements(data):
    s = GameSession(data, 1)
    loot, rng = LootGenerator(data), Rng(3)
    item = next(i for i in (loot.roll_item(9, 3, 1, Rarity.MAGIC, rng) for _ in range(200)) if i.affixes and i.base.req.get("str"))
    lines = describe_item(data, item, s.missing_requirements(item))
    texts = [x.text for x in lines]
    assert lines[0].text == item.name and lines[0].style == f"rarity{int(item.rarity)}"
    assert any(t.startswith("Requires") for t in texts)
    assert len([x for x in lines if x.style == "next"]) == len(item.affixes)
    shield = describe_item(data, Item.plain(data.base("tower_shield")))
    assert "+15% of a blocked hit stopped" in [x.text for x in shield]


def test_comparison_shows_gains_and_losses_against_the_worn_item(data):
    from aethelgard.core.describe import compare_items

    kite, tower = Item.plain(data.base("kite_shield")), Item.plain(data.base("tower_shield"))
    texts = {x.text: x.style for x in compare_items(data, tower, kite)}
    assert texts["Instead of Kite Shield"] == "header"
    assert texts["+8 armor"] == "good" and texts["+5% of a blocked hit stopped"] == "good"
    worse = {x.text: x.style for x in compare_items(data, kite, tower)}
    assert worse["-8 armor"] == "warn"
    assert compare_items(data, kite, None)[0].text == "Nothing worn in this slot"
    assert [x.text for x in compare_items(data, kite, kite)][-1] == "Same numbers"
