"""Modelos dos arquivos em ``aethelgard/data/*.json`` e carregamento com validação.

Os campos ``_comment`` dos JSON são ignorados. Nomes em camelCase do JSON viram snake_case.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def from_dict(cls: type, raw: dict[str, Any]):
    """Constrói uma dataclass a partir de um dict do JSON (camelCase → snake_case, recursivo)."""
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        name = _snake(key)
        if name not in names:
            continue
        kwargs[name] = _convert(hints[name], value)
    return cls(**kwargs)


def _convert(tp: Any, value: Any):
    origin = get_origin(tp)
    if is_dataclass(tp) and isinstance(value, dict):
        return from_dict(tp, value)
    if origin is list:
        (inner,) = get_args(tp)
        return [_convert(inner, v) for v in value]
    if origin is dict:
        _, inner = get_args(tp)
        return {k: _convert(inner, v) for k, v in value.items() if not k.startswith("_")}
    if origin is not None and type(None) in get_args(tp):  # Optional[X]
        inner = next(a for a in get_args(tp) if a is not type(None))
        return None if value is None else _convert(inner, value)
    return value


# ---------------------------------------------------------------- curvas


@dataclass
class Curve:
    """a × n^p × g^n"""

    a: float = 1.0
    p: float = 0.0
    g: float = 1.0

    def at(self, n: float) -> float:
        return self.a * (n**self.p) * (self.g**n)


@dataclass
class ProficiencyCurve(Curve):
    round_to: int = 10
    max_level: int = 30


@dataclass
class MonsterCurves:
    xp: Curve = field(default_factory=Curve)
    hp_pct: Curve = field(default_factory=Curve)
    dmg_pct: Curve = field(default_factory=Curve)
    accuracy: Curve = field(default_factory=Curve)
    defense_per_level: float = 6
    max_level: int = 60


@dataclass
class CurvesData:
    proficiency_xp_to_next: ProficiencyCurve = field(default_factory=ProficiencyCurve)
    monster: MonsterCurves = field(default_factory=MonsterCurves)


@dataclass(frozen=True)
class MonsterLevelRow:
    level: int
    hp_pct: float
    dmg_pct: float
    xp: float
    defense: float
    accuracy: float


# ---------------------------------------------------------------- balance


@dataclass
class XpParams:
    defense_xp_per_hit: float = 0.10
    defense_xp_cap_per_monster: float = 0.75
    trivial_monster_delta: int = -10


@dataclass
class LevelFactorParams:
    table: dict[str, float] = field(default_factory=dict)


@dataclass
class AttributeParams:
    points_per_proficiency_level: int = 3


@dataclass
class CharacterParams:
    secondary_proficiency_weight: float = 0.25
    base_life: float = 50
    life_per_vit: float = 4
    life_per_level: float = 3
    base_energy: float = 30
    energy_per_int: float = 2
    energy_per_two_vit: float = 1


@dataclass
class CombatParams:
    hit_chance_min: float = 0.05
    hit_chance_max: float = 0.95
    armor_cap: float = 0.75
    armor_per_attacker_level: float = 50
    resist_cap: float = 0.75
    crit_multiplier: float = 1.5
    cc_reduction_unique_and_boss: float = 0.5


@dataclass
class SpawnParams:
    special_packs_min: int = 1
    special_packs_max: int = 2
    unique_pack_chance: float = 0.8
    champion_pack_size_min: int = 2
    champion_pack_size_max: int = 4
    minions_min: int = 3
    minions_max: int = 6
    normal_pack_size_min: int = 3
    normal_pack_size_max: int = 5
    minion_leash_tiles: int = 6


@dataclass
class DeathParams:
    gold_loss_pct: float = 0.10
    xp_loss_pct_of_level: float = 0.0
    drop_corpse: bool = True


@dataclass
class GuardParams:
    arc_degrees: float = 120
    base_energy_cost: float = 1.0
    energy_per_damage: float = 0.25
    efficiency_cap: float = 0.6
    move_speed_mult: float = 0.5
    perfect_window_seconds: float = 0.15
    guard_break_stun_seconds: float = 1.0
    guard_raise_cooldown_seconds: float = 0.3


@dataclass
class TimingParams:
    attack_seconds: float = 0.55
    attack_hit_fraction: float = 0.45
    hit_recovery_seconds: float = 0.40
    hit_recovery_threshold_pct_life: float = 1 / 12
    block_recovery_seconds: float = 0.30
    monster_hit_recovery_seconds: float = 0.35
    monster_hit_recovery_threshold_pct_life: float = 0.125
    boss_hit_recovery_mult: float = 0.0
    unique_hit_recovery_mult: float = 0.5
    global_cooldown_seconds: float = 0.2


@dataclass
class BalanceData:
    xp: XpParams = field(default_factory=XpParams)
    level_factor: LevelFactorParams = field(default_factory=LevelFactorParams)
    attributes: AttributeParams = field(default_factory=AttributeParams)
    character: CharacterParams = field(default_factory=CharacterParams)
    combat: CombatParams = field(default_factory=CombatParams)
    spawn: SpawnParams = field(default_factory=SpawnParams)
    death: DeathParams = field(default_factory=DeathParams)
    guard: GuardParams = field(default_factory=GuardParams)
    timing: TimingParams = field(default_factory=TimingParams)


# ---------------------------------------------------------------- monstros


@dataclass
class MonsterRankDef:
    id: str = ""
    level_bonus: int = 0
    hp_mult: float = 1
    xp_mult: float = 1
    dmg_bonus: float = 0
    acc_bonus: float = 0
    modifiers: int = 0
    loot_quality_mult: float = 1
    guaranteed_magic: bool = False


@dataclass
class MonsterRanksData:
    ranks: list[MonsterRankDef] = field(default_factory=list)
    berserker_chance_in_champion_pack: float = 0.2


@dataclass
class MonsterModifierDef:
    id: str = ""
    name: str = ""
    stats: dict[str, float] = field(default_factory=dict)
    on_death: str | None = None
    on_hit: str | None = None
    on_hurt: str | None = None
    ability: str | None = None
    unique_only: bool = False


@dataclass
class MonsterModifiersData:
    modifiers: list[MonsterModifierDef] = field(default_factory=list)


@dataclass
class FamilyDef:
    id: str = ""
    name: str = ""


@dataclass
class MonsterTypeDef:
    id: str = ""
    name: str = ""
    family: str = ""
    hp: float = 100
    dmg_min: float = 1
    dmg_max: float = 2
    exp_pct: float = 100
    move_speed: float = 1.0
    attack_rate: float = 1.0
    range: float = 1
    ai: str = "melee"


@dataclass
class BossDef:
    id: str = ""
    name: str = ""
    base_type: str = ""
    level: int = 1
    hp: float = 100
    dmg_min: float = 1
    dmg_max: float = 2
    exp_pct: float = 100


@dataclass
class MonstersData:
    families: list[FamilyDef] = field(default_factory=list)
    types: list[MonsterTypeDef] = field(default_factory=list)
    bosses: list[BossDef] = field(default_factory=list)


# ---------------------------------------------------------------- mundo


@dataclass
class AreaDef:
    id: str = ""
    name: str = ""
    kind: str = "open"  # town | open | dungeon
    level: int = 0
    level_min: int = 0
    level_max: int = 0
    floors: int = 1
    links: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    normal_packs: int = 0
    size_chunks: list[int] | None = None
    boss: str | None = None
    waypoint: bool = False
    map: str | None = None  # mapa feito à mão no Tiled (relativo a aethelgard/data)

    def level_for_floor(self, floor: int) -> int:
        if self.kind != "dungeon":
            return self.level
        if self.floors <= 1:
            return self.level_min
        t = floor / (self.floors - 1)
        return round(self.level_min + t * (self.level_max - self.level_min))


@dataclass
class ActDef:
    id: str = ""
    name: str = ""
    areas: list[AreaDef] = field(default_factory=list)


@dataclass
class AreasData:
    acts: list[ActDef] = field(default_factory=list)


# ---------------------------------------------------------------- personagem


@dataclass
class ProficiencyDef:
    id: str = ""
    name: str = ""
    xp_source: str = "offense"
    attributes: dict[str, int] = field(default_factory=dict)
    weapon_tags: list[str] = field(default_factory=list)


@dataclass
class TitleDef:
    ids: list[str] = field(default_factory=list)
    title: str = ""


@dataclass
class ProficienciesData:
    proficiencies: list[ProficiencyDef] = field(default_factory=list)
    titles: list[TitleDef] = field(default_factory=list)
    title_secondary_threshold: float = 0.5
    default_title: str = ""


@dataclass
class SkillNodeDef:
    id: str = ""
    tree: str = ""
    name: str = ""
    layer: int = 1
    kind: str = "passive"
    max_points: int = 10
    energy_cost: float = 0
    cooldown: float = 0
    channeled: bool = False
    effects: dict[str, float] = field(default_factory=dict)


@dataclass
class SynergyDef:
    target: str = ""
    from_: str = ""
    stat: str = ""
    per_point: float = 0


@dataclass
class SkillsData:
    diminishing_after: int = 8
    nodes: list[SkillNodeDef] = field(default_factory=list)
    synergies: list[SynergyDef] = field(default_factory=list)


# ---------------------------------------------------------------- itens


@dataclass
class AffixTier:
    level: int = 1
    min: float = 0
    max: float = 0


@dataclass
class AffixDef:
    id: str = ""
    kind: str = "prefix"
    name: str = ""
    group: str = ""
    stat: str = ""
    tiers: list[AffixTier] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ItemBaseDef:
    id: str = ""
    name: str = ""
    slot: str = ""
    tags: list[str] = field(default_factory=list)
    level: int = 1
    dmg_min: float = 0
    dmg_max: float = 0
    armor: float = 0
    guard_efficiency: float = 0
    size: list[int] = field(default_factory=lambda: [1, 1])
    req: dict[str, int] = field(default_factory=dict)


@dataclass
class DropCountDef:
    min: int = 0
    max: int = 1
    no_drop_chance: float = 0


@dataclass
class RarityChances:
    unique: float = 0.004
    rare: float = 0.03
    magic: float = 0.18


@dataclass
class MinMax:
    min: int = 0
    max: int = 0


@dataclass
class ItemsData:
    rarity_chances: RarityChances = field(default_factory=RarityChances)
    drop_count: dict[str, DropCountDef] = field(default_factory=dict)
    drop_kind: dict[str, float] = field(default_factory=dict)
    bases: list[ItemBaseDef] = field(default_factory=list)
    affixes: list[AffixDef] = field(default_factory=list)
    rare_affix_count: MinMax = field(default_factory=MinMax)


# ---------------------------------------------------------------- tudo junto


class DataError(ValueError):
    pass


@dataclass
class GameData:
    curves: CurvesData
    balance: BalanceData
    ranks: MonsterRanksData
    modifiers: MonsterModifiersData
    monsters: MonstersData
    areas: AreasData
    proficiencies: ProficienciesData
    skills: SkillsData
    items: ItemsData
    monster_levels: list[MonsterLevelRow] = field(default_factory=list)
    xp_total_by_level: list[int] = field(default_factory=list)

    FILES = {
        "curves": ("curves.json", CurvesData),
        "balance": ("balance.json", BalanceData),
        "ranks": ("monster_ranks.json", MonsterRanksData),
        "modifiers": ("monster_modifiers.json", MonsterModifiersData),
        "monsters": ("monsters.json", MonstersData),
        "areas": ("areas.json", AreasData),
        "proficiencies": ("proficiencies.json", ProficienciesData),
        "skills": ("skills.json", SkillsData),
        "items": ("items.json", ItemsData),
    }

    # ---- carregamento

    @classmethod
    def load(cls, read: Callable[[str], str] | None = None) -> GameData:
        """Carrega os JSON. Sem ``read``, usa os arquivos empacotados em ``aethelgard/data``."""
        if read is None:
            base = resources.files("aethelgard") / "data"

            def read(name: str) -> str:
                return (base / name).read_text(encoding="utf-8")

        parts = {}
        for key, (fname, model) in cls.FILES.items():
            raw = json.loads(read(fname))
            # "from" é palavra reservada em Python
            if model is SkillsData:
                for s in raw.get("synergies", []):
                    s["from_"] = s.pop("from")
            parts[key] = from_dict(model, raw)
        data = cls(**parts)
        data._build_tables()
        errors = validate(data)
        if errors:
            raise DataError("Dados de jogo inválidos:\n - " + "\n - ".join(errors))
        return data

    @classmethod
    def load_dir(cls, path: str | Path) -> GameData:
        p = Path(path)
        return cls.load(lambda name: (p / name).read_text(encoding="utf-8"))

    def _build_tables(self) -> None:
        c = self.curves.proficiency_xp_to_next
        total, acc = [0], 0
        for n in range(1, c.max_level):
            step = round(c.at(n) / c.round_to) * c.round_to
            acc += max(c.round_to, step)
            total.append(acc)
        self.xp_total_by_level = total  # [n-1] = XP total para estar no nível n

        m = self.curves.monster
        self.monster_levels = [
            MonsterLevelRow(
                level=lv,
                hp_pct=round(m.hp_pct.at(lv), 1),
                dmg_pct=round(m.dmg_pct.at(lv), 2),
                xp=round(m.xp.at(lv), 1),
                defense=m.defense_per_level * lv,
                accuracy=round(m.accuracy.at(lv), 1),
            )
            for lv in range(1, m.max_level + 1)
        ]

    # ---- consultas

    def rank(self, rank_id: str) -> MonsterRankDef:
        for r in self.ranks.ranks:
            if r.id == rank_id:
                return r
        raise KeyError(f"rank '{rank_id}'")

    def monster_type(self, type_id: str) -> MonsterTypeDef:
        for t in self.monsters.types:
            if t.id == type_id:
                return t
        raise KeyError(f"monstro '{type_id}'")

    def monster_level(self, level: int) -> MonsterLevelRow:
        rows = self.monster_levels
        lv = max(rows[0].level, min(level, rows[-1].level))
        return rows[lv - rows[0].level]

    @property
    def all_areas(self) -> list[AreaDef]:
        return [a for act in self.areas.acts for a in act.areas]

    def area(self, area_id: str) -> AreaDef:
        for a in self.all_areas:
            if a.id == area_id:
                return a
        raise KeyError(f"área '{area_id}'")

    def node(self, node_id: str) -> SkillNodeDef:
        for n in self.skills.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"nó '{node_id}'")

    def base(self, base_id: str) -> ItemBaseDef:
        for b in self.items.bases:
            if b.id == base_id:
                return b
        raise KeyError(f"base '{base_id}'")


def validate(d: GameData) -> list[str]:
    """Consistência dos dados: falha cedo, ao carregar, em vez de no meio do jogo."""
    errors: list[str] = []
    xt = d.xp_total_by_level
    if any(b <= a for a, b in zip(xt, xt[1:], strict=False)):
        errors.append("curva de XP não é crescente")

    for r in ("normal", "champion", "berserker", "unique", "minion", "boss"):
        if all(x.id != r for x in d.ranks.ranks):
            errors.append(f"monster_ranks: falta '{r}'")

    families = {f.id for f in d.monsters.families}
    for t in d.monsters.types:
        if t.family not in families:
            errors.append(f"monsters: '{t.id}' com família desconhecida '{t.family}'")
        if t.dmg_min > t.dmg_max:
            errors.append(f"monsters: '{t.id}' dmgMin > dmgMax")
    boss_ids = {b.id for b in d.monsters.bosses}
    type_ids = {t.id for t in d.monsters.types}
    for b in d.monsters.bosses:
        if b.base_type not in type_ids:
            errors.append(f"monsters: chefe '{b.id}' com tipo base inexistente")

    area_ids = {a.id for a in d.all_areas}
    for a in d.all_areas:
        errors += [f"areas: '{a.id}' liga para '{lk}' inexistente" for lk in a.links if lk not in area_ids]
        errors += [f"areas: '{a.id}' usa família '{f}' inexistente" for f in a.families if f not in families]
        if a.boss and a.boss not in boss_ids:
            errors.append(f"areas: '{a.id}' com chefe inexistente '{a.boss}'")

    pts = d.balance.attributes.points_per_proficiency_level
    for p in d.proficiencies.proficiencies:
        if sum(p.attributes.values()) != pts:
            errors.append(f"proficiencies: '{p.id}' distribui {sum(p.attributes.values())} pontos (esperado {pts})")

    prof_ids = {p.id for p in d.proficiencies.proficiencies}
    node_ids = {n.id for n in d.skills.nodes}
    for n in d.skills.nodes:
        if n.tree not in prof_ids:
            errors.append(f"skills: nó '{n.id}' em árvore inexistente '{n.tree}'")
        if n.kind == "active" and n.cooldown < 0:
            errors.append(f"skills: nó '{n.id}' com recarga negativa")
    for s in d.skills.synergies:
        if s.target not in node_ids or s.from_ not in node_ids:
            errors.append(f"skills: sinergia {s.from_} → {s.target} referencia nó inexistente")

    for a in d.items.affixes:
        if not a.tiers:
            errors.append(f"items: afixo '{a.id}' sem faixas")
    if len({a.id for a in d.items.affixes}) != len(d.items.affixes):
        errors.append("items: ids de afixo repetidos")
    return errors
