"""Tempos de ação (recargas, recuperação de golpe e de bloqueio) e o mundo em tempo real."""

import pytest

from aethelgard.core.events import ExitReached, HitBlocked, MonsterKilled, SkillUsed
from aethelgard.core.monsters import MonsterInstance
from aethelgard.core.session import GameSession
from aethelgard.core.timing import Cooldowns, monster_hit_recovery, player_block_recovery, player_hit_recovery, skill_cooldown
from aethelgard.core.world import AttackTarget as Attack
from aethelgard.core.world import MonsterActor, SetGuard, UseSkill, World
from aethelgard.core.world_gen import FLOOR, AreaLayout

DT = 1 / 60


# ---------------------------------------------------------------- funções de tempo


def test_cooldowns_contam_para_tras():
    c = Cooldowns()
    c.start("golpe_escudo", 4.0)
    assert not c.ready("golpe_escudo")
    c.tick(1.0)
    assert c.remaining("golpe_escudo") == pytest.approx(3.0)
    assert c.fraction("golpe_escudo") == pytest.approx(0.75)
    c.tick(3.01)
    assert c.ready("golpe_escudo") and c.fraction("golpe_escudo") == 0


def test_reducao_de_recarga_tem_teto():
    assert skill_cooldown(10, 0.2) == pytest.approx(8)
    assert skill_cooldown(10, 0.9) == pytest.approx(5), "teto de 50%"


def test_recuperacao_de_golpe_so_com_golpe_forte(data):
    t = data.balance.timing
    assert player_hit_recovery(t, 5, 120, 0) == 0, "menos de 1/12 da vida"
    assert player_hit_recovery(t, 10, 120, 0) == pytest.approx(0.40)
    assert player_hit_recovery(t, 10, 120, 1.0) == pytest.approx(0.20), "100% de recuperação rápida = metade do tempo"


def test_recuperacao_de_bloqueio(data):
    t = data.balance.timing
    assert player_block_recovery(t, 0) == pytest.approx(0.30)
    assert player_block_recovery(t, 0.5) == pytest.approx(0.20)


def test_recuperacao_de_monstro_por_rank(data):
    t = data.balance.timing
    assert monster_hit_recovery(t, 20, 100, "normal") == pytest.approx(0.35)
    assert monster_hit_recovery(t, 20, 100, "unique") == pytest.approx(0.175)
    assert monster_hit_recovery(t, 20, 100, "boss") == 0
    assert monster_hit_recovery(t, 5, 100, "normal") == 0


# ---------------------------------------------------------------- mundo de teste


def arena(data, *, monster_hp=1000.0, monster_dmg=(10, 10), monster_acc=1e9, at=(6.0, 5.0), level=3):
    """Sala vazia 12×12 com o jogador em (5,5) e um monstro controlado."""
    s = GameSession(data, 7)
    w = World([s])
    L = AreaLayout.filled(12, 12, FLOOR)
    L.entry = (5, 5)
    w.layout, w.area_id = L, "teste"
    p = w.players[0]
    p.x, p.y = 5.0, 5.0
    t = data.monster_type("bandido")
    m = MonsterInstance(
        instance_id=1, type=t, rank=data.rank("normal"), name="Alvo", level=level, max_hp=monster_hp, hp=monster_hp,
        dmg_min=monster_dmg[0], dmg_max=monster_dmg[1], damage_bonus_pct=0, accuracy=monster_acc, defense=0, xp_base=50,
    )
    w.monsters[1] = MonsterActor(m, at[0], at[1], pack_ids=[1])
    return s, w, p, w.monsters[1]


def run(w, seconds):
    for _ in range(round(seconds / DT)):
        w.step(DT)


def test_jogador_ataca_e_mata_monstro(data):
    s, w, p, m = arena(data, monster_hp=8, monster_dmg=(0.01, 0.01))
    killed = []
    s.bus.subscribe(MonsterKilled, killed.append)
    w.apply(Attack(0, 1))
    run(w, 6)
    assert killed, "monstro morreu"
    assert s.proficiencies["laminas"].xp > 0


def test_habilidade_respeita_recarga(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01))
    s.proficiencies.set_xp("escudo", 1)  # nível 1 já dá 1 ponto
    s.skills.allocate("golpe_escudo")
    used = []
    s.bus.subscribe(SkillUsed, used.append)
    assert w.apply(UseSkill(0, "golpe_escudo", 1)) is None
    run(w, 0.7)
    assert len(used) == 1
    assert 3.0 < p.cooldowns.remaining("golpe_escudo") <= 4.0
    reason = w.apply(UseSkill(0, "golpe_escudo", 1))
    assert reason and "recarga" in reason
    run(w, 3.5)
    assert w.apply(UseSkill(0, "golpe_escudo", 1)) is None


