"""Downloads the Flare art the game uses and converts it to the game's sprite format.

    python -m aethelgard.tools.fetch_art              # only the sheets that are missing
    python -m aethelgard.tools.fetch_art --all        # rebuild every sheet
    python -m aethelgard.tools.fetch_art --from /path/to/flare-game   # from a local checkout, no network

The art is **not** in the repository. The game calls the same code on start when a sheet is missing.
Only the files that ``data/ui/sprites.json`` needs are fetched (about 60 MB), straight from the Flare
repository (https://github.com/flareteam/flare-game, CC-BY-SA 3.0) at the pinned commit, so everyone
gets the same pictures. Each frame is scaled on its own (scaling the whole atlas would bleed
neighbours into each other), packed into one atlas per sheet and written to
``<art dir>/sprites/<sheet>.png`` + ``.json``. The ``.json`` is written last: its presence means the sheet is ready.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from ..client.animation import DIRECTIONS, pack_shelves, parse_flare_animation
from ..core.saves import default_art_dir

Fetch = Callable[[str], bytes]  # path inside flare-game → file contents
Progress = Callable[[str, int, int], None]  # stage, done, total
LICENSE_FILES = ("LICENSE.txt", "CREDITS.txt")


def load_config() -> dict:
    path = resources.files("aethelgard") / "data" / "ui" / "sprites.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sprites_dir(art_dir: Path) -> Path:
    return Path(art_dir) / "sprites"


def sheets_in_use(cfg: dict) -> list[str]:
    """Every sheet named by a look or by the gear table, sorted."""
    found = {s for look in cfg["looks"].values() for s in look["layers"].values()}
    found |= {s for layers in cfg["gear"].values() for s in layers.values()}
    return sorted(found)


def sheet_ready(art_dir: Path, sheet: str) -> bool:
    base = sprites_dir(art_dir) / sheet
    return base.with_suffix(".json").is_file() and base.with_suffix(".png").is_file()


def missing_sheets(cfg: dict, art_dir: Path) -> list[str]:
    return [s for s in sheets_in_use(cfg) if not sheet_ready(art_dir, s)]


def source_of(sheet: str, cfg: dict) -> str:
    """Path of the sheet's animation file inside flare-game."""
    for prefix, folder in cfg["import"]["sources"].items():
        if sheet.startswith(prefix):
            return folder + sheet[len(prefix) :] + ".txt"
    raise KeyError(f"no import source for sheet '{sheet}' (see import.sources in sprites.json)")


# ---------------------------------------------------------------- where the files come from


