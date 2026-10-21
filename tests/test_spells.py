"""Spells (Pyromancy): unlocked in the tree or cast from a wand, cast in place, fire damage that always hits and is resisted."""

import pytest

from aethelgard.core.events import SpellBurst
from aethelgard.core.items import Item
from aethelgard.core.progression import Allocate
from aethelgard.core.session import GameSession
from aethelgard.core.spells import effective_resist, segment_distance, spell_attack
from aethelgard.core.world import UseSkill
from test_ranged_and_leaders import add_monster
from test_world_timing import DT, arena, run


def wear_wand(s, base_id="ember_wand"):
    """Puts the wand in the off hand (the bigger wands' Intelligence requirement is skipped here)."""
    s.inventory.add(Item.plain(s.data.base(base_id)))
    i = next(i for i, it in enumerate(s.inventory.items) if it.base.id == base_id)
    if s.missing_requirements(s.inventory.items[i]):
        s.gear.equip(s.inventory.items.pop(i), "offhand")
        s._gear_changed()
    else:
        s.equip_from_backpack(i, "offhand")


def pyro_arena(data, base_id="ember_wand", **kw):
    s, w, p, m = arena(data, **kw)
    wear_wand(s, base_id)
    p.reset_vitals()
    return s, w, p, m


def level_up(s, tree):
    """Exactly one level more, through the normal XP path (so the level-up event is published)."""
    prof = s.proficiencies[tree]
    s.proficiencies._give(prof, s.data.xp_total_by_level[prof.level] - prof.xp, "test")


# ---------------------------------------------------------------- wands and the tree


def test_a_worn_wand_lets_you_cast_its_spell_and_puts_it_on_the_bar(data):
    s = GameSession(data, 1)
    assert s.skills.points_in("fire_bolt") == 0
    wear_wand(s)
    assert s.skills.points_in("fire_bolt") == 1, "the wand's spellLevel"
    assert s.skill_bar.slot_of("fire_bolt") is not None
    s.unequip("offhand")
    assert s.skills.points_in("fire_bolt") == 0 and s.skill_bar.slot_of("fire_bolt") is None, "not unlocked: gone with the wand"


def test_spells_are_unlocked_in_the_tree_without_any_wand(data):
    s = GameSession(data, 1)
    assert s.skills.allocate("fireball") is Allocate.LAYER_LOCKED
    assert s.allocate_skill("fire_bolt") is Allocate.OK and s.skills.points_in("fire_bolt") == 1
    assert s.skill_bar.slot_of("fire_bolt") is not None
    s.proficiencies.set_xp("pyromancy", data.xp_total_by_level[5])
    assert s.skills.allocate("fireball") is Allocate.OK, "Fire Bolt opens Fireball; no wand needed"
    wear_wand(s)
    assert s.skills.points_in("fire_bolt") == 2, "a wand adds its level on top"


def test_casting_from_a_wand_before_unlocking_still_trains_the_spell(data):
    s, w, p, m = pyro_arena(data)
    assert w.apply(UseSkill(0, "fire_bolt", 1)) is None
    run(w, 1.5)
    assert s.skills.xp.get("fire_bolt", 0) > 0 and s.skills.own_level("fire_bolt") == 0
    assert s.allocate_skill("fire_bolt") is Allocate.OK, "the XP waits for the unlock"


# ---------------------------------------------------------------- casting


def test_fire_bolt_always_hits_ignores_armor_and_feeds_pyromancy(data):
    s, w, p, m = pyro_arena(data, at=(10.0, 5.0))
    m.monster.defense = 1e9  # no blow could hit it
    hp = m.monster.hp
    energy = p.energy
    assert w.apply(UseSkill(0, "fire_bolt", 1)) is None
    run(w, 1.5)
    assert m.monster.hp < hp and s.proficiencies["pyromancy"].xp > 0
    assert p.energy < energy and p.x == 5.0, "cast from where you stand, for Energy"


def test_spell_damage_grows_with_intelligence_and_kindling(data):
    s, w, p, m = pyro_arena(data)
    base = spell_attack(s, "fire_bolt")
    s.proficiencies.set_xp("pyromancy", data.xp_total_by_level[1])  # +3 INT, a point for Kindling
    assert s.skills.allocate("kindling") is Allocate.OK
    more = spell_attack(s, "fire_bolt")
    assert more.damage_bonus_pct == pytest.approx(base.damage_bonus_pct + 3 * data.balance.attributes.spell_damage_per_int + 0.07)


