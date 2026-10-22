"""The last actives and passives of the MVP trees (M3-04, M3-05, M3-08, M3-09) and item bonuses in combat (M6-03, M3-07)."""

import pytest

from aethelgard.core.combat import AttackerStats, player_attack
from aethelgard.core.events import MonsterXpInfo
from aethelgard.core.items import Item, Rarity, RolledAffix
from aethelgard.core.world import ReleaseSkill, UseSkill
from test_maces_and_two_handed import wield
from test_ranged_and_leaders import add_monster
from test_world_timing import DT, arena, run

INFO = MonsterXpInfo(9, 3, 10, 100)


def hard_hit(p, share):
    return AttackerStats(level=1, accuracy=1e9, dmg_min=p.max_life * share, dmg_max=p.max_life * share)


def with_mods(data, base_id, **mods):
    """An item with the given bonus lines (affixes picked by stat, values forced)."""
    affixes = []
    for stat, value in mods.items():
        a = next(a for a in data.items.affixes if a.stat == stat)
        affixes.append(RolledAffix(a, value))
    return Item(data.base(base_id), Rarity.MAGIC, 10, affixes, "Test")


def wear(s, item, slot):
    s.gear.equip(item, slot)
    s._gear_changed()


# ---------------------------------------------------------------- actives


def test_charge_rushes_to_the_target_and_strikes(data):
    s, w, p, m = arena(data, at=(9.5, 5.0), level=1)
    s.grant_skill("charge")
    assert w.apply(UseSkill(0, "charge", 1)) is None
    hp = m.monster.hp
    run(w, 0.3)
    assert p.x > 7.0, "much faster than walking (6.5 tiles/s would be at ~7)"
    run(w, 0.8)
    assert m.monster.hp < hp and p.dash is None


def test_charge_has_a_range(data):
    s, w, p, m = arena(data, at=(11.5, 11.5))
    s.grant_skill("charge")
    m.x, m.y = 5.0 + 9.0, 5.0
    assert "too far" in w.apply(UseSkill(0, "charge", 1))


def test_whirlwind_hits_everyone_around_while_held_and_drains_energy(data):
    s, w, p, m = arena(data, at=(6.0, 5.0), level=1, monster_dmg=(0.01, 0.01))
    other = add_monster(w, data, "bandit", 4.0, 5.0, 2, level=1)
    other.attack_cooldown = 99
    s.grant_skill("whirlwind")
    p.energy = 50.0
    assert w.apply(UseSkill(0, "whirlwind")) is None
    hp = (m.monster.hp, other.monster.hp)
    run(w, 1.5)
    assert m.monster.hp < hp[0] and other.monster.hp < hp[1], "both sides at once"
    regen = p.max_energy * data.balance.character.energy_regen_pct_per_second * 1.5
    assert p.energy == pytest.approx(50.0 - data.node("whirlwind").energy_cost * 1.5 + regen, abs=0.3)
    w.apply(ReleaseSkill(0, "whirlwind"))
    assert p.channel is None
    hp = m.monster.hp
    run(w, 1.0)
    assert m.monster.hp == hp


def test_whirlwind_stops_when_energy_runs_out(data):
    s, w, p, m = arena(data, at=(6.0, 5.0), monster_dmg=(0.01, 0.01))
    s.grant_skill("whirlwind")
    w.apply(UseSkill(0, "whirlwind"))
    p.energy = 1.0
    run(w, 0.6)
    assert p.channel is None


def test_war_cry_weakens_enemies_around(data):
    s, w, p, m = arena(data, at=(7.0, 5.0))
    far = add_monster(w, data, "bandit", 11.5, 11.5, 2)
    s.grant_skill("war_cry")
    before = m.attack.damage_bonus_pct
    assert w.apply(UseSkill(0, "war_cry")) is None
    assert m.attack.damage_bonus_pct == pytest.approx(before - s.skills.effect("war_cry", "enemyDamageReduction"))
    assert far.attack.damage_bonus_pct == pytest.approx(before), "out of its radius"
    assert "war_cry" not in p.buffs
    run(w, s.skills.effect("war_cry", "durationSeconds") + 0.1)
    assert m.attack.damage_bonus_pct == pytest.approx(before)


