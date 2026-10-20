"""Sprites: animation math (no screen), data/ui/sprites.json against the imported sheets, and a draw smoke test."""

import json
import os
from importlib import resources

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from aethelgard.client.animation import (  # noqa: E402
    DIRECTION_NAMES,
    DIRECTIONS,
    choose_pose,
    direction8,
    frame_at,
    frame_by_progress,
    pack_shelves,
    parse_flare_animation,
    pose_frame,
)
from aethelgard.tools.import_flare import sheets_in_use  # noqa: E402

ASSETS = resources.files("aethelgard") / "assets" / "sprites"
REQUIRED = {"stance", "run", "swing", "block", "hit", "die"}


@pytest.fixture(scope="module")
def cfg():
    return json.loads((resources.files("aethelgard") / "data" / "ui" / "sprites.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- math


@pytest.mark.parametrize(
    ("vec", "name"),
    [((1, 0), "E"), ((0, 1), "S"), ((-1, 0), "W"), ((0, -1), "N"), ((1, 1), "SE"), ((-1, -1), "NW"), ((-1, 1), "SW"), ((1, -1), "NE")],
)
def test_direction_follows_the_art_compass(vec, name):
    assert DIRECTION_NAMES[direction8(*vec)] == name


def test_direction_of_a_zero_vector_is_the_default():
    assert direction8(0, 0) == 6 and direction8(0, 0, default=2) == 2


def test_frames_by_time():
    assert [frame_at(4, 0.4, "looped", t) for t in (0, 0.1, 0.35, 0.4, 0.5)] == [0, 1, 3, 0, 1]
    assert [frame_at(4, 0.4, "back_forth", k * 0.1) for k in range(8)] == [0, 1, 2, 3, 2, 1, 0, 1]
    assert frame_at(6, 0.8, "play_once", 99) == 5, "holds the last frame"
    assert frame_at(1, 0.5, "looped", 3) == 0


def test_attack_frames_follow_the_core_timing():
    assert [frame_by_progress(4, p) for p in (0, 0.24, 0.45, 0.99, 1.0, 1.5)] == [0, 0, 1, 3, 3, 3]


def test_pose_priorities():
    kw = dict(moving=True, guarding=True, attack_progress=0.5, attack_anim="cast", state_time=0.2, clock=3.0, stride_cycles=1.25)
    assert choose_pose("dead", **kw).anim == "die"
    assert choose_pose("attacking", **kw).anim == "cast"
    assert choose_pose("hit_recovery", **kw).anim == "hit"
    assert choose_pose("stunned", **kw).anim == "hit"
    assert choose_pose("idle", **kw).anim == "block", "guard up wins over walking"
    kw["guarding"] = False
    run = choose_pose("idle", **kw)
    assert run.anim == "run" and pose_frame(run, 8, 0.5, "looped") == 2, "a quarter into the second stride"
    kw["moving"] = False
    assert choose_pose("idle", **kw).anim == "stance"


def test_parse_flare_animation():
    text = """image=images/enemies/thing.png

[stance]
frames=2
duration=533ms
type=back_forth
frame=0,0,10,20,30,40,15,38
frame=1,SW,1,2,3,4,5,6

[swing]
frames=1
duration=1s
type=play_once
frame=0,7,0,0,8,8,4,8
"""
    image, anims = parse_flare_animation(text)
    assert image == "images/enemies/thing.png"
    assert anims["stance"].frames == 2 and anims["stance"].seconds == pytest.approx(0.533) and anims["stance"].loop == "back_forth"
    assert anims["stance"].rects[(0, 0)] == (10, 20, 30, 40, 15, 38)
    assert anims["stance"].rects[(1, 0)] == (1, 2, 3, 4, 5, 6), "named directions: SW = 0"
    assert anims["swing"].seconds == 1.0 and (0, 7) in anims["swing"].rects


def test_shelf_packing_never_overlaps():
    sizes = [(30, 40), (60, 10), (20, 20), (100, 35), (5, 5)] * 7
    pos, height = pack_shelves(sizes, 128)
    boxes = [pygame.Rect(x, y, w, h) for (x, y), (w, h) in zip(pos, sizes, strict=True)]
    assert all(b.right <= 128 and b.bottom <= height for b in boxes)
    assert not any(a.colliderect(b) for i, a in enumerate(boxes) for b in boxes[i + 1 :])
    with pytest.raises(ValueError):
        pack_shelves([(200, 5)], 128)


# ---------------------------------------------------------------- data against the imported art


def test_every_sheet_in_use_was_imported_and_nothing_else(cfg):
    used = set(sheets_in_use(cfg))
    found = set(_sheet_files())
    assert used == found, "run python -m aethelgard.tools.import_flare after changing looks or gear"


def _sheet_files():
    def walk(node, prefix=""):
        for child in node.iterdir():
            if child.is_dir():
                yield from walk(child, f"{prefix}{child.name}/")
            elif child.name.endswith(".json"):
                yield f"{prefix}{child.name[:-5]}"

    return walk(ASSETS)


def test_sheets_have_the_animations_and_eight_directions(cfg):
    for sheet in sheets_in_use(cfg):
        meta = json.loads((ASSETS / f"{sheet}.json").read_text(encoding="utf-8"))
        assert (ASSETS / f"{sheet}.png").is_file(), sheet
        assert REQUIRED <= set(meta["anims"]), sheet
        for name, a in meta["anims"].items():
            assert len(a["rects"]) == a["frames"], (sheet, name)
            assert all(len(row) == DIRECTIONS for row in a["rects"]), (sheet, name)


def test_looks_and_gear_match_the_game_data(cfg, data):
    types = {t.id for t in data.monsters.types}
    assert set(cfg["looks"]) - {"player"} <= types, "a look is the player or a monster type id"
    bases = {b.id for b in data.items.bases}
    assert set(cfg["gear"]) <= bases, "gear entries are item base ids"
    for look_id, look in cfg["looks"].items():
        attack = look.get("attack", "swing")
        for sheet in look["layers"].values():
            anims = json.loads((ASSETS / f"{sheet}.json").read_text(encoding="utf-8"))["anims"]
            assert attack in anims, (look_id, sheet, attack)
    assert set(cfg["layerOrder"]) == set(DIRECTION_NAMES)
    assert set(cfg["slotLayers"]) <= set(data.items.equipment), "slotLayers keys are equipment slots (items.json)"
    assert all(b.base_type in types for b in data.monsters.bosses), "bosses are drawn with their base type's look"


# ---------------------------------------------------------------- drawing


def test_draws_the_player_and_a_corpse():
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import ActorSprites, SpriteLibrary

    pygame.init()
    try:
        screen = pygame.display.set_mode((200, 200))
        lib = SpriteLibrary()
        spr = ActorSprites(lib)
        cam = Camera(200, 200)

        class Actor:
            x = y = 0.0
            face_x, face_y = 1.0, 0.0
            state, attack_elapsed, attack_duration = "attacking", 0.3, 0.55

        layers = lib.layers("player", [("mainhand", "short_sword"), ("offhand", "buckler"), ("body", "chain_mail")])
        assert layers["main"] == "flare/male/shortsword" and layers["off"] == "flare/male/buckler"
        assert layers["chest"] == "flare/male/chain_cuirass", "armor replaces the bare chest"
        dual = lib.layers("player", [("mainhand", "long_sword"), ("offhand", "short_sword")])
        assert dual["main"] == "flare/male/longsword" and "off" not in dual, "an off-hand sword never takes the main hand's place"
        assert lib.layers("wolf") is None, "no look: the client keeps the placeholder"
        box = spr.draw_actor(screen, cam, ("p", 0), Actor(), layers)
        assert box.h > 25 and box.collidepoint(100, 95), "drawn standing on its feet at the center"
        spr.add_corpse(Actor(), lib.layers("skeleton"), 1.0)
        spr.tick(0.5)
        spr.draw_corpses(screen, cam, lambda x, y: True)
        spr.tick(60)
        assert spr.corpses == [], "bodies go away"
    finally:
        pygame.quit()
