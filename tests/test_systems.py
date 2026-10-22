import math

import numpy as np
import pytest

from aethelgard.core.combat import (
    AttackerStats,
    BlockOutcome,
    CombatMath,
    DefenderStats,
    GuardState,
    Loadout,
    player_attack,
    player_defense,
    player_guard,
)
from aethelgard.core.data import validate
from aethelgard.core.items import Item, ItemDrop, LootGenerator, Rarity, RolledAffix, ScrollDrop, SharedStash
from aethelgard.core.monsters import MonsterFactory, PackKind, PackSpawner, unique_name
from aethelgard.core.rng import Rng
from aethelgard.core.session import GameSession
from aethelgard.core.world_gen import AreaGenerator, ExitKind


def cm(d):
    return CombatMath(d.balance.combat, d.balance.guard)


HIT20 = AttackerStats(5, 1e9, 20, 20)
BARE = DefenderStats(1, 0)

# ---------------------------------------------------------------- RNG


def test_rng_is_deterministic_and_forks_are_independent():
    a, b = Rng(123), Rng(123)
    assert [a.next_u64() for _ in range(500)] == [b.next_u64() for _ in range(500)]
    loot1 = Rng.for_label(7, "loot")
    world = Rng.for_label(7, "world")
    world.next_u64()
    assert Rng.for_label(7, "loot").next_u64() == loot1.next_u64()
    assert Rng.for_label(7, "loot").next_u64() != Rng.for_label(7, "world").next_u64()


def test_rng_bounds():
    r = Rng(5)
    seen = {r.randint(3, 6) for _ in range(3000)}
    assert seen == {3, 4, 5, 6}
    assert all(0 <= r.random() < 1 for _ in range(3000))


# ---------------------------------------------------------------- combat and guard


def test_hit_and_armor(data):
    c = cm(data)
    assert c.hit_chance(100, 100, 10, 10) == pytest.approx(0.5)
    assert c.hit_chance(10000, 1, 10, 1) == 0.95
    assert c.hit_chance(1, 10000, 1, 50) == 0.05
    assert c.armor_reduction(100, 2) == pytest.approx(0.5)
    assert c.armor_reduction(1e9, 1) == 0.75


def test_final_damage(data):
    r = cm(data).resolve_physical(HIT20, DefenderStats(1, 0, 250), Rng(9))
    assert r.hit and r.raw == pytest.approx(20)
    assert r.mitigated == pytest.approx(10)
    assert r.final == pytest.approx(10)


def test_guard_blocks_from_the_front_and_spends_energy(data):
    c = cm(data)
    g = GuardState(raised=True, in_arc=True, energy=100, reduction=0.7)
    r = c.resolve_physical(HIT20, BARE, Rng(1), g)
    assert r.block is BlockOutcome.BLOCKED
    assert r.stopped == pytest.approx(14) and r.final == pytest.approx(6), "a block lets 30% through"
    assert r.guard_energy_cost == pytest.approx(1 + 20 * 0.25)
    assert c.guard_cost(20, 0.3) == pytest.approx(6 * 0.7)
    assert c.guard_cost(20, 0.95) == pytest.approx(6 * 0.4), "efficiency capped at 60%"


def test_block_share_is_capped_and_armor_works_on_what_passes(data):
    c = cm(data)
    huge = c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, True, energy=100, reduction=3.0))
    assert huge.final == pytest.approx(1.0), "never above 95%"
    armored = c.resolve_physical(HIT20, DefenderStats(1, 0, 250), Rng(1), GuardState(True, True, energy=100, reduction=0.7))
    assert armored.reached_armor == pytest.approx(6) and armored.mitigated == pytest.approx(3) and armored.final == pytest.approx(3)