def test_resistance_takes_its_share_and_searing_heat_pierces_it(data):
    assert effective_resist(0.2, 0.0, 0.75) == pytest.approx(0.2)
    assert effective_resist(0.2, 0.15, 0.75) == pytest.approx(0.05)
    assert effective_resist(0.2, 0.5, 0.75) == 0, "pierce never makes a resistance a weakness"
    assert effective_resist(-0.25, 0.5, 0.75) == pytest.approx(-0.25), "the dead still burn"
    assert effective_resist(0.9, 0.0, 0.75) == pytest.approx(0.75)
    s, w, p, m = pyro_arena(data)
    m.monster.type = data.monster_type("zombie")
    assert w.spells.resisted(p, m, 100, "fire") == pytest.approx(125)
    m.monster.type = data.monster_type("shaman")
    assert w.spells.resisted(p, m, 100, "fire") == pytest.approx(80)


def test_fireball_bursts_on_everyone_around_the_first_enemy(data):
    s, w, p, m = pyro_arena(data, "cinder_wand", at=(10.0, 5.0))
    near = add_monster(w, data, "bandit", 10.0, 6.2, 2)
    far = add_monster(w, data, "bandit", 10.0, 9.0, 3)
    bursts = []
    s.bus.subscribe(SpellBurst, bursts.append)
    hp = (m.monster.hp, near.monster.hp, far.monster.hp)
    assert w.apply(UseSkill(0, "fireball", 1)) is None
    run(w, 1.5)
    assert len(bursts) == 1 and bursts[0].look == "fireball"
    assert m.monster.hp < hp[0] and near.monster.hp < hp[1], "both inside the burst"
    assert far.monster.hp == hp[2]


def test_fireball_bursts_where_its_flight_ends(data):
    s, w, p, m = pyro_arena(data, "cinder_wand", at=(1.0, 1.0))
    bursts = []
    s.bus.subscribe(SpellBurst, bursts.append)
    assert w.apply(UseSkill(0, "fireball", None, 5.0, 11.0)) is None  # straight down, into the far wall
    run(w, 2.5)
    assert len(bursts) == 1 and not w.projectiles


def test_fire_wall_burns_whoever_stands_in_it_until_it_dies_down(data):
    s, w, p, m = pyro_arena(data, "blaze_wand", at=(8.0, 5.0))
    m.monster.type = data.monster_type("bandit")
    m.base_speed = 0.0  # stays in the fire
    assert w.apply(UseSkill(0, "fire_wall", 1)) is None
    run(w, 0.4)
    (fire,) = w.spells.fires.values()
    assert segment_distance(8.0, 5.0, fire.ax, fire.ay, fire.bx, fire.by) == pytest.approx(0)
    assert abs(fire.ax - fire.bx) < 1e-6, "across the aim (the aim is along x)"
    hp = m.monster.hp
    run(w, 1.0)
    assert m.monster.hp < hp
    run(w, data.node("fire_wall").base["durationSeconds"])
    assert not w.spells.fires


def test_meteor_lands_after_its_delay_and_leaves_the_ground_burning(data):
    s, w, p, m = pyro_arena(data, "star_wand", at=(9.0, 5.0))
    hp = m.monster.hp
    m.base_speed = 0.0
    assert w.apply(UseSkill(0, "meteor", 1)) is None
    run(w, 0.6)
    assert m.monster.hp == hp and w.spells.fires, "still falling"
    run(w, data.node("meteor").base["delaySeconds"])
    landed = m.monster.hp
    assert landed < hp
    run(w, 1.0)
    assert m.monster.hp < landed, "the ground burns"


def test_ignite_keeps_burning_after_the_hit(data):
    s, w, p, m = pyro_arena(data, at=(10.0, 5.0))
    s.proficiencies.set_xp("pyromancy", data.xp_total_by_level[5])  # level 6
    s.grant_skill("ignite")
    w.apply(UseSkill(0, "fire_bolt", 1))
    run(w, 1.0)
    assert m.burn_left > 0
    hp = m.monster.hp
    run(w, 1.0)
    assert m.monster.hp < hp and m.state != "hit_recovery", "burning hurts but does not stagger"


def test_quick_casting_shortens_the_cast(data):
    s, w, p, m = pyro_arena(data)
    base = w.spells.cast_seconds(p)
    s.proficiencies.set_xp("pyromancy", data.xp_total_by_level[11])
    s.grant_skill("quick_casting")
    assert w.spells.cast_seconds(p) == pytest.approx(base / 1.04)


def test_no_casting_with_the_guard_up_or_in_town(data):
    s, w, p, m = pyro_arena(data)
    p.guarding = True
    assert "guard" in w.apply(UseSkill(0, "fire_bolt", 1))
    p.guarding = False
    w.area_id = data.town_id
    assert "town" in w.apply(UseSkill(0, "fire_bolt", 1))


