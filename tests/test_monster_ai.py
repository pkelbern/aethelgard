"""Monster archetypes, modifier effects, the boss's pattern, sleeping far monsters (M4-03 to M4-06, M0-06)."""

import pytest

from aethelgard.core.data import MonsterModifierDef
from aethelgard.core.events import MonsterKilled
from aethelgard.core.world import MonsterActor
from test_ranged_and_leaders import add_monster
from test_world_timing import DT, arena, run


def typed(data, m: MonsterActor, type_id: str) -> MonsterActor:
    m.monster.type = data.monster_type(type_id)
    m.alert = True
    return m


def with_mod(data, m: MonsterActor, mod_id: str) -> MonsterActor:
    m.monster.modifiers = [next(x for x in data.modifiers.modifiers if x.id == mod_id)]
    return m


# ---------------------------------------------------------------- archetypes


def test_a_boar_charges_and_stuns(data):
    s, w, p, m = arena(data, at=(10.0, 5.0), monster_dmg=(1, 1))
    typed(data, m, "boar")
    states = set()
    for _ in range(90):
        w.step(DT)
        states.add(p.state)
    assert "stunned" in states and p.life < p.max_life
    assert m.ai_cooldowns["charge"] > 0


def test_wolves_come_from_the_side(data):
    s, w, p, m = arena(data, at=(10.0, 5.0), monster_dmg=(0.01, 0.01))
    typed(data, m, "wolf")
    p.god_mode = True
    ys = []
    for _ in range(30):
        w.step(DT)
        ys.append(m.y)
    assert max(abs(y - 5.0) for y in ys) > 0.3, "not a straight line at the player"


def test_a_necromancer_raises_a_dead_follower_without_reward(data):
    s, w, p, m = arena(data, at=(11.0, 11.0))
    typed(data, m, "necromancer")
    dead = add_monster(w, data, "skeleton", 9.5, 10.0, 2, hp=5)
    w._damage_monster(p, dead, 10, "blades")
    assert 2 not in w.monsters and dead in w.fallen
    run(w, 0.2)
    assert 2 in w.monsters and dead.monster.hp == pytest.approx(dead.monster.max_hp * data.balance.monster_ai.revive_hp_pct)
    assert dead.monster.xp_info.xp_base == 0, "raised: no XP a second time"
    drops = len(w.ground)
    w._damage_monster(p, dead, 1000, "blades")
    assert len(w.ground) == drops, "and no loot"


def test_a_shaman_heals_a_wounded_ally(data):
    s, w, p, m = arena(data, at=(11.0, 11.0))
    typed(data, m, "shaman")
    ally = add_monster(w, data, "bandit", 10.0, 10.0, 2)
    ally.monster.hp = ally.monster.max_hp * 0.3
    run(w, 0.1)
    assert ally.monster.hp == pytest.approx(ally.monster.max_hp * (0.3 + data.balance.monster_ai.heal_pct))


# ---------------------------------------------------------------- modifiers


def test_fire_enchanted_hits_add_fire_and_explode_on_death(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10))
    with_mod(data, m, "fire_enchanted")
    assert w.brains.elemental_extra(m) == {"fire": 0.5}
    life = p.life
    w._monster_hit(m, p)
    hit = life - p.life
    assert hit > 10 * 1.4, "10 physical (minus armor) + 5 fire"
    life = p.life
    w._damage_monster(p, m, 10000, "blades")
    assert p.life < life, "the burst on death"


def test_cold_enchanted_slows_the_player(data):
    s, w, p, m = arena(data, monster_dmg=(5, 5))
    with_mod(data, m, "cold_enchanted")
    speed, ias = w.player_speed(p), w.attack_speed(p)
    w._monster_hit(m, p)
    assert w.player_speed(p) == pytest.approx(speed * 0.7) and w.attack_speed(p) == pytest.approx(ias - 0.3)
    w.monsters.clear()
    run(w, 2.1)
    assert w.player_speed(p) == pytest.approx(speed)


