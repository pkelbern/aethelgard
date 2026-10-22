"""Co-op numbers (M10-03), Diablo 2 rules by default (balance.json ``party``).

Pure functions, not wired into the world yet: co-op itself is M10-04. With one player each one gives back the
single-player number, so wiring them in later changes nothing for single player (and the Act 1 simulation).
"""

from __future__ import annotations

from .data import PartyParams


def monster_hp_mult(p: PartyParams, players_in_game: int) -> float:
    """Monster life × (1 + hpPerExtraPlayer × (n − 1)); D2: (n + 1) / 2."""
    return 1.0 + p.hp_per_extra_player * max(0, players_in_game - 1)


def monster_xp_mult(p: PartyParams, players_in_game: int) -> float:
    return 1.0 + p.xp_per_extra_player * max(0, players_in_game - 1)


def split_kill_xp(p: PartyParams, xp: float, levels: dict[int, int]) -> dict[int, float]:
    """XP of one kill for the party members near it (player id → character level): +partyXpBonus for each
    other member, then shared in proportion to level (D2), so a low-level friend is not carried too fast."""
    if not levels:
        return {}
    total = xp * (1.0 + p.party_xp_bonus * (len(levels) - 1))
    level_sum = sum(max(1, lv) for lv in levels.values())
    return {pid: total * max(1, lv) / level_sum for pid, lv in levels.items()}


def no_drop_chance(p: PartyParams, chance: float, players_in_game: int) -> float:
    """More players, fewer empty kills: chance ** ((n + 1) // step) (D2 counts players two by two)."""
    power = max(1, (players_in_game + p.no_drop_players_step - 1) // p.no_drop_players_step)
    return chance**power
