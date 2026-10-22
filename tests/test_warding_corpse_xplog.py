"""Warding (the defense school, M3-12), the corpse with your gear (M1-04) and the XP log (M2-05)."""

import csv

import pytest

from aethelgard.core.events import MonsterXpInfo, XpGained
from aethelgard.core.items import Item
from aethelgard.core.session import GameSession
from aethelgard.core.world import UseSkill
from aethelgard.core.xp_log import XpLog
from test_remaining_skills import INFO, hard_hit
from test_world_timing import DT, arena, run


def cast(w, p, skill):
    w.apply(UseSkill(p.player_id, skill))
    run(w, 0.6)


# ---------------------------------------------------------------- Warding


def test_arcane_barrier_takes_hits_first_and_teaches_warding(data):
    s, w, p, m = arena(data, level=3, monster_dmg=(0.01, 0.01))
    m.attack_cooldown = 99
    s.grant_skill("arcane_barrier")
    cast(w, p, "arcane_barrier")
    pool = p.barrier
    assert pool == pytest.approx(p.max_life * s.skills.effect("arcane_barrier", "barrierPctOfMax"))
    life = p.life
    w._hit_player(p, hard_hit(p, 0.05), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    assert p.life == pytest.approx(life) and p.barrier < pool, "the barrier took it"
    assert s.proficiencies["warding"].xp > 0 and s.skills.xp.get("arcane_barrier", 0) > 0
    w._hit_player(p, hard_hit(p, 0.5), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    assert p.barrier == 0 and "arcane_barrier" not in p.buffs and p.life < life, "broken: the rest reaches life"


def test_fortify_and_mending_make_barriers_stronger(data):
    s, w, p, m = arena(data)
    s.grant_skill("arcane_barrier")
    s.skills.set_level("fortify", 5)
    s.skills.set_level("mending_ward", 5)
    cast(w, p, "arcane_barrier")
    base = p.max_life * s.skills.effect("arcane_barrier", "barrierPctOfMax")
    assert p.barrier_max == pytest.approx(base * (1 + s.skills.effect("fortify", "barrierStrengthPct")))
    p.barrier = 0.01
    run(w, 1.0)
    assert p.barrier > 0.01


def test_elemental_ward_thorns_and_sanctuary(data):
    s, w, p, m = arena(data, at=(6.0, 5.0), monster_dmg=(0.01, 0.01))
    for sk in ("elemental_ward", "thorn_ward", "sanctuary"):
        s.grant_skill(sk)
    res = w.player_resist(p, "cold")
    cast(w, p, "elemental_ward")
    assert w.player_resist(p, "cold") == pytest.approx(res + s.skills.effect("elemental_ward", "buffResist"))
    p.cooldowns.tick(99)
    cast(w, p, "thorn_ward")
    hp = m.monster.hp
    w._hit_player(p, hard_hit(p, 0.1), INFO, m.x, m.y, magic=False, melee=m)
    assert m.monster.hp < hp, "the attacker took part of it back"
    p.cooldowns.tick(99)
    x0 = m.x
    cast(w, p, "sanctuary")
    assert m.x > x0 + 1.0, "pushed away"
    life = p.life
    w._hit_player(p, hard_hit(p, 0.1), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    taken = life - p.life
    expected = p.max_life * 0.1 * (1 - w.player_resist(p, "fire")) * (1 + s.skills.effect("sanctuary", "buffDamageTakenPct"))
    assert taken == pytest.approx(expected, rel=0.01)


def test_mana_shield_takes_part_from_energy(data):
    s, w, p, m = arena(data)
    s.skills.set_level("mana_shield", 5)
    share = s.stat("manaShieldPct")
    life, energy = p.life, p.energy
    w._hit_player(p, hard_hit(p, 0.1), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    assert energy - p.energy == pytest.approx(p.max_life * 0.1 * share)
    assert life - p.life == pytest.approx(p.max_life * 0.1 * (1 - share))


# ---------------------------------------------------------------- corpse


def test_dying_leaves_the_gear_on_the_corpse_and_walking_over_it_takes_it_back(data):
    s, w, p, m = arena(data)
    worn = dict(s.gear.slots)
    p.life = 1
    w._hit_player(p, hard_hit(p, 0.5), INFO, m.x, m.y, magic=True, melee=None, element="fire")
    assert p.state == "dead" and not s.gear.slots and len(s.corpses) == 1
    spot = w.corpse_spot(s.corpses[0])
    w.monsters.clear()
    from aethelgard.core.world import Respawn

    w.apply(Respawn(0))
    s.gear.equip(Item.plain(data.base("long_dagger")), "mainhand")  # something picked up meanwhile
    p.x, p.y = spot[0] + 3, spot[1]
    w.step(DT)
    assert s.corpses, "not there yet"
    p.x, p.y = spot
    w.step(DT)
    assert not s.corpses and s.gear.slots == worn
    assert any(i.base.id == "long_dagger" for i in s.inventory.items), "what you wore meanwhile goes to the backpack"


def test_a_saved_corpse_waits_in_town(data):
    s = GameSession(data, 1)
    s.leave_corpse("crypt", 2, 10.0, 10.0)
    back = GameSession.load(data, s.save())
    assert len(back.corpses) == 1 and back.corpses[0].area_id == data.town_id and back.corpses[0].x is None
    assert set(back.corpses[0].gear) == set(data.items.starting_gear) or back.corpses[0].gear


# ---------------------------------------------------------------- XP log


def test_the_xp_log_writes_a_row_per_grant(data, tmp_path):
    s = GameSession(data, 1)
    log = XpLog(s.bus, tmp_path / "xp.csv", s.proficiencies, clock=lambda: 0.0)
    s.proficiencies._give(s.proficiencies["blades"], 12.5, "offense", MonsterXpInfo(7, 3, 50, 100))
    s.bus.publish(XpGained("armor", 1.0, "armor", 0))
    log.close()
    rows = list(csv.reader((tmp_path / "xp.csv").open(encoding="utf-8")))
    assert rows[0][:3] == ["seconds", "player", "proficiency"]
    assert rows[1][2] == "blades" and rows[1][4] == "offense" and rows[1][5] == "3" and rows[1][7] == "12.50"
    assert rows[2][5] == "", "no monster"