def github_fetch(cfg: dict, timeout: float = 60) -> Fetch:
    imp = cfg["import"]
    base = f"{imp['repository'].rstrip('/')}/{imp['commit']}/"

    def get(rel: str) -> bytes:
        req = urllib.request.Request(base + quote(rel), headers={"User-Agent": "aethelgard-fetch-art"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    return get


def local_fetch(root: Path) -> Fetch:
    return lambda rel: (Path(root) / rel).read_bytes()


# ---------------------------------------------------------------- download + convert


def fetch_art(cfg: dict, art_dir: Path, fetch: Fetch, *, rebuild: bool = False, progress: Progress | None = None, workers: int = 6) -> list[str]:
    """Fetches and converts the sheets that are missing (all of them with ``rebuild``). Returns the sheets built.

    Raw Flare files go to a temporary folder that is deleted at the end. A failed download raises
    and leaves the sheets already finished in place, so running again resumes."""
    sheets = sheets_in_use(cfg) if rebuild else missing_sheets(cfg, art_dir)
    if not sheets:
        return []
    report = progress or (lambda stage, done, total: None)
    root = cfg["import"]["sourceRoot"]
    with tempfile.TemporaryDirectory(prefix="aethelgard-art-") as tmp:
        raw = Path(tmp)

        def get(rel: str) -> str:
            dest = raw / rel
            if not dest.exists():
                data = fetch(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            return rel

        texts = [source_of(s, cfg) for s in sheets]
        images: set[str] = set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, rel in enumerate(pool.map(get, texts), 1):  # small text files first: they name the images
                report("download", i, 2 * len(texts))
                image, _ = parse_flare_animation((raw / rel).read_text(encoding="utf-8"))
                images.add(root + image)
            total = len(texts) + len(images)
            for i, _ in enumerate(pool.map(get, sorted(images)), 1):
                report("download", len(texts) + i, total)
        licenses = {}
        for name in LICENSE_FILES:
            try:
                licenses[name] = fetch(name)
            except Exception:  # the attribution text below still names the source and the license
                licenses[name] = b""
        for i, sheet in enumerate(sheets, 1):
            import_sheet(sheet, cfg, raw, sprites_dir(art_dir))
            report("convert", i, len(sheets))
        write_attribution(cfg, art_dir, licenses)
    return sheets


def import_sheet(sheet: str, cfg: dict, flare: Path, out: Path) -> tuple[int, int]:
    """Converts one Flare sheet found under ``flare`` (same layout as the repository)."""
    import pygame

    imp = cfg["import"]
    scale, keep = imp["scale"], set(imp["anims"])
    image_rel, anims = parse_flare_animation((flare / source_of(sheet, cfg)).read_text(encoding="utf-8"))
    src = pygame.image.load(str(flare / imp["sourceRoot"] / image_rel))

    frames: dict[tuple, pygame.Surface] = {}  # source rect → scaled frame (Flare repeats frames across animations)
    offsets: dict[tuple, tuple[int, int]] = {}
    for anim in (a for name, a in anims.items() if name in keep):
        for x, y, w, h, ox, oy in anim.rects.values():
            key = (x, y, w, h, ox, oy)
            if w <= 0 or h <= 0 or key in frames:
                continue
            sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
            frames[key] = pygame.transform.smoothscale(src.subsurface((x, y, w, h)), (sw, sh))
            offsets[key] = (round(ox * scale), round(oy * scale))

    keys = list(frames)
    width = imp["atlasWidth"]
    pos, height = pack_shelves([frames[k].get_size() for k in keys], width)
    atlas = pygame.Surface((width, max(1, height)), pygame.SRCALPHA)
    placed = {}
    for k, (px, py) in zip(keys, pos, strict=True):
        atlas.blit(frames[k], (px, py))
        placed[k] = [px, py, *frames[k].get_size(), *offsets[k]]

    out_anims = {}
    for name, a in anims.items():
        if name not in keep:
            continue
        empty = [0, 0, 0, 0, 0, 0]
        rects = [[placed.get(a.rects.get((f, d)), empty) for d in range(DIRECTIONS)] for f in range(a.frames)]
        out_anims[name] = {"frames": a.frames, "seconds": round(a.seconds, 3), "loop": a.loop, "rects": rects}

    target = out / f"{sheet}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_png(atlas, target.with_suffix(".png"), imp.get("colors", 256))
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"image": target.with_suffix(".png").name, "anims": out_anims}, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)  # the .json appears last: the sheet is complete
    return len(keys), height


def save_png(surface, path: Path, colors: int) -> None:
    """Palette PNG with alpha when Pillow is installed (about 5× smaller); full color otherwise."""
    import pygame

    try:
        from PIL import Image
    except ImportError:
        pygame.image.save(surface, str(path))
        return
    img = Image.frombytes("RGBA", surface.get_size(), pygame.image.tobytes(surface, "RGBA"))
    if colors:
        img = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    img.save(path, optimize=True)


def write_attribution(cfg: dict, art_dir: Path, licenses: dict[str, bytes]) -> None:
    imp = cfg["import"]
    folder = sprites_dir(art_dir) / "flare"
    folder.mkdir(parents=True, exist_ok=True)
    if licenses.get("LICENSE.txt"):
        (folder / "LICENSE-CC-BY-SA-3.0.txt").write_bytes(licenses["LICENSE.txt"])
    if licenses.get("CREDITS.txt"):
        (folder / "CREDITS-flare-game.txt").write_bytes(licenses["CREDITS.txt"])
    listing = "\n".join(f"- `{s}`" for s in sheets_in_use(cfg))
    (folder / "ATTRIBUTION.md").write_text(
        "# Flare art\n\n"
        "Character and monster sprites in this folder are derived from **Flare** "
        f"(https://github.com/flareteam/flare-game, commit `{imp['commit']}`), by Clint Bellanger, "
        "Justin Jacobs and the Flare contributors listed in `CREDITS-flare-game.txt`.\n\n"
        "License: **Creative Commons Attribution-ShareAlike 3.0** (`LICENSE-CC-BY-SA-3.0.txt`). "
        "Changes: each frame scaled to 1/3 (192×96 tiles → 64×32) and repacked into one atlas per sheet "
        "by `python -m aethelgard.tools.fetch_art`. These derived sprites are under the same license.\n\n"
        f"Sheets:\n\n{listing}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------- command line


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="source", type=Path, help="a local flare-game checkout instead of downloading")
    ap.add_argument("--art-dir", type=Path, default=None, help="where the art goes (default: the per-user data folder)")
    ap.add_argument("--all", action="store_true", help="rebuild every sheet, not only the missing ones")
    args = ap.parse_args()
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    cfg = load_config()
    art_dir = args.art_dir or default_art_dir()
    fetch = local_fetch(args.source) if args.source else github_fetch(cfg)
    last = [""]

    def show(stage: str, done: int, total: int) -> None:
        line = f"{'Downloading' if stage == 'download' else 'Converting'} {done}/{total}"
        if line != last[0]:
            print(line, flush=True)
            last[0] = line

    built = fetch_art(cfg, art_dir, fetch, rebuild=args.all, progress=show)
    print(f"{len(built)} sheet(s) ready in {sprites_dir(art_dir)}" if built else f"All art already in {sprites_dir(art_dir)}")
    pygame.quit()


if __name__ == "__main__":
    main()