def test_spells_reach_only_their_range(data):
    s, w, p, m = pyro_arena(data, "blaze_wand")
    p.aim_x, p.aim_y = 105.0, 5.0
    x, y = w.spells.aim_point(p, "fire_wall")
    assert (x, y) == pytest.approx((5.0 + data.node("fire_wall").base["rangeTiles"], 5.0))


# ---------------------------------------------------------------- data


def test_spells_in_the_data_match_the_world_and_wands_carry_spells(data):
    from aethelgard.core.world import SPELL_SKILLS

    spells = {n.id for n in data.skills.nodes if n.spell}
    assert spells == SPELL_SKILLS, "world.SPELL_SKILLS lists every skills.json node with 'spell'"
    wands = {b.spell for b in data.items.bases if b.spell}
    assert wands <= spells and {"fire_bolt", "ice_bolt", "spark", "heal"} <= wands
    assert "wand" in data.items.equipment["offhand"].accepts
    assert GameSession(data, 1).gear.granted_spells() == {}, "a new character starts with the wand in the backpack"


def test_pyromancy_titles(data):
    s = GameSession(data, 1)
    s.proficiencies.set_xp("pyromancy", data.xp_total_by_level[9])
    assert s.character.title == "Pyromancer"
    s.proficiencies.set_xp("blades", data.xp_total_by_level[7])
    assert s.character.title == "Arcane Blade"


def test_a_bolt_aimed_at_your_feet_flies_where_you_face(data):
    """A cast at a point on the player itself still flies (toward where the player faces)."""
    s, w, p, m = pyro_arena(data)
    w.apply(UseSkill(0, "fire_bolt", None, 5.0, 5.0))
    for _ in range(40):
        w.step(DT)
        if w.projectiles:
            break
    (proj,) = w.projectiles.values()
    assert (proj.dir_x, proj.dir_y) == pytest.approx((p.face_x, p.face_y))


def test_spell_tooltips_say_how_to_unlock_and_what_the_wand_adds(data):
    from aethelgard.core.describe import describe_item, describe_skill

    s = GameSession(data, 1)
    text = " ".join(x.text for x in describe_skill(data, s.skills, "fire_bolt"))
    assert "1 Pyromancy point unlocks it" in text
    wear_wand(s)
    text = " ".join(x.text for x in describe_skill(data, s.skills, "fire_bolt"))
    assert "+1 from your wand" in text
    wand = " ".join(x.text for x in describe_item(data, Item.plain(data.base("ember_wand"))))
    assert "+1 to Fire Bolt while worn" in wand


def test_fire_on_the_ground_never_staggers(data):
    s, w, p, m = pyro_arena(data, "blaze_wand", at=(8.0, 5.0), monster_hp=20)
    m.base_speed = 0.0
    w.spells.fires.clear()
    states = set()
    w.apply(UseSkill(0, "fire_wall", 1))
    for _ in range(150):
        w.step(DT)
        states.add(m.state)
    assert m.monster.hp < 20 and "hit_recovery" not in states


def test_ground_spells_need_a_clear_line_of_sight(data):
    from aethelgard.core.world_gen import WALL

    s, w, p, m = pyro_arena(data, "blaze_wand", at=(10.0, 5.0))
    w.layout.tiles[2:9, 7] = WALL
    assert "cannot see" in w.apply(UseSkill(0, "fire_wall", None, 9.0, 5.0))
    assert "cannot see" in w.apply(UseSkill(0, "fire_wall", None, 7.0, 5.0)), "not inside a wall either"
    s.grant_skill("fire_bolt")
    assert w.apply(UseSkill(0, "fire_bolt", None, 9.0, 5.0)) is None, "a bolt can be fired at a wall (it just stops there)"


def test_a_wand_in_the_main_hand_goes_with_a_shield(data):
    s, w, p, m = arena(data, at=(10.0, 5.0))
    s.inventory.add(Item.plain(data.base("ember_wand")))
    i = next(i for i, it in enumerate(s.inventory.items) if it.base.id == "ember_wand")
    r, msg = s.equip_from_backpack(i, "mainhand")
    assert r.value == "ok", msg
    assert s.gear.has_shield and s.gear.main_hand.base.id == "ember_wand" and not s.gear.dual_wielding
    assert w.skill_block_reason(p, "fire_bolt") is None
    from aethelgard.core.combat import player_attack
    a = player_attack(s.character, s.skills, s.gear)
    assert (a.dmg_min, a.dmg_max) == (1, 2), "a wand hits like a fist"
