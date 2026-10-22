"""Crafting (from Act 2, at the crafter in Kharos): salvage items into materials and spend them on recipes
(data/crafting.json). Pure rules on a character's backpack and materials; the world checks that the player stands
by the crafter (World ``Salvage`` and ``Craft`` commands).

Recipe kinds:

* **reforge**: new random lines for a magic or rare item (same base, rarity and item level);
* **imbue**: a normal item becomes magic at its item level;
* **upgrade**: the same item, lines and all, on the next stronger base of its item class;
* **empower**: a magic item becomes rare, keeping its lines and gaining more;
* **craft**: a normal or magic item becomes a rare of the same base at the character's level, with the recipe's own
  lines added on top of the random ones (stored as "crafted" affixes, see ``GameData.crafted_affixes``).
"""

from __future__ import annotations

from collections import Counter

from .data import CraftRecipeDef, GameData, ItemBaseDef
from .items import Item, LootGenerator, Rarity, RolledAffix, roll_value
from .rng import Rng

RARITY_NAMES = {Rarity.NORMAL: "normal", Rarity.MAGIC: "magic", Rarity.RARE: "rare", Rarity.UNIQUE: "unique", Rarity.SET: "set"}


class Crafter:
    def __init__(self, data: GameData):
        self._d = data
        self._loot = LootGenerator(data)

    # ---- salvage

    def salvage_yield(self, item: Item) -> dict[str, int]:
        """Materials an item breaks into: by rarity, +1 of each per salvageLevelStep item levels."""
        cr = self._d.crafting
        base = cr.salvage.get(RARITY_NAMES[item.rarity], {})
        bonus = item.item_level // max(1, cr.salvage_level_step)
        return {m: n + bonus for m, n in base.items()}

    # ---- recipes

    def next_base(self, base: ItemBaseDef) -> ItemBaseDef | None:
        """The weakest base of the same item class that needs a higher item level (what an upgrade becomes)."""
        stronger = [b for b in self._d.items.bases if b.item_class == base.item_class and b.level > base.level
                    and b.spell == base.spell and b.two_handed == base.two_handed]
        return min(stronger, key=lambda b: b.level) if stronger else None

    def why_not(self, recipe: CraftRecipeDef, item: Item, materials: Counter) -> str | None:
        """Why this recipe cannot be used on this item now (None = it can)."""
        if RARITY_NAMES[item.rarity] not in recipe.rarity:
            return f"{recipe.name} takes {' or '.join(recipe.rarity)} items"
        if recipe.on and item.base.item_class not in self._d.items.classes_named(recipe.on):
            return f"{recipe.name} does not work on a {self._d.items.slot_names.get(item.base.slot, item.base.slot).lower()}"
        if recipe.kind == "upgrade" and self.next_base(item.base) is None:
            return f"{item.base.name} is already the strongest of its kind"
        if recipe.kind == "imbue" and item.base.item_class in self._d.items.classes_named(["jewelry"]):
            return "Rings and amulets are never plain"
        missing = [f"{n - materials[m]} {self._d.crafting.materials[m]}" for m, n in recipe.cost.items() if materials[m] < n]
        if missing:
            return "Missing " + ", ".join(missing)
        return None

    def apply(self, recipe: CraftRecipeDef, item: Item, character_level: int, rng: Rng) -> Item:
        """The new item (the caller checked ``why_not`` and pays the cost)."""
        loot, kind = self._loot, recipe.kind
        if kind == "reforge":
            return loot.make(item.base, item.rarity, item.item_level, rng)
        if kind == "imbue":
            return loot.make(item.base, Rarity.MAGIC, item.item_level, rng)
        if kind == "upgrade":
            base = self.next_base(item.base)
            return Item(base, item.rarity, max(item.item_level, base.level), list(item.affixes), item.name.replace(item.base.name, base.name))
        if kind == "empower":
            return loot.make(item.base, Rarity.RARE, item.item_level, rng, keep=item.affixes)
        # craft: the recipe's lines first, then random ones up to a rare's count
        level = max(item.item_level, character_level)
        crafted = [RolledAffix(self._d.affix(f"crafted_{recipe.id}_{i}"), roll_value(p.min, p.max, rng)) for i, p in enumerate(recipe.props)]
        out = loot.make(item.base, Rarity.RARE, level, rng, keep=crafted)
        return out
