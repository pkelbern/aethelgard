"""XP by use, proficiencies, character sheet and skill trees (GDD sections 4 to 6)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .data import GameData, ProficienciesData, ProficiencyDef, SkillsData, XpParams
from .events import (
    DamageDealt,
    DamageMitigated,
    EventBus,
    HitBlocked,
    MonsterKilled,
    MonsterXpInfo,
    ProficiencyLeveledUp,
    XpGained,
)

# ---------------------------------------------------------------- curves and formulas


class XpCurve:
    """Proficiency XP curve (GDD 5.7). ``total[n-1]`` = XP needed to be at level n."""

    def __init__(self, total_by_level: list[int]):
        self._total = total_by_level
        self.max_level = len(total_by_level)

    def total_for_level(self, level: int) -> int:
        return self._total[max(1, min(level, self.max_level)) - 1]

    def level_for_xp(self, xp: float) -> int:
        lvl = 1
        while lvl < self.max_level and xp >= self._total[lvl]:
            lvl += 1
        return lvl

    def progress(self, xp: float) -> float:
        lvl = self.level_for_xp(xp)
        if lvl >= self.max_level:
            return 1.0
        a, b = self._total[lvl - 1], self._total[lvl]
        return (xp - a) / (b - a)


class LevelFactor:
    """XP factor from the level difference (GDD 5.3)."""

    def __init__(self, table: dict[str, float]):
        self._t = {int(k): v for k, v in table.items()}
        self._min, self._max = min(self._t), max(self._t)

    def __call__(self, delta: int) -> float:
        d = max(self._min, min(delta, self._max))
        return self._t.get(d, 1.0)


class XpCalculator:
    """XP-by-use formulas (GDD 5.4 and 5.5)."""

    def __init__(self, factor: LevelFactor, params: XpParams):
        self._f = factor
        self._p = params

    def factor(self, monster_level: int, prof_level: int) -> float:
        return self._f(monster_level - prof_level)

    def offense(self, m: MonsterXpInfo, effective_damage: float, prof_level: int) -> float:
        if m.max_hp <= 0 or effective_damage <= 0:
            return 0.0
        return m.xp_base * min(1.0, effective_damage / m.max_hp) * self.factor(m.level, prof_level)

    def block(self, m: MonsterXpInfo, prof_level: int) -> float:
        if self.is_trivial(m.level, prof_level):
            return 0.0
        return m.xp_base * self._p.defense_xp_per_hit * self.factor(m.level, prof_level)

    def armor(self, m: MonsterXpInfo, raw: float, mitigated: float, prof_level: int) -> float:
        if raw <= 0 or mitigated <= 0 or self.is_trivial(m.level, prof_level):
            return 0.0
        return m.xp_base * self._p.defense_xp_per_hit * min(1.0, mitigated / raw) * self.factor(m.level, prof_level)

    def defense_cap(self, m: MonsterXpInfo) -> float:
        return m.xp_base * self._p.defense_xp_cap_per_monster

    def is_trivial(self, monster_level: int, prof_level: int) -> bool:
        return monster_level - prof_level <= self._p.trivial_monster_delta


# ---------------------------------------------------------------- proficiencies


class Proficiency:
    def __init__(self, definition: ProficiencyDef, curve: XpCurve, xp: float = 0.0):
        self.definition = definition
        self._curve = curve
        self.xp = xp
        self.level = curve.level_for_xp(xp)

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def progress(self) -> float:
        return self._curve.progress(self.xp)

    def add_xp(self, amount: float) -> int:
        """Adds XP and returns how many levels were gained."""
        if amount <= 0 or self.level >= self._curve.max_level:
            return 0
        before = self.level
        self.xp += amount
        self.level = self._curve.level_for_xp(self.xp)
        return self.level - before

    def restore(self, xp: float) -> None:
        self.xp = max(0.0, xp)
        self.level = self._curve.level_for_xp(self.xp)

    def lose_xp_within_level(self, fraction: float) -> float:
        """Loses a fraction of the XP earned inside the current level; never drops a level."""
        if fraction <= 0:
            return 0.0
        floor = self._curve.total_for_level(self.level)
        lost = max(0.0, self.xp - floor) * min(1.0, fraction)
        self.xp -= lost
        return lost


class ProficiencySet:
    """Proficiencies of ONE character. Listens to combat events and hands out XP (GDD 5)."""

    SHIELD = "shield"
    ARMOR = "armor"

    def __init__(self, data: GameData, calc: XpCalculator, bus: EventBus | None = None, player_id: int = 0):
        self._calc = calc
        self._bus = bus
        self.player_id = player_id
        curve = XpCurve(data.xp_total_by_level)
        self._profs = {d.id: Proficiency(d, curve) for d in data.proficiencies.proficiencies}
        self._defense_xp_by_monster: dict[int, float] = {}
        if bus is not None:
            bus.subscribe(DamageDealt, self.on_damage_dealt)
            bus.subscribe(HitBlocked, self.on_hit_blocked)
            bus.subscribe(DamageMitigated, self.on_damage_mitigated)
            bus.subscribe(MonsterKilled, lambda e: self._defense_xp_by_monster.pop(e.monster.instance_id, None))

    def __getitem__(self, prof_id: str) -> Proficiency:
        return self._profs[prof_id]

    def __contains__(self, prof_id: str) -> bool:
        return prof_id in self._profs

    def __iter__(self):
        return iter(self._profs.values())

    def set_xp(self, prof_id: str, xp: float) -> None:
        self._profs[prof_id].restore(xp)

    def on_damage_dealt(self, e: DamageDealt) -> None:
        if e.player_id != self.player_id or e.proficiency_id not in self._profs:
            return
        p = self._profs[e.proficiency_id]
        self._give(p, self._calc.offense(e.target, e.effective_damage, p.level), "offense")

    def on_hit_blocked(self, e: HitBlocked) -> None:
        if e.player_id != self.player_id:
            return
        p = self._profs[self.SHIELD]
        self._give_defensive(p, e.attacker, self._calc.block(e.attacker, p.level), "block")

    def on_damage_mitigated(self, e: DamageMitigated) -> None:
        if e.player_id != self.player_id:
            return
        p = self._profs[self.ARMOR]
        amount = self._calc.armor(e.attacker, e.raw_damage, e.mitigated_damage, p.level)
        self._give_defensive(p, e.attacker, amount, "armor")

    def defense_xp_from(self, monster_id: int) -> float:
        return self._defense_xp_by_monster.get(monster_id, 0.0)

    def _give_defensive(self, p: Proficiency, m: MonsterXpInfo, amount: float, source: str) -> None:
        if amount <= 0:
            return
        used = self._defense_xp_by_monster.get(m.instance_id, 0.0)
        give = min(max(0.0, self._calc.defense_cap(m) - used), amount)
        if give <= 0:
            return
        self._defense_xp_by_monster[m.instance_id] = used + give
        self._give(p, give, source)

    def _give(self, p: Proficiency, amount: float, source: str) -> None:
        if amount <= 0:
            return
        gained = p.add_xp(amount)
        if self._bus:
            self._bus.publish(XpGained(p.id, amount, source, self.player_id))
            if gained:
                self._bus.publish(ProficiencyLeveledUp(p.id, p.level, self.player_id))


# ---------------------------------------------------------------- character sheet


@dataclass(frozen=True)
class Attributes:
    str: int = 0
    dex: int = 0
    vit: int = 0
    int: int = 0

    def __add__(self, o: Attributes) -> Attributes:
        return Attributes(self.str + o.str, self.dex + o.dex, self.vit + o.vit, self.int + o.int)


class CharacterSheet:
    """Values derived from the proficiencies (GDD 4). Keeps no state of its own."""

    def __init__(self, data: GameData, profs: ProficiencySet):
        self._d = data
        self._profs = profs
        self.base = Attributes(10, 10, 10, 10)
        self.item_bonus = Attributes()

    @property
    def attributes(self) -> Attributes:
        s = d = v = i = 0
        for p in self._profs:
            n = p.level - 1
            a = p.definition.attributes
            s += n * a.get("str", 0)
            d += n * a.get("dex", 0)
            v += n * a.get("vit", 0)
            i += n * a.get("int", 0)
        return self.base + Attributes(s, d, v, i) + self.item_bonus

    @property
    def level(self) -> int:
        return compute_level([p.level for p in self._profs], self._d.balance.character.secondary_proficiency_weight)

    @property
    def max_life(self) -> float:
        c = self._d.balance.character
        return c.base_life + c.life_per_vit * self.attributes.vit + c.life_per_level * self.level

    @property
    def max_energy(self) -> float:
        c = self._d.balance.character
        a = self.attributes
        return c.base_energy + c.energy_per_int * a.int + c.energy_per_two_vit * (a.vit // 2)

    @property
    def title(self) -> str:
        return compute_title([(p.id, p.level) for p in self._profs], self._d.proficiencies)


def compute_level(levels: list[int], secondary_weight: float) -> int:
    ordered = sorted(levels, reverse=True)
    if not ordered:
        return 1
    return ordered[0] + math.floor(secondary_weight * sum(ordered[1:]))


def compute_title(levels: list[tuple[str, int]], pd: ProficienciesData) -> str:
    top = sorted(levels, key=lambda x: (-x[1], x[0]))[:2]
    if not top:
        return ""
    if top[0][1] <= 1:
        return pd.default_title
    ids = {top[0][0]}
    if len(top) > 1 and top[1][1] > 1 and top[1][1] >= top[0][1] * pd.title_secondary_threshold:
        ids.add(top[1][0])
    for t in pd.titles:
        if set(t.ids) == ids:
            return t.title
    for t in pd.titles:
        if t.ids == [top[0][0]]:
            return t.title
    return ""


# ---------------------------------------------------------------- trees


class Allocate(Enum):
    OK = "ok"
    NO_POINTS = "no points left in this tree"
    LAYER_LOCKED = "layer still locked"
    MAXED_OUT = "already maxed out"
    UNKNOWN = "unknown node"


class SkillTrees:
    """1 point per proficiency level in its own tree, level 1 included (GDD 6.1)."""

    def __init__(self, data: SkillsData, profs: ProficiencySet):
        self._d = data
        self._profs = profs
        self._nodes = {n.id: n for n in data.nodes}
        self.points: dict[str, int] = {}

    def node(self, node_id: str):
        return self._nodes[node_id]

    def nodes_of(self, tree: str):
        return [n for n in self._d.nodes if n.tree == tree]

    def points_in(self, node_id: str) -> int:
        return self.points.get(node_id, 0)

    def spent(self, tree: str) -> int:
        return sum(self.points_in(n.id) for n in self.nodes_of(tree))

    def available(self, tree: str) -> int:
        return max(0, self._profs[tree].level - self.spent(tree))

    def can_allocate(self, node_id: str) -> Allocate:
        n = self._nodes.get(node_id)
        if n is None:
            return Allocate.UNKNOWN
        if self._profs[n.tree].level < n.layer:
            return Allocate.LAYER_LOCKED
        if self.points_in(node_id) >= n.max_points:
            return Allocate.MAXED_OUT
        if self.available(n.tree) <= 0:
            return Allocate.NO_POINTS
        return Allocate.OK

    def allocate(self, node_id: str) -> Allocate:
        r = self.can_allocate(node_id)
        if r is Allocate.OK:
            self.points[node_id] = self.points_in(node_id) + 1
        return r

    def restore(self, points: dict[str, int]) -> list[str]:
        """Re-applies saved points through the normal rules. Returns the nodes that did not fit (bad or old save)."""
        rejected = []
        for node_id, pts in points.items():
            for _ in range(pts):
                if self.allocate(node_id) is not Allocate.OK:
                    rejected.append(node_id)
                    break
        return rejected

    def respec(self, tree: str) -> None:
        for n in self.nodes_of(tree):
            self.points.pop(n.id, None)

    def effective_points(self, points: int) -> float:
        n = self._d.diminishing_after
        return points if points <= n else n + (points - n) * 0.5

    def effect(self, node_id: str, stat: str) -> float:
        """Effect value with diminishing returns and synergies."""
        n = self._nodes[node_id]
        value = n.effects.get(stat, 0.0) * self.effective_points(self.points_in(node_id))
        for s in self._d.synergies:
            if s.target == node_id and s.stat == stat:
                value += s.per_point * self.points_in(s.from_)
        return value

    def stat_total(self, stat: str) -> float:
        """Sum of one effect over every node with points (for generic bonuses such as fasterHitRecovery)."""
        return sum(self.effect(nid, stat) for nid in self.points if stat in self._nodes[nid].effects)
