"""Action timings (cooldowns, hit and block recovery) and the real-time world."""

import math
from dataclasses import replace

import pytest

from aethelgard.core.events import ExitReached, HitBlocked, MonsterKilled, PlayerDied, SkillUsed
from aethelgard.core.monsters import MonsterInstance
from aethelgard.core.session import GameSession
from aethelgard.core.timing import Cooldowns, monster_hit_recovery, player_block_recovery, player_hit_recovery, skill_cooldown
from aethelgard.core.world import AttackTarget as Attack
from aethelgard.core.world import MonsterActor, MoveTo, Respawn, SetGuard, UseSkill, World
from aethelgard.core.world_gen import FLOOR, AreaLayout

DT = 1 / 60


# ---------------------------------------------------------------- timing functions


def test_cooldowns_count_down():
    c = Cooldowns()
    c.start("shield_bash", 4.0)
    assert not c.ready("shield_bash")
    c.tick(1.0)
    assert c.remaining("shield_bash") == pytest.approx(3.0)
    assert c.fraction("shield_bash") == pytest.approx(0.75)
    c.tick(3.01)
    assert c.ready("shield_bash") and c.fraction("shield_bash") == 0


def test_cooldown_reduction_is_capped():
    assert skill_cooldown(10, 0.2) == pytest.approx(8)
    assert skill_cooldown(10, 0.9) == pytest.approx(5), "capped at 50%"


def test_hit_recovery_only_on_hard_hits(data):
    t = data.balance.timing
    assert player_hit_recovery(t, 5, 120, 0) == 0, "less than 1/12 of life"
    assert player_hit_recovery(t, 10, 120, 0) == pytest.approx(0.40)
    assert player_hit_recovery(t, 10, 120, 1.0) == pytest.approx(0.20), "100% faster recovery = half the time"


def test_block_recovery(data):
    t = data.balance.timing
    assert player_block_recovery(t, 0) == pytest.approx(0.30)
    assert player_block_recovery(t, 0.5) == pytest.approx(0.20)


def test_monster_recovery_by_rank(data):
    t = data.balance.timing
    assert monster_hit_recovery(t, 20, 100, "normal") == pytest.approx(0.35)
    assert monster_hit_recovery(t, 20, 100, "unique") == pytest.approx(0.175)
    assert monster_hit_recovery(t, 20, 100, "boss") == 0
    assert monster_hit_recovery(t, 5, 100, "normal") == 0


# ---------------------------------------------------------------- test world


def arena(data, *, monster_hp=1000.0, monster_dmg=(10, 10), monster_acc=1e9, at=(6.0, 5.0), level=3):
    """Empty 12×12 room with the player at (5,5) and a controlled monster."""
    s = GameSession(data, 7)
    w = World([s])
    L = AreaLayout.filled(12, 12, FLOOR)
    L.entry = (5, 5)
    w.layout, w.area_id = L, "test"
    p = w.players[0]
    p.x, p.y = 5.0, 5.0
    t = data.monster_type("bandit")
    m = MonsterInstance(
        instance_id=1, type=t, rank=data.rank("normal"), name="Target", level=level, max_hp=monster_hp, hp=monster_hp,
        dmg_min=monster_dmg[0], dmg_max=monster_dmg[1], damage_bonus_pct=0, accuracy=monster_acc, defense=0, xp_base=50,
    )
    w.monsters[1] = MonsterActor(m, at[0], at[1], pack_ids=[1])
    return s, w, p, w.monsters[1]


def run(w, seconds):
    for _ in range(round(seconds / DT)):
        w.step(DT)


def test_player_attacks_and_kills_a_monster(data):
    s, w, p, m = arena(data, monster_hp=8, monster_dmg=(0.01, 0.01))
    killed = []
    s.bus.subscribe(MonsterKilled, killed.append)
    w.apply(Attack(0, 1))
    run(w, 6)
    assert killed, "monster died"
    assert s.proficiencies["blades"].xp > 0


