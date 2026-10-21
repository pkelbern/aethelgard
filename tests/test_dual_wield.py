"""Two weapons, D2 style: alternating swings, a weaker off hand, and the Dual Wield tree."""

import pytest

from aethelgard.core.combat import player_attack
from aethelgard.core.events import DamageDealt
from aethelgard.core.items import Item
from aethelgard.core.progression import compute_title
from test_world_timing import arena, run


def dual(data, level=1, **kw):
    s, w, p, m = arena(data, level=level, **kw)
    s.gear.slots.pop("offhand")
    s.gear.equip(Item.plain(data.base("long_dagger")), "offhand")
    assert s.gear.dual_wielding
    return s, w, p, m


def test_off_hand_is_weaker_and_training_closes_the_gap(data):
    s, w, p, m = dual(data)
    base = player_attack(s.character, s.skills, s.gear, None, "off").dmg_max
    assert base == pytest.approx(6 * 0.7)
    s.allocate_skill("off_hand_training")
    assert player_attack(s.character, s.skills, s.gear, None, "off").dmg_max == pytest.approx(6 * 0.73)
    s.skills.points["off_hand_training"] = 10
    assert player_attack(s.character, s.skills, s.gear, None, "off").dmg_max <= 6, "never above full damage"


def test_with_two_weapons_every_swing_feeds_only_dual_wield(data):
    s, w, p, m = dual(data, monster_dmg=(0.01, 0.01))
    profs = []
    s.bus.subscribe(DamageDealt, lambda e: profs.append(e.proficiency_id))
    from aethelgard.core.world import AttackTarget

    w.apply(AttackTarget(0, 1))
    run(w, 4)
    assert profs and set(profs) == {"dual_wield"}, "Blades is for one blade used alone"
    assert s.proficiencies["dual_wield"].xp > 0 and s.proficiencies["blades"].xp == 0


def test_double_strike_hits_with_both_weapons_in_one_swing(data):
    s, w, p, m = dual(data, monster_dmg=(0.01, 0.01))
    s.allocate_skill("double_strike")
    hits = []
    s.bus.subscribe(DamageDealt, hits.append)
    from aethelgard.core.world import UseSkill

    assert w.apply(UseSkill(0, "double_strike", 1)) is None
    run(w, 0.6)
    assert len(hits) == 2 and all(h.proficiency_id == "dual_wield" for h in hits)


def test_dual_wield_skills_need_two_weapons(data):
    s, w, p, m = arena(data)
    s.allocate_skill("double_strike")
    from aethelgard.core.world import UseSkill

    assert w.apply(UseSkill(0, "double_strike", 1)) == "Double Strike: needs two weapons"


def test_frenzy_stacks_attack_speed_then_fades(data):
    s, w, p, m = dual(data, monster_dmg=(0.01, 0.01))
    s.proficiencies.set_xp("dual_wield", data.xp_total_by_level[5])
    s.grant_skill("frenzy")
    from aethelgard.core.world import UseSkill

    before = w.attack_speed(p)
    for _ in range(3):
        w.apply(UseSkill(0, "frenzy", 1))
        run(w, 0.7)
    assert p.frenzy_stacks >= 2 and w.attack_speed(p) > before
    run(w, 5)
    assert p.frenzy_stacks == 0


def test_whirling_blades_hits_all_around_with_both_hands(data):
    s, w, p, m = dual(data, monster_dmg=(0.01, 0.01), at=(4.0, 5.0))  # behind the player
    s.proficiencies.set_xp("dual_wield", data.xp_total_by_level[11])
    s.grant_skill("whirling_blades")
    hits = []
    s.bus.subscribe(DamageDealt, hits.append)
    from aethelgard.core.world import UseSkill

    assert w.apply(UseSkill(0, "whirling_blades", None, 9.0, 5.0)) is None  # aimed away from it
    run(w, 0.6)
    assert len(hits) == 2


def test_blade_guard_deflects_melee_and_bloodlust_heals(data):
    s, w, p, m = dual(data, monster_dmg=(10, 10), level=14)
    s.proficiencies.set_xp("dual_wield", data.xp_total_by_level[17])
    s.skills.points["blade_guard"] = 10
    s.skills.points["bloodlust"] = 10
    p.reset_vitals()
    deflected = []
    from aethelgard.core.events import FloatingText

    s.bus.subscribe(FloatingText, lambda e: deflected.append(e) if e.text == "deflected" else None)
    run(w, 20)
    assert deflected, "about 1 in 6 melee hits turned aside at 10 points"
    p.life = 10
    w._damage_monster(p, m, 50, "dual_wield")
    assert p.life == pytest.approx(10 + 50 * s.skills.effect("bloodlust", "lifeLeechPct"))


def test_titles_for_dual_wielders(data):
    pd = data.proficiencies
    assert compute_title([("blades", 10), ("dual_wield", 8), ("shield", 1), ("armor", 1)], pd) == "Blademaster"
    assert compute_title([("dual_wield", 10), ("blades", 2), ("shield", 1), ("armor", 1)], pd) == "Twinblade"


def test_dual_wield_tree_has_eight_nodes_in_four_layers(data):
    nodes = [n for n in data.skills.nodes if n.tree == "dual_wield"]
    assert len(nodes) == 8 and sorted({n.layer for n in nodes}) == [1, 6, 12, 18]
    assert sum(n.kind == "active" for n in nodes) == 4
