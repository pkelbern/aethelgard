"""Simulação de progressão do Ato 1: ``python -m aethelgard.tools.simulate``."""

from ..core.data import GameData
from ..core.simulation import run_act


def main() -> None:
    d = GameData.load()
    print(f"{'Etapa':34} {'Nível':>5} {'XP do abate':>12} {'Lâminas':>8} {'Escudo':>7} {'Couraça':>8}")
    for s in run_act(d):
        print(f"{s.label:34} {s.monster_level:>5} {s.kill_xp:>12.0f} {s.main_level:>8} {s.shield_level:>7} {s.armor_level:>8}")


if __name__ == "__main__":
    main()
