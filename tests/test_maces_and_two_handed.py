"""Maces (one-hand maces and hammers) and Two-Handed (great swords, axes, mauls): gear, XP, skills, passives."""

import pytest

from aethelgard.core.combat import player_attack
from aethelgard.core.events import DamageDealt
from aethelgard.core.items import Item
from aethelgard.core.world import UseSkill
from test_ranged_and_leaders import add_monster
from test_world_timing import arena, run


def wield(s, base_id):
    """Puts the weapon in the main hand (requirements skipped: the tests are about the rules, not Strength)."""
    item = Item.plain(s.data.base(base_id))
    if item.base.two_handed:
        s.gear.slots.pop("offhand", None)
    s.gear.equip(item, "mainhand")
    s._gear_changed()


def hits_of(s):
    out = []
    s.bus.subscribe(DamageDealt, out.append)
    return out


# ---------------------------------------------------------------- gear and XP


def test_a_plain_swing_feeds_the_weapons_proficiency(data):
    for base, prof in (("short_sword", "blades"), ("club", "maces"), ("maul", "two_handed")):
        s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
        wield(s, base)
        assert w.swing_proficiency(p) == prof
    s, w, p, m = arena(data)
    wield(s, "ember_wand")
    assert w.swing_proficiency(p) == "blades", "a wand in the hand swings like a fist (unarmed)"
    wield(s, "club")
    s.gear.equip(Item.plain(data.base("short_sword")), "offhand")
    assert w.swing_proficiency(p) == "dual_wield"


def test_great_weapons_take_both_hands_and_swing_slower(data):
    s, w, p, m = arena(data)
    assert "offhand" not in s.gear.slots_for(Item.plain(data.base("claymore")))
    before = w.attack_speed(p)
    s.inventory.add(Item.plain(data.base("battle_axe")))
    i = next(i for i, it in enumerate(s.inventory.items) if it.base.id == "battle_axe")
    s.equip_from_backpack(i) if not s.missing_requirements(s.inventory.items[i]) else wield(s, "battle_axe")
    assert s.gear.off_hand is None, "the shield goes back to the backpack"
    assert w.attack_speed(p) == pytest.approx(before - 0.15)


def test_skills_need_their_weapon(data):
    s, w, p, m = arena(data)
    s.grant_skill("crushing_blow")
    s.grant_skill("heavy_swing")
    assert w.apply(UseSkill(0, "crushing_blow", 1)) == "Crushing Blow: needs a mace or hammer"
    assert w.apply(UseSkill(0, "heavy_swing", 1)) == "Heavy Swing: needs a two-handed weapon"
    wield(s, "mace")
    assert w.skill_block_reason(p, "crushing_blow") is None


def test_weapon_passives_read_the_weapons_tags(data):
    s, w, p, m = arena(data)
    wield(s, "club")
    base = player_attack(s.character, s.skills, s.gear).damage_bonus_pct
    s.grant_skill("sharp_blade", 3)
    assert player_attack(s.character, s.skills, s.gear).damage_bonus_pct == pytest.approx(base), "Sharp Blade is for blades"
    s.grant_skill("heavy_head", 2)
    assert player_attack(s.character, s.skills, s.gear).damage_bonus_pct == pytest.approx(base + 0.12)
    wield(s, "maul")
    low = player_attack(s.character, s.skills, s.gear).crit_chance
    s.grant_skill("overpower", 5)
    assert player_attack(s.character, s.skills, s.gear).crit_chance == pytest.approx(low + 0.10)


# ---------------------------------------------------------------- maces


def test_ground_slam_hits_all_around(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(4.0, 5.0))  # behind
    wield(s, "club")
    add_monster(w, data, "bandit", 6.5, 5.0, 2)
    s.grant_skill("crushing_blow")
    s.grant_skill("ground_slam")
    hits = hits_of(s)
    for _ in range(12):  # swings can miss
        w.apply(UseSkill(0, "ground_slam", None, 9.0, 5.0))
        run(w, 0.7)
        if {e.target.instance_id for e in hits} == {1, 2}:
            break
    assert {e.target.instance_id for e in hits} == {1, 2} and all(e.skill_id == "ground_slam" for e in hits)
    assert s.proficiencies["maces"].xp > 0 and s.skills.xp["ground_slam"] > 0