def test_golpe_de_escudo_atordoa(data):
    s, w, p, m = arena(data, level=1)  # nível 1: o golpe quase sempre acerta
    s.skills.allocate("golpe_escudo")
    w.apply(UseSkill(0, "golpe_escudo", 1))
    stunned = False
    for _ in range(60):
        w.step(DT)
        stunned = stunned or m.state == "stunned"
    assert stunned


def test_golpe_forte_causa_recuperacao_e_interrompe_ataque(data):
    s, w, p, m = arena(data, monster_dmg=(30, 30))  # vida do jogador ~103: 30 > 1/12
    w.apply(Attack(0, 1))
    states = set()
    for _ in range(240):
        w.step(DT)
        states.add(p.state)
    assert "hit_recovery" in states


def test_golpe_fraco_nao_causa_recuperacao(data):
    s, w, p, m = arena(data, monster_dmg=(2, 2))
    states = set()
    for _ in range(240):
        w.step(DT)
        states.add(p.state)
    assert "hit_recovery" not in states
    assert p.life < p.max_life


def test_bloqueio_gera_recuperacao_e_impede_ataque(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10))
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)
    w.apply(SetGuard(0, True, 1, 0))  # monstro está à direita (+x)
    run(w, 0.3)  # passa da janela perfeita
    seen_block_recovery = False
    for _ in range(180):
        w.step(DT)
        if p.state == "block_recovery":
            seen_block_recovery = True
            assert p.guarding
    assert blocks and not any(b.perfect for b in blocks[1:])
    assert seen_block_recovery
    assert p.life == pytest.approx(p.max_life), "nada passou pela guarda"
    assert s.proficiencies["escudo"].xp > 0


def test_guarda_tem_recarga_para_erguer_de_novo(data):
    s, w, p, m = arena(data, monster_dmg=(0.01, 0.01), at=(10.0, 10.0))
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert p.guarding
    w.apply(SetGuard(0, False, 1, 0))
    w.step(DT)
    assert not p.guarding
    w.apply(SetGuard(0, True, 1, 0))
    w.step(DT)
    assert not p.guarding, "ainda em recarga"
    run(w, 0.35)
    assert p.guarding


def test_aparo_perfeito_contra_ataca_com_aparar(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10))
    s.proficiencies.set_xp("escudo", data.xp_total_by_level[5])  # nível 6 libera Aparar
    s.skills.allocate("aparar")
    blocks = []
    s.bus.subscribe(HitBlocked, blocks.append)
    # ergue a guarda só no momento do golpe do monstro
    for _ in range(600):
        if m.state == "attacking" and m.attack_elapsed > m.attack_duration * 0.5 - 0.1 and not p.guarding:
            w.apply(SetGuard(0, True, 1, 0))
        if p.guarding and p.guard_time > 0.2:
            w.apply(SetGuard(0, False, 1, 0))
        w.step(DT)
        if any(b.perfect for b in blocks):
            break
    assert any(b.perfect for b in blocks)
    assert m.monster.hp < m.monster.max_hp, "contra-ataque do Aparar"


def test_muralha_bloqueia_por_tras_sem_custo(data):
    s, w, p, m = arena(data, monster_dmg=(10, 10), at=(4.0, 5.0))  # monstro atrás (−x)
    s.proficiencies.set_xp("escudo", data.xp_total_by_level[11])  # nível 12
    s.skills.allocate("muralha")
    p.reset_vitals()
    w.apply(SetGuard(0, True, 1, 0))  # olhando para +x
    run(w, 0.3)
    assert w.apply(UseSkill(0, "muralha")) is None
    energy = p.energy
    run(w, 2.0)
    assert p.life == pytest.approx(p.max_life)
    assert p.energy >= energy - 0.5, "Muralha não gasta Energia por bloqueio"
    assert w.apply(UseSkill(0, "muralha")).startswith("Muralha: recarga")


def test_saida_de_area_emite_evento(data):
    s = GameSession(data, 3)
    w = World([s])
    w.enter_area("planicie")
    exits = []
    s.bus.subscribe(ExitReached, exits.append)
    p = w.players[0]
    target = next(e for e in w.layout.exits if e.target_area == "vila")
    w.monsters.clear()
    p.x, p.y = float(target.pos[0]), float(target.pos[1])
    run(w, 1.0)
    assert exits and exits[0].target_area == "vila"


def test_chegada_ao_lado_da_saida_de_origem(data):
    s = GameSession(data, 3)
    w = World([s])
    w.enter_area("planicie", came_from="caverna")
    p = w.players[0]
    entrance = next(e for e in w.layout.exits if e.target_area == "caverna")
    assert (round(p.x), round(p.y)) != entrance.pos
    assert abs(p.x - entrance.pos[0]) + abs(p.y - entrance.pos[1]) <= 2
