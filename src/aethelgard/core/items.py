"""Loot (GDD 10.3), equipment, backpack and shared stash (GDD 10.5).

Every place that holds items (equipment, backpack, stash, save file) stores them with the same
``item_to_dict``/``item_from_dict`` pair, so an item survives any trip between them unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from .data import RARITY_NAMES, AffixDef, FixedItemDef, GameData, ItemBaseDef, SetDef
from .rng import Rng


class Rarity(IntEnum):
    """GDD 10.1. Saves store the number, so the order only grows at the end from now on."""

    NORMAL = 0
    MAGIC = 1
    RARE = 2
    SET = 3
    UNIQUE = 4

    @classmethod
    def named(cls, name: str) -> Rarity:
        return cls(RARITY_NAMES.index(name))


def roll_value(lo: float, hi: float, rng: Rng) -> float:
    """One number in lo–hi: whole numbers for flat stats, two decimals for percentages. Affixes, uniques and
    set items all roll the same way."""
    if lo == hi:
        return lo
    v = rng.uniform(lo, hi)
    return round(v) if lo >= 1 else round(v, 2)


@dataclass(frozen=True)
class RolledAffix:
    affix: AffixDef
    value: float

    @property
    def stat(self) -> str:
        return self.affix.stat


@dataclass
class Item:
    """``affixes`` are the random lines of magic and rare items; ``fixed`` is the unique or set item it is, with
    ``fixed_values`` = its rolled numbers in the order of the definition's props."""

    base: ItemBaseDef
    rarity: Rarity
    item_level: int
    affixes: list[RolledAffix] = field(default_factory=list)
    name: str = ""
    fixed: FixedItemDef | None = None
    fixed_values: list[float] = field(default_factory=list)

    @classmethod
    def plain(cls, base: ItemBaseDef) -> Item:
        """A normal item with no affixes (starting gear)."""
        return cls(base, Rarity.NORMAL, base.level, [], base.name)

    @property
    def slot(self) -> str:
        return self.base.slot

    @property
    def set_id(self) -> str | None:
        return self.fixed.set_id if self.fixed else None

    @property
    def mods(self) -> list[tuple[str, float]]:
        """Every bonus line as (stat, value): affixes, or the unique/set item's own lines. Set bonuses are
        not here: they depend on what else is worn (``Loadout.totals``)."""
        if self.fixed:
            return [(p.stat, v) for p, v in zip(self.fixed.props, self.fixed_values, strict=False)]
        return [(a.stat, a.value) for a in self.affixes]

    def stat(self, stat: str) -> float:
        return sum(v for k, v in self.mods if k == stat)


# ---------------------------------------------------------------- drops


@dataclass(frozen=True)
class GoldDrop:
    amount: int


@dataclass(frozen=True)
class PotionDrop:
    kind: str  # life | energy

    @property
    def consumable_id(self) -> str:
        return f"{self.kind}_potion"


@dataclass(frozen=True)
class ScrollDrop:
    kind: str  # portal

    @property
    def consumable_id(self) -> str:
        return f"{self.kind}_scroll"


@dataclass(frozen=True)
class ItemDrop:
    item: Item


Drop = GoldDrop | PotionDrop | ScrollDrop | ItemDrop


def drop_name(drop: Drop) -> str:
    """What the player reads: in the log when picking it up, and on the floor label (Alt)."""
    if isinstance(drop, GoldDrop):
        return f"{drop.amount} Gold"
    if isinstance(drop, PotionDrop):
        return f"{drop.kind.capitalize()} Potion"
    if isinstance(drop, ScrollDrop):
        return f"{drop.kind.capitalize()} Scroll"
    return drop.item.name


@dataclass
class GroundItem:
    """A drop lying in the area until someone picks it up (it is gone when the area is left: worlds regenerate)."""

    id: int
    drop: Drop
    x: float
    y: float


