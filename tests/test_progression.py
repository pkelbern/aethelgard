import pytest

from aethelgard.core.data import validate
from aethelgard.core.events import (
    DamageDealt,
    DamageMitigated,
    EventBus,
    HitBlocked,
    MonsterKilled,
    MonsterXpInfo,
    ProficiencyLeveledUp,
)
from aethelgard.core.progression import Allocate, LevelFactor, ProficiencySet, XpCalculator, XpCurve, compute_level, compute_title
from aethelgard.core.session import GameSession
from aethelgard.core.simulation import run_act


def calc(d):
    return XpCalculator(LevelFactor(d.balance.level_factor.table), d.balance.xp)


def test_data_loads_and_is_valid(data):
    assert validate(data) == []
    assert [p.id for p in data.proficiencies.proficiencies] == [
        "blades", "shield", "armor", "dual_wield", "bows", "maces", "two_handed", "pyromancy"]
    assert len(data.skills.nodes) == 8 * len(data.proficiencies.proficiencies), "8 nodes per tree"
    assert all(n.cooldown >= 0 for n in data.skills.nodes if n.kind == "active")
    assert data.town_id == "village"


def test_own_curves_have_the_expected_pace(data):
    c = XpCurve(data.xp_total_by_level)
    assert c.max_level == 30
    assert c.level_for_xp(0) == 1
    assert c.level_for_xp(data.xp_total_by_level[1]) == 2
    assert c.level_for_xp(1e12) == 30

    # same-level kills to level up: few at the start, many at 20
    def kills(n):
        need = data.xp_total_by_level[n] - data.xp_total_by_level[n - 1]
        return need / (data.monster_level(n).xp * 0.9)

    assert 5 <= kills(1) <= 25
    assert 150 <= kills(20) <= 400
    assert data.monster_level(1).hp_pct < data.monster_level(10).hp_pct < data.monster_level(30).hp_pct


def test_level_factor(data):
    f = LevelFactor(data.balance.level_factor.table)
    assert f(0) == f(5) == f(-5) == 1.0
    assert f(6) == 0.88
    assert f(10) == f(25) == 0.02
    assert f(-6) == 0.81
    assert f(-40) == 0.05


def test_worked_example(data):
    wolf = MonsterXpInfo(1, 7, 81.0, 120)
    c = calc(data)
    assert c.offense(wolf, 30, 5) == pytest.approx(20.25)
    assert c.block(wolf, 4) == pytest.approx(8.1)
    assert c.defense_cap(wolf) == pytest.approx(60.75)


def test_overkill_does_not_count(data):
    m = MonsterXpInfo(1, 5, 63, 40)
    assert calc(data).offense(m, 400, 5) == pytest.approx(63)


def test_defensive_xp_cap(data):
    bus = EventBus()
    profs = ProficiencySet(data, calc(data), bus)
    m = MonsterXpInfo(42, 5, 100, 50)
    for _ in range(100):
        bus.publish(HitBlocked(m))
        bus.publish(DamageMitigated(m, 10, 5))
    assert profs["shield"].xp + profs["armor"].xp == pytest.approx(75)
    bus.publish(MonsterKilled(m, "normal"))
    assert profs.defense_xp_from(42) == 0


def test_trivial_monster(data):
    m = MonsterXpInfo(1, 1, 30, 10)
    c = calc(data)
    assert c.block(m, 11) == 0
    assert c.offense(m, 10, 11) == pytest.approx(30 * 0.05)


def test_xp_through_the_bus_and_level_up(data):
    s = GameSession(data, 1)
    ups = []
    s.bus.subscribe(ProficiencyLeveledUp, ups.append)
    s.bus.publish(DamageDealt("blades", MonsterXpInfo(1, 2, 400, 10), 10))
    assert s.proficiencies["blades"].xp == pytest.approx(400)
    assert s.proficiencies["blades"].level == 2
    assert len(ups) == 1


