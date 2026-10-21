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
from aethelgard.tools.fetch_art import ArtError, fetch_art, load_config, missing_sheets, repo_path, sheets_in_use, source_for  # noqa: E402

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


def test_parse_flare_grid_format():
    """The older Flare format (community packs): one grid, a column per frame, a row per direction."""
    text = """image=images/enemies/wolf.png
render_size=128,96
render_offset=64,80

[stance]
position=0
frames=4
duration=400ms
type=back_forth

[run]
position=4
frames=8
duration=533
type=looped
"""
    image, anims = parse_flare_animation(text)
    assert image == "images/enemies/wolf.png"
    assert anims["stance"].rects[(0, 0)] == (0, 0, 128, 96, 64, 80)
    assert anims["run"].rects[(0, 0)] == (512, 0, 128, 96, 64, 80), "run starts at column 4"
    assert anims["run"].rects[(7, 7)] == (11 * 128, 7 * 96, 128, 96, 64, 80), "direction = row"
    assert anims["run"].seconds == pytest.approx(0.533) and len(anims["run"].rects) == 8 * DIRECTIONS


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
    npc_looks = {o["look"] for o in cfg["tiles"]["objects"].values() if "look" in o}
    assert set(cfg["looks"]) - {"player"} - npc_looks <= types, "a look is the player, a map NPC or a monster type id"
    assert set(cfg["gear"]) <= {b.id for b in data.items.bases}, "gear entries are item base ids"
    assert set(cfg["slotLayers"]) <= set(data.items.equipment), "slotLayers keys are equipment slots (items.json)"
    assert set(cfg["layerOrder"]) == set(DIRECTION_NAMES)
    assert all(look.get("attack", "swing") in cfg["import"]["anims"] for look in cfg["looks"].values())
    assert REQUIRED <= set(cfg["import"]["anims"])
    assert all(b.base_type in types for b in data.monsters.bosses), "bosses are drawn with their base type's look"
    for sheet in sheets_in_use(cfg):
        prefix, src, rest = source_for(sheet, cfg)
        if src["kind"] == "repo":
            assert repo_path(src, rest).endswith(".txt")
        else:
            assert rest in src["sheets"], f"{sheet}: the pack must say which files make it"
        assert src["license"] and src["credit"] and (src.get("page") or src.get("url"))


def test_alpha_wolf_and_wolf_have_art(cfg):
    assert cfg["looks"]["alpha_wolf"]["layers"]["body"] != cfg["looks"]["wolf"]["layers"]["body"], "the alpha is its own recolor"


def test_every_weapon_and_monster_has_a_look(cfg, data):
    assert {t.id for t in data.monsters.types} <= set(cfg["looks"])
    weapons = {b.id for b in data.items.bases if b.slot in ("mainhand", "offhand")}
    assert weapons <= set(cfg["gear"]), "every weapon and shield is visible in the hand"


def test_sizes_follow_the_hero_like_diablo_2(cfg):
    from aethelgard.client.sprites import SpriteLibrary

    lib = SpriteLibrary(cfg, "/nonexistent")
    hero = lib.target_height("player")
    assert 85 <= hero <= 105, "about 13% of a 720 px screen, as the hero in Diablo 2 at 800×600"
    assert lib.target_height("skeleton") == hero, "humanoids: the hero's height"
    assert 0.65 * hero <= lib.target_height("wolf") < lib.target_height("alpha_wolf") < hero, "beasts lower, but not tiny"


def test_the_art_is_not_in_the_repository():
    from importlib import resources

    assert not (resources.files("aethelgard") / "assets").is_dir(), "the art is downloaded when the game opens, never committed"


# ---------------------------------------------------------------- download (fake sources, no network)

FLARE = "https://flare.test/"
PACK = "https://packs.test/wolfs.7z"


def small_cfg(cfg):
    """The real config, reduced to the player, a skeleton and the two wolves, with fake URLs, full scale and color."""
    c = json.loads(json.dumps(cfg))
    c["looks"] = {k: cfg["looks"][k] for k in ("player", "skeleton", "wolf", "alpha_wolf", "boar")}
    c["import"]["sources"]["oga-boars/"]["urls"] = ["https://packs.test/boar_defs.7z", "https://packs.test/boar_2.png"]
    for key in ("tiles", "loot", "projectiles"):  # map, drops and shots have their own tests
        c.pop(key, None)
    c["gear"] = {"short_sword": cfg["gear"]["short_sword"]}
    c["import"].update(scale=1.0, colors=0, atlasWidth=128)
    c["import"]["sources"]["flare/"]["base"] = FLARE
    c["import"]["sources"]["oga-wolves/"]["url"] = PACK
    return c