class LootGenerator:
    def __init__(self, data: GameData):
        self._d = data

    def roll_drops(self, rank_id: str, item_level: int, magic_find: float, rng: Rng) -> list[Drop]:
        it = self._d.items
        count = it.drop_count[rank_id]
        rank = self._d.rank(rank_id)
        drops: list[Drop] = []
        if rng.chance(count.no_drop_chance):
            return drops
        need_magic = rank.guaranteed_magic
        for _ in range(rng.randint(max(1, count.min), max(1, count.max))):
            kind = "equipment" if need_magic else rng.weighted(it.drop_kind)
            if kind == "gold":
                drops.append(GoldDrop(max(1, round(rng.uniform(2.0, 6.0) * item_level))))
            elif kind == "potion":
                drops.append(PotionDrop("life" if rng.chance(0.6) else "energy"))
            else:
                minimum = Rarity.MAGIC if need_magic else Rarity.NORMAL
                drops.append(ItemDrop(self.roll_item(item_level, rank.loot_quality_mult, magic_find, minimum, rng)))
                need_magic = False
        return drops

    def roll_rarity(self, quality: float, magic_find: float, minimum: Rarity, rng: Rng) -> Rarity:
        """Tested in order unique → set → rare → magic (GDD 10.3)."""
        c = self._d.items.rarity_chances
        m = quality * (1 + magic_find)
        for rarity, chance in ((Rarity.UNIQUE, c.unique), (Rarity.SET, c.set), (Rarity.RARE, c.rare), (Rarity.MAGIC, c.magic)):
            if rng.chance(chance * m):
                return max(rarity, minimum)
        return minimum

    def roll_item(self, item_level: int, quality: float, magic_find: float, minimum: Rarity, rng: Rng, maximum: Rarity = Rarity.UNIQUE) -> Item:
        """A random item for this item level. ``maximum`` caps the rarity (the vendor sells normal and magic only)."""
        rarity = min(self.roll_rarity(quality, magic_find, minimum, rng), maximum)
        if rarity in (Rarity.UNIQUE, Rarity.SET):
            pool = self._d.items.uniques if rarity == Rarity.UNIQUE else self._d.items.set_pieces
            allowed = {f.id: f.weight for f in pool if f.level <= item_level}
            if allowed:
                return self.make_fixed(self._d.fixed_item(rng.weighted(allowed)), item_level, rng)
            rarity = Rarity.RARE  # nothing hand-made this early: a rare instead
        base = rng.choice([b for b in self._d.items.bases if b.level <= item_level])
        rarity = min(max(rarity, Rarity.named(self._d.item_class(base).min_rarity)), max(maximum, Rarity.MAGIC))
        affixes: list[RolledAffix] = []
        if rarity == Rarity.MAGIC:
            prefix = rng.chance(0.75)
            suffix = (not prefix) or rng.chance(0.5)
            if prefix:
                self._try_add(affixes, "prefix", base, item_level, rng)
            if suffix:
                self._try_add(affixes, "suffix", base, item_level, rng)
        elif rarity == Rarity.RARE:
            rc = self._d.items.rare_affix_count
            for _ in range(rng.randint(rc.min, rc.max)):
                pre = sum(1 for a in affixes if a.affix.kind == "prefix")
                suf = len(affixes) - pre
                kind = "suffix" if pre >= 3 else "prefix" if suf >= 3 else rng.choice(["prefix", "suffix"])
                self._try_add(affixes, kind, base, item_level, rng)
        return Item(base, rarity, item_level, affixes, self._name(base, rarity, affixes, rng))

    def make_fixed(self, fixed: FixedItemDef, item_level: int, rng: Rng) -> Item:
        """A unique or a set piece: its own base and name, each line rolled in its range."""
        rarity = Rarity.SET if fixed.set_id else Rarity.UNIQUE
        values = [roll_value(p.min, p.max, rng) for p in fixed.props]
        return Item(self._d.base(fixed.base), rarity, item_level, [], fixed.name, fixed, values)

    def _try_add(self, out: list[RolledAffix], kind: str, base: ItemBaseDef, ilvl: int, rng: Rng) -> None:
        """Adds one affix that makes sense on this item class (items.json 'on'), never two of a group.
        When no affix of that kind is left, tries the other kind (boots have few prefixes)."""
        used = {a.affix.group for a in out}
        allowed = [a for a in self._d.items.affix_pool(base.item_class) if a.group not in used and any(t.level <= ilvl for t in a.tiers)]
        other = "suffix" if kind == "prefix" else "prefix"
        if sum(1 for a in out if a.affix.kind == other) < 3 and not any(a.kind == kind for a in allowed):
            kind = other
        pool = [a for a in allowed if a.kind == kind]
        if not pool:
            return
        aff = rng.choice(pool)
        tier = max((t for t in aff.tiers if t.level <= ilvl), key=lambda t: t.level)
        out.append(RolledAffix(aff, roll_value(tier.min, tier.max, rng)))

    def _name(self, base: ItemBaseDef, rarity: Rarity, affixes: list[RolledAffix], rng: Rng) -> str:
        """English order: "<prefix> <base> <suffix>", e.g. "Sharp Short Sword of the Bear"."""
        if rarity == Rarity.MAGIC:
            pre = next((a.affix.name for a in affixes if a.affix.kind == "prefix"), "")
            suf = next((a.affix.name for a in affixes if a.affix.kind == "suffix"), "")
            return " ".join(x for x in (pre, base.name, suf) if x)
        if rarity == Rarity.RARE:
            names = self._d.items.rare_names
            return f"{rng.choice(names.first)} {rng.choice(names.second)}"
        return base.name