def test_blade_dance_goes_through_the_line_hitting_each_once(data):
    s, w, p, m = arena(data, at=(7.0, 5.0), level=1)
    second = add_monster(w, data, "bandit", 8.5, 5.2, 2, level=1)
    s.gear.equip(Item.plain(data.base("short_sword")), "offhand")
    s.grant_skill("blade_dance")
    hp = (m.monster.hp, second.monster.hp)
    assert w.apply(UseSkill(0, "blade_dance", None, 11.0, 5.0)) is None
    run(w, 0.8)
    assert p.x > 8.5, "it went through them"
    assert m.monster.hp < hp[0] and second.monster.hp < hp[1]


def test_rain_of_arrows_keeps_hitting_the_circle(data):
    s, w, p, m = arena(data, at=(9.0, 5.0), level=1)
    other = add_monster(w, data, "bandit", 9.0, 6.5, 2, level=1)
    wield(s, "short_bow")
    s.grant_skill("rain_of_arrows")
    hp = (m.monster.hp, other.monster.hp)
    assert w.apply(UseSkill(0, "rain_of_arrows", None, 9.0, 5.5)) is None
    run(w, 1.5)
    assert m.monster.hp < hp[0] and other.monster.hp < hp[1]
    run(w, 3.0)
    assert not w.spells.fires


# ---------------------------------------------------------------- passives


def test_bleed_keeps_hurting_after_the_blow(data):
    s, w, p, m = arena(data, level=1)
    s.skills.set_level("sharp_blade", 1)
    s.skills.set_level("bleed", 10)
    s.skills.points["bleed"] = 10
    m.monster.hp = m.monster.max_hp = 10000
    for _ in range(40):
        w.strike(p, m, None, ("main",))
        if "bleed" in m.dots:
            break
    assert "bleed" in m.dots
    hp = m.monster.hp
    run(w, 1.0)
    assert m.monster.hp < hp and m.state != "hit_recovery"


def test_combat_rhythm_stacks_attack_speed(data):
    s, w, p, m = arena(data, level=1)
    s.skills.set_level("combat_rhythm", 5)
    base = w.attack_speed(p)
    m.monster.defense = 0
    for _ in range(3):
        w._after_blow(p, m, s.gear.main_hand, None)
    assert p.rhythm_stacks == 3
    assert w.attack_speed(p) == pytest.approx(base + 3 * s.skills.effect("combat_rhythm", "attackSpeedPerStackPct"))
    run(w, 2.5)
    assert p.rhythm_stacks == 0, "falls off without hits"


def test_killing_blow_and_colossus_bonuses(data):
    s, w, p, m = arena(data)
    s.skills.set_level("killing_blow", 5)
    atk = player_attack(s.character, s.skills, s.gear)
    assert w._hit_bonuses(p, m, atk).crit_chance == pytest.approx(atk.crit_chance)
    m.monster.hp = m.monster.max_hp * 0.2
    assert w._hit_bonuses(p, m, atk).crit_chance == pytest.approx(atk.crit_chance + s.skills.effect("killing_blow", "critChanceLowLife"))
    life = s.character.max_life
    s.skills.set_level("colossus", 5)
    assert s.character.max_life == pytest.approx(life * (1 + s.skills.effect("colossus", "maxLifePct")))
    p.life = p.max_life * 0.45  # 5 tenths missing
    extra = w._hit_bonuses(p, m, atk).damage_bonus_pct - atk.damage_bonus_pct
    assert extra == pytest.approx(5 * s.skills.effect("colossus", "damagePerMissingLife10Pct"))