def _sheet_png(path, color):
    img = pygame.Surface((64, 8), pygame.SRCALPHA)
    for d in range(DIRECTIONS):
        img.fill((*color[:2], 20 * d, 255), (d * 8, 0, 8, 8))
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(img, str(path))


def fake_web(tmp_path, c):
    """URL → bytes: a Flare-like repository (compressed format) and a 7z wolf pack (grid format, a recolor, no hit/block)."""
    files = {}
    src = c["import"]["sources"]["flare/"]
    for sheet in sheets_in_use(c):
        if not sheet.startswith("flare/"):
            continue
        name = sheet.rsplit("/", 1)[1]
        png = tmp_path / "flare" / f"{name}.png"
        _sheet_png(png, (200, 200))
        lines = [f"image=images/{name}.png"]
        for anim in c["import"]["anims"]:
            lines += ["", f"[{anim}]", "frames=1", "duration=100ms", "type=play_once"]
            lines += [f"frame=0,{d},{d * 8},0,8,8,4,8" for d in range(DIRECTIONS)]
        files[FLARE + repo_path(src, sheet[len("flare/") :])] = "\n".join(lines).encode()
        files[FLARE + src["imageRoot"] + f"images/{name}.png"] = png.read_bytes()
    files[FLARE + "LICENSE.txt"] = b"CC-BY-SA 3.0"

    import py7zr

    pack = tmp_path / "pack"
    grid = ["image=images/enemies/wolf.png", "render_size=8,8", "render_offset=4,8"]
    for i, anim in enumerate(("stance", "run", "swing", "die")):
        grid += ["", f"[{anim}]", f"position={i}", "frames=1", "duration=100ms", "type=play_once"]
    (pack / "wolfs" / "animations").mkdir(parents=True)
    (pack / "wolfs" / "animations" / "wolf.txt").write_text("\n".join(grid))
    for name, color in (("wolf.png", (120, 120)), ("wolf_alpha.png", (250, 30))):
        img = pygame.Surface((32, 64), pygame.SRCALPHA)
        img.fill((*color, 90, 255))
        (pack / "wolfs" / "images").mkdir(parents=True, exist_ok=True)
        pygame.image.save(img, str(pack / "wolfs" / "images" / name))
    (pack / "wolfs" / "LICENSE.txt").write_text("CC-BY-SA 3.0 - Danimal")
    with py7zr.SevenZipFile(tmp_path / "wolfs.7z", "w") as z:
        z.writeall(pack / "wolfs", "wolfs")
    files[PACK] = (tmp_path / "wolfs.7z").read_bytes()

    defs = tmp_path / "boardefs"  # boar pack: animation files in one .7z, the sheet as a loose PNG
    defs.mkdir()
    for name in ("boar.txt", "boar_rider.txt"):
        (defs / name).write_text("\n".join(grid).replace("images/enemies/wolf.png", "images/" + name.replace(".txt", ".png")))
    with py7zr.SevenZipFile(tmp_path / "defs.7z", "w") as z:
        z.writeall(defs, "defs")
    img = pygame.Surface((32, 64), pygame.SRCALPHA)
    img.fill((140, 90, 40, 255))
    pygame.image.save(img, str(tmp_path / "boar_2.png"))
    boar_urls = c["import"]["sources"]["oga-boars/"]["urls"]
    files[boar_urls[0]] = (tmp_path / "defs.7z").read_bytes()
    files[boar_urls[1]] = (tmp_path / "boar_2.png").read_bytes()

    calls = []

    def fetch(url):
        calls.append(url)
        if url not in files:
            raise OSError(f"404 {url}")
        return files[url]

    fetch.calls = calls
    return fetch


