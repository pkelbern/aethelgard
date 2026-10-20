"""Sprites: animation math, sprites.json against the game data, the art download (no network: a fake Flare tree) and drawing."""

import json
import os

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
from aethelgard.tools.fetch_art import fetch_art, load_config, local_fetch, missing_sheets, sheets_in_use, source_of  # noqa: E402

REQUIRED = {"stance", "run", "swing", "block", "hit", "die"}


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture
def screen():
    pygame.init()
    yield pygame.display.set_mode((200, 200))
    pygame.quit()


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


# ---------------------------------------------------------------- sprites.json against the game data


def test_looks_and_gear_match_the_game_data(cfg, data):
    types = {t.id for t in data.monsters.types}
    assert set(cfg["looks"]) - {"player"} <= types, "a look is the player or a monster type id"
    assert set(cfg["gear"]) <= {b.id for b in data.items.bases}, "gear entries are item base ids"
    assert set(cfg["slotLayers"]) <= set(data.items.equipment), "slotLayers keys are equipment slots (items.json)"
    assert set(cfg["layerOrder"]) == set(DIRECTION_NAMES)
    assert all(look.get("attack", "swing") in cfg["import"]["anims"] for look in cfg["looks"].values())
    assert REQUIRED <= set(cfg["import"]["anims"])
    assert all(b.base_type in types for b in data.monsters.bosses), "bosses are drawn with their base type's look"
    for sheet in sheets_in_use(cfg):
        assert source_of(sheet, cfg).endswith(".txt")


def test_the_art_is_not_in_the_repository():
    from importlib import resources

    assert not (resources.files("aethelgard") / "assets").is_dir(), "the art is downloaded on first run, never committed"


# ---------------------------------------------------------------- download (fake Flare tree, no network)


def make_flare_tree(root, sheets_cfg):
    """Tiny Flare-like files: one 8×8 frame per direction for every animation, colored per direction."""
    imp = sheets_cfg["import"]
    for sheet in sheets_in_use(sheets_cfg):
        name = sheet.rsplit("/", 1)[1]
        img = pygame.Surface((64, 8), pygame.SRCALPHA)
        for d in range(DIRECTIONS):
            img.fill((30 * d, 200, 100, 255), (d * 8, 0, 8, 8))
        image_rel = f"images/{name}.png"
        (root / imp["sourceRoot"] / "images").mkdir(parents=True, exist_ok=True)
        pygame.image.save(img, str(root / imp["sourceRoot"] / image_rel))
        lines = [f"image={image_rel}"]
        for anim in imp["anims"]:
            lines += ["", f"[{anim}]", "frames=1", "duration=100ms", "type=play_once"]
            lines += [f"frame=0,{d},{d * 8},0,8,8,4,8" for d in range(DIRECTIONS)]
        txt = root / source_of(sheet, sheets_cfg)
        txt.parent.mkdir(parents=True, exist_ok=True)
        txt.write_text("\n".join(lines), encoding="utf-8")
    (root / "LICENSE.txt").write_text("CC-BY-SA 3.0", encoding="utf-8")


def small_cfg(cfg):
    """The real config, reduced to the player and one monster, at full scale and full color."""
    c = json.loads(json.dumps(cfg))
    c["looks"] = {"player": cfg["looks"]["player"], "skeleton": cfg["looks"]["skeleton"]}
    c["gear"] = {"short_sword": cfg["gear"]["short_sword"]}
    c["import"].update(scale=1.0, colors=0, atlasWidth=128)
    return c


def test_download_builds_the_missing_sheets_and_resumes(screen, cfg, tmp_path):
    c = small_cfg(cfg)
    make_flare_tree(tmp_path / "flare", c)
    art = tmp_path / "art"
    assert missing_sheets(c, art) == sheets_in_use(c)
    calls = []
    fetch = local_fetch(tmp_path / "flare")
    built = fetch_art(c, art, lambda rel: calls.append(rel) or fetch(rel))
    assert built == sheets_in_use(c) and missing_sheets(c, art) == []
    assert (art / "sprites" / "flare" / "ATTRIBUTION.md").is_file()
    assert (art / "sprites" / "flare" / "LICENSE-CC-BY-SA-3.0.txt").read_text() == "CC-BY-SA 3.0"
    meta = json.loads((art / "sprites" / "flare" / "enemies" / "skeleton.json").read_text())
    assert REQUIRED <= set(meta["anims"]) and all(len(r) == DIRECTIONS for r in meta["anims"]["run"]["rects"])
    calls.clear()
    assert fetch_art(c, art, fetch) == [] and calls == [], "nothing missing: nothing downloaded"
    (art / "sprites" / "flare" / "enemies" / "skeleton.json").unlink()
    assert fetch_art(c, art, fetch) == ["flare/enemies/skeleton"], "only what is missing"


