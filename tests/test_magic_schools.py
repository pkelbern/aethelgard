"""Cryomancy (cold: slows and freezes), Electromancy (lightning: wild rolls, chains, storms) and Restoration
(heals, holy bolts, blessings): the deliveries and after-effects of core/spells.py for every element."""

import pytest

from aethelgard.core.events import DamageDealt, XpGained
from aethelgard.core.world import UseSkill
from test_ranged_and_leaders import add_monster
from test_world_timing import DT, arena, run

LUCKY = type("Lucky", (), {"chance": staticmethod(lambda x: x > 0), "uniform": staticmethod(lambda a, b: b),
                           "randint": staticmethod(lambda a, b: a)})()


def mage(data, skill, level=1, **kw):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), **kw)
    s.grant_skill(skill, level)
    p.reset_vitals()
    return s, w, p, m


def cast(w, skill, *target, seconds=1.0):
    r = w.apply(UseSkill(0, skill, *target))
    run(w, seconds)
    return r


# ---------------------------------------------------------------- cold


def test_ice_bolt_slows_and_the_slow_wears_off(data):
    s, w, p, m = mage(data, "ice_bolt", at=(9.0, 5.0))
    m.base_speed = 0.0  # stays where it is
    speed = m.monster.type.move_speed
    assert cast(w, "ice_bolt", 1) is None
    assert m.monster.hp < 1000 and m.chill_left > 0 and m.chill_pct == pytest.approx(0.3)
    assert m.stat("moveSpeedPct") == pytest.approx(-0.3) and m.stat("attackSpeedPct") == pytest.approx(-0.3)
    assert s.proficiencies["cryomancy"].xp > 0 and s.skills.xp["ice_bolt"] > 0
    run(w, 2.5)
    assert m.chill_left == 0 and m.stat("moveSpeedPct") == 0 and m.monster.type.move_speed == speed


def test_frost_nova_needs_no_aim_and_hits_all_around(data):
    s, w, p, m = mage(data, "frost_nova", at=(3.5, 5.0))
    other = add_monster(w, data, "bandit", 6.5, 6.0, 2)
    far = add_monster(w, data, "bandit", 10.0, 10.0, 3)
    assert cast(w, "frost_nova") is None
    assert m.monster.hp < 1000 and other.monster.hp < 1000 and far.monster.hp == 1000
    assert m.chill_left > 0 and other.chill_left > 0


def test_deep_freeze_freezes_cold_hits(data):
    s, w, p, m = mage(data, "ice_bolt", at=(8.0, 5.0))
    s.grant_skill("deep_freeze", 3)
    s.combat_rng = LUCKY
    cast(w, "ice_bolt", 1, seconds=0.8)
    assert m.state == "stunned", "frozen solid (the same state as a stun)"


def test_blizzard_drops_shards_on_random_spots_of_its_circle(data):
    from aethelgard.core.events import SpellBurst

    s, w, p, m = mage(data, "blizzard", at=(9.0, 5.0))
    m.base_speed = 0.0
    shards = []
    s.bus.subscribe(SpellBurst, shards.append)
    assert cast(w, "blizzard", None, 9.0, 5.0, seconds=3.5) is None
    radius = s.skills.effect("blizzard", "radiusTiles")
    assert len(shards) >= 8 and all((e.x - 9.0) ** 2 + (e.y - 5.0) ** 2 <= radius**2 + 1e-6 for e in shards)
    assert len({(round(e.x, 2), round(e.y, 2)) for e in shards}) > 5, "spread over the circle, not on one point"
    assert m.monster.hp < 1000, "some shards land on it"
    fires = [f for f in w.spells.fires.values() if f.skill == "blizzard"]
    p.cooldowns.tick(99)
    p.energy = 999.0
    cast(w, "blizzard", None, 7.0, 5.0, seconds=0.6)
    assert len([f for f in w.spells.fires.values() if f.skill == "blizzard"]) <= 1 and fires, "casting again moves it: one at a time"


def test_glacial_spike_freezes_what_it_bursts_on(data):
    s, w, p, m = mage(data, "glacial_spike", at=(9.0, 5.0))
    m.base_speed = 0.0
    states = set()
    w.apply(UseSkill(0, "glacial_spike", 1))
    for _ in range(90):
        w.step(DT)
        states.add(m.state)
    assert "stunned" in states and m.monster.hp < 1000


# ---------------------------------------------------------------- lightning


