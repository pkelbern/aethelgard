import numpy as np
import pytest

from aethelgard.core.combat import (
    AttackerStats,
    BlockOutcome,
    CombatMath,
    DefenderStats,
    GuardState,
    Loadout,
    player_attack,
    player_defense,
    player_guard,
)
from aethelgard.core.items import ItemDrop, LootGenerator, Rarity, SharedStash
from aethelgard.core.monsters import MonsterFactory, PackKind, PackSpawner
from aethelgard.core.rng import Rng
from aethelgard.core.session import GameSession
from aethelgard.core.world_gen import AreaGenerator, ExitKind


def cm(d):
    return CombatMath(d.balance.combat, d.balance.guard)


HIT20 = AttackerStats(5, 1e9, 20, 20)
BARE = DefenderStats(1, 0)

# ---------------------------------------------------------------- RNG


def test_rng_deterministico_e_forks_independentes():
    a, b = Rng(123), Rng(123)
    assert [a.next_u64() for _ in range(500)] == [b.next_u64() for _ in range(500)]
    loot1 = Rng.for_label(7, "loot")
    world = Rng.for_label(7, "world")
    world.next_u64()
    assert Rng.for_label(7, "loot").next_u64() == loot1.next_u64()
    assert Rng.for_label(7, "loot").next_u64() != Rng.for_label(7, "world").next_u64()


def test_rng_limites():
    r = Rng(5)
    seen = {r.randint(3, 6) for _ in range(3000)}
    assert seen == {3, 4, 5, 6}
    assert all(0 <= r.random() < 1 for _ in range(3000))


# ---------------------------------------------------------------- combate e guarda


def test_acerto_e_armadura(data):
    c = cm(data)
    assert c.hit_chance(100, 100, 10, 10) == pytest.approx(0.5)
    assert c.hit_chance(10000, 1, 10, 1) == 0.95
    assert c.hit_chance(1, 10000, 1, 50) == 0.05
    assert c.armor_reduction(100, 2) == pytest.approx(0.5)
    assert c.armor_reduction(1e9, 1) == 0.75


def test_dano_final(data):
    r = cm(data).resolve_physical(HIT20, DefenderStats(1, 0, 250), Rng(9))
    assert r.hit and r.raw == pytest.approx(20)
    assert r.mitigated == pytest.approx(10)
    assert r.final == pytest.approx(10)


def test_guarda_bloqueia_de_frente_e_gasta_energia(data):
    c = cm(data)
    g = GuardState(raised=True, in_arc=True, energy=100)
    r = c.resolve_physical(HIT20, BARE, Rng(1), g)
    assert r.block is BlockOutcome.BLOCKED and r.final == 0
    assert r.guard_energy_cost == pytest.approx(1 + 20 * 0.25)
    assert c.guard_cost(20, 0.3) == pytest.approx(6 * 0.7)
    assert c.guard_cost(20, 0.95) == pytest.approx(6 * 0.4), "eficiência limitada a 60%"


def test_guarda_nao_cobre_costas_nem_guarda_baixa(data):
    c = cm(data)
    assert c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, False, energy=100)).block is BlockOutcome.NONE
    assert c.resolve_physical(HIT20, BARE, Rng(1), GuardState(False, True, energy=100)).block is BlockOutcome.NONE


def test_janela_perfeita_e_guarda_quebrada(data):
    c = cm(data)
    perf = c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, True, perfect=True))
    assert perf.block is BlockOutcome.PERFECT and perf.guard_energy_cost == 0
    broke = c.resolve_physical(HIT20, BARE, Rng(1), GuardState(True, True, energy=2))
    assert broke.block is BlockOutcome.GUARD_BROKEN
    assert broke.final == pytest.approx(20) and broke.guard_energy_cost == 2


def test_arco_de_120_graus():
    assert CombatMath.in_arc(1, 0, 1, 0, 120)
    assert CombatMath.in_arc(1, 0, 1, 1.5, 120)
    assert not CombatMath.in_arc(1, 0, 1, 2, 120)
    assert not CombatMath.in_arc(1, 0, -1, 0, 120)
    assert CombatMath.in_arc(1, 0, -1, 0, 360)


