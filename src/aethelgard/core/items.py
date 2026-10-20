"""Loot (GDD 10.3) e baú compartilhado (GDD 10.5)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from .data import AffixDef, GameData, ItemBaseDef
from .rng import Rng


class Rarity(IntEnum):
    NORMAL = 0
    MAGIC = 1
    RARE = 2
    UNIQUE = 3


@dataclass(frozen=True)
class RolledAffix:
    affix: AffixDef
    value: float


@dataclass
class Item:
    base: ItemBaseDef
    rarity: Rarity
    item_level: int
    affixes: list[RolledAffix] = field(default_factory=list)
    name: str = ""

    def stat(self, stat: str) -> float:
        return sum(a.value for a in self.affixes if a.affix.stat == stat)


@dataclass(frozen=True)
class GoldDrop:
    amount: int


@dataclass(frozen=True)
class PotionDrop:
    kind: str


@dataclass(frozen=True)
class ScrollDrop:
    kind: str


@dataclass(frozen=True)
class ItemDrop:
    item: Item


_RARE_A = ["Ruína", "Fúria", "Sombra", "Morte", "Pesar", "Tormenta", "Corvo", "Lua", "Espinho", "Brasa"]
_RARE_B = ["Mordida", "Guarda", "Grito", "Véu", "Presa", "Marca", "Coroa", "Lâmina", "Pele", "Juramento"]


class LootGenerator:
    def __init__(self, data: GameData):
        self._d = data

    def roll_drops(self, rank_id: str, item_level: int, magic_find: float, rng: Rng) -> list:
        it = self._d.items
        count = it.drop_count[rank_id]
        rank = self._d.rank(rank_id)
        drops: list = []
        if rng.chance(count.no_drop_chance):
            return drops
        need_magic = rank.guaranteed_magic
        for _ in range(rng.randint(max(1, count.min), max(1, count.max))):
            kind = "equipment" if need_magic else rng.weighted(it.drop_kind)
            if kind == "gold":
                drops.append(GoldDrop(max(1, round(rng.uniform(2.0, 6.0) * item_level))))
            elif kind == "potion":
                drops.append(PotionDrop("vida" if rng.chance(0.6) else "energia"))
            elif kind == "scroll":
                drops.append(ScrollDrop("portal"))
            else:
                minimum = Rarity.MAGIC if need_magic else Rarity.NORMAL
                drops.append(ItemDrop(self.roll_item(item_level, rank.loot_quality_mult, magic_find, minimum, rng)))
                need_magic = False
        return drops

    def roll_rarity(self, quality: float, magic_find: float, minimum: Rarity, rng: Rng) -> Rarity:
        c = self._d.items.rarity_chances
        m = quality * (1 + magic_find)
        if rng.chance(c.unique * m):
            return Rarity.UNIQUE
        if rng.chance(c.rare * m):
            return Rarity.RARE
        if rng.chance(c.magic * m):
            return Rarity.MAGIC
        return minimum

    def roll_item(self, item_level: int, quality: float, magic_find: float, minimum: Rarity, rng: Rng) -> Item:
        bases = [b for b in self._d.items.bases if b.level <= item_level]
        base = rng.choice(bases)
        rarity = max(self.roll_rarity(quality, magic_find, minimum, rng), minimum)
        if rarity == Rarity.UNIQUE:  # únicos feitos à mão ainda não existem (backlog M6-06)
            rarity = Rarity.RARE
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

    def _try_add(self, out: list[RolledAffix], kind: str, base: ItemBaseDef, ilvl: int, rng: Rng) -> None:
        used = {a.affix.group for a in out}
        pool = [
            a
            for a in self._d.items.affixes
            if a.kind == kind and a.group not in used and set(a.tags) & set(base.tags) and any(t.level <= ilvl for t in a.tiers)
        ]
        if not pool:
            return
        aff = rng.choice(pool)
        tier = max((t for t in aff.tiers if t.level <= ilvl), key=lambda t: t.level)
        v = rng.uniform(tier.min, tier.max)
        v = round(v) if tier.min >= 1 else round(v, 2)
        out.append(RolledAffix(aff, v))

    @staticmethod
    def _name(base: ItemBaseDef, rarity: Rarity, affixes: list[RolledAffix], rng: Rng) -> str:
        if rarity == Rarity.MAGIC:
            pre = next((a.affix.name for a in affixes if a.affix.kind == "prefix"), "")
            suf = next((a.affix.name for a in affixes if a.affix.kind == "suffix"), "")
            return " ".join(x for x in (base.name, pre, suf) if x)
        if rarity == Rarity.RARE:
            return f"{rng.choice(_RARE_A)} {rng.choice(_RARE_B)}"
        return base.name


# ---------------------------------------------------------------- baú compartilhado


def item_to_dict(item: Item) -> dict:
    return {
        "base": item.base.id,
        "rarity": int(item.rarity),
        "itemLevel": item.item_level,
        "name": item.name,
        "affixes": [{"id": a.affix.id, "value": a.value} for a in item.affixes],
    }


def item_from_dict(d: GameData, raw: dict) -> Item:
    affixes = {a.id: a for a in d.items.affixes}
    return Item(
        base=d.base(raw["base"]),
        rarity=Rarity(raw["rarity"]),
        item_level=raw["itemLevel"],
        affixes=[RolledAffix(affixes[a["id"]], a["value"]) for a in raw["affixes"]],
        name=raw["name"],
    )


class SharedStash:
    """Baú comum a todos os personagens, num arquivo próprio e versionado."""

    VERSION = 1
    DEFAULT_CAPACITY = 100

    def __init__(self, capacity: int = DEFAULT_CAPACITY, gold: int = 0):
        self.capacity = capacity
        self.gold = gold
        self.items: list[dict] = []

    @property
    def full(self) -> bool:
        return len(self.items) >= self.capacity

    def deposit(self, item: Item) -> bool:
        if self.full:
            return False
        self.items.append(item_to_dict(item))
        return True

    def withdraw(self, index: int, d: GameData) -> Item | None:
        if not 0 <= index < len(self.items):
            return None
        return item_from_dict(d, self.items.pop(index))

    def to_json(self) -> str:
        return json.dumps(
            {"version": self.VERSION, "capacity": self.capacity, "gold": self.gold, "items": self.items},
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> SharedStash:
        raw = json.loads(text)
        if raw.get("version", 1) > cls.VERSION:
            raise ValueError(f"Baú da versão {raw['version']} é mais novo que o jogo.")
        s = cls(raw.get("capacity", cls.DEFAULT_CAPACITY), raw.get("gold", 0))
        s.items = list(raw.get("items", []))
        return s
