"""Waypoints, the shared stash and the merchant: commands the world checks (stand by the NPC)."""

import json

import pytest

from aethelgard.core.events import StashChanged, WaypointReached
from aethelgard.core.items import Item, SharedStash
from aethelgard.core.saves import SaveStore
from aethelgard.core.session import GameSession
from aethelgard.core.town import item_price, sell_price
from aethelgard.core.world import Buy, BuyConsumable, Sell, StashGold, StashPut, StashTake, TravelWaypoint, World

DT = 1 / 60


def in_town(data):
    s = GameSession(data, 4)
    w = World([s])
    w.enter_area(data.town_id)
    w.stash = SharedStash()
    return s, w, w.players[0]


def stand_by(w, p, role):
    cell = next(c for c, props in w.layout.markers.values() if props.get("role") == role)
    p.x, p.y = cell[0] + 1.0, float(cell[1])


# ---------------------------------------------------------------- waypoints


def test_touching_a_waypoint_activates_it_and_travel_needs_both_ends(data):
    s, w, p = in_town(data)
    seen = []
    s.bus.subscribe(WaypointReached, seen.append)
    w.enter_area("woods")
    w.monsters.clear()
    wp = w.layout.waypoint
    p.x, p.y = float(wp[0]) + 3, float(wp[1])
    w.step(DT)
    assert "woods" not in s.waypoints
    p.x, p.y = float(wp[0]), float(wp[1])
    w.step(DT)
    assert "woods" in s.waypoints and seen and seen[-1].new
    assert w.apply(TravelWaypoint(0, "hills")) == "You have not found that waypoint yet"
    assert w.apply(TravelWaypoint(0, data.town_id)) is None and w.area_id == data.town_id
    assert abs(p.x - w.layout.waypoint[0]) + abs(p.y - w.layout.waypoint[1]) <= 2
    p.x += 6
    assert w.apply(TravelWaypoint(0, "woods")) == "Stand on a waypoint to travel"


def test_arriving_on_a_waypoint_does_not_reopen_the_list(data):
    s, w, p = in_town(data)
    s.waypoints.add("woods")
    wp = w.layout.waypoint
    p.x, p.y = float(wp[0]), float(wp[1])
    w.apply(TravelWaypoint(0, "woods"))
    seen = []
    s.bus.subscribe(WaypointReached, seen.append)
    w.monsters.clear()
    w.step(DT)
    assert not seen


def test_waypoints_are_saved(data):
    s = GameSession(data, 1)
    s.waypoints |= {"woods", "crypt"}
    back = GameSession.load(data, s.save())
    assert back.waypoints == {data.town_id, "woods", "crypt"}
    raw = json.loads(s.save())
    raw["waypoints"].append("not_an_area")
    assert "not_an_area" not in GameSession.load(data, json.dumps(raw)).waypoints


# ---------------------------------------------------------------- stash


def test_stash_moves_items_and_gold_only_beside_it(data):
    s, w, p = in_town(data)
    s.gold = 100
    s.inventory.add(Item.plain(data.base("ring")))
    p.x, p.y = 1.0, 1.0
    assert w.apply(StashPut(0, 0)) == "Stand by the stash"
    stand_by(w, p, "stash")
    changed = []
    s.bus.subscribe(StashChanged, changed.append)
    assert w.apply(StashPut(0, 0)) is None and not s.inventory.items and len(w.stash.items) == 1
    assert w.apply(StashGold(0, 70)) is None and (s.gold, w.stash.gold) == (30, 70)
    assert w.apply(StashGold(0, -500)) is None and (s.gold, w.stash.gold) == (100, 0), "clamped to what is there"
    assert w.apply(StashTake(0, 0)) is None and s.inventory.items[0].base.id == "ring"
    assert len(changed) == 4


def test_stash_file_round_trip(data, tmp_path):
    store = SaveStore.for_stash(tmp_path)
    assert store.load_stash(data).items == []
    st = SharedStash(gold=12)
    st.deposit(Item.plain(data.base("kite_shield")))
    store.save_stash(st)
    back = store.load_stash(data)
    assert back.gold == 12 and back.items[0].base.id == "kite_shield"


# ---------------------------------------------------------------- merchant


def test_vendor_restocks_on_every_town_visit_with_normal_and_magic_only(data):
    s, w, p = in_town(data)
    first = [i.name for i in w.vendor.stock]
    assert len(first) == data.items.trade.stock_size and all(int(i.rarity) <= 1 for i in w.vendor.stock)
    w.enter_area("plains")
    w.enter_area(data.town_id)
    assert [i.name for i in w.vendor.stock] != first


def test_buying_and_selling(data):
    s, w, p = in_town(data)
    stand_by(w, p, "vendor")
    item = w.vendor.stock[0]
    price = item_price(data, item)
    s.gold = price - 1
    assert w.apply(Buy(0, 0)) == f"{item.name} costs {price} gold"
    s.gold = price
    assert w.apply(Buy(0, 0)) is None and s.gold == 0 and s.inventory.items[-1] is item
    assert w.apply(Sell(0, len(s.inventory.items) - 1)) is None
    assert s.gold == sell_price(data, item) < price and w.vendor.stock[-1] is item, "can be bought back"
    s.gold = 30
    assert w.apply(BuyConsumable(0, "life_potion")) is None and s.consumables["life_potion"] == 1 and s.gold == 0


def test_trading_needs_the_merchant_nearby(data):
    s, w, p = in_town(data)
    p.x, p.y = 1.0, 1.0
    assert w.apply(Buy(0, 0)) == "Stand by the merchant"


def test_prices_grow_with_rarity_and_level(data):
    plain = Item.plain(data.base("short_sword"))
    assert item_price(data, plain) == pytest.approx(20 + 6 * plain.item_level)
    assert sell_price(data, plain) == round(item_price(data, plain) * 0.2)
