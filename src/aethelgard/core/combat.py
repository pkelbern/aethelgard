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
    "melee_edge",
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
    reduction: float = 0.0  # share of the hit a normal block stops (capped by guard.blockReductionCap)


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
    stopped: float = 0.0  # stopped by the guard; armor then works on raw − stopped

    @property
    def reached_armor(self) -> float:
        return self.raw - self.stopped

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
        guarded = guard.raised and guard.in_arc  # a hit on the raised guard always reaches it: no dodging while blocking
        if not always_hit and not guarded and not rng.chance(self.hit_chance(a.accuracy, d.defense, a.level, d.level)):
            return MISS
        crit = rng.chance(a.crit_chance)
        raw = rng.uniform(a.dmg_min, a.dmg_max) * (1 + a.damage_bonus_pct) * (self.p.crit_multiplier if crit else 1)

        outcome, cost, share = BlockOutcome.NONE, 0.0, 0.0
        if guard.raised and guard.in_arc:
            if guard.perfect:  # no Energy, the most a block can stop
                outcome, share = BlockOutcome.PERFECT, self.g.block_reduction_cap
            elif guard.free_block:  # Bulwark: no Energy, normal share
                outcome, share = BlockOutcome.BLOCKED, guard.reduction
            else:
                cost = self.guard_cost(raw, guard.efficiency)
                if guard.energy >= cost:
                    outcome, share = BlockOutcome.BLOCKED, guard.reduction
                else:
                    outcome, cost = BlockOutcome.GUARD_BROKEN, guard.energy  # out of Energy: the guard breaks

        stopped = raw * self.block_share(share)
        passed = raw - stopped
        reduction = 1 - (1 - self.armor_reduction(d.armor, a.level)) * (1 - max(0.0, min(d.physical_reduction, 0.9)))
        mitigated = passed * reduction
        return AttackResult(True, outcome, crit, raw, mitigated, passed - mitigated, cost, stopped)

    def block_share(self, share: float) -> float:
        """A block never stops the whole hit: at most ``guard.blockReductionCap`` (GDD 7.4)."""
        return max(0.0, min(share, self.g.block_reduction_cap))


# ---------------------------------------------------------------- player


@dataclass(frozen=True)
class GuardProfile:
    can_guard: bool
    arc_degrees: float
    efficiency: float
    perfect_window: float
    block_reduction: float = 0.0


BASE_ACCURACY = 20.0
DEFENSE_PER_DEX = 2.0
ACCURACY_PER_DEX = 5.0
CRIT_PER_DEX = 0.002
DAMAGE_PER_STR = 0.01


def player_attack(sheet, skills, gear: Loadout, skill: str | None = None, hand: str = "main") -> AttackerStats:
    """STR: +1% damage per point. DEX: +5 accuracy and +0.2% crit per point (GDD 4.2)."""
    a = sheet.attributes
    w = gear.weapon(hand)
    dmg_min, dmg_max = (w.base.dmg_min, w.base.dmg_max) if w else (1, 2)
    if hand == "off" and gear.dual_wielding:  # the weaker hand (Off-Hand Training narrows the gap)
        mult = min(1.0, sheet.data.balance.dual_wield.off_hand_damage_mult + skills.effect("off_hand_training", "offHandDamagePct"))
        dmg_min, dmg_max = dmg_min * mult, dmg_max * mult
    bonus = a.str * DAMAGE_PER_STR
    acc = BASE_ACCURACY + a.dex * ACCURACY_PER_DEX
    crit = min(0.5, a.dex * CRIT_PER_DEX)
    tags = w.base.tags if w else []
    if "sword" in tags:  # Sharp Blade: one-hand blades only
        bonus += skills.effect("sharp_blade", "swordDamagePct")
    if "bow" in tags:
        bonus += skills.effect("steady_aim", "bowDamagePct")
        acc *= 1 + skills.effect("eagle_eye", "bowAccuracyPct")
        crit += skills.effect("hunters_mark", "bowCritChance")
    if skill:
        bonus += skills.effect(skill, "damagePct")
        acc *= 1 + skills.effect(skill, "accuracyPct")
    return AttackerStats(sheet.level, acc, dmg_min, dmg_max, bonus, min(0.75, crit))


def player_defense(sheet, skills, gear: Loadout) -> DefenderStats:
    # Iron Skin raises all armor; Heavy Shield only the shield's own (nothing without a shield)
    armor = gear.armor * (1 + skills.effect("iron_skin", "armorPct")) + gear.shield_armor * skills.effect("heavy_shield", "shieldArmorPct")
    return DefenderStats(sheet.level, sheet.attributes.dex * DEFENSE_PER_DEX, armor)


def player_guard(skills, gear: Loadout, g: GuardParams) -> GuardProfile:
    if not gear.has_shield:
        return GuardProfile(False, 0, 0, 0)
    arc = g.arc_degrees + skills.effect("heavy_shield", "guardArcDegrees")
    eff = gear.off_hand.base.guard_efficiency + skills.effect("defensive_stance", "guardEfficiency")
    window = g.perfect_window_seconds + skills.effect("parry", "perfectWindowSeconds")
    block = g.block_reduction + gear.off_hand.base.block_reduction + skills.effect("defensive_stance", "blockReduction")
    return GuardProfile(True, arc, min(eff, g.efficiency_cap), window, min(block, g.block_reduction_cap))


def monster_attack(m) -> AttackerStats:
    return AttackerStats(m.level, m.accuracy, m.dmg_min, m.dmg_max, m.damage_bonus_pct, m.stat("critChance"))


def monster_defense(m) -> DefenderStats:
    return DefenderStats(m.level, m.defense, 0.0, m.stat("physicalReduction"))


def melee_edge(a: AttackerStats, p: CombatParams) -> AttackerStats:
    """A melee hit on a ranged fighter: more damage and accuracy (players and monsters alike)."""
    return replace(a, damage_bonus_pct=a.damage_bonus_pct + p.melee_vs_ranged_damage_pct, accuracy=a.accuracy * (1 + p.melee_vs_ranged_accuracy_pct))


def with_bonus(a: AttackerStats, extra_pct: float, always_hit: bool = False) -> AttackerStats:
    return replace(a, damage_bonus_pct=a.damage_bonus_pct + extra_pct, accuracy=1e9 if always_hit else a.accuracy)
