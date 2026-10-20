import pytest

from aethelgard.core.data import validate
from aethelgard.core.events import (
    DamageDealt,
    DamageMitigated,
    EventBus,
    HitBlocked,
    MonsterKilled,
    MonsterXpInfo,
    ProficiencyLeveledUp,
)
from aethelgard.core.progression import Allocate, LevelFactor, ProficiencySet, XpCalculator, XpCurve, compute_level, compute_title
from aethelgard.core.session import GameSession
from aethelgard.core.simulation import run_act


def calc(d):
    return XpCalculator(LevelFactor(d.balance.level_factor.table), d.balance.xp)


def test_dados_carregam_e_sao_validos(data):
    assert validate(data) == []
    assert len(data.proficiencies.proficiencies) == 3
    assert len(data.skills.nodes) == 24
    assert all(n.cooldown >= 0 for n in data.skills.nodes if n.kind == "active")


def test_curvas_proprias_tem_o_ritmo_esperado(data):
    c = XpCurve(data.xp_total_by_level)
    assert c.max_level == 30
    assert c.level_for_xp(0) == 1
    assert c.level_for_xp(data.xp_total_by_level[1]) == 2
    assert c.level_for_xp(1e12) == 30
    # abates de monstros do mesmo nível para subir: poucos no início, muitos no 20
    def kills(n):
        need = data.xp_total_by_level[n] - data.xp_total_by_level[n - 1]
        return need / (data.monster_level(n).xp * 0.9)
    assert 5 <= kills(1) <= 25
    assert 150 <= kills(20) <= 400
    assert data.monster_level(1).hp_pct < data.monster_level(10).hp_pct < data.monster_level(30).hp_pct


def test_fator_de_nivel(data):
    f = LevelFactor(data.balance.level_factor.table)
    assert f(0) == f(5) == f(-5) == 1.0
    assert f(6) == 0.88
    assert f(10) == f(25) == 0.02
    assert f(-6) == 0.81
    assert f(-40) == 0.05


def test_exemplo_trabalhado(data):
    lobo = MonsterXpInfo(1, 7, 81.0, 120)
    c = calc(data)
    assert c.offense(lobo, 30, 5) == pytest.approx(20.25)
    assert c.block(lobo, 4) == pytest.approx(8.1)
    assert c.defense_cap(lobo) == pytest.approx(60.75)


def test_overkill_nao_conta(data):
    m = MonsterXpInfo(1, 5, 63, 40)
    assert calc(data).offense(m, 400, 5) == pytest.approx(63)


def test_teto_de_xp_defensivo(data):
    bus = EventBus()
    profs = ProficiencySet(data, calc(data), bus)
    m = MonsterXpInfo(42, 5, 100, 50)
    for _ in range(100):
        bus.publish(HitBlocked(m))
        bus.publish(DamageMitigated(m, 10, 5))
    assert profs["escudo"].xp + profs["couraca"].xp == pytest.approx(75)
    bus.publish(MonsterKilled(m, "normal"))
    assert profs.defense_xp_from(42) == 0


def test_monstro_trivial(data):
    m = MonsterXpInfo(1, 1, 30, 10)
    c = calc(data)
    assert c.block(m, 11) == 0
    assert c.offense(m, 10, 11) == pytest.approx(30 * 0.05)


def test_xp_pelo_barramento_e_level_up(data):
    s = GameSession(data, 1)
    ups = []
    s.bus.subscribe(ProficiencyLeveledUp, ups.append)
    s.bus.publish(DamageDealt("laminas", MonsterXpInfo(1, 2, 400, 10), 10))
    assert s.proficiencies["laminas"].xp == pytest.approx(400)
    assert s.proficiencies["laminas"].level == 2
    assert len(ups) == 1


def test_cada_jogador_so_recebe_o_proprio_xp(data):
    bus = EventBus()
    p0 = ProficiencySet(data, calc(data), bus, player_id=0)
    p1 = ProficiencySet(data, calc(data), bus, player_id=1)
    m = MonsterXpInfo(1, 2, 100, 10)
    bus.publish(DamageDealt("laminas", m, 10, player_id=1))
    bus.publish(HitBlocked(m, player_id=0))
    assert p0["laminas"].xp == 0 and p1["laminas"].xp == 100
    assert p0["escudo"].xp == pytest.approx(10) and p1["escudo"].xp == 0


def test_nivel_e_titulo(data):
    assert compute_level([20, 12, 8], 0.25) == 25
    pd = data.proficiencies
    assert compute_title([("laminas", 10), ("escudo", 6), ("couraca", 2)], pd) == "Guardião"
    assert compute_title([("laminas", 10), ("escudo", 4), ("couraca", 2)], pd) == "Duelista"
    assert compute_title([("laminas", 3), ("escudo", 9), ("couraca", 8)], pd) == "Bastião"
    assert compute_title([("laminas", 1), ("escudo", 1), ("couraca", 1)], pd) == "Recruta"


def test_vila_feita_no_tiled(data):
    s = GameSession(data, 1)
    layout, packs = s.enter_area("vila")
    assert packs == [] and layout.validate() is None
    assert any(e.target_area == "planicie" for e in layout.exits)
    roles = {props.get("role") for _, props in layout.markers.values()}
    assert {"vendor", "stash"} <= roles
    assert layout.waypoint is not None


def test_atributos_pelo_perfil(data):
    s = GameSession(data, 1)
    s.proficiencies.set_xp("laminas", data.xp_total_by_level[4])  # nível 5
    a = s.character.attributes
    assert (a.str, a.dex, a.vit) == (18, 14, 10)
    assert s.character.level == 5
    assert s.character.max_life == pytest.approx(50 + 40 + 15)


def test_arvore_camadas_pontos_sinergias(data):
    s = GameSession(data, 1)
    t = s.skills
    assert t.available("laminas") == 1
    assert t.allocate("corte_amplo") is Allocate.LAYER_LOCKED
    assert t.allocate("lamina_afiada") is Allocate.OK
    assert t.allocate("lamina_afiada") is Allocate.NO_POINTS
    s.proficiencies.set_xp("laminas", data.xp_total_by_level[5])  # nível 6
    assert t.allocate("corte_amplo") is Allocate.OK
    assert t.allocate("golpe_escudo") is Allocate.OK
    assert t.effect("golpe_escudo", "damagePct") == pytest.approx(0.10 + 0.06)
    assert t.effective_points(10) == pytest.approx(9)
    t.respec("laminas")
    assert t.available("laminas") == 6


def test_simulacao_do_ato_1_na_meta(data):
    steps = run_act(data)
    assert [s.main_level for s in steps] == [5, 7, 8, 9, 10, 11, 12, 13, 13]
    last = steps[-1]
    assert 12 <= last.main_level <= 14, "meta de design: Lâminas 12–14 no chefe"
    assert last.shield_level == 9
    assert last.armor_level == 7