def test_guerreiro_inicial(data):
    s = GameSession(data, 1)
    atk = player_attack(s.character, s.skills, s.gear)
    g = player_guard(s.skills, s.gear, data.balance.guard)
    assert (atk.dmg_min, atk.dmg_max) == (2, 7)
    assert atk.damage_bonus_pct == pytest.approx(0.10)
    assert g.can_guard and g.arc_degrees == 120 and g.efficiency == 0
    assert player_defense(s.character, s.skills, s.gear).armor == pytest.approx(13)
    s.skills.allocate("postura_defensiva")
    assert player_guard(s.skills, s.gear, data.balance.guard).efficiency == pytest.approx(0.03)
    lobo = MonsterFactory(data).create(data.monster_type("lobo"), 2, "normal", Rng(1))
    avg_hit = (atk.dmg_min + atk.dmg_max) / 2 * (1 + atk.damage_bonus_pct)
    assert 1.5 <= lobo.max_hp / avg_hit <= 5


# ---------------------------------------------------------------- monstros


def test_multiplicadores_de_rank(data):
    f = MonsterFactory(data)
    t = data.monster_type("lobo")
    rng = Rng(3)
    n, c, u, m = (f.create(t, 5, r, rng) for r in ("normal", "champion", "unique", "minion"))
    assert (n.level, c.level, u.level, m.level) == (5, 7, 8, 8)
    xp = lambda lv: data.monster_level(lv).xp * t.exp_pct / 100  # noqa: E731
    assert n.xp_base == pytest.approx(xp(5))
    assert c.xp_base == pytest.approx(xp(7) * 3)
    assert u.xp_base == pytest.approx(xp(8) * 5)
    hp = lambda lv: t.hp * data.monster_level(lv).hp_pct / 100  # noqa: E731
    assert hp(7) * 3 * 0.9 - 1 <= c.max_hp <= hp(7) * 3 * 1.1 + 1
    assert c.damage_bonus_pct == pytest.approx(0.9)


def test_dano_nao_passa_da_vida(data):
    m = MonsterFactory(data).create(data.monster_type("zumbi"), 3, "normal", Rng(1))
    hp = m.hp
    assert m.take_damage(hp * 10) == pytest.approx(hp)
    assert not m.alive and m.take_damage(5) == 0


def test_pacotes_por_area(data):
    sp = PackSpawner(data, MonsterFactory(data))
    area = data.area("bosque")
    uniques = total = 0
    for seed in range(200):
        packs = sp.spawn_area(area, 0, Rng(seed))
        assert sum(p.kind == PackKind.NORMAL for p in packs) == area.normal_packs
        special = [p for p in packs if p.kind in (PackKind.UNIQUE, PackKind.CHAMPION)]
        assert 1 <= len(special) <= 2
        total += len(special)
        for p in special:
            if p.kind == PackKind.UNIQUE:
                uniques += 1
                assert 3 <= len(p.members) - 1 <= 6
                assert len(p.leader.modifiers) == 2
                assert all(m.leader_id == p.leader.instance_id for m in p.members[1:])
            else:
                assert 2 <= len(p.members) <= 4
                assert len({m.modifiers[0].id for m in p.members}) == 1
    assert 0.72 <= uniques / total <= 0.88


def test_chefe_so_no_ultimo_andar(data):
    sp = PackSpawner(data, MonsterFactory(data))
    cripta = data.area("cripta")
    assert not any(p.kind == PackKind.BOSS for p in sp.spawn_area(cripta, 0, Rng(1)))
    last = sp.spawn_area(cripta, 2, Rng(1))
    boss = [p for p in last if p.kind == PackKind.BOSS]
    assert len(boss) == 1 and boss[0].leader.level == 12


# ---------------------------------------------------------------- loot e baú


def test_unico_sempre_derruba_magico(data):
    loot, rng = LootGenerator(data), Rng(11)
    for _ in range(300):
        drops = loot.roll_drops("unique", 8, 0, rng)
        assert 2 <= len(drops) <= 4
        assert any(isinstance(d, ItemDrop) and d.item.rarity >= Rarity.MAGIC for d in drops)


def test_itens_respeitam_nivel_e_grupos(data):
    loot, rng = LootGenerator(data), Rng(21)
    for _ in range(1500):
        ilvl = rng.randint(1, 15)
        item = loot.roll_item(ilvl, 3, 0.5, Rarity.NORMAL, rng)
        assert item.base.level <= ilvl
        groups = [a.affix.group for a in item.affixes]
        assert len(groups) == len(set(groups))
        for a in item.affixes:
            assert any(t.level <= ilvl for t in a.affix.tiers)
            assert set(a.affix.tags) & set(item.base.tags)


def test_loot_reproduzivel(data):
    def roll(seed):
        loot, rng = LootGenerator(data), Rng(seed)
        return [repr(d) for _ in range(40) for d in loot.roll_drops("champion", 6, 0, rng)]

    assert roll(99) == roll(99)
    assert roll(99) != roll(100)