def test_block_reduction_grows_with_shield_and_defensive_stance(data):
    s = GameSession(data, 1)
    g = data.balance.guard
    assert player_guard(s.skills, s.gear, g).block_reduction == pytest.approx(0.7), "buckler adds nothing"
    s.allocate_skill("defensive_stance")
    assert player_guard(s.skills, s.gear, g).block_reduction == pytest.approx(0.725)
    s.gear.equip(Item.plain(data.base("tower_shield")))
    s.skills.points["defensive_stance"] = 10
    assert player_guard(s.skills, s.gear, g).block_reduction == pytest.approx(0.95), "0.7 + 0.15 + 9 × 0.025 capped"


def test_no_dodging_while_blocking(data):
    c = cm(data)
    clumsy = AttackerStats(1, 1, 20, 20)  # almost always misses an agile defender...
    nimble = DefenderStats(30, 1e6)
    assert not c.resolve_physical(clumsy, nimble, Rng(1)).hit
    r = c.resolve_physical(clumsy, nimble, Rng(1), GuardState(True, True, energy=100, reduction=0.7))
    assert r.hit and r.block is BlockOutcome.BLOCKED, "...but a hit on the raised guard always lands on the shield"
    behind = c.resolve_physical(clumsy, nimble, Rng(1), GuardState(True, False, energy=100, reduction=0.7))
    assert not behind.hit, "from behind the guard does not help, dodging still does"


def test_guard_does_not_cover_the_back_nor_when_lowered(data):
    c = cm(data)
    assert c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, False, energy=100)).block is BlockOutcome.NONE
    assert c.resolve_physical(HIT20, BARE, Rng(1), GuardState(False, True, energy=100)).block is BlockOutcome.NONE


def test_perfect_window_and_broken_guard(data):
    c = cm(data)
    perf = c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, True, perfect=True))
    assert perf.block is BlockOutcome.PERFECT and perf.guard_energy_cost == 0
    assert perf.final == pytest.approx(20 * 0.05), "a perfect block stops the cap (95%), never everything"
    broke = c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, True, energy=2))
    assert broke.block is BlockOutcome.GUARD_BROKEN
    assert broke.final == pytest.approx(20) and broke.guard_energy_cost == 2


def test_120_degree_arc():
    assert CombatMath.in_arc(1, 0, 1, 0, 120)
    assert CombatMath.in_arc(1, 0, 1, 1.5, 120)
    assert not CombatMath.in_arc(1, 0, 1, 2, 120)
    assert not CombatMath.in_arc(1, 0, -1, 0, 120)
    assert CombatMath.in_arc(1, 0, -1, 0, 360)


def test_starting_warrior(data):
    s = GameSession(data, 1)
    atk = player_attack(s.character, s.skills, s.gear)
    g = player_guard(s.skills, s.gear, data.balance.guard)
    assert (atk.dmg_min, atk.dmg_max) == (2, 7)
    assert atk.damage_bonus_pct == pytest.approx(0.10)
    assert g.can_guard and g.arc_degrees == 120 and g.efficiency == 0
    assert player_defense(s.character, s.skills, s.gear).armor == pytest.approx(13)
    s.skills.allocate("defensive_stance")
    assert player_guard(s.skills, s.gear, data.balance.guard).efficiency == pytest.approx(0.03)
    wolf = MonsterFactory(data).create(data.monster_type("wolf"), 2, "normal", Rng(1))
    avg_hit = (atk.dmg_min + atk.dmg_max) / 2 * (1 + atk.damage_bonus_pct)
    assert 1.5 <= wolf.max_hp / avg_hit <= 5


# ---------------------------------------------------------------- monsters


def test_rank_multipliers(data):
    f = MonsterFactory(data)
    t = data.monster_type("wolf")
    rng = Rng(3)
    n, c, u, m = (f.create(t, 5, r, rng) for r in ("normal", "champion", "unique", "minion"))
    assert (n.level, c.level, u.level, m.level) == (5, 7, 8, 8)
    xp = lambda lv: data.monster_level(lv).xp * t.exp_pct / 100  # noqa: E731
    assert n.xp_base == pytest.approx(xp(5))
    assert c.xp_base == pytest.approx(xp(7) * 3)
    assert u.xp_base == pytest.approx(xp(8) * 5)
    hp = lambda lv: t.hp * data.monster_level(lv).hp_pct / 100  # noqa: E731
    assert hp(7) * 3 * 0.9 - 1 <= c.max_hp <= hp(7) * 3 * 1.1 + 1
    assert c.damage_bonus_pct == pytest.approx(0.9)


