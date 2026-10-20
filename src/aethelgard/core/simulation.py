"""Simulação por valor esperado de quem limpa o ato inteiro (GDD 5.8).

Use sempre que mudar curvas, multiplicadores ou áreas: ``python -m aethelgard.tools.simulate``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import GameData
from .progression import LevelFactor, XpCurve


@dataclass(frozen=True)
class SimulationOptions:
    average_exp_pct: float = 0.9
    main_share: float = 0.85
    shield_share: float = 0.30
    armor_share: float = 0.15


@dataclass(frozen=True)
class SimulationStep:
    label: str
    monster_level: int
    kill_xp: float
    main_level: int
    shield_level: int
    armor_level: int


def run_act(d: GameData, act_id: str = "ato1", o: SimulationOptions = SimulationOptions()) -> list[SimulationStep]:
    curve = XpCurve(d.xp_total_by_level)
    f = LevelFactor(d.balance.level_factor.table)
    s = d.balance.spawn
    act = next(a for a in d.areas.acts if a.id == act_id)
    champ, uniq, minion = d.rank("champion"), d.rank("unique"), d.rank("minion")
    avg_normal = (s.normal_pack_size_min + s.normal_pack_size_max) / 2
    avg_special = (s.special_packs_min + s.special_packs_max) / 2
    avg_champs = (s.champion_pack_size_min + s.champion_pack_size_max) / 2
    avg_minions = (s.minions_min + s.minions_max) / 2

    def xp_at(level: int, exp_pct: float = o.average_exp_pct) -> float:
        return d.monster_level(level).xp * exp_pct

    main = shield = armor = 0.0
    steps: list[SimulationStep] = []

    def apply(label: str, level: int, kill_xp_for) -> None:
        nonlocal main, shield, armor
        pm, ps, pa = curve.level_for_xp(main), curve.level_for_xp(shield), curve.level_for_xp(armor)
        kill = kill_xp_for(pm)
        fm = f(level - pm)
        main += kill * o.main_share
        if fm > 0:
            shield += kill * o.shield_share * f(level - ps) / fm
            armor += kill * o.armor_share * f(level - pa) / fm
        levels = (curve.level_for_xp(main), curve.level_for_xp(shield), curve.level_for_xp(armor))
        steps.append(SimulationStep(label, level, kill, *levels))

    for area in act.areas:
        if area.kind == "town":
            continue
        floors = max(1, area.floors) if area.kind == "dungeon" else 1
        for fl in range(floors):
            lv = area.level_for_floor(fl)
            normals = area.normal_packs * avg_normal
            label = f"{area.name} – andar {fl + 1}" if floors > 1 else area.name

            def kill_xp(p: int, lv: int = lv, normals: float = normals) -> float:
                total = normals * xp_at(lv) * f(lv - p)
                total += avg_special * s.unique_pack_chance * (
                    xp_at(lv + uniq.level_bonus) * uniq.xp_mult * f(lv + uniq.level_bonus - p)
                    + avg_minions * xp_at(lv + minion.level_bonus) * minion.xp_mult * f(lv + minion.level_bonus - p)
                )
                total += avg_special * (1 - s.unique_pack_chance) * avg_champs * (
                    xp_at(lv + champ.level_bonus) * champ.xp_mult * f(lv + champ.level_bonus - p)
                )
                return total

            apply(label, lv, kill_xp)
        if area.boss:
            boss = next(b for b in d.monsters.bosses if b.id == area.boss)
            rank = d.rank("boss")
            apply(boss.name, boss.level, lambda p, b=boss, r=rank: xp_at(b.level, b.exp_pct / 100) * r.xp_mult * f(b.level - p))
    return steps
