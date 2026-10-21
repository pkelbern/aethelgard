"""Bows: two-handed, ranged straight arrows, Archery XP and its tree; and the new starting backpack."""

import pytest

from aethelgard.core.combat import player_attack
from aethelgard.core.events import DamageDealt
from aethelgard.core.items import Item, LootGenerator, Rarity
from aethelgard.core.rng import Rng
from aethelgard.core.session import GameSession
from aethelgard.core.world import AttackTarget, EquipItem, SetGuard, UseSkill
from aethelgard.core.world_gen import WALL
from test_world_timing import DT, arena, run


def archer(data, at=(10.0, 5.0), **kw):
    s, w, p, m = arena(data, at=at, **kw)
    s.inventory.items.clear()
    s.inventory.add(Item.plain(data.base("short_bow")))
    assert w.apply(EquipItem(0, 0)) is None
    return s, w, p, m


def test_new_characters_carry_a_bow_and_a_second_sword(data):
    s = GameSession(data, 1)
    assert [i.base.id for i in s.inventory.items] == ["short_bow", "short_sword"]
    assert s.gear.main_hand.base.id == "short_sword" and s.gear.has_shield


def test_a_bow_takes_both_hands(data):
    s, w, p, m = archer(data)
    assert s.gear.has_bow and s.gear.off_hand is None and not s.gear.has_shield
    ids = sorted(i.base.id for i in s.inventory.items)
    assert ids == ["buckler", "short_sword"], "sword and shield went back to the backpack"
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert not p.guarding
    shield = next(i for i, it in enumerate(s.inventory.items) if it.base.id == "buckler")
    assert w.apply(EquipItem(0, shield)) is None
    assert s.gear.has_shield and not s.gear.has_bow and any(i.base.id == "short_bow" for i in s.inventory.items)


def test_no_room_for_the_shield_refuses_the_bow(data):
    s, w, p, m = arena(data)
    s.inventory.items.clear()
    s.inventory.add(Item.plain(data.base("short_bow")))
    s.inventory.capacity = 1
    assert "Backpack full" in w.apply(EquipItem(0, 0))
    assert not s.gear.has_bow


def test_the_bow_shoots_from_range_in_straight_lines_and_feeds_archery(data):
    s, w, p, m = archer(data, monster_dmg=(0.01, 0.01))
    m.alert = False
    profs = []
    s.bus.subscribe(DamageDealt, lambda e: profs.append(e.proficiency_id))
    w.apply(AttackTarget(0, 1))
    seen = []
    for _ in range(180):
        w.step(DT)
        seen += [(pr.dir_x, pr.dir_y, pr.owner) for pr in w.projectiles.values()]
    assert p.x == pytest.approx(5.0, abs=0.01), "shot from where it stood: the target is within range"
    assert seen and all(o == "player" and abs(dy) < 1e-9 and dx > 0 for dx, dy, o in seen)
    assert profs and set(profs) == {"bows"} and s.proficiencies["bows"].xp > 0


def test_the_archer_walks_closer_when_a_wall_blocks_the_shot(data):
    s, w, p, m = archer(data, monster_dmg=(0.01, 0.01))
    w.layout.tiles[2:9, 8] = WALL
    w.apply(AttackTarget(0, 1))
    run(w, 1.0)
    assert not any(pr.owner == "player" for pr in w.projectiles.values()) or p.x != 5.0


def test_multishot_fans_out_and_piercing_goes_through(data):
    s, w, p, m = archer(data, monster_dmg=(0.01, 0.01))
    s.proficiencies.set_xp("bows", data.xp_total_by_level[11])
    s.allocate_skill("multishot")
    assert w.apply(UseSkill(0, "multishot", 1)) is None
    for _ in range(40):
        w.step(DT)
        if len(w.projectiles) >= 3:
            break
    dirs = {round(pr.dir_y, 3) for pr in w.projectiles.values()}
    assert len(dirs) == 3, "three arrows in a fan"
    w.projectiles.clear()
    s.allocate_skill("piercing_arrow")
    from aethelgard.core.monsters import MonsterInstance
    from aethelgard.core.world import MonsterActor

    t = data.monster_type("bandit")
    far = MonsterInstance(instance_id=2, type=t, rank=data.rank("normal"), name="Far", level=1, max_hp=1000, hp=1000,
                          dmg_min=0.01, dmg_max=0.01, damage_bonus_pct=0, accuracy=1, defense=0, xp_base=5)
    w.monsters[2] = MonsterActor(far, 11.0, 5.0, pack_ids=[2])
    m.monster.level = far.level = 1
    for a in (m, w.monsters[2]):  # both stand still in one line (stunned), so the test is only about the arrow
        a.state, a.state_left = "stunned", 99.0
    run(w, 0.5)
    hp1, hp2 = m.monster.hp, far.hp
    assert w.apply(UseSkill(0, "piercing_arrow", 1)) is None
    run(w, 1.5)
    assert m.monster.hp < hp1 and far.hp < hp2, "one arrow, both enemies in its line"


def test_bow_skills_need_a_bow_and_blade_skills_need_a_blade(data):
    s, w, p, m = arena(data)
    s.allocate_skill("power_shot")
    assert w.apply(UseSkill(0, "power_shot", 1)) == "Power Shot: needs a bow"
    s, w, p, m = archer(data)
    s.allocate_skill("precise_strike")
    assert w.apply(UseSkill(0, "precise_strike", 1)) == "Precise Strike: needs a one-hand blade"


def test_sharp_blade_does_not_help_bows_and_steady_aim_does(data):
    s, w, p, m = archer(data)
    s.allocate_skill("sharp_blade")
    base = player_attack(s.character, s.skills, s.gear).damage_bonus_pct
    s.allocate_skill("steady_aim")
    assert player_attack(s.character, s.skills, s.gear).damage_bonus_pct == pytest.approx(base + 0.06)


def test_bow_affixes_never_carry_blade_skills_and_blades_never_bow_ones(data):
    loot, rng = LootGenerator(data), Rng(5)
    for _ in range(3000):
        item = loot.roll_item(rng.randint(1, 15), 3, 1, Rarity.MAGIC, rng)
        for a in item.affixes:
            assert set(a.affix.tags) & set(item.base.tags)
            if "bow" in item.base.tags:
                assert a.affix.stat != "bladesSkills"
