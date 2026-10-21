"""Knockback, straight projectiles (monsters and Shield Throw), leader packs and auras, armor XP pace."""

import math
from dataclasses import replace

import pytest

from aethelgard.core.data import ProjectileDef
from aethelgard.core.events import DamageMitigated, HitBlocked, MonsterKilled, MonsterXpInfo
from aethelgard.core.monsters import MonsterFactory, MonsterInstance, PackKind, PackSpawner
from aethelgard.core.progression import LevelFactor, XpCalculator
from aethelgard.core.rng import Rng
from aethelgard.core.world import MonsterActor, SetGuard, UseSkill
from aethelgard.core.world_gen import WALL
from test_world_timing import DT, arena, run


def add_monster(w, data, type_id: str, x: float, y: float, mid: int, hp: float = 1000.0, level: int = 3) -> MonsterActor:
    t = data.monster_type(type_id)
    m = MonsterInstance(instance_id=mid, type=t, rank=data.rank("normal"), name=t.name, level=level, max_hp=hp, hp=hp,
                        dmg_min=10, dmg_max=10, damage_bonus_pct=0, accuracy=1e9, defense=0, xp_base=50)
    w.monsters[mid] = MonsterActor(m, x, y, pack_ids=[mid])
    return w.monsters[mid]


# ---------------------------------------------------------------- knockback


def test_shield_bash_knocks_the_target_back(data):
    s, w, p, m = arena(data, level=1)
    s.allocate_skill("shield_bash")
    x0 = m.x
    w.apply(UseSkill(0, "shield_bash", 1))
    run(w, 0.8)
    assert m.x - x0 == pytest.approx(1.5, abs=0.15), "pushed 1.5 tiles away from the player (+x)"


def test_knockback_stops_at_walls(data):
    s, w, p, m = arena(data, level=1)
    w.layout.tiles[:, 7] = WALL  # a wall right behind the monster
    s.allocate_skill("shield_bash")
    w.apply(UseSkill(0, "shield_bash", 1))
    run(w, 0.8)
    assert m.x < 6.6


# ---------------------------------------------------------------- monster projectiles


def archer_arena(data, at=(10.0, 5.0)):
    s, w, p, m = arena(data, at=at, level=12)
    m.monster.type = data.monster_type("skeleton_archer")
    m.alert = True
    return s, w, p, m


def test_archers_shoot_visible_projectiles_that_fly_straight(data):
    s, w, p, m = archer_arena(data)
    seen = []
    for _ in range(240):
        w.step(DT)
        seen += [(pr.x, pr.y, pr.dir_x, pr.dir_y) for pr in w.projectiles.values()]
    assert seen, "the shot exists in the world, so the client can draw it"
    assert all(abs(dy) < 1e-9 and dx < 0 for _, _, dx, dy in seen), "straight at the player (−x), never turning"
    assert p.life < p.max_life


def test_stepping_out_of_the_line_dodges_the_shot(data):
    s, w, p, m = archer_arena(data)
    for _ in range(240):
        w.step(DT)
        if w.projectiles:
            break
    assert w.projectiles
    w.monsters.clear()  # only this arrow is left; it keeps the archer's numbers
    p.y += 1.5  # sidestep after the shot left the bow
    life = p.life
    for _ in range(90):
        w.step(DT)
    assert p.life == pytest.approx(life) and not w.projectiles, "the arrow flew past and ran out of range"


def test_walls_stop_projectiles(data):
    s, w, p, m = archer_arena(data)
    proj = w._launch(x=9.0, y=5.0, speed=9, radius=0.12, max_range=9, look="arrow", owner="monster", owner_id=1,
                     attack=m.attack, info=m.monster.xp_info)
    proj.aim(p.x, p.y)
    w.layout.tiles[5, 7] = WALL
    life = p.life
    for _ in range(60):
        w._step_projectiles(DT)
    assert not w.projectiles and p.life == pytest.approx(life)