def test_riposte_after_a_block(data):
    s, w, p, m = arena(data)
    s.skills.set_level("riposte", 5)
    p.guarding, p.guard_time = True, 1.0
    w._hit_player(p, hard_hit(p, 0.05), INFO, m.x, m.y, magic=False, melee=None)
    assert p.riposte_ready
    atk = player_attack(s.character, s.skills, s.gear)
    assert w._hit_bonuses(p, m, atk).damage_bonus_pct == pytest.approx(atk.damage_bonus_pct + s.skills.effect("riposte", "damageAfterBlockPct"))
    w._after_blow(p, m, s.gear.main_hand, None)
    assert not p.riposte_ready


def test_elemental_shots_ignore_armor_and_meet_resistance(data):
    s, w, p, m = arena(data)
    p.god_mode = False
    shot = hard_hit(p, 0.2)
    life = p.life
    w._hit_player(p, shot, INFO, m.x, m.y, magic=True, melee=None, element="lightning")
    plain = life - p.life
    assert plain == pytest.approx(p.max_life * 0.2), "armor does nothing against lightning"
    p.life = p.max_life
    s.skills.set_level("elemental_resistance", 5)
    s.skills.set_level("bastion", 5)
    res = w.player_resist(p, "lightning")
    assert res == pytest.approx(s.skills.effect("elemental_resistance", "elementalResist") + s.skills.effect("bastion", "allResistWithShield"))
    w._hit_player(p, shot, INFO, m.x, m.y, magic=True, melee=None, element="lightning")
    assert p.max_life - p.life == pytest.approx(plain * (1 - res))
    assert data.monster_type("shaman").projectile.element == "lightning"


def test_second_wind_heals_once_when_life_runs_low(data):
    s, w, p, m = arena(data)
    s.skills.set_level("second_wind", 5)
    p.life = p.max_life * 0.3
    w._lose_life(p, p.max_life * 0.1)
    assert p.life > p.max_life * 0.25, "healed"
    p.life = p.max_life * 0.3
    w._lose_life(p, p.max_life * 0.1)
    assert p.life == pytest.approx(p.max_life * 0.2), "only once per cooldown"