def test_chain_lightning_jumps_to_different_enemies(data):
    s, w, p, m = mage(data, "chain_lightning", at=(8.0, 5.0))
    for k, (x, y) in enumerate(((10.0, 5.0), (10.0, 7.0), (8.0, 8.0)), 2):
        add_monster(w, data, "bandit", x, y, k)
    for a in w.monsters.values():
        a.base_speed = 0.0
    hits = []
    s.bus.subscribe(DamageDealt, hits.append)
    assert cast(w, "chain_lightning", 1, seconds=1.5) is None
    ids = [e.target.instance_id for e in hits]
    assert len(ids) == 3 and len(set(ids)) == 3, "the first enemy and 2 jumps, never the same one twice"


def test_overcharge_doubles_lightning_damage(data):
    plain, w, p, m = mage(data, "spark", at=(8.0, 5.0))
    plain.combat_rng = type("Fixed", (), {"chance": staticmethod(lambda x: False), "uniform": staticmethod(lambda a, b: b)})()
    cast(w, "spark", 1)
    normal = 1000 - m.monster.hp
    s, w, p, m = mage(data, "spark", at=(8.0, 5.0))
    s.grant_skill("overcharge", 5)
    s.combat_rng = LUCKY
    cast(w, "spark", 1)
    assert 1000 - m.monster.hp == pytest.approx(2 * normal)


def test_thunderstorm_strikes_enemies_inside_over_time(data):
    s, w, p, m = mage(data, "thunderstorm", at=(9.0, 5.0))
    m.base_speed = 0.0
    hits = []
    s.bus.subscribe(DamageDealt, hits.append)
    assert cast(w, "thunderstorm", None, 9.0, 5.0, seconds=3.0) is None
    assert len(hits) >= 3 and all(e.skill_id == "thunderstorm" for e in hits)


# ---------------------------------------------------------------- restoration


def test_heal_heals_and_teaches_only_when_needed(data):
    s, w, p, m = mage(data, "heal", at=(7.0, 5.0))
    xp = []
    s.bus.subscribe(XpGained, xp.append)
    assert cast(w, "heal", seconds=1.6) is None
    assert not [e for e in xp if e.proficiency_id == "restoration"], "nothing to heal: nothing learned"
    p.life = p.max_life * 0.5
    cast(w, "heal", seconds=1.6)
    assert p.life == pytest.approx(p.max_life * (0.5 + 0.12 + 0.02), abs=1.0), "12% + 2% per level"
    assert s.proficiencies["restoration"].xp > 0 and s.skills.xp["heal"] > 0


def test_holy_bolt_hurts_the_undead_more(data):
    s, w, p, m = mage(data, "holy_bolt", at=(8.0, 5.0))
    s.combat_rng = LUCKY
    cast(w, "holy_bolt", 1)
    living = 1000 - m.monster.hp
    s, w, p, m = mage(data, "holy_bolt", at=(8.0, 5.0))
    del w.monsters[1]
    z = add_monster(w, data, "zombie", 8.0, 5.0, 1)
    s.combat_rng = LUCKY
    cast(w, "holy_bolt", 1)
    assert 1000 - z.monster.hp == pytest.approx(living * 1.5)


def test_blessing_of_might_and_devotion(data):
    s, w, p, m = mage(data, "blessing_of_might", at=(10.0, 10.0))
    before = w.attack_speed(p)
    assert cast(w, "blessing_of_might", seconds=0.5) is None
    assert p.buffs["blessing_of_might"] == pytest.approx(21 - 0.25, abs=0.1), "20 s + 1 s per level"
    assert w.attack_speed(p) == pytest.approx(before + 0.06) and p.buff_total("buffDamagePct") == pytest.approx(0.17)
    s.grant_skill("devotion", 4)
    w.apply_self_skill(p, "blessing_of_might")
    assert p.buffs["blessing_of_might"] == pytest.approx(21 * 1.2)


def test_divine_aegis_and_grace_restore_life(data):
    s, w, p, m = mage(data, "divine_aegis", at=(11.0, 11.0))
    p.life = 10.0
    w.apply_self_skill(p, "divine_aegis")
    run(w, 1.0)
    assert p.life == pytest.approx(10.0 + 3.5, abs=0.2), "3 per second + 0.5 per level"
    s.grant_skill("grace", 5)
    p.life = 10.0
    run(w, 1.0)
    assert p.life == pytest.approx(10.0 + 3.5 + 2.0, abs=0.2)