def test_guard_blocks_arrows_but_not_magic_unless_bulwark(data):
    s, w, p, m = archer_arena(data)
    w.apply(SetGuard(0, True, 1, 0))  # facing the archer (+x)
    run(w, 0.3)
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)

    def shot(magic: bool):
        proj = w._launch(x=7.0, y=5.0, speed=9, radius=0.12, max_range=9, look="bolt", owner="monster", owner_id=1,
                         magic=magic, attack=m.attack, info=m.monster.xp_info)
        proj.aim(p.x, p.y)
        for _ in range(30):
            w._step_projectiles(DT)

    shot(False)
    assert len(blocks) == 1, "physical arrow: blocked"
    shot(True)
    assert len(blocks) == 1, "magic bolt goes through the guard"
    p.bulwark_left = 3.0
    shot(True)
    assert len(blocks) == 2, "Bulwark stops magic too"


def test_ranged_monsters_need_a_clear_line_to_shoot(data):
    s, w, p, m = archer_arena(data)
    w.layout.tiles[2:9, 8] = WALL  # wall between them
    for _ in range(120):
        w.step(DT)
    assert not w.projectiles
    assert m.x < 10.0, "it walks to get a clear shot"


# ---------------------------------------------------------------- Shield Throw


def test_shield_throw_flies_straight_and_bounces_to_the_next_enemy(data):
    s, w, p, m = arena(data, at=(9.0, 5.0), level=1)
    s.proficiencies.set_xp("shield", data.xp_total_by_level[17])  # level 18
    s.grant_skill("shield_throw")
    other = add_monster(w, data, "bandit", 9.0, 8.0, 2, level=1)
    hp1, hp2 = m.monster.hp, other.monster.hp
    assert w.apply(UseSkill(0, "shield_throw", None, 9.0, 5.0)) is None
    run(w, 2.0)
    assert m.monster.hp < hp1 and other.monster.hp < hp2, "first hit, then a new straight line to the second enemy"
    assert not w.projectiles


def test_shield_throw_needs_a_shield(data):
    s, w, p, m = arena(data)
    s.proficiencies.set_xp("shield", data.xp_total_by_level[17])
    s.grant_skill("shield_throw")
    s.gear.slots.pop("offhand", None)
    assert "needs a shield" in w.apply(UseSkill(0, "shield_throw", 1))


def test_projectile_definition_is_in_the_data(data):
    assert data.monster_type("skeleton_archer").projectile == ProjectileDef(speed=9, radius=0.12, magic=False, look="arrow")
    assert data.monster_type("shaman").projectile.magic
    assert data.monster_type("wolf").projectile is None


# ---------------------------------------------------------------- leaders


def test_leaders_never_spawn_alone_or_in_special_packs(data):
    sp = PackSpawner(data, MonsterFactory(data))
    led_seen = 0
    for seed in range(60):
        for area_id in ("plains", "crypt"):
            for p in sp.spawn_area(data.area(area_id), 0, Rng(seed)):
                kinds = [m.type for m in p.members]
                leaders = [t for t in kinds if t.leader]
                if p.kind == PackKind.LED:
                    led_seen += 1
                    assert kinds[0].leader and len(leaders) == 1 and len(kinds) >= 2
                    assert all(t.id in kinds[0].leader.followers for t in kinds[1:])
                    assert all(m.leader_id == p.members[0].instance_id for m in p.members[1:])
                else:
                    assert not leaders, f"{p.kind}: leaders only head their own followers"
    assert led_seen > 0


def test_few_leaders_per_area(data):
    sp = PackSpawner(data, MonsterFactory(data))
    for seed in range(40):
        packs = sp.spawn_area(data.area("plains"), 0, Rng(seed))
        assert sum(p.kind == PackKind.LED for p in packs) <= data.monster_type("shaman").leader.max_per_area


def test_shaman_aura_buffs_nearby_monsters_while_alive(data):
    s, w, p, m = arena(data, at=(10.0, 10.0))
    shaman = add_monster(w, data, "shaman", 9.0, 10.0, 2)
    far = add_monster(w, data, "bandit", 1.0, 1.0, 3)
    w.step(DT)
    aura = data.monster_type("shaman").leader.aura.stats
    assert m.buffs == pytest.approx(aura) and m.attack.damage_bonus_pct == pytest.approx(aura["damagePct"])
    assert not far.buffs and not shaman.buffs, "out of range, and never on itself"
    base = m.attack_duration
    shaman.monster.hp = 0
    w.step(DT)
    assert not m.buffs and m.attack_duration > base - 1e-9


