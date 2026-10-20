"""Action bar (slots bound to skills) and tooltip text built from the data."""

import json

import pytest

from aethelgard.core.data import validate
from aethelgard.core.describe import Line, describe_skill, format_value, stat_line
from aethelgard.core.progression import Bind
from aethelgard.core.session import GameSession


def trained(data) -> GameSession:
    """Blades 9 and Shield 12: Wide Slash and Bulwark are unlocked."""
    s = GameSession(data, 1)
    s.proficiencies.set_xp("blades", data.xp_total_by_level[8])
    s.proficiencies.set_xp("shield", data.xp_total_by_level[11])
    return s


def texts(lines: list[Line], style: str | None = None) -> list[str]:
    return [x.text for x in lines if style is None or x.style == style]


# ---------------------------------------------------------------- fixed values (base) and next level


def test_fixed_values_do_not_grow_with_points(data):
    s = trained(data)
    s.allocate_skill("shield_bash")
    s.allocate_skill("shield_bash")
    t = s.skills
    assert t.effect("shield_bash", "stunSeconds") == pytest.approx(0.6), "the stun is fixed; points raise the damage"
    assert t.effect_at("wide_slash", "arcDegrees", 5) == pytest.approx(120)
    assert t.effect_at("wide_slash", "arcDegrees", 0) == 0, "nothing before the skill is learned"
    assert t.effect_at("bulwark", "durationSeconds", 1) == pytest.approx(3.2), "3 s base + 0.2 s per point"


def test_effect_at_previews_the_next_point_with_diminishing_returns(data):
    s = trained(data)
    t = s.skills
    assert t.effect_at("precise_strike", "damagePct", 8) == pytest.approx(0.15 * 8)
    assert t.effect_at("precise_strike", "damagePct", 9) == pytest.approx(0.15 * 8.5), "half value after the 8th point"
    s.allocate_skill("sharp_blade")
    assert t.effect_at("shield_bash", "damagePct", 1) == pytest.approx(0.10 + 0.06), "synergies count at any level"


# ---------------------------------------------------------------- action bar


def test_learned_actives_fill_the_bar_and_passives_do_not(data):
    s = trained(data)
    s.allocate_skill("sharp_blade")
    s.allocate_skill("precise_strike")
    s.allocate_skill("precise_strike")
    s.allocate_skill("wide_slash")
    assert s.skill_bar.slots == ["precise_strike", "wide_slash", None, None, None, None]
    assert len(s.skill_bar) == data.skills.bar_slots == 6


def test_binding_moves_and_swaps(data):
    s = trained(data)
    for node in ("precise_strike", "wide_slash", "bulwark"):
        s.allocate_skill(node)
    bar = s.skill_bar
    assert bar.bind(5, "bulwark") is Bind.OK
    assert bar.slots == ["precise_strike", "wide_slash", None, None, None, "bulwark"], "moved, old slot freed"
    assert bar.bind(0, "wide_slash") is Bind.OK
    assert bar.slots[:2] == ["wide_slash", "precise_strike"], "swap with what was there"
    bar.clear(0)
    assert bar.skill_at(0) is None and bar.slot_of("wide_slash") is None


def test_only_learned_actives_can_be_bound(data):
    s = trained(data)
    s.allocate_skill("sharp_blade")
    bar = s.skill_bar
    assert bar.bind(0, "sharp_blade") is Bind.NOT_ACTIVE
    assert bar.bind(0, "wide_slash") is Bind.NOT_LEARNED
    assert bar.bind(9, "wide_slash") is Bind.BAD_SLOT
    assert bar.bind(0, "nope") is Bind.NOT_ACTIVE


def test_full_bar_does_not_auto_bind(data):
    s = trained(data)
    s.skills.allocate("shield_bash")
    s.skill_bar.slots = ["a", "b", "c", "d", "e", "f"]  # stand-ins: a full bar
    assert s.skill_bar.auto_bind("shield_bash") is None
    assert s.skill_bar.slot_of("shield_bash") is None


def test_sync_drops_unlearned_skills_after_a_respec(data):
    s = trained(data)
    s.allocate_skill("precise_strike")
    s.allocate_skill("wide_slash")
    s.skills.respec("blades")
    s.skill_bar.sync()
    assert s.skill_bar.slots == [None] * 6