def test_lightning_enchanted_sparks_when_hurt(data):
    s, w, p, m = arena(data)
    mod = MonsterModifierDef("x", "Charged", on_hurt="charged_bolt", params={"chargedChance": 1.0, "chargedBolts": 4, "chargedDamagePct": 0.3})
    m.monster.modifiers = [mod]
    w._damage_monster(p, m, 1, "blades")
    sparks = [pr for pr in w.projectiles.values() if pr.owner == "monster"]
    assert len(sparks) == 4 and all(pr.element == "lightning" for pr in sparks)


def test_vampiric_heals_by_what_it_deals(data):
    s, w, p, m = arena(data, monster_dmg=(20, 20))
    with_mod(data, m, "vampiric")
    m.monster.hp = 100
    w._monster_hit(m, p)
    assert m.monster.hp > 100


def test_teleporting_appears_beside_the_player(data):
    s, w, p, m = arena(data, at=(11.0, 11.0))
    with_mod(data, m, "teleporting")
    m.alert = True
    w.step(DT)
    assert abs(m.x - p.x) + abs(m.y - p.y) < 2.0


def test_fury_aura_from_a_modifier(data):
    s, w, p, m = arena(data, at=(10.0, 10.0))
    with_mod(data, m, "fury_aura")
    other = add_monster(w, data, "bandit", 9.0, 10.0, 2)
    w.step(DT)
    assert other.buffs.get("damagePct") == pytest.approx(0.3)


# ---------------------------------------------------------------- boss


def boss_arena(data):
    s, w, p, m = arena(data, at=(8.0, 5.0), monster_dmg=(10, 10))
    boss = data.monsters.bosses[0]
    m.monster.boss = boss
    m.monster.rank = data.rank("boss")
    m.monster.hp = m.monster.max_hp = 1000
    m.alert = True
    return s, w, p, m


def test_the_boss_telegraphs_its_specials(data):
    s, w, p, m = boss_arena(data)
    w.step(DT)
    assert w.brains.telegraphs and m.state == "winding"
    (g,) = w.brains.telegraphs.values()
    life = p.life
    run(w, g.total + 0.1)
    assert p.life < life, "standing in it"
    m.ai_cooldowns.clear()
    w.step(DT)
    (g,) = w.brains.telegraphs.values()
    p.x = 1.0  # step out
    life = p.life
    run(w, g.total + 0.1)
    assert p.life == pytest.approx(life), "out of the area: the special misses"


def test_the_boss_enrages_and_summons_in_phase_two(data):
    s, w, p, m = boss_arena(data)
    m.monster.hp = 400  # below half
    w.step(DT)
    assert m.phase == 2 and m.extra("attackSpeedPct") == pytest.approx(0.3)
    m.ai_cooldowns = {"cleave": 99, "bone_slam": 99}
    m.state = "idle"
    w.brains.telegraphs.clear()
    run(w, 1.5)
    summoned = [o for o in w.monsters.values() if o is not m]
    assert len(summoned) == 4 and all(o.monster.no_reward for o in summoned)


# ---------------------------------------------------------------- performance


def test_far_monsters_sleep_until_alert(data):
    s, w, p, m = arena(data, at=(11.0, 11.0))
    data.balance.monster_ai.sleep_tiles, old = 1.0, data.balance.monster_ai.sleep_tiles
    try:
        m.x, m.y, m.base_speed = 11.0, 11.0, 3.0
        w.step(DT)
        assert (m.x, m.y) == (11.0, 11.0) and not m.alert
    finally:
        data.balance.monster_ai.sleep_tiles = old


def test_the_body_grid_finds_the_same_collisions(data):
    s, w, p, m = arena(data, at=(5.5, 5.0))
    w.step(DT)
    assert w._grid is not None
    assert w.blocked_by_body(p, 5.3, 5.0) is m


def test_kills_still_count_for_raised_ones(data):
    s, w, p, m = arena(data)
    killed = []
    s.bus.subscribe(MonsterKilled, killed.append)
    m.monster.no_reward = True
    w._damage_monster(p, m, 10000, "blades")
    assert killed and killed[0].monster.xp_base == 0