def test_failed_download_raises_and_leaves_no_half_sheet(screen, cfg, tmp_path):
    c = small_cfg(cfg)

    def offline(rel):
        raise OSError("no network")

    with pytest.raises(OSError):
        fetch_art(c, tmp_path / "art", offline)
    assert missing_sheets(c, tmp_path / "art") == sheets_in_use(c)
    assert not list((tmp_path / "art").rglob("*.json"))


def test_background_download_then_real_sprites(screen, cfg, tmp_path):
    from aethelgard.client.sprites import ArtDownload, SpriteLibrary

    c = small_cfg(cfg)
    make_flare_tree(tmp_path / "flare", c)
    lib = SpriteLibrary(c, tmp_path / "art")
    assert lib.sheet("flare/enemies/skeleton") is None, "not downloaded yet"
    dl = ArtDownload(lib, fetch=local_fetch(tmp_path / "flare"))
    assert dl.start()
    dl.wait(30)
    assert dl.poll() == "Art downloaded." and dl.poll() is None, "reported once"
    assert lib.sheet("flare/enemies/skeleton") is not None, "loaded after the download"
    assert not ArtDownload(lib).start(), "nothing missing: no second download"


def test_failed_background_download_keeps_the_game_going(screen, cfg, tmp_path):
    from aethelgard.client.sprites import ArtDownload, SpriteLibrary

    def offline(rel):
        raise OSError("no network")

    dl = ArtDownload(SpriteLibrary(small_cfg(cfg), tmp_path / "art"), fetch=offline)
    assert dl.start()
    dl.wait(30)
    msg = dl.poll()
    assert dl.state == "failed" and "no network" in msg and "fetch_art" in msg


# ---------------------------------------------------------------- drawing


class Actor:
    x = y = 0.0
    face_x, face_y = 1.0, 0.0
    state, attack_elapsed, attack_duration = "attacking", 0.3, 0.55


def test_missing_sprite_draws_an_obvious_stand_in(screen, cfg, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import MISSING_COLORS, ActorSprites, SpriteLibrary

    lib = SpriteLibrary(cfg, tmp_path / "empty")
    spr = ActorSprites(lib)
    layers = lib.layers("player", [("mainhand", "short_sword"), ("offhand", "buckler"), ("body", "chain_mail")])
    assert layers["main"] == "flare/male/shortsword" and layers["off"] == "flare/male/buckler"
    assert layers["chest"] == "flare/male/chain_cuirass", "armor replaces the bare chest"
    dual = lib.layers("player", [("mainhand", "long_sword"), ("offhand", "short_sword")])
    assert dual["main"] == "flare/male/longsword" and "off" not in dual, "an off-hand sword never takes the main hand's place"
    assert lib.layers("wolf") is None, "no look at all: the client keeps the old placeholder"

    screen.fill((0, 0, 0))
    box = spr.draw_actor(screen, Camera(200, 200), ("p", 0), Actor(), layers)
    assert box.h > 30 and box.bottom == 100, "stands on its feet at the center"
    colors = {tuple(screen.get_at((x, y)))[:3] for x in range(box.left, box.right) for y in range(box.top, box.bottom)}
    assert MISSING_COLORS[0] in colors, "magenta: impossible to miss"
    spr.add_corpse(Actor(), lib.layers("skeleton"), 1.0)
    spr.draw_corpses(screen, Camera(200, 200), lambda x, y: True)  # a missing corpse lies down, no crash


def test_draws_real_frames_and_corpses(screen, cfg, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import MISSING_COLORS, ActorSprites, SpriteLibrary

    c = small_cfg(cfg)
    make_flare_tree(tmp_path / "flare", c)
    fetch_art(c, tmp_path / "art", local_fetch(tmp_path / "flare"))
    lib = SpriteLibrary(c, tmp_path / "art")
    spr = ActorSprites(lib)
    screen.fill((0, 0, 0))
    box = spr.draw_actor(screen, Camera(200, 200), ("m", 1), Actor(), lib.layers("skeleton"))
    assert box.size == (8, 8) and box.midbottom == (100, 100), "the frame's foot point sits on the actor"
    assert tuple(screen.get_at(box.center))[:3] != MISSING_COLORS[0]
    spr.add_corpse(Actor(), lib.layers("skeleton"), 1.0)
    spr.tick(0.5)
    spr.draw_corpses(screen, Camera(200, 200), lambda x, y: True)
    spr.tick(60)
    assert spr.corpses == [], "bodies go away"