def test_damage_does_not_exceed_life(data):
    m = MonsterFactory(data).create(data.monster_type("zombie"), 3, "normal", Rng(1))
    hp = m.hp
    assert m.take_damage(hp * 10) == pytest.approx(hp)
    assert not m.alive and m.take_damage(5) == 0


def test_packs_per_area(data):
    sp = PackSpawner(data, MonsterFactory(data))
    area = data.area("woods")
    uniques = total = 0
    for seed in range(200):
        packs = sp.spawn_area(area, 0, Rng(seed))
        assert sum(p.kind in (PackKind.NORMAL, PackKind.LED) for p in packs) == area.normal_packs
        special = [p for p in packs if p.kind in (PackKind.UNIQUE, PackKind.CHAMPION)]
        assert 1 <= len(special) <= 2
        total += len(special)
        for p in special:
            if p.kind == PackKind.UNIQUE:
                uniques += 1
                assert 3 <= len(p.members) - 1 <= 6
                assert len(p.leader.modifiers) == 2
                assert all(m.leader_id == p.leader.instance_id for m in p.members[1:])
            else:
                assert 2 <= len(p.members) <= 4
                assert len({m.modifiers[0].id for m in p.members}) == 1
    assert 0.72 <= uniques / total <= 0.88


def test_unique_names_come_from_data(data):
    n = data.monsters.unique_names
    name = unique_name(data, Rng(1))
    assert any(name.startswith(p) for p in n.prefixes)
    assert any(name.endswith(e) for e in n.epithets)


def test_boss_only_on_the_last_floor(data):
    sp = PackSpawner(data, MonsterFactory(data))
    crypt = data.area("crypt")
    assert not any(p.kind == PackKind.BOSS for p in sp.spawn_area(crypt, 0, Rng(1)))
    last = sp.spawn_area(crypt, 2, Rng(1))
    boss = [p for p in last if p.kind == PackKind.BOSS]
    assert len(boss) == 1 and boss[0].leader.level == 12


# ---------------------------------------------------------------- loot and stash


def test_unique_always_drops_magic(data):
    loot, rng = LootGenerator(data), Rng(11)
    for _ in range(300):
        drops = [d for d in loot.roll_drops("unique", 8, 0, rng) if not isinstance(d, ScrollDrop)]  # + a rare scroll
        assert 2 <= len(drops) <= 4
        assert any(isinstance(d, ItemDrop) and d.item.rarity >= Rarity.MAGIC for d in drops)


def test_items_respect_level_and_groups(data):
    loot, rng = LootGenerator(data), Rng(21)
    for _ in range(1500):
        ilvl = rng.randint(1, 15)
        item = loot.roll_item(ilvl, 3, 0.5, Rarity.NORMAL, rng)
        assert item.base.level <= ilvl
        groups = [a.affix.group for a in item.affixes]
        assert len(groups) == len(set(groups))
        for a in item.affixes:
            assert any(t.level <= ilvl for t in a.affix.tiers)
            assert a.affix in data.items.affix_pool(item.base.item_class)


def test_magic_names_use_english_order(data):
    loot = LootGenerator(data)
    base = data.base("short_sword")
    affixes = [RolledAffix(data.affix("sharp"), 10), RolledAffix(data.affix("of_the_bear"), 3)]
    assert loot._name(base, Rarity.MAGIC, affixes, Rng(1)) == "Sharp Short Sword of the Bear"
    rare = loot._name(base, Rarity.RARE, [], Rng(1))
    assert rare.split()[0] in data.items.rare_names.first


def test_loot_is_reproducible(data):
    def roll(seed):
        loot, rng = LootGenerator(data), Rng(seed)
        return [repr(d) for _ in range(40) for d in loot.roll_drops("champion", 6, 0, rng)]

    assert roll(99) == roll(99)
    assert roll(99) != roll(100)