def test_download_builds_the_missing_sheets_and_resumes(screen, cfg, tmp_path):
    c = small_cfg(cfg)
    fetch = fake_web(tmp_path, c)
    art = tmp_path / "art"
    assert missing_sheets(c, art) == sheets_in_use(c)
    built = fetch_art(c, art, fetch)
    assert sorted(built) == sheets_in_use(c) and missing_sheets(c, art) == []
    assert (art / "sprites" / "flare" / "ATTRIBUTION.md").is_file()
    assert "Danimal" in (art / "sprites" / "oga-wolves" / "ATTRIBUTION.md").read_text()
    assert (art / "sprites" / "oga-wolves" / "LICENSE.txt").is_file(), "the pack's own license travels with the art"
    meta = json.loads((art / "sprites" / "flare" / "enemies" / "skeleton.json").read_text())
    assert REQUIRED <= set(meta["anims"]) and all(len(r) == DIRECTIONS for r in meta["anims"]["run"]["rects"])
    wolf = json.loads((art / "sprites" / "oga-wolves" / "wolf.json").read_text())
    assert set(wolf["anims"]) == {"stance", "run", "swing", "die"} and wolf["anims"]["run"]["rects"][0][7][2:4] == [8, 8]
    fetch.calls.clear()
    assert fetch_art(c, art, fetch) == [] and fetch.calls == [], "nothing missing: nothing downloaded"
    (art / "sprites" / "oga-wolves" / "alpha_wolf.json").unlink()
    assert fetch_art(c, art, fetch) == ["oga-wolves/alpha_wolf"], "only what is missing"
    assert fetch.calls == [PACK]


def test_recolor_uses_its_own_image(screen, cfg, tmp_path):
    from aethelgard.client.sprites import SpriteLibrary

    c = small_cfg(cfg)
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    lib = SpriteLibrary(c, tmp_path / "art")
    grey = lib.sheet("oga-wolves/wolf").image.get_at((1, 1))
    red = lib.sheet("oga-wolves/alpha_wolf").image.get_at((1, 1))
    assert grey[:2] == (120, 120) and red[:2] == (250, 30)


def test_one_source_failing_does_not_stop_the_others(screen, cfg, tmp_path):
    c = small_cfg(cfg)
    fetch = fake_web(tmp_path, c)

    def no_pack(url):
        if url == PACK:
            raise OSError("403 Forbidden")
        return fetch(url)

    with pytest.raises(ArtError) as err:
        fetch_art(c, tmp_path / "art", no_pack)
    assert any("oga-wolves" in p and "403" in p for p in err.value.problems)
    assert missing_sheets(c, tmp_path / "art") == ["oga-wolves/alpha_wolf", "oga-wolves/wolf"], "Flare sheets were built"
    assert sorted(fetch_art(c, tmp_path / "art", fetch)) == ["oga-wolves/alpha_wolf", "oga-wolves/wolf"], "a retry fetches the rest"


def test_pack_without_the_expected_file_names_it(screen, cfg, tmp_path):
    c = small_cfg(cfg)
    c["import"]["sources"]["oga-wolves/"]["sheets"]["alpha_wolf"]["image"] = ["*nope*"]
    with pytest.raises(ArtError) as err:
        fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    msg = " ".join(err.value.problems)
    assert "alpha_wolf" in msg and "wolf_alpha.png" in msg, "the error lists what the pack has"


def test_failed_download_leaves_no_half_sheet(screen, cfg, tmp_path):
    c = small_cfg(cfg)

    def offline(url):
        raise OSError("no network")

    with pytest.raises(ArtError):
        fetch_art(c, tmp_path / "art", offline)
    assert missing_sheets(c, tmp_path / "art") == sheets_in_use(c)
    assert not list((tmp_path / "art").rglob("*.json"))


def test_background_download_then_real_sprites(screen, cfg, tmp_path):
    from aethelgard.client.sprites import ArtDownload, SpriteLibrary

    c = small_cfg(cfg)
    lib = SpriteLibrary(c, tmp_path / "art")
    assert lib.sheet("flare/enemies/skeleton") is None, "not downloaded yet"
    dl = ArtDownload(lib, fetch=fake_web(tmp_path, c))
    assert dl.start()
    dl.wait(60)
    assert dl.fraction == 1.0
    assert dl.poll() == "Art downloaded." and dl.poll() is None, "reported once"
    assert lib.sheet("flare/enemies/skeleton") is not None and lib.sheet("oga-wolves/alpha_wolf") is not None
    assert not ArtDownload(lib).start(), "nothing missing: no second download"