def test_bar_is_saved_exactly_and_old_saves_get_one(data):
    s = trained(data)
    s.allocate_skill("precise_strike")
    s.allocate_skill("bulwark")
    s.skill_bar.bind(3, "bulwark")
    s.skill_bar.clear(0)
    back = GameSession.load(data, s.save())
    assert back.skill_bar.slots == [None, None, None, "bulwark", None, None], "empty slots stay empty"
    old = json.loads(s.save())
    old["version"] = 2
    del old["skillBar"]
    v2 = GameSession.load(data, json.dumps(old))
    assert v2.skill_bar.slots[:2] == ["precise_strike", "bulwark"]


# ---------------------------------------------------------------- tooltip text


def test_values_are_formatted_from_stats_json(data):
    pct, sec, deg, count = (data.stat(x) for x in ("damagePct", "stunSeconds", "arcDegrees", "bounces"))
    assert format_value(pct, 0.15) == "+15%"
    assert format_value(pct, 0.15, signed=False) == "15%"
    assert format_value(sec, 0.6) == "0.6 s"
    assert format_value(deg, 120) == "120°"
    assert format_value(count, 2.4) == "2" and format_value(count, 0.2, exact=True) == "0.2"
    assert stat_line(pct, 0.3) == "+30% damage"
    assert stat_line(sec, 0.6) == "Stun: 0.6 s"


def test_skill_tooltip_explains_now_and_next_level(data):
    s = trained(data)
    s.allocate_skill("sharp_blade")
    s.allocate_skill("sharp_blade")
    s.allocate_skill("shield_bash")
    lines = describe_skill(data, s.skills, "shield_bash")
    assert lines[0] == Line("Shield Bash", "title")
    body = texts(lines, "body")[0]
    assert "22% more damage" in body and "0.6 s" in body, "10% + 2 × 6% from Sharp Blade, fixed stun"
    assert "Current level (1)" in texts(lines) and "Next level (2)" in texts(lines)
    assert "+32% damage   (+10%)" in texts(lines, "next")
    assert "Energy: 4  ·  Cooldown: 4 s" in texts(lines)
    assert any("per point in Sharp Blade" in t for t in texts(lines))


def test_tooltip_of_an_unlearned_or_locked_skill(data):
    s = GameSession(data, 1)  # level 1 everywhere
    lines = describe_skill(data, s.skills, "wide_slash", implemented=True)
    assert "Next level (1)" in texts(lines) and "Current level (0)" not in texts(lines)
    assert "Requires Blades level 6 (now 1)." in texts(lines, "warn")
    precise = describe_skill(data, s.skills, "precise_strike")
    assert "1 free Blades point to spend." in texts(precise, "good")
    assert "Energy: 3  ·  Cooldown: only the global 0.2 s" in texts(precise)
    soon = describe_skill(data, s.skills, "charge", implemented=False)
    assert any("Not in the prototype yet" in t for t in texts(soon, "warn"))


def test_maxed_skill_and_diminishing_note(data):
    s = trained(data)
    s.skills.points["sharp_blade"] = 8
    assert any("From point 9 on" in t for t in texts(describe_skill(data, s.skills, "sharp_blade")))
    s.skills.points["sharp_blade"] = 10
    lines = describe_skill(data, s.skills, "sharp_blade")
    assert "Maxed out." in texts(lines, "good") and not any(t.startswith("Next level") for t in texts(lines))


def test_every_node_has_a_tooltip_without_errors(data):
    s = trained(data)
    for n in data.skills.nodes:
        lines = describe_skill(data, s.skills, n.id)
        assert "{" not in texts(lines, "body")[0], f"placeholder left in {n.id}"


def test_validation_catches_missing_stats_and_bad_descriptions(fresh_data):
    fresh_data.node("precise_strike").description = "Hits for {nope}."
    fresh_data.node("sharp_blade").effects["madeUpStat"] = 1
    fresh_data.ranks.ranks[0].name = ""
    errors = validate(fresh_data)
    assert any("unknown stat '{nope}'" in e for e in errors)
    assert any("'madeUpStat', missing from stats.json" in e for e in errors)
    assert any("has no name" in e for e in errors)
