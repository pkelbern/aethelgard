"""Item kinds (GDD 10.1–10.2): bonuses that make sense for the item, jewelry never plain, hand-made uniques
and sets with partial bonuses."""

import pytest

from aethelgard.core.describe import describe_item
from aethelgard.core.items import Item, Loadout, LootGenerator, Rarity, SharedStash, item_from_dict, item_to_dict
from aethelgard.core.rng import Rng
from aethelgard.core.town import Vendor


def _many(data, rarity, n=3000, seed=3):
    loot, rng = LootGenerator(data), Rng(seed)
    return [loot.roll_item(rng.randint(1, 16), 3, 1, rarity, rng, maximum=Rarity.RARE) for _ in range(n)]


@pytest.mark.parametrize(
    ("item_class", "never"),
    [
        ("bow", {"bladesSkills", "blockReduction", "guardEfficiency", "armorPct", "moveSpeedPct"}),
        ("sword", {"bowsSkills", "bowRangeTiles", "blockReduction", "guardEfficiency", "moveSpeedPct"}),
        ("boots", {"attackSpeedPct", "damagePct", "guardEfficiency"}),
        ("gloves", {"moveSpeedPct", "guardEfficiency"}),
        ("shield", {"damagePct", "attackSpeedPct", "moveSpeedPct"}),
        ("ring", {"armorPct", "moveSpeedPct", "attackSpeedPct"}),
    ],
)
def test_affixes_fit_the_item(data, item_class, never):
    stats = {a.stat for item in _many(data, Rarity.MAGIC) + _many(data, Rarity.RARE, seed=4) if item.base.item_class == item_class for a in item.affixes}
    assert stats, f"no {item_class} rolled"
    assert not stats & never


def test_every_affix_can_roll_somewhere_and_every_class_has_affixes(data):
    for a in data.items.affixes:
        assert data.items.classes_named(a.on), a.id
    for cid in data.items.item_classes:
        pool = data.items.affix_pool(cid)
        assert any(a.kind == "prefix" for a in pool) and any(a.kind == "suffix" for a in pool), cid


def test_rares_fill_their_lines_even_on_items_with_few_prefixes(data):
    rares = [i for i in _many(data, Rarity.RARE, seed=8) if i.rarity == Rarity.RARE]
    assert all(len(i.affixes) >= data.items.rare_affix_count.min for i in rares if i.item_level >= 8)


def test_rings_and_amulets_are_never_plain(data):
    jewels = [i for i in _many(data, Rarity.NORMAL, n=4000) if i.base.item_class in ("ring", "amulet")]
    assert jewels and all(i.rarity >= Rarity.MAGIC and i.mods for i in jewels)
    vendor = Vendor(data)
    for seed in range(20):
        vendor.restock(10, Rng(seed))
        assert all(i.rarity <= Rarity.MAGIC for i in vendor.stock)
        assert all(i.rarity == Rarity.MAGIC for i in vendor.stock if i.base.item_class in ("ring", "amulet"))


def test_uniques_are_fixed_items_with_only_their_ranges_rolling(data):
    loot = LootGenerator(data)
    thorn = data.fixed_item("thornbite")
    rolls = [loot.make_fixed(thorn, 10, Rng(s)) for s in range(30)]
    for it in rolls:
        assert it.rarity == Rarity.UNIQUE and it.name == "Thornbite" and it.base.id == "short_sword" and not it.affixes
        assert [k for k, _ in it.mods] == [p.stat for p in thorn.props]
        for p, v in zip(thorn.props, it.fixed_values, strict=True):
            assert p.min <= v <= p.max
    assert len({it.stat("damagePct") for it in rolls}) > 1, "the damage range rolls"
    assert {it.stat("lifeLeechPct") for it in rolls} == {0.03}, "fixed lines never change"


def test_unique_and_set_rolls_respect_item_level(data):
    loot, rng = LootGenerator(data), Rng(12)
    for _ in range(400):
        ilvl = rng.randint(1, 15)
        it = loot.roll_item(ilvl, 400, 0, Rarity.NORMAL, rng)  # quality so high almost everything is unique
        if it.fixed:
            assert it.fixed.level <= ilvl
        else:
            assert it.rarity <= Rarity.RARE  # nothing hand-made this early: rare instead
    early = [loot.roll_item(1, 400, 0, Rarity.NORMAL, Rng(s)) for s in range(50)]
    assert all(i.fixed is None for i in early)


def test_set_bonuses_add_up_with_more_pieces(data):
    loot, rng = LootGenerator(data), Rng(1)
    st = data.item_set("sentinels_vigil")
    visor, hauberk, bulwark = (loot.make_fixed(p, 15, rng) for p in st.pieces)
    gear = Loadout.for_data(data)
    gear.equip(visor)
    assert gear.set_bonuses() == [] and gear.stat_total("maxLife") == 0
    gear.equip(hauberk)
    assert gear.set_pieces_worn(st.id) == 2
    assert gear.stat_total("maxLife") == pytest.approx(20 + 20)  # hauberk line + 2-piece bonus
    assert gear.stat_total("shieldSkills") == 0
    gear.equip(bulwark)
    assert gear.stat_total("shieldSkills") == 1 and gear.stat_total("fasterHitRecovery") == pytest.approx(0.2)
    assert gear.stat_total("vit") == 5 and gear.stat_total("armorPct") == pytest.approx(visor.stat("armorPct") + hauberk.stat("armorPct"))
    lit = [x for x in describe_item(data, bulwark, gear=gear) if x.style == "good"]
    assert len(lit) == 1 + 3  # every bonus line is active


def test_the_same_set_piece_twice_counts_once(data):
    loot, rng = LootGenerator(data), Rng(2)
    piece = data.item_set("grey_fletcher").pieces[0]
    gear = Loadout.for_data(data)
    gear.equip(loot.make_fixed(piece, 10, rng), "hands")
    assert gear.set_pieces_worn("grey_fletcher") == 1 and gear.stat_total("accuracy") == 0


def test_unique_and_set_items_survive_the_stash(data):
    loot, rng = LootGenerator(data), Rng(5)
    items = [loot.make_fixed(f, 15, rng) for f in data.items.uniques + data.items.set_pieces]
    stash = SharedStash()
    for it in items:
        assert stash.deposit(it)
    back = SharedStash.from_json(data, stash.to_json())
    for a, b in zip(items, back, strict=True):
        assert (b.rarity, b.name, b.fixed, b.mods) == (a.rarity, a.name, a.fixed, a.mods)


def test_a_line_added_to_a_unique_later_loads_at_its_minimum(data):
    it = LootGenerator(data).make_fixed(data.fixed_item("emberline"), 10, Rng(3))
    raw = item_to_dict(it)
    raw["fixed"]["values"] = raw["fixed"]["values"][:2]
    old = item_from_dict(data, raw)
    assert [v for _, v in old.mods][2:] == [p.min for p in it.fixed.props[2:]]


def test_plain_items_still_work_for_starting_gear(data):
    item = Item.plain(data.base("short_sword"))
    assert item.mods == [] and item.stat("damagePct") == 0


def test_validation_catches_item_mistakes(fresh_data):
    from aethelgard.core.data import validate

    d = fresh_data
    d.items.affixes[0].on = ["spoon"]
    d.items.uniques[0].base = "no_such_base"
    d.items.sets[0].bonuses[0].pieces = 1
    errors = "\n".join(validate(d))
    assert "unknown class or group 'spoon'" in errors
    assert "unknown base 'no_such_base'" in errors
    assert "bonuses must go up from 2 pieces" in errors
