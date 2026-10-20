"""Imprime as tabelas geradas pelas curvas de data/curves.json: ``python -m aethelgard.tools.curves``."""

from ..core.data import GameData


def main() -> None:
    d = GameData.load()
    print("Proficiência: nível, XP total, XP para o próximo, abates de monstro do mesmo nível")
    t = d.xp_total_by_level
    for n in range(1, len(t)):
        need = t[n] - t[n - 1]
        print(f"{n:>3} {t[n - 1]:>10} {need:>9} {need / (d.monster_level(n).xp * 0.9):>8.0f}")
    print("\nMonstro: nível, vida %, dano %, XP, defesa, precisão")
    for r in d.monster_levels[:40]:
        print(f"{r.level:>3} {r.hp_pct:>7} {r.dmg_pct:>6} {r.xp:>7} {r.defense:>6} {r.accuracy:>7}")


if __name__ == "__main__":
    main()