def test_necromancer_makes_the_dead_tougher(data):
    s, w, p, m = arena(data, at=(10.0, 10.0))
    add_monster(w, data, "necromancer", 9.0, 10.0, 2)
    w.step(DT)
    assert m.defense.physical_reduction == pytest.approx(0.1)


# ---------------------------------------------------------------- pace


def test_armor_xp_counts_low_mitigation_fully_enough(data):
    c = XpCalculator(LevelFactor(data.balance.level_factor.table), data.balance.xp)
    m = MonsterXpInfo(1, 2, 100, 50)
    assert c.armor(m, 10, 1, 2) == pytest.approx(100 * 0.1 * 0.4), "10% mitigated = 40% of the per-hit XP"
    assert c.armor(m, 10, 5, 2) == pytest.approx(100 * 0.1), "25% or more = all of it"


def test_armor_xp_still_has_the_per_monster_cap(data):
    s, w, p, m = arena(data)
    for _ in range(100):
        s.bus.publish(DamageMitigated(m.monster.xp_info, 10, 5))
    assert s.proficiencies["armor"].xp == pytest.approx(m.monster.xp_base * 0.75)
    s.bus.publish(MonsterKilled(m.monster.xp_info, "normal"))


def test_recovery_regenerates_slowly(data):
    n = data.node("recovery")
    assert n.effects["lifeRegenPerSecond"] == pytest.approx(0.2)


def test_monster_stats_from_buffs_use_the_actor(data):
    s, w, p, m = arena(data)
    m.buffs = {"moveSpeedPct": 0.5}
    assert m.speed == pytest.approx(m.monster.type.move_speed * data.balance.movement.monster_base_tiles_per_second * 1.5)
    m.monster.type = replace(m.monster.type, projectile=ProjectileDef())
    assert m.ranged and math.isfinite(m.reach)


def test_a_projectile_at_the_end_of_its_range_is_removed_not_left_hanging(data):
    """Regression: traveled stopped at 8.999999999999998 of 9; the rest was too small to move it, so the
    arrow stayed in the air forever and hit whoever walked over it."""
    s, w, p, m = archer_arena(data)
    proj = w._launch(x=25.316406071651343, y=48.52090911029274, speed=9, radius=0.12, max_range=9, look="arrow",
                     owner="monster", owner_id=1, attack=m.attack, info=m.monster.xp_info)
    proj.dir_x, proj.dir_y = 0.939068000696471, -0.34373142141493696
    proj.traveled = 8.999999999999998
    w._step_projectiles(DT)
    assert not w.projectiles


def test_archers_back_off_from_a_sword_up_close(data):
    s, w, p, m = archer_arena(data, at=(6.5, 5.0))  # 1.5 tiles from the player
    m.attack_cooldown = 10.0
    run(w, 0.5)
    assert m.x > 6.5 + 0.5, "steps away instead of shooting point-blank"
    assert not w.projectiles


def test_a_cornered_archer_shoots_from_where_it_is(data):
    s, w, p, m = archer_arena(data, at=(10.0, 5.0))
    p.x = 8.5  # the player between the archer and... nothing: walls all around the archer
    for x, y in ((11, 4), (11, 5), (11, 6), (10, 4), (10, 6), (9, 4), (9, 6)):
        w.layout.tiles[y, x] = WALL
    seen = False
    for _ in range(240):
        w.step(DT)
        seen = seen or bool(w.projectiles)
    assert seen


def test_a_huge_boss_reaches_whoever_stands_against_it(data):
    s, w, p, m = arena(data, at=(6.4, 5.0), monster_dmg=(5, 5))
    m.monster.rank = data.rank("boss")  # 2.2 times the size: its center stays far from the player's
    contact = m.radius + p.radius
    assert contact > 1.2, "the old reach (1.2 from the center) could never touch the player"
    m.x = p.x + contact + 0.05
    m.alert = True
    life = p.life
    run(w, 3.0)
    assert p.life < life