def test_shared_stash(data):
    item = LootGenerator(data).roll_item(10, 3, 1, Rarity.RARE, Rng(4))
    stash = SharedStash(gold=50)
    assert stash.deposit(item)
    other = SharedStash.from_json(data, stash.to_json())
    back = other.withdraw(0)
    assert other.gold == 50 and back.name == item.name and back.base.id == item.base.id
    assert back.stat("str") == pytest.approx(item.stat("str"))
    full = SharedStash(*SharedStash.size_of(item))  # exactly one item's room
    assert full.deposit(item) and not full.deposit(item)


def test_starting_loadout_comes_from_data(data):
    g = Loadout.starting(data)
    assert g.has_shield and g.armor == 13
    assert [i.base.id for i in g.slots.values()] == data.items.starting_gear
    old = g.equip(Item.plain(data.base("kite_shield")))
    assert old.base.id == "buckler" and g.off_hand.base.id == "kite_shield"


# ---------------------------------------------------------------- generated world


def _floors(area):
    return area.floors if area.kind == "dungeon" else 1


@pytest.mark.parametrize("seeds", [range(12)])
def test_maps_are_valid(data, seeds):
    gen = AreaGenerator(data)
    for area in data.all_areas:
        if area.kind == "town":
            continue
        for fl in range(_floors(area)):
            for seed in seeds:
                L = gen.generate(area, fl, seed, 20)
                assert L.validate() is None
                assert len(L.spawn_points) == 20
                assert L.floor_count() > L.width * L.height / 8
                last = fl == _floors(area) - 1
                if area.kind == "dungeon" and not last:
                    assert sum(e.kind == ExitKind.STAIRS_DOWN for e in L.exits) == 1
                if area.boss and last:
                    assert L.boss_spot is not None
                if area.kind == "open":
                    assert len(L.exits) == len(area.links)


@pytest.mark.slow
def test_a_hundred_seeds_per_area(data):
    test_maps_are_valid(data, range(100))


def test_same_seed_same_map(data):
    gen = AreaGenerator(data)
    area = data.area("plains")
    a, b, c = gen.generate(area, 0, 555, 10), gen.generate(area, 0, 555, 10), gen.generate(area, 0, 556, 10)
    assert np.array_equal(a.tiles, b.tiles) and a.spawn_points == b.spawn_points
    assert not np.array_equal(a.tiles, c.tiles)


# ---------------------------------------------------------------- session and death


def test_death(fresh_data):
    s = GameSession(fresh_data, 1)
    s.gold = 1000
    s.proficiencies.set_xp("blades", fresh_data.xp_total_by_level[1] * 2)
    r = s.apply_death()
    assert r.gold_lost == 100 and r.xp_lost == {} and r.leaves_corpse
    fresh_data.balance.death.xp_loss_pct_of_level = 0.10
    gained = s.proficiencies["blades"].xp - fresh_data.xp_total_by_level[1]
    r2 = s.apply_death()
    assert r2.xp_lost["blades"] == pytest.approx(gained * 0.10)
    for _ in range(200):
        s.apply_death()
    assert s.proficiencies["blades"].level == 2, "never loses a level"


def test_entering_an_area(data):
    s = GameSession(data, 42)
    layout, packs = s.enter_area("crypt", 2)
    assert layout.validate() is None
    assert any(p.kind == PackKind.BOSS for p in packs)
    assert len(layout.spawn_points) == sum(p.kind != PackKind.BOSS for p in packs)
    plain, _ = s.enter_area("plains")
    assert any(e.target_area == "village" for e in plain.exits)
    assert any(e.target_area == "cave" and e.kind == ExitKind.ENTRANCE for e in plain.exits)


def test_path_goes_around_a_wall(data):
    from aethelgard.core.pathfinding import find_path
    from aethelgard.core.world_gen import FLOOR, WALL, AreaLayout

    L = AreaLayout.filled(10, 10, FLOOR)
    L.tiles[5, 0:9] = WALL  # horizontal wall with a gap at x=9
    path = find_path(L, (2, 2), (2, 8))
    assert path and path[-1] == (2, 8)
    assert all(L.is_floor(c) for c in path)
    assert any(x == 9 for x, _ in path), "goes through the gap"
    assert find_path(L, (2, 2), (4, 5))[-1] != (4, 5), "a goal on a wall becomes the nearest floor"