def test_failed_background_download_keeps_the_game_going(screen, cfg, tmp_path):
    from aethelgard.client.sprites import ArtDownload, SpriteLibrary

    def offline(url):
        raise OSError("no network")

    dl = ArtDownload(SpriteLibrary(small_cfg(cfg), tmp_path / "art"), fetch=offline)
    assert dl.start()
    dl.wait(30)
    msg = dl.poll()
    assert dl.state == "failed" and "no network" in msg and "fetch_art" in msg and dl.problems


# ---------------------------------------------------------------- drawing


class Actor:
    x = y = 0.0
    face_x, face_y = 1.0, 0.0
    state, attack_elapsed, attack_duration = "attacking", 0.3, 0.55


def _has_magenta(screen, box):
    from aethelgard.client.sprites import MISSING_COLORS

    return any(tuple(screen.get_at((x, y)))[:3] == MISSING_COLORS[0] for x in range(box.left, box.right) for y in range(box.top, box.bottom))


def test_missing_sprite_draws_an_obvious_stand_in(screen, cfg, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import ActorSprites, SpriteLibrary

    lib = SpriteLibrary(cfg, tmp_path / "empty")
    spr = ActorSprites(lib)
    layers = lib.layers("player", [("mainhand", "short_sword"), ("offhand", "buckler"), ("body", "chain_mail")])
    assert layers["main"] == "flare/male/shortsword" and layers["off"] == "flare/male/buckler"
    assert layers["chest"] == "flare/male/chain_cuirass", "armor replaces the bare chest"
    dual = lib.layers("player", [("mainhand", "long_sword"), ("offhand", "short_sword")])
    assert dual["main"] == "flare/male/longsword", "an off-hand sword never takes the main hand's place"
    assert dual["off"] == "mirror:flare/male/shortsword", "it is drawn in the left hand: right-hand art, mirrored"
    swapped = lib.swap_hands(dual)
    assert swapped["main"] == "flare/male/shortsword" and swapped["off"] == "mirror:flare/male/longsword"
    assert lib.swap_hands(layers) == layers, "a shield never changes hands"

    screen.fill((0, 0, 0))
    box = spr.draw_actor(screen, Camera(200, 200), ("p", 0), Actor(), layers)
    assert box.h > 30 and box.bottom == 100, "stands on its feet at the center"
    assert _has_magenta(screen, box), "magenta: impossible to miss"
    spr.add_corpse(Actor(), lib.layers("skeleton"), 1.0)
    spr.draw_corpses(screen, Camera(200, 200), lambda x, y: True)  # a missing corpse lies down, no crash


def test_monster_type_without_art_is_a_stand_in_too(screen, cfg, data, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import ActorSprites, SpriteLibrary

    lib = SpriteLibrary(cfg, tmp_path / "empty")
    assert lib.without_look(t.id for t in data.monsters.types) == [], "every monster type has art defined"
    assert lib.layers("dragon") is None and lib.without_look(["dragon"]) == ["dragon"]
    screen.fill((0, 0, 0))
    box = ActorSprites(lib).draw_missing(screen, Camera(200, 200), ("m", 9), Actor())
    assert box.h > 30 and _has_magenta(screen, box)


def test_draws_real_frames_fallback_animations_and_corpses(screen, cfg, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import ActorSprites, SpriteLibrary

    c = small_cfg(cfg)
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    lib = SpriteLibrary(c, tmp_path / "art")
    spr = ActorSprites(lib)
    screen.fill((0, 0, 0))
    box = spr.draw_actor(screen, Camera(200, 200), ("m", 1), Actor(), lib.layers("skeleton"))
    assert box.size == (8, 8) and box.midbottom == (100, 100), "the frame's foot point sits on the actor"
    assert not _has_magenta(screen, box)
    wolf = lib.sheet("oga-wolves/wolf")
    assert lib.anim_for(wolf, "hit") == "stance" and lib.anim_for(wolf, "die") == "die", "the pack has no hit: falls back"
    hurt = Actor()
    hurt.state = "hit_recovery"
    assert spr.draw_actor(screen, Camera(200, 200), ("m", 2), hurt, lib.layers("wolf")).size == (8, 8)
    spr.add_corpse(Actor(), lib.layers("skeleton"), 1.0)
    spr.tick(0.5)
    spr.draw_corpses(screen, Camera(200, 200), lambda x, y: True)
    spr.tick(60)
    assert spr.corpses == [], "bodies go away"


def test_mirrored_sheet_flips_the_mirrored_facing(screen, cfg, tmp_path):
    from aethelgard.client.sprites import MIRROR, SpriteLibrary

    assert all(MIRROR[MIRROR[d]] == d for d in range(DIRECTIONS)), "mirroring twice is the identity"
    assert [DIRECTION_NAMES[MIRROR[d]] for d in range(DIRECTIONS)] == ["NE", "N", "NW", "W", "SW", "S", "SE", "E"]
    c = small_cfg(cfg)
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    lib = SpriteLibrary(c, tmp_path / "art")
    base, mirrored = lib.sheet("flare/male/shortsword"), lib.sheet("mirror:flare/male/shortsword")
    for d in range(DIRECTIONS):
        surf, ox, oy = mirrored.frame("swing", 0, d)
        src, sx, sy = base.frame("swing", 0, MIRROR[d])
        assert surf.get_size() == src.get_size() and (ox, oy) == (src.get_width() - sx, sy)
        assert surf.get_at((0, 0)) == src.get_at((src.get_width() - 1, 0)), "flipped left ↔ right"
    assert lib.sheet("mirror:flare/male/nothing") is None


def test_dual_wield_swings_draw_both_weapons(screen, cfg, tmp_path):
    from aethelgard.client.iso import Camera
    from aethelgard.client.sprites import ActorSprites, SpriteLibrary

    c = small_cfg(cfg)
    c["gear"]["long_sword"] = cfg["gear"]["long_sword"]
    c["gear"]["long_sword"]["main"] = cfg["gear"]["short_sword"]["main"]  # the fake web only has the short sword
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    lib = SpriteLibrary(c, tmp_path / "art")
    spr = ActorSprites(lib)
    layers = lib.layers("player", [("mainhand", "short_sword"), ("offhand", "short_sword")])
    for mirrored in (False, True):
        screen.fill((0, 0, 0))
        drawn = layers if not mirrored else lib.swap_hands(layers)
        box = spr.draw_actor(screen, Camera(200, 200), ("p", 0), Actor(), drawn, mirrored=mirrored)
        assert box.w > 0 and not _has_magenta(screen, box)


def test_pack_in_several_files_picks_the_right_animation(screen, cfg, tmp_path):
    from aethelgard.client.sprites import SpriteLibrary

    c = small_cfg(cfg)
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    boar = SpriteLibrary(c, tmp_path / "art").sheet("oga-boars/boar")
    assert boar is not None and boar.image.get_at((1, 1))[:3] == (140, 90, 40), "the loose PNG, not a rider"


def test_old_sheets_are_rebuilt_and_frames_are_trimmed(screen, cfg, tmp_path):
    c = small_cfg(cfg)
    art = tmp_path / "art"
    fetch_art(c, art, fake_web(tmp_path, c))
    meta_path = art / "sprites" / "flare" / "enemies" / "skeleton.json"
    meta = json.loads(meta_path.read_text())
    assert meta["version"] == c["import"]["version"]
    meta["version"] = 1
    meta_path.write_text(json.dumps(meta))
    assert missing_sheets(c, art) == ["flare/enemies/skeleton"], "built by an older import: rebuilt on the next start"


def test_trimmed_frame_keeps_the_feet_in_place(screen, cfg, tmp_path):
    from aethelgard.tools.fetch_art import Job, convert_sheet

    img = pygame.Surface((80, 40), pygame.SRCALPHA)
    for d in range(DIRECTIONS):  # grid cell 10×40: a 4×10 figure standing at the bottom, 3 px from the left
        img.fill((255, 255, 255, 255), (d * 10 + 3, 30, 4, 10))
    img_path, txt = tmp_path / "t.png", tmp_path / "t.txt"
    pygame.image.save(img, str(img_path))
    lines = ["image=t.png", "render_size=10,40", "render_offset=5,40", "", "[stance]", "position=0", "frames=1", "duration=100ms"]
    txt.write_text("\n".join(lines))
    c = small_cfg(cfg)
    convert_sheet(Job("x/t", txt, img_path, 1.0), c, tmp_path / "out")
    rect = json.loads((tmp_path / "out" / "x" / "t.json").read_text())["anims"]["stance"]["rects"][0][0]
    assert rect[2:] == [4, 10, 2, 10], "4×10 after trimming; the feet point moved with it (5-3, 40-30)"


def test_every_look_is_drawn_at_its_target_height(screen, cfg, tmp_path):
    from aethelgard.client.sprites import SpriteLibrary

    c = small_cfg(cfg)
    fetch_art(c, tmp_path / "art", fake_web(tmp_path, c))
    lib = SpriteLibrary(c, tmp_path / "art")
    for look in ("skeleton", "wolf", "boar"):
        h = lib.stand_height(lib.layers(look).values())
        assert h * lib.look_scale(look) == pytest.approx(lib.target_height(look))
    assert SpriteLibrary(c, tmp_path / "none").look_scale("wolf") == pytest.approx(lib.missing_scale("wolf")), "stand-in, same height"


# ---------------------------------------------------------------- map art, drops, shots


def test_parse_flare_tileset_with_several_images():
    from aethelgard.client.animation import is_flare_tileset, parse_flare_tileset

    text = "[tileset]\nimg=a.png\ntile=16,0,0,192,96,96,48\n\n[tileset]\nimg=b.png\ntile=200,10,20,96,96,48,80\n"
    images, tiles = parse_flare_tileset(text)
    assert is_flare_tileset(text) and images == ["a.png", "b.png"]
    assert tiles[16] == (0, 0, 0, 192, 96, 96, 48) and tiles[200] == (1, 10, 20, 96, 96, 48, 80)


def test_every_map_object_item_and_shot_has_art_defined(cfg, data):
    assert {b.id for b in data.items.bases} <= set(cfg["loot"]["items"]), "every item has a picture on the ground"
    looks = {t.projectile.look for t in data.monsters.types if t.projectile}
    assert looks <= set(cfg["projectiles"]["looks"]), "every monster shot has art"
    tiles = cfg["tiles"]
    assert {a.kind for a in data.all_areas} <= set(tiles["kindThemes"])
    assert set(tiles["areaThemes"]) <= {a.id for a in data.all_areas}
    assert {"waypoint", "stairsDown", "stairsUp", "entrance", "exit", "stash", "vendor"} <= set(tiles["objects"])
    assert all(o["look"] in cfg["looks"] for o in tiles["objects"].values() if "look" in o)


def _tileset_cfg(cfg):
    """sprites.json with a fake tileset: floors 16–17, walls 64 (east face), 65 (south face), 66 (back), props 240."""
    c = json.loads(json.dumps(cfg))
    th = {"sheet": "flare/tiles/test", "floor": {"16": 1, "17": 1}, "wallE": {"64": 1}, "wallS": {"65": 1}, "wallBack": {"66": 1}}
    c["tiles"]["themes"] = {"crypt": th, "open": {"sheet": "flare/tiles/test", "floor": {"16": 1}, "under": True,
                                                   "props": {"240": 1}, "propChance": [1.0, 0.0]}}
    c["tiles"]["objects"] = {"waypoint": {"sheet": "flare/tiles/test", "tiles": [16, 17], "flat": True},
                             "stairsDown": {"sheet": "flare/tiles/test", "tile": 17, "flat": True},
                             "stairsUp": {"sheet": "flare/tiles/test", "tile": 64}, "vendor": {"look": "player"}}
    return c


def _write_tileset(art):
    folder = art / "sprites" / "flare" / "tiles"
    folder.mkdir(parents=True)
    img = pygame.Surface((64 * 6, 120), pygame.SRCALPHA)
    img.fill((90, 90, 90, 255))
    pygame.image.save(img, str(folder / "test.png"))
    tiles = {str(i): [64 * k, 0, 64, 32 if i < 60 else 109, 32, 16 if i < 60 else 96] for k, i in enumerate((16, 17, 64, 65, 66, 240))}
    (folder / "test.json").write_text(json.dumps({"version": 2, "image": "test.png", "tiles": tiles}))


def test_walls_face_the_room_and_open_ground_gets_trees(screen, cfg, tmp_path):
    from aethelgard.client.sprites import SpriteLibrary
    from aethelgard.client.tilemap import MapArt
    from aethelgard.core.world_gen import FLOOR, WALL, AreaLayout, Exit, ExitKind

    c = _tileset_cfg(cfg)
    _write_tileset(tmp_path / "art")
    lib = SpriteLibrary(c, tmp_path / "art")
    L = AreaLayout.filled(10, 10, WALL)
    L.tiles[3:7, 3:7] = FLOOR  # a 4×4 room
    L.exits.append(Exit((5, 5), ExitKind.STAIRS_DOWN, None))
    L.markers["Ilsa"] = ((4, 4), {"role": "vendor"})
    art = MapArt.build(lib, L, "crypt", "dungeon")
    props = {(p.x, p.y): p.tile for cy in range(2) for cx in range(2) for p in art.props_in_chunk(cx, cy, 8)}
    assert props[(2, 4)] == 64, "west of the room: its east face looks at the floor"
    assert props[(4, 2)] == 65, "north of the room: its south face looks at the floor"
    assert props[(7, 4)] == 66 and props[(4, 7)] == 66, "between the camera and the room: back walls"
    assert (0, 0) not in props, "solid rock far from the room: nothing"
    assert art.floor_tile(4, 4) is not None and art.floor_tile(0, 0) is None
    assert [o.kind for o in art.objects] == ["stairsDown"] and art.objects[0].flat
    assert [n.role for n in art.npcs] == ["vendor"] and art.has_art((4, 4)) and art.has_art((5, 5))
    assert MapArt.build(lib, L, "crypt", "dungeon").props_in_chunk(0, 0, 8) == art.props_in_chunk(0, 0, 8), "same map, same look"

    open_art = MapArt.build(lib, L, "plains", "open")
    assert not open_art.props_in_chunk(0, 0, 10), "no trees within exitClearance of an exit: its sign stays in sight"
    L.exits.clear()
    open_art = MapArt.build(lib, L, "plains", "open")
    near = [p for p in open_art.props_in_chunk(0, 0, 10) if max(abs(p.x - 4.5), abs(p.y - 4.5)) <= 3]
    assert near and all(p.tile == 240 for p in near), "the edge of a clearing is lined with trees"
    assert open_art.floor_tile(0, 0) is not None, "grass under the trees"
    assert MapArt.build(SpriteLibrary(c, tmp_path / "none"), L, "crypt", "dungeon") is None, "no art: placeholder shapes"


def test_drops_and_shots_pick_their_art(screen, cfg):
    from aethelgard.client.sprites import EffectArt, SpriteLibrary
    from aethelgard.core.items import GoldDrop, PotionDrop

    fx = EffectArt(SpriteLibrary(cfg, "/nonexistent"))
    assert fx.loot_sheet(GoldDrop(3)) == "flare/loot/coins5"
    assert fx.loot_sheet(GoldDrop(40)) == "flare/loot/coins25" and fx.loot_sheet(GoldDrop(500)) == "flare/loot/coins100"
    assert fx.loot_sheet(PotionDrop("life")) == "flare/loot/hp_potion"
    assert fx.draw_drop(screen, (10, 10), 1, GoldDrop(3)) is None, "not downloaded: the placeholder shape"
    assert not fx.draw_shot(screen, (10, 10), "arrow", 1, 0)


def test_tileset_is_converted_to_our_cell_size(screen, cfg, tmp_path):
    from aethelgard.tools.fetch_art import Job, convert_sheet

    for name, color in (("a.png", (200, 0, 0, 255)), ("b.png", (0, 0, 200, 255))):
        img = pygame.Surface((192, 192), pygame.SRCALPHA)
        img.fill(color)
        pygame.image.save(img, str(tmp_path / name))
    txt = tmp_path / "tileset_test.txt"
    txt.write_text("[tileset]\nimg=a.png\ntile=16,0,0,192,96,96,48\n\n[tileset]\nimg=b.png\ntile=64,0,0,192,192,96,144\n")
    convert_sheet(Job("flare/tiles/test", txt, tmp_path / "a.png", 64 / 192, [tmp_path / "a.png", tmp_path / "b.png"]), cfg, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "flare" / "tiles" / "test.json").read_text())
    assert meta["tiles"]["16"][2:] == [64, 32, 32, 16], "a floor lands exactly on a 64×32 cell"
    assert meta["tiles"]["64"][2:] == [64, 64, 32, 48], "a wall keeps its foot on the cell center"
    atlas = pygame.image.load(str(tmp_path / "out" / "flare" / "tiles" / "test.png"))
    x, y = meta["tiles"]["64"][:2]
    r, _, b, _ = atlas.get_at((x + 10, y + 10))
    assert r < 20 and b > 180, "the second section reads the second image (blue)"