# ---------------------------------------------------------------- serialization


def item_to_dict(item: Item) -> dict:
    out = {
        "base": item.base.id,
        "rarity": int(item.rarity),
        "itemLevel": item.item_level,
        "name": item.name,
        "affixes": [{"id": a.affix.id, "value": a.value} for a in item.affixes],
    }
    if item.fixed:
        out["fixed"] = {"id": item.fixed.id, "values": list(item.fixed_values)}
    return out


def item_from_dict(d: GameData, raw: dict) -> Item:
    """A unique or set item keeps its rolled numbers; a line added to it later in the data loads at its minimum."""
    fixed = d.fixed_item(raw["fixed"]["id"]) if raw.get("fixed") else None
    values = list(raw["fixed"]["values"]) if fixed else []
    if fixed:
        values = values[: len(fixed.props)] + [p.min for p in fixed.props[len(values) :]]
    return Item(
        base=d.base(fixed.base if fixed else raw["base"]),
        rarity=Rarity(raw["rarity"]),
        item_level=raw["itemLevel"],
        affixes=[RolledAffix(d.affix(a["id"]), a["value"]) for a in raw["affixes"]],
        name=fixed.name if fixed else raw["name"],
        fixed=fixed,
        fixed_values=values,
    )


# ---------------------------------------------------------------- containers


