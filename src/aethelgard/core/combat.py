"""Combat formulas (GDD 7). Order: hit → guard → armor → final damage."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from .data import CombatParams, GuardParams
from .items import Loadout
from .rng import Rng

__all__ = [
    "AttackResult",
    "AttackerStats",
    "BlockOutcome",
    "CombatMath",
    "DefenderStats",
    "GuardProfile",
    "GuardState",
    "Loadout",
    "monster_attack",
    "monster_defense",
    "player_attack",
    "player_defense",
    "player_guard",
    "with_bonus",
]


@dataclass(frozen=True)
class AttackerStats:
    level: int
    accuracy: float
    dmg_min: float
    dmg_max: float
    damage_bonus_pct: float = 0.0
    crit_chance: float = 0.0


@dataclass(frozen=True)
class DefenderStats:
    """Passive defense. Blocking is active and comes in :class:`GuardState`."""

    level: int
    defense: float
    armor: float = 0.0
    physical_reduction: float = 0.0


@dataclass(frozen=True)
class GuardState:
    """Guard at the moment of the hit: raised, attacker inside the arc, perfect window, efficiency, Energy."""

    raised: bool = False
    in_arc: bool = False
    perfect: bool = False
    efficiency: float = 0.0
    energy: float = 0.0
    free_block: bool = False


NO_GUARD = GuardState()


class BlockOutcome(Enum):
    NONE = "none"
    BLOCKED = "blocked"
    PERFECT = "perfect"
    GUARD_BROKEN = "guard_broken"


@dataclass(frozen=True)
class AttackResult:
    hit: bool
    block: BlockOutcome = BlockOutcome.NONE
    crit: bool = False
    raw: float = 0.0
    mitigated: float = 0.0
    final: float = 0.0
    guard_energy_cost: float = 0.0

    @property
    def blocked(self) -> bool:
        return self.block in (BlockOutcome.BLOCKED, BlockOutcome.PERFECT)


MISS = AttackResult(hit=False)


class CombatMath:
    def __init__(self, combat: CombatParams, guard: GuardParams):
        self.p = combat
        self.g = guard

    def hit_chance(self, accuracy: float, defense: float, att_level: int, def_level: int) -> float:
        """D2 style: 2 × ACC/(ACC+DEF) × ALvl/(ALvl+DLvl), clamped to [5%, 95%]."""
        if accuracy + defense <= 0:
            return self.p.hit_chance_max
        c = 2.0 * accuracy / (accuracy + defense) * att_level / (att_level + def_level)
        return max(self.p.hit_chance_min, min(c, self.p.hit_chance_max))

    def armor_reduction(self, armor: float, attacker_level: int) -> float:
        if armor <= 0:
            return 0.0
        r = armor / (armor + self.p.armor_per_attacker_level * max(1, attacker_level))
        return min(r, self.p.armor_cap)

    def guard_cost(self, raw: float, efficiency: float) -> float:
        eff = max(0.0, min(efficiency, self.g.efficiency_cap))
        return (self.g.base_energy_cost + raw * self.g.energy_per_damage) * (1 - eff)

    @staticmethod
    def in_arc(face_x: float, face_y: float, to_x: float, to_y: float, arc_degrees: float) -> bool:
        if arc_degrees >= 360:
            return True
        lf, la = math.hypot(face_x, face_y), math.hypot(to_x, to_y)
        if lf < 1e-9 or la < 1e-9:
            return True
        cos = (face_x * to_x + face_y * to_y) / (lf * la)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        return angle <= arc_degrees / 2 + 1e-9

    def resolve_physical(
        self, a: AttackerStats, d: DefenderStats, rng: Rng, guard: GuardState = NO_GUARD, always_hit: bool = False
    ) -> AttackResult:
        if not always_hit and not rng.chance(self.hit_chance(a.accuracy, d.defense, a.level, d.level)):
            return MISS
        crit = rng.chance(a.crit_chance)
        raw = rng.uniform(a.dmg_min, a.dmg_max) * (1 + a.damage_bonus_pct) * (self.p.crit_multiplier if crit else 1)

        outcome, cost = BlockOutcome.NONE, 0.0
        if guard.raised and guard.in_arc:
            if guard.perfect:
                return AttackResult(True, BlockOutcome.PERFECT, crit, raw, raw, 0.0, 0.0)
            if guard.free_block:
                return AttackResult(True, BlockOutcome.BLOCKED, crit, raw, raw, 0.0, 0.0)
            cost = self.guard_cost(raw, guard.efficiency)
            if guard.energy >= cost:
                return AttackResult(True, BlockOutcome.BLOCKED, crit, raw, raw, 0.0, cost)
            outcome, cost = BlockOutcome.GUARD_BROKEN, guard.energy  # out of Energy: the guard breaks

        reduction = 1 - (1 - self.armor_reduction(d.armor, a.level)) * (1 - max(0.0, min(d.physical_reduction, 0.9)))
        mitigated = raw * reduction
        return AttackResult(True, outcome, crit, raw, mitigated, raw - mitigated, cost)


# ---------------------------------------------------------------- player


@dataclass(frozen=True)
class GuardProfile:
    can_guard: bool
    arc_degrees: float
    efficiency: float
    perfect_window: float


BASE_ACCURACY = 20.0
DEFENSE_PER_DEX = 2.0


def player_attack(sheet, skills, gear: Loadout, skill: str | None = None) -> AttackerStats:
    """STR: +1% damage per point. DEX: +5 accuracy and +0.2% crit per point (GDD 4.2)."""
    a = sheet.attributes
    w = gear.main_hand
    dmg_min, dmg_max = (w.base.dmg_min, w.base.dmg_max) if w else (1, 2)
    bonus = a.str * 0.01 + skills.effect("sharp_blade", "swordDamagePct")
    acc = BASE_ACCURACY + a.dex * 5
    if skill:
        bonus += skills.effect(skill, "damagePct")
        acc *= 1 + skills.effect(skill, "accuracyPct")
    return AttackerStats(sheet.level, acc, dmg_min, dmg_max, bonus, min(0.5, a.dex * 0.002))


def player_defense(sheet, skills, gear: Loadout) -> DefenderStats:
    armor = gear.armor * (1 + skills.effect("iron_skin", "armorPct") + skills.effect("heavy_shield", "shieldArmorPct"))
    return DefenderStats(sheet.level, sheet.attributes.dex * DEFENSE_PER_DEX, armor)


def player_guard(skills, gear: Loadout, g: GuardParams) -> GuardProfile:
    if not gear.has_shield:
        return GuardProfile(False, 0, 0, 0)
    arc = g.arc_degrees + skills.effect("heavy_shield", "guardArcDegrees")
    eff = gear.off_hand.base.guard_efficiency + skills.effect("defensive_stance", "guardEfficiency")
    window = g.perfect_window_seconds + skills.effect("parry", "perfectWindowSeconds")
    return GuardProfile(True, arc, min(eff, g.efficiency_cap), window)


def monster_attack(m) -> AttackerStats:
    return AttackerStats(m.level, m.accuracy, m.dmg_min, m.dmg_max, m.damage_bonus_pct, m.stat("critChance"))


def monster_defense(m) -> DefenderStats:
    return DefenderStats(m.level, m.defense, 0.0, m.stat("physicalReduction"))


def with_bonus(a: AttackerStats, extra_pct: float, always_hit: bool = False) -> AttackerStats:
    return replace(a, damage_bonus_pct=a.damage_bonus_pct + extra_pct, accuracy=1e9 if always_hit else a.accuracy)
