"""Crafting at Master Orrin in Kharos (Act 2): salvage into materials, recipes (crafting.json)."""

import pytest

from aethelgard.core.crafting import Crafter
from aethelgard.core.items import Item, LootGenerator, Rarity, item_from_dict, item_to_dict
from aethelgard.core.rng import Rng
from aethelgard.core.session import GameSession
from aethelgard.core.world import Craft, Salvage, World


def at_the_bench(data):
    s = GameSession(data, 2)
    w = World([s])
    w.enter_area("kharos")
    cell, _ = w.layout.markers["Master Orrin"]
    p = w.players[0]
    p.x, p.y = cell[0] + 1.0, float(cell[1])
    s.inventory.items.clear()
    s.inventory.pos.clear()
    return s, w


def magic(data, base="long_sword", level=12):
    return LootGenerator(data).make(data.base(base), Rarity.MAGIC, level, Rng(4))


def test_salvage_gives_materials_by_rarity_and_level(data):
    s, w = at_the_bench(data)
    s.inventory.add(Item.plain(data.base("short_sword")))
    assert w.apply(Salvage(0, 0)) is None
    assert s.materials["scrap"] == 2 and not s.inventory.items
    s.inventory.add(magic(data, level=12))
    w.apply(Salvage(0, 0))
    assert s.materials["essence"] == 2 + 12 // data.crafting.salvage_level_step


def test_only_by_the_crafter(data):
    s = GameSession(data, 2)
    w = World([s])
    w.enter_area("village")
    assert w.apply(Salvage(0, 0)) == "Stand by the crafter"


def test_reforge_keeps_base_rarity_and_level(data):
    s, w = at_the_bench(data)
    item = magic(data)
    s.inventory.add(item)
    assert "Missing" in w.apply(Craft(0, "reforge_magic", 0))
    s.materials["essence"] = 10
    assert w.apply(Craft(0, "reforge_magic", 0)) is None
    new = s.inventory.items[0]
    assert new is not item and (new.base, new.rarity, new.item_level) == (item.base, item.rarity, item.item_level)
    assert s.materials["essence"] == 7


def test_upgrade_moves_to_the_next_base_of_the_kind(data):
    c = Crafter(data)
    assert c.next_base(data.base("short_sword")).id == "gladius"
    assert c.next_base(data.base("crystal_sword")) is None
    assert c.next_base(data.base("ember_wand")) is None, "a wand keeps its spell: no upgrade into another school"
    s, w = at_the_bench(data)
    item = magic(data, "long_sword")
    s.inventory.add(item)
    s.materials.update({"scrap": 8, "shards": 1})
    assert w.apply(Craft(0, "upgrade", 0)) is None
    new = s.inventory.items[0]
    assert new.base.id == "bastard_sword" and new.affixes == item.affixes


def test_empower_keeps_the_lines_and_makes_a_rare(data):
    s, w = at_the_bench(data)
    item = magic(data)
    s.inventory.add(item)
    s.materials.update({"essence": 4, "shards": 4, "aether": 1})
    assert w.apply(Craft(0, "empower", 0)) is None
    new = s.inventory.items[0]
    assert new.rarity == Rarity.RARE and all(a in new.affixes for a in item.affixes)


def test_a_craft_adds_its_lines_survives_saving_and_respects_item_classes(data):
    s, w = at_the_bench(data)
    s.inventory.add(Item.plain(data.base("boots")))
    s.materials.update({"shards": 6, "aether": 2})
    assert "does not work" in w.apply(Craft(0, "craft_blood", 0))
    assert w.apply(Craft(0, "craft_safety", 0)) is None
    new = s.inventory.items[0]
    stats = [a.stat for a in new.affixes]
    assert new.rarity == Rarity.RARE and "armorPct" in stats and "elementalResist" in stats
    assert stats.count("armorPct") == 1, "the random lines never repeat the crafted ones"
    back = item_from_dict(data, item_to_dict(new))
    assert back.mods == new.mods
    loaded = GameSession.load(data, s.save())
    assert loaded.materials["aether"] == 1


@pytest.mark.parametrize("recipe", ["reforge_magic", "imbue", "craft_hit_power"])
def test_recipes_refuse_the_wrong_rarity(data, recipe):
    c = Crafter(data)
    from collections import Counter

    rich = Counter({m: 99 for m in data.crafting.materials})
    unique = LootGenerator(data).make_fixed(data.items.uniques[0], 10, Rng(1))
    assert c.why_not(data.recipe(recipe), unique, rich) is not None