def test_each_player_only_gets_their_own_xp(data):
    bus = EventBus()
    p0 = ProficiencySet(data, calc(data), bus, player_id=0)
    p1 = ProficiencySet(data, calc(data), bus, player_id=1)
    m = MonsterXpInfo(1, 2, 100, 10)
    bus.publish(DamageDealt("blades", m, 10, player_id=1))
    bus.publish(HitBlocked(m, player_id=0))
    assert p0["blades"].xp == 0 and p1["blades"].xp == 100
    assert p0["shield"].xp == pytest.approx(10) and p1["shield"].xp == 0


def test_level_and_title(data):
    assert compute_level([20, 12, 8], 0.25) == 25
    pd = data.proficiencies
    assert compute_title([("blades", 10), ("shield", 6), ("armor", 2)], pd) == "Guardian"
    assert compute_title([("blades", 10), ("shield", 4), ("armor", 2)], pd) == "Duelist"
    assert compute_title([("blades", 3), ("shield", 9), ("armor", 8)], pd) == "Bastion"
    assert compute_title([("blades", 1), ("shield", 1), ("armor", 1)], pd) == "Recruit"


def test_village_made_in_tiled(data):
    s = GameSession(data, 1)
    layout, packs = s.enter_area("village")
    assert packs == [] and layout.validate() is None
    assert any(e.target_area == "plains" for e in layout.exits)
    roles = {props.get("role") for _, props in layout.markers.values()}
    assert {"vendor", "stash"} <= roles
    assert layout.waypoint is not None


def test_attributes_from_the_profile(data):
    s = GameSession(data, 1)
    s.proficiencies.set_xp("blades", data.xp_total_by_level[4])  # level 5
    a = s.character.attributes
    assert (a.str, a.dex, a.vit) == (18, 14, 10)
    assert s.character.level == 5
    assert s.character.max_life == pytest.approx(50 + 40 + 15)


def test_unused_proficiencies_do_not_raise_the_level(data):
    from aethelgard.core.progression import compute_level

    assert compute_level([1, 1, 1, 1, 1], 0.25) == 1, "a new character is level 1 however many proficiencies exist"
    assert compute_level([13, 9, 7, 1, 1], 0.25) == compute_level([13, 9, 7], 0.25) == 17


def test_tree_layers_points_synergies(data):
    s = GameSession(data, 1)
    t = s.skills
    assert t.available("blades") == 1
    assert t.allocate("wide_slash") is Allocate.LAYER_LOCKED
    assert t.allocate("sharp_blade") is Allocate.OK
    assert t.allocate("sharp_blade") is Allocate.NO_POINTS
    s.proficiencies.set_xp("blades", data.xp_total_by_level[5])  # level 6
    assert t.allocate("wide_slash") is Allocate.REQUIRES, "Precise Strike comes first"
    assert t.allocate("precise_strike") is Allocate.OK and t.allocate("wide_slash") is Allocate.OK
    assert t.allocate("shield_bash") is Allocate.OK
    assert t.effect("shield_bash", "damagePct") == pytest.approx(0.10 + 0.06)
    assert t.effective_points(10) == pytest.approx(9)
    t.respec("blades")
    assert t.available("blades") == 6 and not t.unlocked & {"precise_strike", "wide_slash"}


def test_restoring_points_respects_the_rules(data):
    s = GameSession(data, 1)
    rejected = s.skills.restore({"sharp_blade": 1, "bleed": 3})  # bleed needs Blades 6
    assert s.skills.points_in("sharp_blade") == 1 and s.skills.points_in("bleed") == 0
    assert rejected == ["bleed"]


def test_act_1_simulation_on_target(data):
    steps = run_act(data)
    assert [s.main_level for s in steps] == [4, 6, 7, 9, 10, 11, 12, 13, 13], "smaller packs early (areas.json packSize)"
    last = steps[-1]
    assert 12 <= last.main_level <= 14, "design target: Blades 12–14 at the boss"
    assert last.shield_level == 9
    assert last.armor_level == 7
