"""Hand-made pieces from Tiled stamped by the seed (M5-03 open areas, M5-04 dungeon rooms) and their chests."""

import numpy as np
import pytest

from aethelgard.core.events import LootPicked
from aethelgard.core.items import ChestDrop, GoldDrop, ItemDrop, PotionDrop, ScrollDrop
from aethelgard.core.session import GameSession
from aethelgard.core.world import PickUp, World
from aethelgard.core.world_gen import FLOOR, AreaGenerator, AreaLayout, load_tiled_map, stamp


@pytest.mark.parametrize("which", ["open", "rooms"])
def test_every_prefab_loads_and_is_connected_inside(data, which):
    for p in getattr(data.areas.prefabs, which):
        piece = load_tiled_map(p.map)
        assert piece.mask is not None and piece.mask.any()
        if which == "rooms":
            assert piece.connectors, f"{p.id}: a room needs a connector"
            start = piece.connectors[0]
        else:
            start = piece.entry
            assert piece.is_floor(start), f"{p.id}: the entry is floor"
        piece.entry = start
        dist = piece.distances_from(start)
        for c in piece.chests + piece.spawn_points + piece.connectors:
            assert dist[c[1], c[0]] >= 0, f"{p.id}: {c} cannot be reached inside the piece"


def test_stamping_keeps_the_cells_a_piece_leaves_empty():
    L = AreaLayout.filled(6, 6, FLOOR)
    piece = AreaLayout.filled(3, 3)
    piece.mask = np.zeros((3, 3), dtype=bool)
    piece.mask[1, 1] = True  # one wall in the middle, the rest untouched
    piece.chests.append((0, 0))
    stamp(L, piece, 2, 2)
    assert L.tiles[3, 3] == 0 and L.tiles[2, 2] == FLOOR and L.chests == [(2, 2)]


@pytest.mark.parametrize("area_id", ["plains", "woods", "hills", "cave", "crypt"])
def test_areas_get_pieces_and_stay_valid_and_deterministic(data, area_id):
    gen = AreaGenerator(data)
    a = data.area(area_id)
    total = 0
    for seed in range(6):
        for fl in range(a.floors):
            L = gen.generate(a, fl, seed, a.normal_packs)
            assert L.validate() is None
            dist = L.distances_from(L.entry)
            assert all(L.is_floor(c) and dist[c[1], c[0]] >= 0 for c in L.chests), "every chest can be reached"
            total += len(L.chests)
            again = gen.generate(a, fl, seed, a.normal_packs)
            assert again.chests == L.chests and (again.tiles == L.tiles).all()
    assert total > 0


def test_a_chest_opens_into_loot_once(data):
    s = GameSession(data, 4)
    w = World([s])
    w.enter_area("plains")
    chests = [g for g in w.ground.values() if isinstance(g.drop, ChestDrop)]
    assert chests and chests[0].drop.level == data.area("plains").level
    g = chests[0]
    p = w.players[0]
    p.x, p.y = g.x + 0.5, g.y
    w.monsters.clear()
    picked = []
    s.bus.subscribe(LootPicked, picked.append)
    w.apply(PickUp(0, g.id))
    for _ in range(30):
        w.step(1 / 60)
    assert g.id not in w.ground and isinstance(picked[0].drop, ChestDrop)
    loot = [x for x in w.ground.values() if not isinstance(x.drop, ChestDrop) and abs(x.x - g.x) < 2 and abs(x.y - g.y) < 2]
    assert all(isinstance(x.drop, GoldDrop | PotionDrop | ItemDrop | ScrollDrop) for x in loot)
    w.enter_area("village")
    w.enter_area("plains")
    assert g.id not in w.ground, "opened chests stay open for the rest of the game"