def test_skill_respects_its_cooldown(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
    s.proficiencies.set_xp("shield", 1)  # level 1 already gives 1 point
    s.skills.allocate("shield_bash")
    used = []
    s.bus.subscribe(SkillUsed, used.append)
    assert w.apply(UseSkill(0, "shield_bash", 1)) is None
    run(w, 0.7)
    assert len(used) == 1
    assert 3.0 < p.cooldowns.remaining("shield_bash") <= 4.0
    reason = w.apply(UseSkill(0, "shield_bash", 1))
    assert reason and "cooldown" in reason
    run(w, 3.5)
    assert w.apply(UseSkill(0, "shield_bash", 1)) is None


def test_shield_bash_stuns(data):
    s, w, p, m = arena(data, level=1)  # level 1: the bash almost always hits
    s.skills.allocate("shield_bash")
    w.apply(UseSkill(0, "shield_bash", 1))
    stunned = False
    for _ in range(60):
        w.step(DT)
        stunned = stunned or m.state == "stunned"
    assert stunned


def test_hard_hit_causes_recovery_and_interrupts_the_attack(data):
    s, w, p, m = arena(data, monster_dmg=(30, 30))  # player's life ~103: 30 > 1/12
    w.apply(Attack(0, 1))
    states = set()
    for _ in range(240):
        w.step(DT)
        states.add(p.state)
    assert "hit_recovery" in states


def test_weak_hit_causes_no_recovery(data):
    s, w, p, m = arena(data, monster_dmg=(2, 2))
    states = set()
    for _ in range(240):
        w.step(DT)
        states.add(p.state)
    assert "hit_recovery" not in states
    assert p.life < p.max_life


def test_block_causes_recovery_and_prevents_attacking(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10))
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)
    w.apply(SetGuard(0, True, 1, 0))  # the monster is to the right (+x)
    run(w, 0.3)  # past the perfect window
    seen_block_recovery = False
    for _ in range(180):
        w.step(DT)
        if p.state == "block_recovery":
            seen_block_recovery = True
            assert p.guarding
    assert blocks and not any(b.perfect for b in blocks[1:])
    assert seen_block_recovery
    taken = p.max_life - p.life
    assert 0 < taken <= 10 * 0.3 * len(blocks) + 1e-6, "a block lets at most 30% through, and armor softens it"
    assert s.proficiencies["shield"].xp > 0


def test_guard_has_a_cooldown_before_rising_again(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(10.0, 10.0))
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert p.guarding
    w.apply(SetGuard(0, False, 1, 0))
    w.step(DT)
    assert not p.guarding
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert not p.guarding, "still on cooldown"
    run(w, 0.35)
    assert p.guarding


def test_perfect_parry_counterattacks_with_parry(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10))
    s.proficiencies.set_xp("shield", data.xp_total_by_level[5])  # level 6 unlocks Parry
    s.skills.allocate("parry")
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)
    # raise the guard only at the moment of the monster's swing
    for _ in range(600):
        if m.state == "attacking" and m.attack_elapsed > m.attack_duration * 0.5 - 0.1 and not p.guarding:
            w.apply(SetGuard(0, True, 1, 0))
        if p.guarding and p.guard_time > 0.2:
            w.apply(SetGuard(0, False, 1, 0))
        w.step(DT)
        if any(b.perfect for b in blocks):
            break
    assert any(b.perfect for b in blocks)
    assert m.monster.hp < m.monster.max_hp, "Parry counterattack"


def test_bulwark_blocks_from_behind_for_free(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10), at=(4.0, 5.0), level=14)  # monster behind (−x), level close to the player's
    s.proficiencies.set_xp("shield", data.xp_total_by_level[11])  # level 12
    s.skills.allocate("bulwark")
    p.reset_vitals()
    w.apply(SetGuard(0, True, 1, 0))  # facing +x
    run(w, 0.3)
    assert w.apply(UseSkill(0, "bulwark")) is None
    energy = p.energy
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)
    run(w, 3.0)  # Bulwark lasts 3.2 s; enough swings to land a few despite the 5% minimum miss
    assert blocks and p.max_life - p.life <= 10 * 0.3 * len(blocks) + 1e-6, "blocked from behind, still not free of damage"
    assert p.energy >= energy - 0.5, "Bulwark spends no Energy per block"
    assert w.apply(UseSkill(0, "bulwark")).startswith("Bulwark: cooldown")


def test_area_exit_emits_an_event(data):
    s = GameSession(data, 3)
    w = World([s])
    w.enter_area("plains")
    exits = []
    s.bus.subscribe(ExitReached, exits.append)
    p = w.players[0]
    target = next(e for e in w.layout.exits if e.target_area == "village")
    w.monsters.clear()
    p.x, p.y = float(target.pos[0]), float(target.pos[1])
    run(w, 1.0)
    assert exits and exits[0].target_area == "village"


def test_arrival_next_to_the_exit_you_came_from(data):
    s = GameSession(data, 3)
    w = World([s])
    w.enter_area("plains", came_from="cave")
    p = w.players[0]
    entrance = next(e for e in w.layout.exits if e.target_area == "cave")
    assert (round(p.x), round(p.y)) != entrance.pos
    assert abs(p.x - entrance.pos[0]) + abs(p.y - entrance.pos[1]) <= 2


# ---------------------------------------------------------------- bodies and death


