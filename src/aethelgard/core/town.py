"""Town services (GDD 10.5 and 12.2): the vendor's stock and prices. The world runs the trades as
commands (``Buy``, ``Sell``, ``BuyConsumable``) and checks the player stands by the NPC."""

from __future__ import annotations

from .data import GameData
from .items import Item, ItemContainer, LootGenerator, Rarity
from .rng import Rng


def item_price(d: GameData, item: Item) -> int:
    """What the vendor asks for an item (rarity, item level and number of bonus lines)."""
    t = d.items.trade
    mult = t.rarity_mult[min(int(item.rarity), len(t.rarity_mult) - 1)]
    return max(1, round((t.base_price + t.per_item_level * item.item_level + t.per_affix * len(item.mods)) * mult))


def sell_price(d: GameData, item: Item) -> int:
    """What the vendor pays: a fraction of its own price, never zero."""
    return max(1, round(item_price(d, item) * d.items.trade.sell_fraction))


class Vendor:
    """The merchant's items, on a shelf of the same grid as the stash. Normal and magic only (rares, set items
    and uniques are found, not bought)."""

    COLS, ROWS = 10, 10

    def __init__(self, d: GameData):
        self._d = d
        self.stock = ItemContainer(self.COLS, self.ROWS)

    def restock(self, item_level: int, rng: Rng) -> None:
        loot = LootGenerator(self._d)
        self.stock = ItemContainer(self.COLS, self.ROWS)
        for _ in range(self._d.items.trade.stock_size):  # what does not fit on the shelf is not offered
            self.stock.add(loot.roll_item(max(1, item_level), 1.0, 0.0, Rarity.NORMAL, rng, maximum=Rarity.MAGIC))
