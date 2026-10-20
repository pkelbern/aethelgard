"""Event bus and game events.

Combat publishes; progression, loot, HUD and achievements listen. Every player event carries
``player_id`` (0 in single player) to prepare for multiplayer: each character only listens to
its own events, and the host can relay them without changing the rules.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable[[Any], None]) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: Callable[[Any], None]) -> None:
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def publish(self, event: Any) -> None:
        for h in list(self._handlers[type(event)]):
            h(event)


@dataclass(frozen=True)
class MonsterXpInfo:
    """Monster data for the XP formulas. ``xp_base`` already includes the type's Exp% and the rank multiplier."""

    instance_id: int
    level: int
    xp_base: float
    max_hp: float


@dataclass(frozen=True)
class DamageDealt:
    proficiency_id: str
    target: MonsterXpInfo
    effective_damage: float
    player_id: int = 0


@dataclass(frozen=True)
class HitBlocked:
    attacker: MonsterXpInfo
    perfect: bool = False
    player_id: int = 0


@dataclass(frozen=True)
class DamageMitigated:
    attacker: MonsterXpInfo
    raw_damage: float
    mitigated_damage: float
    player_id: int = 0


@dataclass(frozen=True)
class MonsterKilled:
    monster: MonsterXpInfo
    rank: str
    killer_player_id: int | None = None


@dataclass(frozen=True)
class XpGained:
    proficiency_id: str
    amount: float
    source: str
    player_id: int = 0


@dataclass(frozen=True)
class ProficiencyLeveledUp:
    proficiency_id: str
    new_level: int
    player_id: int = 0


# ---- real-time world events (for HUD, sound and effects)


@dataclass(frozen=True)
class FloatingText:
    x: float
    y: float
    text: str
    kind: str  # damage | crit | miss | block | perfect | hurt | xp | info | levelup


@dataclass(frozen=True)
class PlayerDied:
    player_id: int


@dataclass(frozen=True)
class ExitReached:
    player_id: int
    kind: str
    target_area: str | None


@dataclass(frozen=True)
class SkillUsed:
    player_id: int
    skill_id: str


@dataclass(frozen=True)
class LootPicked:
    """A player tried to pick up a drop. ``stored`` is False when the backpack was full: it stays on the ground."""

    player_id: int
    drop: Any  # items.Drop (kept as Any so events.py does not import items.py)
    stored: bool = True