def test_player_cannot_walk_through_a_monster(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(6.0, 5.0))
    touch = p.radius + m.radius
    w.apply(MoveTo(0, 9, 5))
    closest = 9.0
    for _ in range(240):
        w.step(DT)
        closest = min(closest, math.hypot(p.x - m.x, p.y - m.y))
    assert closest >= touch - 1e-6, "bodies never overlap"
    assert p.x > m.x, "the player walks around the monster instead of stopping for good"


def test_monster_without_collision_is_explicit_and_lets_you_through(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(6.0, 5.0))
    m.monster.type = replace(m.monster.type, collides=False)  # e.g. a ghost: collides=false in monsters.json
    w.apply(MoveTo(0, 9, 5))
    closest = 9.0
    for _ in range(240):
        w.step(DT)
        closest = min(closest, math.hypot(p.x - m.x, p.y - m.y))
    assert closest < 0.1, "walked straight through it"
    assert all(t.collides for t in data.monsters.types), "every Act 1 monster has a body"


def test_monster_size_comes_from_type_and_rank(data):
    s, w, p, m = arena(data)
    assert m.radius == pytest.approx(data.monster_type("bandit").radius)
    m.monster.rank = data.rank("boss")
    assert m.radius == pytest.approx(data.monster_type("bandit").radius * 2.2)


def test_death_waits_for_respawn(data):
    s, w, p, m = arena(data, monster_dmg=(500, 500))
    died = []
    s.bus.subscribe(PlayerDied, died.append)
    run(w, 3)
    assert died and p.state == "dead" and p.life == 0
    assert not p.collides, "the body on the floor blocks no one"
    w.apply(MoveTo(0, 9, 9))
    run(w, 0.5)
    assert p.state == "dead" and not p.path, "dead players ignore orders"
    w.monsters.clear()
    w.apply(Respawn(0))
    assert p.state == "idle" and p.life == pytest.approx(p.max_life)


def test_packs_spawn_without_overlapping_bodies(data):
    s = GameSession(data, 11)
    w = World([s])
    w.enter_area("plains")
    ms = [m for m in w.monsters.values() if m.collides]
    bad = sum(1 for i, a in enumerate(ms) for b in ms[i + 1 :] if math.hypot(a.x - b.x, a.y - b.y) < a.radius + b.radius - 1e-6)
    assert bad <= len(ms) // 50, "almost every body starts on a free spot (a crowded corner may still overlap)"


def test_monster_faces_its_target_and_keeps_facing_during_the_swing(data):
    """The facing is world state (the client picks the sprite direction from it; in co-op the host sends it)."""
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(5.0, 7.0))  # monster below the player (+y)
    m.alert = True
    w.step(DT)
    assert (m.face_x, m.face_y) == pytest.approx((0.0, -1.0))
    run(w, 1.0)
    assert m.state == "attacking" or m.attack_cooldown > 0
    p.x = 7.0  # the player steps aside in the middle of the swing
    if m.state == "attacking":
        w.step(DT)
        assert (m.face_x, m.face_y) == pytest.approx((0.0, -1.0)), "a swing keeps its direction"


def test_movement_speeds_come_from_balance(data):
    """Player outruns every Act 1 monster, as in Diablo 2; numbers live in balance.json."""
    mv = data.balance.movement
    assert 5.5 <= mv.player_tiles_per_second <= 8
    fastest = max(t.move_speed for t in data.monsters.types) * mv.monster_base_tiles_per_second
    assert fastest < mv.player_tiles_per_second
    s = GameSession(data, 7)
    w = World([s])
    w.enter_area("plains")
    m = next(iter(w.monsters.values()))
    assert m.base_speed == mv.monster_base_tiles_per_second


def test_monster_swings_are_short_but_the_pace_is_unchanged(data):
    s, w, p, m = arena(data)
    assert m.attack_duration == pytest.approx(0.6), "bandit: attackRate 1.0 used to swing for 1.4 s"
    assert m.attack_interval == pytest.approx(1.3 * 1.4), "same time between hits as before"
    m.monster.type = data.monster_type("zombie")  # the slowest attacker
    assert m.attack_duration <= 0.6 and m.attack_interval > m.attack_duration


def test_first_areas_have_smaller_packs(data):
    from aethelgard.core.monsters import MonsterFactory, PackKind, PackSpawner
    from aethelgard.core.rng import Rng

    sp = PackSpawner(data, MonsterFactory(data))
    for seed in range(20):
        for p in sp.spawn_area(data.area("plains"), 0, Rng(seed)):
            if p.kind == PackKind.NORMAL:
                assert 2 <= len(p.members) <= 3