def test_unshakable_spreads_part_of_a_hit_over_time(data):
    s, w, p, m = arena(data)
    s.skills.set_level("unshakable", 10)
    share = s.skills.effect("unshakable", "deferredDamagePct")
    w._hit_player(p, hard_hit(p, 0.2), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    now = p.max_life - p.life
    assert now == pytest.approx(p.max_life * 0.2 * (1 - share))
    m.base_speed = 0
    w.monsters.clear()
    run(w, s.skills.effect("unshakable", "deferSeconds") + 0.1)
    regen = s.stat("lifeRegenPerSecond") * 3.1
    assert p.max_life - p.life == pytest.approx(p.max_life * 0.2 - regen, abs=0.5)


def test_every_mvp_node_is_implemented(data):
    from aethelgard.core.world import is_implemented

    missing = [n.id for n in data.skills.nodes if not is_implemented(n.id)]
    assert not missing, missing


# ---------------------------------------------------------------- item bonuses


def test_item_bonuses_count_in_combat_and_on_the_sheet(data):
    s, w, p, m = arena(data)
    atk = player_attack(s.character, s.skills, s.gear)
    life, speed, ias = s.character.max_life, w.player_speed(p), w.attack_speed(p)
    wear(s, with_mods(data, "ring", maxLife=20, str=5), "ring1")
    wear(s, with_mods(data, "boots", moveSpeedPct=0.2), "feet")
    wear(s, with_mods(data, "gloves", attackSpeedPct=0.1), "hands")
    assert s.character.attributes.str == 10 + 5
    assert s.character.max_life == pytest.approx(life + 20)
    assert w.player_speed(p) == pytest.approx(speed * 1.2)
    assert w.attack_speed(p) == pytest.approx(ias + 0.1)
    assert player_attack(s.character, s.skills, s.gear).damage_bonus_pct == pytest.approx(atk.damage_bonus_pct + 5 * data.balance.attributes.damage_per_str)


def test_plus_skills_items_raise_only_skills_you_have(data):
    s, w, p, m = arena(data)
    s.grant_skill("precise_strike")
    wear(s, with_mods(data, "amulet", bladesSkills=2), "neck")
    assert s.skills.points_in("precise_strike") == 3
    assert s.skills.points_in("charge") == 0, "a skill you do not have stays at 0"


def test_recovery_and_cooldown_bonuses_from_items(data):
    s, w, p, m = arena(data)
    wear(s, with_mods(data, "leather_armor", fasterHitRecovery=0.5), "body")
    assert s.stat("fasterHitRecovery") == pytest.approx(0.5)
    wear(s, with_mods(data, "amulet", cooldownReduction=0.2), "neck")
    s.grant_skill("shield_bash")
    w.apply(UseSkill(0, "shield_bash", 1))
    run(w, 0.7)
    assert p.cooldowns.remaining("shield_bash") <= data.node("shield_bash").cooldown * 0.8


def test_fire_damage_on_a_weapon_is_resisted_like_a_spell(data):
    s, w, p, m = arena(data, level=1)
    m.monster.type = data.monster_type("zombie")  # weak to fire
    s.gear.slots["mainhand"] = with_mods(data, "short_sword", fireDamage=10)
    s._gear_changed()
    m.monster.defense = 0
    hp = m.monster.hp
    for _ in range(30):
        w.strike(p, m, None, ("main",))
        if m.monster.hp < hp:
            break
    assert hp - m.monster.hp >= 10 * 1.25 - 1e-6


def test_movement_with_items_is_faster_on_the_map(data):
    s, w, p, m = arena(data)
    w.monsters.clear()
    from aethelgard.core.world import MoveTo

    def walked():
        p.x, p.y = 1.0, 5.0
        w.apply(MoveTo(0, 11.0, 5.0))
        for _ in range(round(0.4 / DT)):
            w.step(DT)
        return p.x - 1.0

    plain = walked()
    wear(s, with_mods(data, "boots", moveSpeedPct=0.5), "feet")
    assert walked() == pytest.approx(plain * 1.5, rel=0.1)


# ---------------------------------------------------------------- belt, Scroll of Forgetting, grid


def test_the_belt_limits_potions(data):
    from aethelgard.core.items import PotionDrop

    s, w, p, m = arena(data)
    cap = data.items.belt_without
    for _ in range(cap):
        assert s.collect(PotionDrop("life"))
    assert not s.collect(PotionDrop("life")), "no belt: full"
    wear(s, Item.plain(data.base("belt")), "waist")
    assert s.carry_limit("life_potion") == cap + data.base("belt").belt_slots
    assert s.collect(PotionDrop("life"))


def test_scroll_of_forgetting_gives_a_tree_back(data):
    from aethelgard.core.world import ForgetTree

    s, w, p, m = arena(data)
    s.proficiencies.set_xp("blades", data.xp_total_by_level[4])
    s.allocate_skill("precise_strike")
    s.allocate_skill("sharp_blade")
    assert s.skills.available("blades") == 3
    assert "Scroll" in w.apply(ForgetTree(0, "blades"))
    s.consumables["respec_scroll"] = 1
    assert w.apply(ForgetTree(0, "blades")) is None
    assert s.skills.available("blades") == 5 and s.consumables["respec_scroll"] == 0
    assert s.skill_bar.slot_of("precise_strike") is None
    assert data.items.consumables["respec_scroll"].price == 0, "found, never bought"


def test_items_take_their_size_in_the_grid(data):
    from aethelgard.core.items import Inventory

    inv = Inventory(4, 3)
    armor = Item.plain(data.base("leather_armor"))  # 2 × 3
    assert inv.add(armor) and inv.pos == [(0, 0)]
    assert inv.add(Item.plain(data.base("helm"))) and inv.pos[1] == (2, 0)
    assert not inv.can_add(Item.plain(data.base("leather_armor")))
    assert inv.item_at(1, 2) == 0 and inv.item_at(3, 1) == 1 and inv.item_at(3, 2) is None
    assert not inv.move(1, 1, 0), "overlaps the armor"
    assert inv.move(1, 2, 1)
    back = Inventory(4, 3)
    back.load_list(data, inv.to_list())
    assert back.pos == inv.pos, "places survive a save"
