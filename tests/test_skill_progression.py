"""Skill trees: prerequisites, actives unlocked with a point and levelled only by use, gates on passives."""

import json

import pytest

from aethelgard.core.data import LevelGate, validate
from aethelgard.core.events import DamageDealt, MonsterXpInfo, SkillLeveledUp, SkillPracticed
from aethelgard.core.progression import Allocate
from aethelgard.core.session import GameSession
from aethelgard.core.world import UseSkill
from test_world_timing import arena, run


def at_level(data, tree, level, seed=1) -> GameSession:
    s = GameSession(data, seed)
    s.proficiencies.set_xp(tree, data.xp_total_by_level[level - 1])
    return s


def hit(s, skill, damage=50.0, monster_level=1, prof=None):
    """One hit with ``skill`` on a monster with 100 life and 100 XP."""
    prof = prof or s.data.node(skill).tree
    s.bus.publish(DamageDealt(prof, MonsterXpInfo(1, monster_level, 100, 100), damage, 0, skill))


# ---------------------------------------------------------------- prerequisites and points


def test_an_active_needs_the_one_before_it_and_costs_one_point(data):
    s = at_level(data, "bows", 12)
    t = s.skills
    assert t.allocate("multishot") is Allocate.REQUIRES
    assert t.missing_requirements("piercing_arrow") == ["multishot"]
    assert t.allocate("power_shot") is Allocate.OK and t.points_in("power_shot") == 1
    assert t.allocate("power_shot") is Allocate.BY_USE, "a second point does nothing: it grows by use"
    assert t.allocate("multishot") is Allocate.OK
    assert t.spent("bows") == 2 and t.available("bows") == 10


def test_passives_need_a_point_in_the_passive_before_them(data):
    s = at_level(data, "pyromancy", 12)
    t = s.skills
    assert t.allocate("ignite") is Allocate.REQUIRES
    assert t.allocate("kindling") is Allocate.OK and t.allocate("ignite") is Allocate.OK
    assert t.allocate("fire_bolt") is Allocate.OK, "actives and passives are separate chains"


def test_validation_rejects_bad_links(fresh_data):
    fresh_data.node("fireball").requires = ["power_shot"]
    fresh_data.node("fire_bolt").requires = ["meteor"]
    fresh_data.node("kindling").level_gates = [LevelGate(10, "ignite", 5)]
    fresh_data.node("meteor").level_gates = [LevelGate(10, "fire_bolt", 5)]
    errors = " | ".join(validate(fresh_data))
    assert "from another tree" in errors and "later layer" in errors
    assert "only actives have levelGates" in errors and "needs a passive node" in errors


def test_requirements_have_no_cycles(fresh_data):
    fresh_data.node("precise_strike").requires = ["whirlwind"]
    fresh_data.node("whirlwind").layer = 1
    fresh_data.node("charge").layer = fresh_data.node("wide_slash").layer = 1
    assert any("cycle" in e for e in validate(fresh_data))


# ---------------------------------------------------------------- levels by use


def test_an_active_grows_only_with_its_own_hits(data):
    s = at_level(data, "blades", 18)
    t = s.skills
    for node in ("precise_strike", "wide_slash"):
        t.allocate(node)
    for _ in range(40):
        hit(s, "precise_strike")
        hit(s, "", prof="blades")  # plain swings feed Blades, not the skills
    assert t.points_in("precise_strike") > 1
    assert t.points_in("wide_slash") == 1, "never used: still level 1 at Blades 18"
    assert s.proficiencies["blades"].xp > data.xp_total_by_level[17]


def test_skill_xp_follows_the_proficiency_formula_and_rate(data):
    s = at_level(data, "blades", 1)
    s.skills.allocate("precise_strike")
    hit(s, "precise_strike", damage=50.0)
    expected = s.xp.offense(MonsterXpInfo(1, 1, 100, 100), 50.0, 1) * data.skills.skill_xp_rate
    assert s.skills.xp["precise_strike"] == pytest.approx(expected)


def test_level_ups_are_announced(data):
    s = at_level(data, "blades", 1)
    s.skills.allocate("precise_strike")
    ups = []
    s.bus.subscribe(SkillLeveledUp, ups.append)
    s.skills.add_xp("precise_strike", data.xp_total_by_level[2])
    assert [(e.skill_id, e.level) for e in ups] == [("precise_strike", 3)]