class ItemContainer:
    """A list of items with a capacity: the backpack and the shared stash are both one of these."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items: list[Item] = []

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @property
    def full(self) -> bool:
        return len(self.items) >= self.capacity

    def add(self, item: Item) -> bool:
        if self.full:
            return False
        self.items.append(item)
        return True

    def take(self, index: int) -> Item | None:
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def to_list(self) -> list[dict]:
        return [item_to_dict(i) for i in self.items]

    def load_list(self, d: GameData, raw: list[dict]) -> None:
        self.items = [item_from_dict(d, x) for x in raw][: self.capacity]


class Inventory(ItemContainer):
    """One character's backpack. The grid placement arrives with the inventory screen (M6-03);
    until then the grid size (items.json → inventory) only limits how many items fit."""

    @classmethod
    def for_data(cls, d: GameData) -> Inventory:
        return cls(d.items.inventory.capacity)


class Loadout:
    """Equipped items by equipment slot (items.json → equipment). Two ring slots; the off hand takes a
    shield or a one-hand weapon. Affixes start counting with M6-03."""

    LEGACY = {"ring": "ring1"}  # saves before two rings existed

    def __init__(self, accepts: dict[str, list[str]], sets: list[SetDef] | None = None):
        self.accepts = accepts
        self.sets = {s.id: s for s in sets or []}
        self.slots: dict[str, Item] = {}

    @classmethod
    def for_data(cls, d: GameData) -> Loadout:
        return cls({k: list(v.accepts) for k, v in d.items.equipment.items()}, d.items.sets)

    @classmethod
    def starting(cls, d: GameData) -> Loadout:
        gear = cls.for_data(d)
        for base_id in d.items.starting_gear:
            gear.equip(Item.plain(d.base(base_id)))
        return gear

    def slots_for(self, item: Item) -> list[str]:
        """Every slot the item fits in (a two-handed weapon only in the main hand)."""
        return [k for k, acc in self.accepts.items() if item.slot in acc and not (item.base.two_handed and k == "offhand")]

    def native_slots(self, item: Item) -> list[str]:
        """Slots made for this kind of item (a sword's is the main hand, not the off hand)."""
        return [k for k, acc in self.accepts.items() if acc and acc[0] == item.slot]

    def auto_slot(self, item: Item) -> str | None:
        """Where a double-click puts it: a free slot of its own kind, else the first one (a swap)."""
        native = self.native_slots(item) or self.slots_for(item)
        return next((k for k in native if k not in self.slots), native[0] if native else None)

    def equip(self, item: Item, slot: str | None = None) -> Item | None:
        """Puts the item in ``slot`` (or its automatic slot) and returns what was there before."""
        slot = slot or self.auto_slot(item)
        if slot is None or slot not in self.slots_for(item):
            raise ValueError(f"{item.name} does not fit in {slot}")
        previous = self.slots.get(slot)
        self.slots[slot] = item
        return previous

    def get(self, slot: str) -> Item | None:
        return self.slots.get(slot)

    @property
    def main_hand(self) -> Item | None:
        return self.get("mainhand")

    @property
    def off_hand(self) -> Item | None:
        return self.get("offhand")

    @property
    def body(self) -> Item | None:
        return self.get("body")

    @property
    def has_shield(self) -> bool:
        return self.off_hand is not None and "shield" in self.off_hand.base.tags

    @property
    def has_bow(self) -> bool:
        return self.main_hand is not None and "bow" in self.main_hand.base.tags

    @property
    def has_blade(self) -> bool:
        """A one-hand blade in the main hand (Blades skills need it)."""
        return self.main_hand is not None and "sword" in self.main_hand.base.tags

    def meets(self, need) -> bool:
        """``GearNeed`` from proficiencies.json: what a tree's actives need in hand."""
        if need is None:
            return True
        if need.dual_wield:
            return self.dual_wielding
        if need.off_tag:
            return self.off_hand is not None and need.off_tag in self.off_hand.base.tags
        return self.main_hand is not None and need.main_tag in self.main_hand.base.tags

    @property
    def dual_wielding(self) -> bool:
        return self.main_hand is not None and self.off_hand is not None and self.off_hand.base.dmg_max > 0

    def granted_spells(self) -> dict[str, int]:
        """Spell → levels from worn wands (items.json bases 'spell' and 'spellLevel')."""
        out: dict[str, int] = {}
        for i in self.slots.values():
            if i.base.spell:
                out[i.base.spell] = out.get(i.base.spell, 0) + i.base.spell_level
        return out

    def weapon(self, hand: str = "main") -> Item | None:
        """The weapon that swings this time: ``main``, or ``off`` when holding two weapons."""
        return self.off_hand if hand == "off" and self.dual_wielding else self.main_hand

    @property
    def armor(self) -> float:
        return sum(i.base.armor for i in self.slots.values())

    # ---- bonuses from what is worn (affixes, uniques, set items and set bonuses)

    def set_pieces_worn(self, set_id: str) -> int:
        """Different pieces of the set being worn (the same piece twice counts once)."""
        return len({i.fixed.id for i in self.slots.values() if i.set_id == set_id})

    def set_bonuses(self) -> list[tuple[SetDef, int]]:
        """(set, pieces worn) for every set with a partial or full bonus active."""
        out = []
        for set_id in dict.fromkeys(i.set_id for i in self.slots.values() if i.set_id):
            st, n = self.sets[set_id], self.set_pieces_worn(set_id)
            if any(b.pieces <= n for b in st.bonuses):
                out.append((st, n))
        return out

    def totals(self) -> dict[str, float]:
        """Every bonus of the equipment summed by stat: one place for combat, the character sheet and tooltips.
        Cached until what is worn changes (it is read many times a frame)."""
        key = tuple((k, id(v)) for k, v in self.slots.items())
        if getattr(self, "_totals_key", None) != key:
            self._totals_key, self._totals = key, self._sum_totals()
        return self._totals

    def _sum_totals(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for item in self.slots.values():
            for k, v in item.mods:
                out[k] = out.get(k, 0.0) + v
        for st, n in self.set_bonuses():
            for b in st.bonuses:
                if b.pieces <= n:
                    for k, v in b.stats.items():
                        out[k] = out.get(k, 0.0) + v
        return out

    def stat_total(self, stat: str) -> float:
        return self.totals().get(stat, 0.0)

    @property
    def shield_armor(self) -> float:
        return self.off_hand.base.armor if self.has_shield else 0.0

    def to_dict(self) -> dict[str, dict]:
        return {slot: item_to_dict(i) for slot, i in self.slots.items()}

    @classmethod
    def from_dict(cls, d: GameData, raw: dict[str, dict]) -> Loadout:
        gear = cls.for_data(d)
        for slot, item in raw.items():
            it = item_from_dict(d, item)
            slot = cls.LEGACY.get(slot, slot)
            gear.equip(it, slot if slot in gear.slots_for(it) else None)
        return gear


class SharedStash(ItemContainer):
    """Stash shared by every character, in its own versioned file."""

    VERSION = 2
    DEFAULT_CAPACITY = 100

    def __init__(self, capacity: int = DEFAULT_CAPACITY, gold: int = 0):
        super().__init__(capacity)
        self.gold = gold

    def deposit(self, item: Item) -> bool:
        return self.add(item)

    def withdraw(self, index: int) -> Item | None:
        return self.take(index)

    def to_json(self) -> str:
        return json.dumps(
            {"version": self.VERSION, "capacity": self.capacity, "gold": self.gold, "items": self.to_list()},
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, d: GameData, text: str) -> SharedStash:
        raw = json.loads(text)
        if raw.get("version", 1) > cls.VERSION:
            raise ValueError(f"Stash version {raw['version']} is newer than the game.")
        s = cls(raw.get("capacity", cls.DEFAULT_CAPACITY), raw.get("gold", 0))
        s.load_list(d, raw.get("items", []))
        return s