def test_affixes_only_roll_where_they_make_sense(data):
    loot, rng = LootGenerator(data), Rng(77)
    seen: dict[str, set[str]] = {}
    for _ in range(4000):
        item = loot.roll_item(rng.randint(5, 15), 3, 1, Rarity.MAGIC, rng)
        for a in item.affixes:
            seen.setdefault(a.affix.stat, set()).add(item.base.item_class)
    assert seen.get("attackSpeedPct", set()) <= {"sword", "mace", "great", "bow", "gloves"}, "no attack speed on armor, boots or jewelry"
    assert seen.get("guardEfficiency", set()) <= {"shield"}
    assert seen.get("moveSpeedPct", set()) <= {"boots"}, "movement speed only on boots"
    assert not seen.get("armorPct", set()) & {"sword", "bow", "wand", "ring", "amulet"}


# ---------------------------------------------------------------- continuity between areas


def _opposite(side):
    from aethelgard.core.world_gen import EAST, NORTH, SOUTH, WEST

    return {NORTH: SOUTH, SOUTH: NORTH, EAST: WEST, WEST: EAST}[side]


def _side_of(L, pos):
    from aethelgard.core.world_gen import EAST, NORTH, SOUTH, WEST

    x, y = pos
    return NORTH if y == 0 else SOUTH if y == L.height - 1 else WEST if x == 0 else EAST if x == L.width - 1 else None


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_leaving_an_area_you_come_in_on_the_facing_side_at_the_same_point(data, seed):
    """Walk out of the village's east edge and you enter the plains from the west, at the same height."""
    from aethelgard.core.world_gen import side_toward

    gen = AreaGenerator(data)
    layouts = {a.id: gen.generate(a, 0, seed, 10) for a in data.all_areas if a.kind != "dungeon"}
    for a_id, L in layouts.items():
        a = data.area(a_id)
        for e in L.exits:
            b = data.area(e.target_area)
            if e.kind != ExitKind.EDGE:
                continue
            back = next(x for x in layouts[b.id].exits if x.target_area == a_id)
            Lb = layouts[b.id]
            assert _side_of(L, e.pos) == side_toward(a, b), f"{a_id} → {b.id} on the side facing it"
            assert _side_of(Lb, back.pos) == _opposite(_side_of(L, e.pos)), f"{b.id} → {a_id} on the opposite side"
            along_a = (e.pos[1] / (L.height - 1)) if e.pos[0] in (0, L.width - 1) else e.pos[0] / (L.width - 1)
            along_b = (back.pos[1] / (Lb.height - 1)) if back.pos[0] in (0, Lb.width - 1) else back.pos[0] / (Lb.width - 1)
            assert along_a == pytest.approx(along_b, abs=0.08), "the same point along the edge"


def test_linked_areas_must_sit_side_by_side_on_the_act_map(fresh_data):
    fresh_data.area("woods").map_pos = [5, 5]
    assert any("not side by side" in e for e in validate(fresh_data))


def test_waypoints_never_stand_next_to_an_exit(data):
    from aethelgard.core.world_gen import WAYPOINT_EXIT_GAP, AreaGenerator

    gen = AreaGenerator(data)
    checked = 0
    for area in data.all_areas:
        if not area.waypoint or area.kind == "town":
            continue
        for seed in range(12):
            L = gen.generate(area, 0, seed, 6)
            assert L.waypoint is not None
            near = [e for e in L.exits if math.hypot(e.pos[0] - L.waypoint[0], e.pos[1] - L.waypoint[1]) < WAYPOINT_EXIT_GAP]
            assert not near, f"{area.id} seed {seed}: waypoint {L.waypoint} next to {near[0].kind}"
            checked += 1
    assert checked
