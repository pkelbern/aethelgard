"""Barramento de eventos e eventos de jogo.

O combate publica; progressão, loot, HUD e conquistas escutam. Todo evento de jogador carrega
``player_id`` (0 no single-player) — preparação para o multiplayer: cada personagem só ouve os
próprios eventos, e o host pode retransmiti-los sem mudar as regras.
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
    """Dados do monstro para calcular XP. ``xp_base`` já inclui Exp% do tipo e multiplicador do rank."""

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


# ---- eventos do mundo em tempo real (para HUD, som e efeitos)


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