def test_hammer_blow_stuns_and_knocks_back(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
    wield(s, "war_hammer")
    s.grant_skill("hammer_blow")
    stunned = False
    for _ in range(5):  # a swing can miss
        w.apply(UseSkill(0, "hammer_blow", 1))
        for _ in range(60):
            w.step(1 / 60)
            stunned = stunned or m.state == "stunned"
        if stunned:
            break
    assert stunned and m.x > 6.0


def test_bone_breaker_and_executioner_raise_damage_on_the_right_targets(data):
    s, w, p, m = arena(data)
    zombie = add_monster(w, data, "zombie", 7.0, 5.0, 2)
    base_bandit = w._hit_bonuses(p, m, player_attack(s.character, s.skills, s.gear)).damage_bonus_pct
    s.grant_skill("bone_breaker", 4)
    assert w._hit_bonuses(p, m, player_attack(s.character, s.skills, s.gear)).damage_bonus_pct == pytest.approx(base_bandit)
    assert w._hit_bonuses(p, zombie, player_attack(s.character, s.skills, s.gear)).damage_bonus_pct == pytest.approx(base_bandit + 0.20)
    s.grant_skill("executioner", 2)
    m.state, m.state_left = "stunned", 1.0
    assert w._hit_bonuses(p, m, player_attack(s.character, s.skills, s.gear)).damage_bonus_pct == pytest.approx(base_bandit + 0.10)


def test_concussive_stuns_with_maces_only(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
    s.grant_skill("concussive", 10)
    s.skills.points["concussive"] = 10
    w._weapon_stun(p, m, s.gear.main_hand)
    assert m.state != "stunned", "a sword never stuns through Concussive"
    wield(s, "mace")
    s.combat_rng = type("Lucky", (), {"chance": staticmethod(lambda x: True)})()  # every roll succeeds
    w._weapon_stun(p, m, s.gear.main_hand)
    assert m.state == "stunned" and m.state_left == pytest.approx(0.5)


# ---------------------------------------------------------------- two-handed


def test_berserk_trades_defense_for_damage_and_wears_off(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
    wield(s, "zweihander")
    s.grant_skill("berserk")
    assert w.apply(UseSkill(0, "berserk")) is None
    assert p.buffs["berserk"] == pytest.approx(6.2)
    assert p.buff_total("buffDamagePct") == pytest.approx(0.33) and p.buff_total("buffDefensePct") == pytest.approx(-0.5)
    assert w._hit_bonuses(p, m, player_attack(s.character, s.skills, s.gear)).damage_bonus_pct == pytest.approx(
        player_attack(s.character, s.skills, s.gear).damage_bonus_pct + 0.33)
    run(w, 6.5)
    assert "berserk" not in p.buffs and p.buff_total("buffDamagePct") == 0


def test_cleave_hits_in_front_only(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(6.0, 5.0))
    wield(s, "battle_axe")
    behind = add_monster(w, data, "bandit", 4.0, 5.0, 2)
    s.grant_skill("cleave")
    hits = hits_of(s)
    for _ in range(12):  # swings can miss
        w.apply(UseSkill(0, "cleave", None, 9.0, 5.0))
        run(w, 0.8)
        if any(e.target.instance_id == 1 for e in hits):
            break
    ids = {e.target.instance_id for e in hits}
    assert 1 in ids and behind.monster.instance_id not in ids


def test_the_bulwark_still_works_as_a_buff(data):
    s, w, p, m = arena(data)
    s.grant_skill("bulwark")
    assert w.apply(UseSkill(0, "bulwark")) is None
    assert p.bulwark_left > 3.0 and "bulwark" in p.buffs
