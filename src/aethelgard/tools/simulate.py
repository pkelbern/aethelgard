"""Progression simulation, act by act with the same character: ``python -m aethelgard.tools.simulate``."""

from ..core.data import GameData
from ..core.simulation import run_campaign


def main() -> None:
    d = GameData.load()
    act_of = {a.name: act.name for act in d.areas.acts for a in act.areas}
    print(f"{'Step':34} {'Level':>5} {'Kill XP':>12} {'Blades':>8} {'Shield':>7} {'Armor':>8}")
    shown = None
    for s in run_campaign(d):
        act = next((name for area, name in act_of.items() if s.label.startswith(area)), shown)
        if act != shown:
            print(f"-- {act}")
            shown = act
        print(f"{s.label:34} {s.monster_level:>5} {s.kill_xp:>12.0f} {s.main_level:>8} {s.shield_level:>7} {s.armor_level:>8}")


if __name__ == "__main__":
    main()
