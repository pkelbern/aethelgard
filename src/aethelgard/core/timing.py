"""Action timings (GDD 7.7): skill cooldowns, hit recovery and block recovery.

Everything in seconds. "faster…" bonuses shorten the duration like in D2, but continuously:
``duration = base / (1 + bonus)``. Skill cooldown: ``cooldown × (1 − reduction)``, with the
reduction capped at 50%.
"""

from __future__ import annotations

from .data import TimingParams

MAX_COOLDOWN_REDUCTION = 0.5


class Cooldowns:
    """Countdown per key (skill id, 'guard', 'global'...)."""

    def __init__(self) -> None:
        self._left: dict[str, float] = {}
        self._total: dict[str, float] = {}

    def start(self, key: str, seconds: float) -> None:
        if seconds <= 0:
            self._left.pop(key, None)
            return
        self._left[key] = seconds
        self._total[key] = seconds

    def remaining(self, key: str) -> float:
        return self._left.get(key, 0.0)

    def fraction(self, key: str) -> float:
        """Fraction left (1 = just started, 0 = ready) — for the HUD animation."""
        left = self._left.get(key, 0.0)
        total = self._total.get(key, 0.0)
        return left / total if total > 0 else 0.0

    def ready(self, key: str) -> bool:
        return self._left.get(key, 0.0) <= 0

    def tick(self, dt: float) -> None:
        done = []
        for k in self._left:
            self._left[k] -= dt
            if self._left[k] <= 0:
                done.append(k)
        for k in done:
            del self._left[k]

    def active(self) -> dict[str, float]:
        return dict(self._left)


def scaled(base: float, faster: float) -> float:
    return base / (1 + max(0.0, faster))


def skill_cooldown(base: float, reduction: float) -> float:
    return base * (1 - max(0.0, min(reduction, MAX_COOLDOWN_REDUCTION)))


def player_hit_recovery(t: TimingParams, damage: float, max_life: float, faster: float) -> float:
    """Player hit recovery: only when the hit takes ≥ 1/12 of max life (as in D2)."""
    if max_life <= 0 or damage < max_life * t.hit_recovery_threshold_pct_life:
        return 0.0
    return scaled(t.hit_recovery_seconds, faster)


def player_block_recovery(t: TimingParams, faster: float) -> float:
    return scaled(t.block_recovery_seconds, faster)


def monster_hit_recovery(t: TimingParams, damage: float, max_hp: float, rank: str) -> float:
    if max_hp <= 0 or damage < max_hp * t.monster_hit_recovery_threshold_pct_life:
        return 0.0
    mult = {"boss": t.boss_hit_recovery_mult, "unique": t.unique_hit_recovery_mult}.get(rank, 1.0)
    return t.monster_hit_recovery_seconds * mult
