"""Models for the files in ``aethelgard/data/*.json``, plus loading with validation.

``_comment`` fields in the JSON are ignored. camelCase JSON names become snake_case.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def from_dict(cls: type, raw: dict[str, Any]):
    """Builds a dataclass from a JSON dict (camelCase → snake_case, recursive)."""
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


# ---------------------------------------------------------------- curves


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
    armor_xp_full_at_mitigation: float = 0.25
    trivial_monster_delta: int = -10


@dataclass
class LevelFactorParams:
    table: dict[str, float] = field(default_factory=dict)


@dataclass
class AttributeParams:
    """What each attribute point does (GDD 4.2)."""

    points_per_proficiency_level: int = 3
    base_accuracy: float = 20.0
    damage_per_str: float = 0.01  # +1% physical damage
    accuracy_per_dex: float = 5.0
    crit_per_dex: float = 0.002
    defense_per_dex: float = 2.0
    spell_damage_per_int: float = 0.01  # +1% spell damage


@dataclass
class MagicParams:
    """Spells (core/spells.py): cast time, and how often fire on the ground and burning deal their damage."""

    cast_seconds: float = 0.5
    cast_release_fraction: float = 0.5  # the spell leaves the hand at this share of the cast
    tick_seconds: float = 0.5  # fire walls, burning ground and burning monsters hurt in steps this long


@dataclass
class CharacterParams:
    secondary_proficiency_weight: float = 0.25
    level_up_heal_pct: float = 0.25
    energy_regen_pct_per_second: float = 0.02
    guard_energy_regen_pct_per_second: float = 0.005
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
    knockback_seconds: float = 0.15
    melee_vs_ranged_damage_pct: float = 0.25
    melee_vs_ranged_accuracy_pct: float = 0.25
    in_place_arc_degrees: float = 90  # Shift+click swing: what it can hit in front
    ranged_kite_tiles: float = 3.0  # archers and casters step back from anyone closer than this
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
class LootParams:
    auto_pickup_gold: bool = True
    pickup_range_tiles: float = 1.0
    gold_pickup_range_tiles: float = 0.6
    scatter_tiles: float = 1.2


@dataclass
class DeathParams:
    gold_loss_pct: float = 0.10
    xp_loss_pct_of_level: float = 0.0
    drop_corpse: bool = True


@dataclass
class MovementParams:
    player_tiles_per_second: float = 6.5
    monster_base_tiles_per_second: float = 3.75


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
    block_reduction: float = 0.7
    block_reduction_cap: float = 0.95


@dataclass
class BowParams:
    arrow_speed: float = 14
    arrow_radius: float = 0.12


@dataclass
class TownPortalParams:
    cooldown_seconds: float = 5.0
    cast_seconds: float = 1.5  # standing still while it opens; moving, attacking or a hard hit stops it


@dataclass
class DualWieldParams:
    off_hand_damage_mult: float = 0.7


@dataclass
class CollisionParams:
    player_radius: float = 0.42
    wall_margin_max: float = 0.45


@dataclass
class ExitParams:
    touch_radius_tiles: float = 1.4


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
    monster_swing_seconds: float = 0.6


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
    movement: MovementParams = field(default_factory=MovementParams)
    exits: ExitParams = field(default_factory=ExitParams)
    timing: TimingParams = field(default_factory=TimingParams)
    collision: CollisionParams = field(default_factory=CollisionParams)
    dual_wield: DualWieldParams = field(default_factory=DualWieldParams)
    town_portal: TownPortalParams = field(default_factory=TownPortalParams)
    bow: BowParams = field(default_factory=BowParams)
    loot: LootParams = field(default_factory=LootParams)
    magic: MagicParams = field(default_factory=MagicParams)


# ---------------------------------------------------------------- monsters


@dataclass
class MonsterRankDef:
    id: str = ""
    name: str = ""
    size_mult: float = 1
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
class ProjectileDef:
    speed: float = 8.0  # tiles/s
    radius: float = 0.15
    magic: bool = False
    look: str = "arrow"


@dataclass
class AuraDef:
    radius_tiles: float = 6
    stats: dict[str, float] = field(default_factory=dict)


@dataclass
class LeaderDef:
    pack_chance: float = 0.25
    max_per_area: int = 3
    followers: list[str] = field(default_factory=list)
    aura: AuraDef | None = None


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
    radius: float = 0.42  # body, in tiles (times the rank sizeMult)
    collides: bool = True  # False: passes through bodies (must be explicit in monsters.json)
    projectile: ProjectileDef | None = None  # ranged attacker
    leader: LeaderDef | None = None  # spawns only at the head of a pack and buffs the others
    resist: dict[str, float] = field(default_factory=dict)  # element → share of spell damage it ignores (negative = weak)


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
class UniqueNameParts:
    """Unique monster names: ``<prefix><suffix> <epithet>`` (e.g. "Bonemaw the Hungry")."""

    prefixes: list[str] = field(default_factory=lambda: ["Nameless"])
    suffixes: list[str] = field(default_factory=lambda: [""])
    epithets: list[str] = field(default_factory=lambda: [""])


@dataclass
class MonstersData:
    unique_names: UniqueNameParts = field(default_factory=UniqueNameParts)
    families: list[FamilyDef] = field(default_factory=list)
    types: list[MonsterTypeDef] = field(default_factory=list)
    bosses: list[BossDef] = field(default_factory=list)


# ---------------------------------------------------------------- world


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
    map: str | None = None  # hand-made Tiled map (relative to aethelgard/data)
    pack_size: list[int] | None = None  # [min, max] monsters in a normal pack here (default: balance.json spawn)
    map_pos: list[int] | None = None  # [x, y] on the act map: which side each edge exit is on

    def normal_pack_range(self, spawn) -> tuple[int, int]:
        if self.pack_size:
            return self.pack_size[0], self.pack_size[1]
        return spawn.normal_pack_size_min, spawn.normal_pack_size_max

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


# ---------------------------------------------------------------- character


@dataclass
class ProficiencyDef:
    id: str = ""
    name: str = ""
    xp_source: str = "offense"
    description: str = ""
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


SPELL_CASTS = ("bolt", "ball", "wall", "meteor")  # how a spell is delivered (core/spells.py)
ELEMENTS = ("fire",)


@dataclass
class SpellDef:
    """A node that is a spell: learned from a wand (never bought with points first), deals elemental damage."""

    cast: str = "bolt"  # bolt | ball | wall | meteor
    element: str = "fire"
    look: str = ""  # how the client draws its projectile or zone


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
    short_name: str = ""
    description: str = ""
    effects: dict[str, float] = field(default_factory=dict)  # per point
    base: dict[str, float] = field(default_factory=dict)  # fixed once learned
    spell: SpellDef | None = None

    @property
    def stat_ids(self) -> list[str]:
        """Stats of this node in display order: fixed ones first, then the ones that grow per point."""
        return list(dict.fromkeys([*self.base, *self.effects]))


@dataclass
class SynergyDef:
    target: str = ""
    from_: str = ""
    stat: str = ""
    per_point: float = 0


@dataclass
class SkillsData:
    diminishing_after: int = 8
    bar_slots: int = 6
    nodes: list[SkillNodeDef] = field(default_factory=list)
    synergies: list[SynergyDef] = field(default_factory=list)


# ---------------------------------------------------------------- stats (tooltip catalog)

STAT_FORMATS = ("pct", "flat", "seconds", "degrees", "tiles", "count", "flag")


@dataclass
class StatDef:
    id: str = ""
    label: str = ""
    format: str = "flat"
    signed: bool = True


@dataclass
class StatsData:
    stats: list[StatDef] = field(default_factory=list)


# ---------------------------------------------------------------- items


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
    on: list[str] = field(default_factory=list)  # item classes or class groups it may roll on (items.json itemClasses)


@dataclass
class ItemClassDef:
    """What an item is (sword, bow, boots, ring…): decides which affixes make sense on it (GDD 10.2)."""

    groups: list[str] = field(default_factory=list)  # e.g. weapon, armor, jewelry: affixes may name a group instead of each class
    min_rarity: str = "normal"  # jewelry is never plain: a ring with nothing on it is worth nothing


@dataclass
class PropDef:
    """One numbered line of a unique, a set item or a set bonus: fixed (``value``) or rolled in ``min``–``max``."""

    stat: str = ""
    min: float = 0
    max: float = 0
    value: float | None = None

    def __post_init__(self):
        if self.value is not None:
            self.min = self.max = self.value


@dataclass
class FixedItemDef:
    """A hand-made item (unique or set piece): always the same base, name and lines; only the ranges roll."""

    id: str = ""
    name: str = ""
    base: str = ""
    level: int = 1  # minimum item level to drop
    weight: float = 1.0  # how often it is picked among the ones allowed at that item level
    props: list[PropDef] = field(default_factory=list)
    set_id: str | None = None  # filled for set pieces when the data loads


@dataclass
class SetBonusDef:
    pieces: int = 2  # active while at least this many different pieces of the set are worn
    stats: dict[str, float] = field(default_factory=dict)


@dataclass
class SetDef:
    id: str = ""
    name: str = ""
    pieces: list[FixedItemDef] = field(default_factory=list)
    bonuses: list[SetBonusDef] = field(default_factory=list)  # partial bonuses add up as more pieces are worn


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
    block_reduction: float = 0
    two_handed: bool = False  # bows: no shield, no second weapon
    range_tiles: float = 0  # bows: how far it shoots
    spell: str | None = None  # wands: the spell (skills.json node) it lets you cast while equipped
    spell_level: int = 0  # wands: levels of that spell it gives while equipped
    size: list[int] = field(default_factory=lambda: [1, 1])
    req: dict[str, int] = field(default_factory=dict)
    item_class: str = ""  # items.json itemClasses: which affixes may roll on it


@dataclass
class DropCountDef:
    min: int = 0
    max: int = 1
    no_drop_chance: float = 0


@dataclass
class RarityChances:
    unique: float = 0.004
    set: float = 0.008
    rare: float = 0.03
    magic: float = 0.18


@dataclass
class MinMax:
    min: int = 0
    max: int = 0


@dataclass
class InventorySize:
    columns: int = 10
    rows: int = 6

    @property
    def capacity(self) -> int:
        return self.columns * self.rows


@dataclass
class RareNameParts:
    first: list[str] = field(default_factory=lambda: ["Rare"])
    second: list[str] = field(default_factory=lambda: ["Item"])


@dataclass
class TradeParams:
    base_price: float = 20
    per_item_level: float = 6
    per_affix: float = 25
    rarity_mult: list[float] = field(default_factory=lambda: [1, 3, 8, 15, 20])
    sell_fraction: float = 0.2
    stock_size: int = 18
    npc_range_tiles: float = 3.0


@dataclass
class EquipSlotDef:
    name: str = ""
    accepts: list[str] = field(default_factory=list)


@dataclass
class ConsumableDef:
    name: str = ""
    restore: str | None = None  # life | energy
    pct_of_max: float = 0.0
    seconds: float = 0.0
    cooldown_seconds: float = 0.5
    price: int = 0


@dataclass
class ItemsData:
    inventory: InventorySize = field(default_factory=InventorySize)
    starting_gear: list[str] = field(default_factory=list)
    starting_backpack: list[str] = field(default_factory=list)
    slot_names: dict[str, str] = field(default_factory=dict)  # item type (base 'slot') → name in tooltips
    consumables: dict[str, ConsumableDef] = field(default_factory=dict)
    equipment: dict[str, EquipSlotDef] = field(default_factory=dict)
    trade: TradeParams = field(default_factory=TradeParams)  # equipment slot → what it accepts, in screen order
    rare_names: RareNameParts = field(default_factory=RareNameParts)
    rarity_chances: RarityChances = field(default_factory=RarityChances)
    drop_count: dict[str, DropCountDef] = field(default_factory=dict)
    drop_kind: dict[str, float] = field(default_factory=dict)
    bases: list[ItemBaseDef] = field(default_factory=list)
    affixes: list[AffixDef] = field(default_factory=list)
    rare_affix_count: MinMax = field(default_factory=MinMax)
    item_classes: dict[str, ItemClassDef] = field(default_factory=dict)
    uniques: list[FixedItemDef] = field(default_factory=list)
    sets: list[SetDef] = field(default_factory=list)
    _pools: dict[str, list[AffixDef]] = field(default_factory=dict, repr=False)

    def classes_named(self, names: list[str]) -> set[str]:
        """Item classes listed directly or through one of their groups."""
        return {cid for cid, c in self.item_classes.items() if cid in names or set(c.groups) & set(names)}

    def affix_pool(self, item_class: str) -> list[AffixDef]:
        """Every affix that may roll on this item class, in file order (built once)."""
        if item_class not in self._pools:
            self._pools[item_class] = [a for a in self.affixes if item_class in self.classes_named(a.on)]
        return self._pools[item_class]

    @property
    def set_pieces(self) -> list[FixedItemDef]:
        return [p for s in self.sets for p in s.pieces]


# ---------------------------------------------------------------- everything together


class DataError(ValueError):
    pass


def _find(items, item_id: str, what: str):
    """Looks up a definition by id; one helper for every ``GameData`` query."""
    for x in items:
        if x.id == item_id:
            return x
    raise KeyError(f"{what} '{item_id}'")


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
    stats: StatsData
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
        "stats": ("stats.json", StatsData),
    }

    # ---- loading

    @classmethod
    def load(cls, read: Callable[[str], str] | None = None) -> GameData:
        """Loads the JSON files. Without ``read``, uses the files packaged in ``aethelgard/data``."""
        if read is None:
            base = resources.files("aethelgard") / "data"

            def read(name: str) -> str:
                return (base / name).read_text(encoding="utf-8")

        parts = {}
        for key, (fname, model) in cls.FILES.items():
            raw = json.loads(read(fname))
            # "from" is a reserved word in Python
            if model is SkillsData:
                for s in raw.get("synergies", []):
                    s["from_"] = s.pop("from")
            parts[key] = from_dict(model, raw)
        data = cls(**parts)
        data._build_tables()
        errors = validate(data)
        if errors:
            raise DataError("Invalid game data:\n - " + "\n - ".join(errors))
        return data

    @classmethod
    def load_dir(cls, path: str | Path) -> GameData:
        p = Path(path)
        return cls.load(lambda name: (p / name).read_text(encoding="utf-8"))

    def _build_tables(self) -> None:
        for st in self.items.sets:
            for piece in st.pieces:
                piece.set_id = st.id
        c = self.curves.proficiency_xp_to_next
        total, acc = [0], 0
        for n in range(1, c.max_level):
            step = round(c.at(n) / c.round_to) * c.round_to
            acc += max(c.round_to, step)
            total.append(acc)
        self.xp_total_by_level = total  # [n-1] = total XP to be at level n

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

    # ---- queries

    def rank(self, rank_id: str) -> MonsterRankDef:
        return _find(self.ranks.ranks, rank_id, "rank")

    def monster_type(self, type_id: str) -> MonsterTypeDef:
        return _find(self.monsters.types, type_id, "monster")

    def monster_level(self, level: int) -> MonsterLevelRow:
        rows = self.monster_levels
        lv = max(rows[0].level, min(level, rows[-1].level))
        return rows[lv - rows[0].level]

    @property
    def all_areas(self) -> list[AreaDef]:
        return [a for act in self.areas.acts for a in act.areas]

    @property
    def town_id(self) -> str:
        """The first town of the first act: where new and loaded characters start."""
        return next(a.id for a in self.all_areas if a.kind == "town")

    def area(self, area_id: str) -> AreaDef:
        return _find(self.all_areas, area_id, "area")

    def proficiency(self, prof_id: str) -> ProficiencyDef:
        return _find(self.proficiencies.proficiencies, prof_id, "proficiency")

    def node(self, node_id: str) -> SkillNodeDef:
        return _find(self.skills.nodes, node_id, "node")

    def base(self, base_id: str) -> ItemBaseDef:
        return _find(self.items.bases, base_id, "base")

    def affix(self, affix_id: str) -> AffixDef:
        return _find(self.items.affixes, affix_id, "affix")

    def item_class(self, base: ItemBaseDef) -> ItemClassDef:
        return self.items.item_classes[base.item_class]

    def fixed_item(self, item_id: str) -> FixedItemDef:
        """A unique or a set piece by id (the ids never clash: validate() checks)."""
        return _find(self.items.uniques + self.items.set_pieces, item_id, "unique or set item")

    def item_set(self, set_id: str) -> SetDef:
        return _find(self.items.sets, set_id, "set")

    def stat(self, stat_id: str) -> StatDef:
        return _find(self.stats.stats, stat_id, "stat")


def _validate_stats(d: GameData) -> list[str]:
    """Every stat a tooltip may show has a label and a format, and every description placeholder exists."""
    errors: list[str] = []
    stat_ids = {s.id for s in d.stats.stats}
    if len(stat_ids) != len(d.stats.stats):
        errors.append("stats: duplicate stat ids")
    errors += [f"stats: '{s.id}' has unknown format '{s.format}'" for s in d.stats.stats if s.format not in STAT_FORMATS]
    used = {(f"node '{n.id}'", k) for n in d.skills.nodes for k in n.stat_ids}
    used |= {(f"synergy into '{s.target}'", s.stat) for s in d.skills.synergies}
    used |= {(f"affix '{a.id}'", a.stat) for a in d.items.affixes}
    used |= {(f"item '{f.id}'", p.stat) for f in d.items.uniques + d.items.set_pieces for p in f.props}
    used |= {(f"set '{st.id}' bonus", k) for st in d.items.sets for b in st.bonuses for k in b.stats}
    used |= {(f"aura of '{t.id}'", k) for t in d.monsters.types if t.leader and t.leader.aura for k in t.leader.aura.stats}
    errors += [f"stats: {where} uses '{stat}', missing from stats.json" for where, stat in sorted(used) if stat not in stat_ids]
    for n in d.skills.nodes:
        if not n.description:
            errors.append(f"skills: node '{n.id}' has no description")
            continue
        known = set(n.stat_ids) | {s.stat for s in d.skills.synergies if s.target == n.id}
        try:
            fields_ = {f for _, f, _, _ in string.Formatter().parse(n.description) if f}
        except ValueError as ex:
            errors.append(f"skills: node '{n.id}' description is malformed ({ex})")
            continue
        errors += [f"skills: node '{n.id}' description uses unknown stat '{{{f}}}'" for f in sorted(fields_ - known)]
    return errors


def validate(d: GameData) -> list[str]:
    """Data consistency: fail early, on load, instead of in the middle of a game."""
    errors: list[str] = []
    xt = d.xp_total_by_level
    if any(b <= a for a, b in zip(xt, xt[1:], strict=False)):
        errors.append("XP curve is not increasing")

    for r in ("normal", "champion", "berserker", "unique", "minion", "boss"):
        if all(x.id != r for x in d.ranks.ranks):
            errors.append(f"monster_ranks: missing '{r}'")
    errors += [f"monster_ranks: '{x.id}' has no name" for x in d.ranks.ranks if not x.name]
    errors += [f"monster_ranks: '{x.id}' sizeMult must be positive" for x in d.ranks.ranks if x.size_mult <= 0]
    errors += [f"monsters: '{t.id}' radius must be positive" for t in d.monsters.types if t.radius <= 0]
    g = d.balance.guard
    if not 0 <= g.block_reduction <= g.block_reduction_cap < 1:
        errors.append("balance: need 0 <= guard.blockReduction <= guard.blockReductionCap < 1 (a block never stops everything)")

    families = {f.id for f in d.monsters.families}
    for t in d.monsters.types:
        if t.family not in families:
            errors.append(f"monsters: '{t.id}' has unknown family '{t.family}'")
        if t.dmg_min > t.dmg_max:
            errors.append(f"monsters: '{t.id}' dmgMin > dmgMax")
    boss_ids = {b.id for b in d.monsters.bosses}
    type_ids = {t.id for t in d.monsters.types}
    for t in d.monsters.types:
        if t.leader:
            errors += [f"monsters: leader '{t.id}' has unknown follower '{f}'" for f in t.leader.followers if f not in type_ids]
            errors += [f"monsters: leader '{t.id}' cannot follow itself" for f in t.leader.followers if f == t.id]
            if not t.leader.followers:
                errors.append(f"monsters: leader '{t.id}' has no followers")
    for b in d.monsters.bosses:
        if b.base_type not in type_ids:
            errors.append(f"monsters: boss '{b.id}' has a missing base type")

    area_ids = {a.id for a in d.all_areas}
    for a in d.all_areas:
        errors += [f"areas: '{a.id}' links to missing '{lk}'" for lk in a.links if lk not in area_ids]
        errors += [f"areas: '{a.id}' uses missing family '{f}'" for f in a.families if f not in families]
        if a.boss and a.boss not in boss_ids:
            errors.append(f"areas: '{a.id}' has missing boss '{a.boss}'")
    by_id = {a.id: a for a in d.all_areas}
    for a in d.all_areas:
        for lk in a.links:
            b = by_id.get(lk)
            if b is None or a.kind == "dungeon" or b.kind == "dungeon":
                continue  # dungeons are entered from inside an area
            if a.map_pos is None or b.map_pos is None:
                errors.append(f"areas: '{a.id}' and '{lk}' share an edge, both need mapPos")
                continue
            dx, dy = b.map_pos[0] - a.map_pos[0], b.map_pos[1] - a.map_pos[1]
            if abs(dx) + abs(dy) != 1:
                errors.append(f"areas: '{a.id}' {a.map_pos} and '{lk}' {b.map_pos} are linked but not side by side on the act map")
            if a.id not in b.links:
                errors.append(f"areas: '{a.id}' links to '{lk}' but not the way back")
    for a in d.all_areas:
        if a.pack_size is not None and not (len(a.pack_size) == 2 and 1 <= a.pack_size[0] <= a.pack_size[1]):
            errors.append(f"areas: '{a.id}' packSize must be [min, max] with 1 <= min <= max")
    if all(a.kind != "town" for a in d.all_areas):
        errors.append("areas: no town to start in")

    pts = d.balance.attributes.points_per_proficiency_level
    for p in d.proficiencies.proficiencies:
        if sum(p.attributes.values()) != pts:
            errors.append(f"proficiencies: '{p.id}' hands out {sum(p.attributes.values())} points (expected {pts})")

    prof_ids = {p.id for p in d.proficiencies.proficiencies}
    node_ids = {n.id for n in d.skills.nodes}
    for n in d.skills.nodes:
        if n.tree not in prof_ids:
            errors.append(f"skills: node '{n.id}' in missing tree '{n.tree}'")
        if n.kind == "active" and n.cooldown < 0:
            errors.append(f"skills: node '{n.id}' has a negative cooldown")
        if n.spell is not None:
            if n.kind != "active":
                errors.append(f"skills: spell '{n.id}' must be active")
            if n.spell.cast not in SPELL_CASTS:
                errors.append(f"skills: spell '{n.id}' has unknown cast '{n.spell.cast}' (one of {', '.join(SPELL_CASTS)})")
            if n.spell.element not in ELEMENTS:
                errors.append(f"skills: spell '{n.id}' has unknown element '{n.spell.element}'")
            if not any(b.spell == n.id for b in d.items.bases):
                errors.append(f"skills: spell '{n.id}' can only be learned from a wand, and no wand has it")
    for b in d.items.bases:
        if b.spell is not None and (b.spell not in node_ids or d.node(b.spell).spell is None):
            errors.append(f"items: wand '{b.id}' gives '{b.spell}', which is not a spell")
        if b.spell is not None and b.spell_level < 1:
            errors.append(f"items: wand '{b.id}' needs spellLevel >= 1")
    for t in d.monsters.types:
        errors += [f"monsters: '{t.id}' resists unknown element '{e}'" for e in t.resist if e not in ELEMENTS]
    for s in d.skills.synergies:
        if s.target not in node_ids or s.from_ not in node_ids:
            errors.append(f"skills: synergy {s.from_} → {s.target} references a missing node")
    if d.skills.bar_slots < 1:
        errors.append("skills: barSlots must be at least 1")
    errors += _validate_stats(d)

    errors += _validate_item_kinds(d)
    base_ids = {b.id for b in d.items.bases}
    errors += [f"items: starting gear '{b}' is not a base" for b in d.items.starting_gear + d.items.starting_backpack if b not in base_ids]
    errors += [f"items: bow '{b.id}' needs rangeTiles" for b in d.items.bases if "bow" in b.tags and b.range_tiles <= 0]
    errors += [f"items: base '{b.id}' uses slot '{b.slot}' with no entry in slotNames" for b in d.items.bases if b.slot not in d.items.slot_names]
    accepted = {t for e in d.items.equipment.values() for t in e.accepts}
    errors += [f"items: no equipment slot accepts '{b.slot}' (base '{b.id}')" for b in d.items.bases if b.slot not in accepted]
    for cid in ("life_potion", "energy_potion"):  # what PotionDrop gives
        if cid not in d.items.consumables:
            errors.append(f"items: consumable '{cid}' missing (drops give it)")
    errors += [f"items: consumable '{k}' restores unknown '{c.restore}'" for k, c in d.items.consumables.items() if c.restore not in (None, "life", "energy")]
    if d.items.inventory.capacity <= 0:
        errors.append("items: inventory has no room")
    return errors


RARITY_NAMES = ("normal", "magic", "rare", "set", "unique")  # same order as items.Rarity


def _validate_item_kinds(d: GameData) -> list[str]:
    """Item classes, affix rules, uniques and sets (GDD 10)."""
    it, errors = d.items, []
    groups = {g for c in it.item_classes.values() for g in c.groups}
    for cid, c in it.item_classes.items():
        if c.min_rarity not in RARITY_NAMES[:3]:
            errors.append(f"items: class '{cid}' minRarity '{c.min_rarity}' must be normal, magic or rare")
        if cid in groups:
            errors.append(f"items: '{cid}' is both an item class and a class group")
    errors += [f"items: base '{b.id}' has unknown itemClass '{b.item_class}'" for b in it.bases if b.item_class not in it.item_classes]
    for b in it.bases:
        if b.id in it.starting_gear + it.starting_backpack and b.item_class in it.item_classes and it.item_classes[b.item_class].min_rarity != "normal":
            errors.append(f"items: starting item '{b.id}' can never be a plain item (class minRarity)")
    for a in it.affixes:
        if not a.on:
            errors.append(f"items: affix '{a.id}' has no 'on' (which item classes it may roll on)")
        errors += [f"items: affix '{a.id}' rolls on unknown class or group '{n}'" for n in a.on if n not in it.item_classes and n not in groups]
        if not a.tiers:
            errors.append(f"items: affix '{a.id}' has no tiers")
        errors += [f"items: affix '{a.id}' has a tier with min > max" for t in a.tiers if t.min > t.max]
    if len({a.id for a in it.affixes}) != len(it.affixes):
        errors.append("items: duplicate affix ids")
    base_ids = {b.id for b in it.bases}
    fixed = it.uniques + it.set_pieces
    if len({f.id for f in fixed}) != len(fixed):
        errors.append("items: unique and set item ids must all be different")
    for f in fixed:
        if f.base not in base_ids:
            errors.append(f"items: '{f.id}' uses unknown base '{f.base}'")
        elif f.level < d.base(f.base).level:
            errors.append(f"items: '{f.id}' drops below the level of its base '{f.base}'")
        if not f.props:
            errors.append(f"items: '{f.id}' has no properties")
        errors += [f"items: '{f.id}' property '{p.stat}' has min > max" for p in f.props if p.min > p.max]
        if f.weight <= 0:
            errors.append(f"items: '{f.id}' needs a weight above 0")
    for st in it.sets:
        if len(st.pieces) < 2:
            errors.append(f"items: set '{st.id}' needs at least 2 pieces")
        counts = [b.pieces for b in st.bonuses]
        if not counts or counts != sorted(set(counts)) or counts[0] < 2 or counts[-1] > len(st.pieces):
            errors.append(f"items: set '{st.id}' bonuses must go up from 2 pieces to at most {len(st.pieces)}, one per count")
    return errors
