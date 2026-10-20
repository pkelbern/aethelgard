"""Deterministic pseudo-random generator (xoshiro256**).

Same seed → same sequence, on any machine and Python version. Use one generator per system
(world, combat, loot, spawn) with :meth:`Rng.fork`, so that changing one system does not alter
the results of the others. Never use the ``random`` module in game rules.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from typing import TypeVar

T = TypeVar("T")
MASK = (1 << 64) - 1


def _rotl(x: int, k: int) -> int:
    return ((x << k) | (x >> (64 - k))) & MASK


def _splitmix64(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & MASK
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
    return state, z ^ (z >> 31)


def stable_hash(text: str) -> int:
    """FNV-1a 64-bit — stable across runs (Python's ``hash()`` is not)."""
    h = 14695981039346656037
    for ch in text.encode("utf-8"):
        h ^= ch
        h = (h * 1099511628211) & MASK
    return h


def _combine(a: int, b: int) -> int:
    x = (a ^ (b + 0x9E3779B97F4A7C15 + ((a << 6) & MASK) + (a >> 2))) & MASK
    return _splitmix64(x)[1]


class Rng:
    __slots__ = ("seed", "_s")

    def __init__(self, seed: int):
        self.seed = seed & MASK
        st = self.seed
        s = []
        for _ in range(4):
            st, v = _splitmix64(st)
            s.append(v)
        self._s = s

    @classmethod
    def for_label(cls, master_seed: int, label: str) -> Rng:
        return cls(_combine(master_seed & MASK, stable_hash(label)))

    def fork(self, label: str) -> Rng:
        return Rng.for_label(self.seed, label)

    def next_u64(self) -> int:
        s = self._s
        result = (_rotl((s[1] * 5) & MASK, 7) * 9) & MASK
        t = (s[1] << 17) & MASK
        s[2] ^= s[0]
        s[3] ^= s[1]
        s[1] ^= s[2]
        s[0] ^= s[3]
        s[2] ^= t
        s[3] = _rotl(s[3], 45)
        return result

    def random(self) -> float:
        """[0, 1)"""
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def randint(self, lo: int, hi: int) -> int:
        """Integer in [lo, hi], inclusive."""
        if hi < lo:
            raise ValueError("hi < lo")
        return lo + self.next_u64() % (hi - lo + 1)

    def uniform(self, lo: float, hi: float) -> float:
        return lo + self.random() * (hi - lo)

    def chance(self, p: float) -> bool:
        return self.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("empty sequence")
        return seq[self.randint(0, len(seq) - 1)]

    def weighted(self, weights: Mapping[str, float]) -> str:
        keys = sorted(weights)
        r = self.random() * sum(weights.values())
        for k in keys:
            if r < weights[k]:
                return k
            r -= weights[k]
        return keys[-1]

    def shuffle(self, seq: MutableSequence[T]) -> None:
        for i in range(len(seq) - 1, 0, -1):
            j = self.randint(0, i)
            seq[i], seq[j] = seq[j], seq[i]
