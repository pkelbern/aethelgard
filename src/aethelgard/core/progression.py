"""XP by use, proficiencies, character sheet and skill trees (GDD sections 4 to 6)."""

from __future__ import annotations

import math
from collections.abc import Callable
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
    SkillLeveledUp,
    SkillPracticed,
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
        share = min(1.0, mitigated / raw / max(1e-9, self._p.armor_xp_full_at_mitigation))
        return m.xp_base * self._p.defense_xp_per_hit * share * self.factor(m.level, prof_level)

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
        self.curve = curve = XpCurve(data.xp_total_by_level)
        self._skill_tree_of = {n.id: n.tree for n in data.skills.nodes}.get
        self._profs = {d.id: Proficiency(d, curve) for d in data.proficiencies.proficiencies}
        self._defense_xp_by_monster: dict[int, float] = {}
        if bus is not None:
            bus.subscribe(DamageDealt, self.on_damage_dealt)
            bus.subscribe(HitBlocked, self.on_hit_blocked)
            bus.subscribe(DamageMitigated, self.on_damage_mitigated)
            bus.subscribe(SkillPracticed, self.on_practiced)
            bus.subscribe(MonsterKilled, lambda e: self._defense_xp_by_monster.pop(e.monster.instance_id, None))

    def __getitem__(self, prof_id: str) -> Proficiency:
        return self._profs[prof_id]

    @property
    def bus(self) -> EventBus | None:
        return self._bus

    @property
    def calc(self) -> XpCalculator:
        return self._calc

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

    def on_practiced(self, e: SkillPracticed) -> None:
        """A skill that hits nothing (a heal, a blessing, a war cry) used near a monster: its tree learns from it."""
        node = self._skill_tree_of(e.skill_id)
        if e.player_id != self.player_id or node not in self._profs:
            return
        p = self._profs[node]
        if not self._calc.is_trivial(e.target.level, p.level):
            self._give(p, e.target.xp_base * e.share * self._calc.factor(e.target.level, p.level), "practice")

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

    def __init__(self, data: GameData, profs: ProficiencySet, bonus: Callable[[str], float] | None = None):
        self._d = data
        self._profs = profs
        self.base = Attributes(10, 10, 10, 10)
        self._bonus = bonus or (lambda stat: 0.0)  # equipment and passives by stat (GameSession.stat)

    @property
    def item_bonus(self) -> Attributes:
        """Attributes from what is worn (affixes, uniques, sets)."""
        b = self._bonus
        return Attributes(round(b("str")), round(b("dex")), round(b("vit")), round(b("int")))

    @property
    def data(self) -> GameData:
        return self._d

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
        """Base + Vitality + level, + flat life from items, then maxLifePct (Colossus)."""
        c = self._d.balance.character
        flat = c.base_life + c.life_per_vit * self.attributes.vit + c.life_per_level * self.level + self._bonus("maxLife")
        return flat * (1 + self._bonus("maxLifePct"))

    @property
    def max_energy(self) -> float:
        c = self._d.balance.character
        a = self.attributes
        return c.base_energy + c.energy_per_int * a.int + c.energy_per_two_vit * (a.vit // 2) + self._bonus("maxEnergy")

    @property
    def title(self) -> str:
        return compute_title([(p.id, p.level) for p in self._profs], self._d.proficiencies)


def compute_level(levels: list[int], secondary_weight: float) -> int:
    ordered = sorted(levels, reverse=True)
    if not ordered:
        return 1
    # proficiencies never used (still level 1) add nothing: a new proficiency must not raise everyone's level
    return ordered[0] + math.floor(secondary_weight * sum(lv for lv in ordered[1:] if lv > 1))


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
    REQUIRES = "take the skills before it first"
    MAXED_OUT = "already maxed out"
    BY_USE = "already unlocked: it levels up as you use it"
    UNKNOWN = "unknown node"


class SkillTrees:
    """One tree per proficiency, 1 point per proficiency level, level 1 included (GDD 6.1).

    * **Passives** take points: one level per point, up to ``max_points``.
    * **Actives** cost one point to unlock and then level up **by use only**: every hit of the skill gives it
      its own XP (the proficiency XP of that hit × ``skillXpRate``), on the same curve as the proficiencies.
      A skill never used stays at level 1, whatever the proficiency level. ``levelGates`` hold an active
      back until a passive has enough points; its XP keeps building and the levels arrive when the gate opens.
    * ``requires``: an active needs the actives before it unlocked; a passive needs a point in the passives
      before it.
    * Worn wands add levels to their spell (``granted``, read from the gear every time), even before it is
      unlocked, so it can be cast (and practised) while the wand is worn.

    ``points_in`` is the node's level with all of that, so every rule that reads a level (effects, the action
    bar, the cast) sees the same number.
    """

    def __init__(self, data: SkillsData, profs: ProficiencySet, granted: Callable[[], dict[str, int]] | None = None,
                 tree_bonus: Callable[[str], float] | None = None):
        self._d = data
        self._profs = profs
        self._nodes = {n.id: n for n in data.nodes}
        self._granted = granted or dict
        self._tree_bonus = tree_bonus or (lambda tree: 0.0)  # +N to every skill of a tree you have (items: <tree>Skills)
        self._curve = profs.curve
        self.points: dict[str, int] = {}  # tree points in passives
        self.unlocked: set[str] = set()  # actives bought with a point
        self.xp: dict[str, float] = {}  # actives: XP by use
        bus = profs.bus
        if bus is not None:
            bus.subscribe(DamageDealt, self.on_damage_dealt)
            bus.subscribe(SkillPracticed, self.on_practiced)

    def node(self, node_id: str):
        return self._nodes[node_id]

    def nodes_of(self, tree: str):
        return [n for n in self._d.nodes if n.tree == tree]

    # ---- levels

    def is_active(self, node_id: str) -> bool:
        n = self._nodes.get(node_id)
        return n is not None and n.kind == "active"

    def is_spell(self, node_id: str) -> bool:
        n = self._nodes.get(node_id)
        return n is not None and n.spell is not None

    def taken(self, node_id: str) -> bool:
        """Unlocked (an active) or with a point (a passive): what ``requires`` asks for."""
        return node_id in self.unlocked if self.is_active(node_id) else self.points.get(node_id, 0) > 0

    def missing_requirements(self, node_id: str) -> list[str]:
        return [r for r in self._nodes[node_id].requires if not self.taken(r)]

    def xp_level(self, node_id: str) -> int:
        """The level the active's XP is worth, before gates (1 with no XP)."""
        return self._curve.level_for_xp(self.xp.get(node_id, 0.0))

    def xp_progress(self, node_id: str) -> float:
        """0..1 of the way to the next level by XP (for the tree's bar)."""
        return self._curve.progress(self.xp.get(node_id, 0.0))

    def closed_gates(self, node_id: str) -> list:
        return [g for g in self._nodes[node_id].level_gates if self.points.get(g.node, 0) < g.points]

    def gate_holding(self, node_id: str):
        """The gate that is keeping this active below the level its XP is worth, if any."""
        xp_level = self.xp_level(node_id)
        held = [g for g in self.closed_gates(node_id) if g.level <= xp_level]
        return min(held, key=lambda g: g.level) if held else None

    def own_level(self, node_id: str) -> int:
        """Level without wands: passive points, or the unlocked active's XP level held by its gates."""
        n = self._nodes[node_id]
        if n.kind != "active":
            return self.points.get(node_id, 0)
        if node_id not in self.unlocked:
            return 0
        cap = min([g.level - 1 for g in self.closed_gates(node_id)], default=self._curve.max_level)
        return max(1, min(self.xp_level(node_id), cap))

    def granted(self, node_id: str) -> int:
        return self._granted().get(node_id, 0)

    def points_in(self, node_id: str) -> int:
        """The node's level: its own level + what worn wands give + "+N to <tree> skills" from items, which only
        raises skills you already have (as in Diablo 2)."""
        own = self.own_level(node_id)
        extra = int(self._tree_bonus(self._nodes[node_id].tree)) if own > 0 else 0
        return own + extra + self.granted(node_id)

    def spent(self, tree: str) -> int:
        return sum(self.points.get(n.id, 0) + (n.id in self.unlocked) for n in self.nodes_of(tree))

    def available(self, tree: str) -> int:
        return max(0, self._profs[tree].level - self.spent(tree))

    def level_of(self, tree: str) -> int:
        return self._profs[tree].level

    # ---- points

    def can_allocate(self, node_id: str) -> Allocate:
        n = self._nodes.get(node_id)
        if n is None:
            return Allocate.UNKNOWN
        if n.kind == "active" and node_id in self.unlocked:
            return Allocate.BY_USE
        if self._profs[n.tree].level < n.layer:
            return Allocate.LAYER_LOCKED
        if self.missing_requirements(node_id):
            return Allocate.REQUIRES
        if n.kind != "active" and self.points.get(node_id, 0) >= n.max_points:
            return Allocate.MAXED_OUT
        if self.available(n.tree) <= 0:
            return Allocate.NO_POINTS
        return Allocate.OK

    def allocate(self, node_id: str) -> Allocate:
        r = self.can_allocate(node_id)
        if r is not Allocate.OK:
            return r
        if self.is_active(node_id):
            self.unlocked.add(node_id)
            return r
        gated = {a.id: self.own_level(a.id) for a in self._d.nodes if any(g.node == node_id for g in a.level_gates)}
        self.points[node_id] = self.points.get(node_id, 0) + 1
        for a, before in gated.items():  # a gate opened: the levels the XP had built arrive now
            self._announce(a, before)
        return r

    def restore(self, points: dict[str, int], unlocked=(), xp: dict[str, float] | None = None) -> list[str]:
        """Re-applies a save through the normal rules, in as many passes as prerequisites need.
        Returns the nodes that did not fit (bad or old save)."""
        self.xp.update({k: max(0.0, float(v)) for k, v in (xp or {}).items() if self.is_active(k)})
        todo = {k: 1 for k in unlocked if self.is_active(k)} | {k: v for k, v in points.items() if not self.is_active(k)}
        progress = True
        while todo and progress:
            progress = False
            for node_id in list(todo):
                while todo.get(node_id, 0) > 0 and self.allocate(node_id) is Allocate.OK:
                    todo[node_id] -= 1
                    progress = True
                if todo.get(node_id) == 0:
                    del todo[node_id]
        return sorted(todo)

    def respec(self, tree: str) -> None:
        """Gives back the tree's points. What the actives learned by use stays (unlock them again to use it)."""
        for n in self.nodes_of(tree):
            self.points.pop(n.id, None)
            self.unlocked.discard(n.id)

    def set_level(self, node_id: str, level: int) -> None:
        """Debug console and tests: puts a node at ``level`` outside the rules (an active gets the XP of that level,
        and its gates still apply)."""
        n = self._nodes[node_id]
        if n.kind == "active":
            if level <= 0:
                self.unlocked.discard(node_id)
            else:
                self.unlocked.add(node_id)
                self.xp[node_id] = float(self._curve.total_for_level(level))
        elif level <= 0:
            self.points.pop(node_id, None)
        else:
            self.points[node_id] = min(level, n.max_points)

    # ---- XP by use

    def add_xp(self, node_id: str, amount: float) -> None:
        if amount <= 0 or not self.is_active(node_id):
            return
        before = self.own_level(node_id)
        self.xp[node_id] = self.xp.get(node_id, 0.0) + amount
        self._announce(node_id, before)

    def _announce(self, node_id: str, before: int) -> None:
        bus = self._profs.bus
        after = self.own_level(node_id)
        if bus is not None and after > before and node_id in self.unlocked:
            bus.publish(SkillLeveledUp(self._profs.player_id, node_id, after))

    def on_damage_dealt(self, e: DamageDealt) -> None:
        if e.player_id != self._profs.player_id or not self.is_active(e.skill_id):
            return
        amount = self._profs.calc.offense(e.target, e.effective_damage, self.xp_level(e.skill_id))
        self.add_xp(e.skill_id, amount * self._d.skill_xp_rate)

    def on_practiced(self, e: SkillPracticed) -> None:
        if e.player_id != self._profs.player_id or not self.is_active(e.skill_id):
            return
        calc, lvl = self._profs.calc, self.xp_level(e.skill_id)
        if not calc.is_trivial(e.target.level, lvl):
            self.add_xp(e.skill_id, e.target.xp_base * e.share * calc.factor(e.target.level, lvl))

    # ---- effects

    def effective_points(self, points: int) -> float:
        n = self._d.diminishing_after
        return points if points <= n else n + (points - n) * 0.5

    def synergies_into(self, node_id: str):
        return [s for s in self._d.synergies if s.target == node_id]

    def stats_of(self, node_id: str) -> list[str]:
        """Every stat the node has: fixed, per point and received from synergies, in display order."""
        n = self._nodes[node_id]
        return list(dict.fromkeys([*n.stat_ids, *(s.stat for s in self.synergies_into(node_id))]))

    def effect_at(self, node_id: str, stat: str, points: int) -> float:
        """Value of a stat if the node had ``points`` levels (tooltips show the next level with this).

        Fixed ``base`` + per-level ``effects`` with diminishing returns. Synergies always count, with the
        level the source node has now.
        """
        n = self._nodes[node_id]
        value = n.base.get(stat, 0.0) + n.effects.get(stat, 0.0) * self.effective_points(points) if points > 0 else 0.0
        for s in self.synergies_into(node_id):
            if s.stat == stat:
                value += s.per_point * self.points_in(s.from_)
        return value

    def effect(self, node_id: str, stat: str) -> float:
        """Current value of a stat, with diminishing returns and synergies."""
        return self.effect_at(node_id, stat, self.points_in(node_id))

    def stat_total(self, stat: str) -> float:
        """Sum of one stat over every passive with a level (generic bonuses such as fasterHitRecovery). An active's
        stats count only when it is used (its hit, or its buff while it lasts: ``PlayerActor.buff_total``)."""
        return sum(self.effect(n.id, stat) for n in self._d.nodes if n.kind != "active" and stat in n.stat_ids and self.points_in(n.id) > 0)

    def learned_actives(self) -> list[str]:
        """Active nodes with a level (unlocked, or given by a wand), in tree order: what can go on the bar."""
        return [n.id for n in self._d.nodes if n.kind == "active" and self.points_in(n.id) > 0]


class Bind(Enum):
    OK = "ok"
    NOT_ACTIVE = "only active skills go on the bar"
    NOT_LEARNED = "unlock the skill first"
    BAD_SLOT = "no such slot"


class SkillBar:
    """Active skills bound to the action bar slots (GDD 6.1). The client maps each slot to a key.

    Each skill sits in at most one slot: binding it elsewhere moves it and leaves the old slot empty;
    a skill that was already in the target slot is taken off the bar (no swap). It belongs to the
    character and is saved with it.
    """

    def __init__(self, trees: SkillTrees, size: int):
        self._trees = trees
        self.slots: list[str | None] = [None] * size

    def __len__(self) -> int:
        return len(self.slots)

    def skill_at(self, slot: int) -> str | None:
        return self.slots[slot] if 0 <= slot < len(self.slots) else None

    def slot_of(self, node_id: str) -> int | None:
        return self.slots.index(node_id) if node_id in self.slots else None

    def can_bind(self, slot: int, node_id: str) -> Bind:
        if not 0 <= slot < len(self.slots):
            return Bind.BAD_SLOT
        try:
            node = self._trees.node(node_id)
        except KeyError:
            return Bind.NOT_ACTIVE
        if node.kind != "active":
            return Bind.NOT_ACTIVE
        if self._trees.points_in(node_id) <= 0:
            return Bind.NOT_LEARNED
        return Bind.OK

    def bind(self, slot: int, node_id: str) -> Bind:
        r = self.can_bind(slot, node_id)
        if r is Bind.OK:
            old = self.slot_of(node_id)
            if old is not None:
                self.slots[old] = None
            self.slots[slot] = node_id
        return r

    def clear(self, slot: int) -> None:
        if 0 <= slot < len(self.slots):
            self.slots[slot] = None

    def auto_bind(self, node_id: str) -> int | None:
        """A newly learned active goes to the first free slot, if there is one and it is not on the bar yet."""
        if self.slot_of(node_id) is not None or None not in self.slots:
            return None
        slot = self.slots.index(None)
        return slot if self.bind(slot, node_id) is Bind.OK else None

    def drop_unusable(self) -> None:
        """Empties the slots whose skill has no level any more (respec, a wand taken off)."""
        self.slots = [x if x is not None and self.can_bind(i, x) is Bind.OK else None for i, x in enumerate(self.slots)]

    def sync(self) -> None:
        """Drops skills that are no longer learned (respec) and fills free slots with learned actives."""
        self.drop_unusable()
        for node_id in self._trees.learned_actives():
            self.auto_bind(node_id)

    def to_list(self) -> list[str | None]:
        return list(self.slots)

    def restore(self, slots: list[str | None]) -> None:
        """Puts back a saved bar exactly (empty slots stay empty), skipping what no longer fits."""
        self.slots = [None] * len(self.slots)
        for i, node_id in enumerate(slots[: len(self.slots)]):
            if node_id is not None:
                self.bind(i, node_id)