def test_bau_compartilhado(data):
    item = LootGenerator(data).roll_item(10, 3, 1, Rarity.RARE, Rng(4))
    stash = SharedStash(gold=50)
    assert stash.deposit(item)
    other = SharedStash.from_json(stash.to_json())
    back = other.withdraw(0, data)
    assert other.gold == 50 and back.name == item.name and back.base.id == item.base.id
    assert back.stat("str") == pytest.approx(item.stat("str"))
    full = SharedStash(capacity=1)
    assert full.deposit(item) and not full.deposit(item)


# ---------------------------------------------------------------- mundo gerado


def _floors(area):
    return area.floors if area.kind == "dungeon" else 1


@pytest.mark.parametrize("seeds", [range(12)])
def test_mapas_validos(data, seeds):
    gen = AreaGenerator(data)
    for area in data.all_areas:
        if area.kind == "town":
            continue
        for fl in range(_floors(area)):
            for seed in seeds:
                L = gen.generate(area, fl, seed, 20)
                assert L.validate() is None
                assert len(L.spawn_points) == 20
                assert L.floor_count() > L.width * L.height / 8
                last = fl == _floors(area) - 1
                if area.kind == "dungeon" and not last:
                    assert sum(e.kind == ExitKind.STAIRS_DOWN for e in L.exits) == 1
                if area.boss and last:
                    assert L.boss_spot is not None
                if area.kind == "open":
                    assert len(L.exits) == len(area.links)


@pytest.mark.slow
def test_cem_seeds_por_area(data):
    test_mapas_validos(data, range(100))


def test_mesma_seed_mesmo_mapa(data):
    gen = AreaGenerator(data)
    area = data.area("planicie")
    a, b, c = gen.generate(area, 0, 555, 10), gen.generate(area, 0, 555, 10), gen.generate(area, 0, 556, 10)
    assert np.array_equal(a.tiles, b.tiles) and a.spawn_points == b.spawn_points
    assert not np.array_equal(a.tiles, c.tiles)


# ---------------------------------------------------------------- sessão, morte e save


def test_morte(fresh_data):
    s = GameSession(fresh_data, 1)
    s.gold = 1000
    s.proficiencies.set_xp("laminas", fresh_data.xp_total_by_level[1] * 2)
    r = s.apply_death()
    assert r.gold_lost == 100 and r.xp_lost == {} and r.leaves_corpse
    fresh_data.balance.death.xp_loss_pct_of_level = 0.10
    gained = s.proficiencies["laminas"].xp - fresh_data.xp_total_by_level[1]
    r2 = s.apply_death()
    assert r2.xp_lost["laminas"] == pytest.approx(gained * 0.10)
    for _ in range(200):
        s.apply_death()
    assert s.proficiencies["laminas"].level == 2, "nunca perde nível"


def test_save_e_load(data):
    s = GameSession(data, 2024)
    s.gold = 321
    s.proficiencies.set_xp("laminas", 5000)
    s.skills.allocate("lamina_afiada")
    s.skills.allocate("lamina_afiada")
    loaded = GameSession.load(data, s.save(), new_world_seed=77)
    assert loaded.master_seed == 77 and loaded.gold == 321
    assert loaded.proficiencies["laminas"].xp == pytest.approx(5000)
    assert loaded.skills.points_in("lamina_afiada") == 2


def test_entrar_em_area(data):
    s = GameSession(data, 42)
    layout, packs = s.enter_area("cripta", 2)
    assert layout.validate() is None
    assert any(p.kind == PackKind.BOSS for p in packs)
    assert len(layout.spawn_points) == sum(p.kind != PackKind.BOSS for p in packs)
    plain, _ = s.enter_area("planicie")
    assert any(e.target_area == "vila" for e in plain.exits)
    assert any(e.target_area == "caverna" and e.kind == ExitKind.ENTRANCE for e in plain.exits)


def test_caminho_contorna_parede(data):
    from aethelgard.core.pathfinding import find_path
    from aethelgard.core.world_gen import FLOOR, WALL, AreaLayout

    L = AreaLayout.filled(10, 10, FLOOR)
    L.tiles[5, 0:9] = WALL  # parede horizontal com passagem em x=9
    path = find_path(L, (2, 2), (2, 8))
    assert path and path[-1] == (2, 8)
    assert all(L.is_floor(c) for c in path)
    assert any(x == 9 for x, _ in path), "passa pela abertura"
    assert find_path(L, (2, 2), (4, 5))[-1] != (4, 5), "destino em parede vira o chão mais próximo"


def test_loadout_inicial(data):
    g = Loadout.starting(data)
    assert g.has_shield and g.armor == 13