def test_a_gate_holds_the_level_and_the_passive_opens_it(data):
    s = at_level(data, "pyromancy", 20)
    t = s.skills
    t.allocate("fire_bolt")
    t.add_xp("fire_bolt", data.xp_total_by_level[13])  # worth level 14
    assert t.xp_level("fire_bolt") == 14 and t.points_in("fire_bolt") == 9, "level 10 needs Kindling 5"
    assert t.gate_holding("fire_bolt").node == "kindling"
    ups = []
    s.bus.subscribe(SkillLeveledUp, ups.append)
    for _ in range(5):
        assert t.allocate("kindling") is Allocate.OK
    assert t.points_in("fire_bolt") == 14 and [e.level for e in ups] == [14], "the XP was kept: every level arrives at once"
    assert t.gate_holding("fire_bolt") is None


def test_no_cap_from_the_proficiency_level(data):
    s = at_level(data, "pyromancy", 1)
    s.skills.allocate("fire_bolt")
    s.skills.add_xp("fire_bolt", data.xp_total_by_level[8])
    assert s.skills.points_in("fire_bolt") == 9 > s.proficiencies["pyromancy"].level


def test_respec_gives_back_points_and_keeps_what_was_learned_by_use(data):
    s = at_level(data, "blades", 6)
    t = s.skills
    t.allocate("precise_strike")
    t.add_xp("precise_strike", data.xp_total_by_level[4])
    t.respec("blades")
    assert t.available("blades") == 6 and t.points_in("precise_strike") == 0
    t.allocate("precise_strike")
    assert t.points_in("precise_strike") == 5


def test_utility_actives_learn_from_being_used_near_monsters(data):
    s = at_level(data, "shield", 12)
    s.skills.allocate("shield_bash")
    s.skills.allocate("bulwark")
    s.bus.publish(SkillPracticed(0, "bulwark", MonsterXpInfo(1, 12, 100, 100), data.node("bulwark").use_xp))
    assert s.skills.xp["bulwark"] > 0


def test_war_cry_and_bulwark_practice_in_the_world(data):
    s, w, p, m = arena(data, at=(7.0, 5.0), level=12)
    s.proficiencies.set_xp("shield", data.xp_total_by_level[11])
    s.grant_skill("bulwark")
    xp = s.skills.xp["bulwark"]
    assert w.apply(UseSkill(0, "bulwark")) is None
    run(w, 0.3)
    assert s.skills.xp["bulwark"] > xp


def test_hits_in_the_world_feed_the_skill(data):
    s, w, p, m = arena(data, at=(6.0, 5.0), monster_dmg=(0.01, 0.01))
    s.grant_skill("precise_strike")
    hits = []
    s.bus.subscribe(DamageDealt, hits.append)
    for _ in range(5):  # a swing can miss
        assert w.apply(UseSkill(0, "precise_strike", 1)) is None
        run(w, 0.8)
    assert any(e.skill_id == "precise_strike" for e in hits) and s.skills.xp["precise_strike"] > 0


# ---------------------------------------------------------------- saves


def test_unlocks_and_skill_xp_survive_a_save(data):
    s = at_level(data, "pyromancy", 6)
    for node in ("fire_bolt", "kindling", "fireball"):
        s.allocate_skill(node)
    s.skills.add_xp("fire_bolt", 1234.0)
    back = GameSession.load(data, s.save())
    assert back.skills.unlocked == {"fire_bolt", "fireball"} and back.skills.points == {"kindling": 1}
    assert back.skills.xp["fire_bolt"] == pytest.approx(s.skills.xp["fire_bolt"])
    assert back.skills.points_in("fire_bolt") == s.skills.points_in("fire_bolt")


def test_version_5_saves_keep_their_levels_and_get_points_back(data):
    s = at_level(data, "pyromancy", 8)
    old = json.loads(s.save())
    old.update(version=5, skillPoints={"fire_bolt": 3, "kindling": 2}, learnedSpells=["fire_bolt"], spellsInTraining=["fireball"])
    for key in ("unlockedSkills", "skillXp"):
        del old[key]
    back = GameSession.load(data, json.dumps(old))
    t = back.skills
    assert t.unlocked == {"fire_bolt"} and t.points_in("fire_bolt") == 4, "3 points + learned: level 4, by XP now"
    assert t.points_in("kindling") == 2 and t.available("pyromancy") == 8 - 3, "only the unlock costs a point"
