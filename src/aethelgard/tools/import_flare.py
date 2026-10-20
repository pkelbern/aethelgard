"""Converts the Flare art (https://github.com/flareteam/flare-game, CC-BY-SA 3.0) to the game's sprite format.

    git clone --depth 1 https://github.com/flareteam/flare-game /tmp/flare-game
    python -m aethelgard.tools.import_flare /tmp/flare-game

Imports every sheet that ``data/ui/sprites.json`` uses (looks and gear), scales each frame on its
own (scaling the whole atlas would bleed neighbours into each other), packs the frames into one
atlas per sheet and writes ``assets/sprites/<sheet>.png`` + ``.json``. Run it again after adding a
look or a gear entry; sheets already imported are rebuilt, so the output never drifts from the data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..client.animation import DIRECTIONS, pack_shelves, parse_flare_animation

PACKAGE = Path(__file__).resolve().parents[1]
CONFIG = PACKAGE / "data" / "ui" / "sprites.json"
OUT = PACKAGE / "assets" / "sprites"


def sheets_in_use(cfg: dict) -> list[str]:
    """Every sheet named by a look or by the gear table, sorted (the importer's to-do list)."""
    found = {s for look in cfg["looks"].values() for s in look["layers"].values()}
    found |= {s for layers in cfg["gear"].values() for s in layers.values()}
    return sorted(found)


def source_of(sheet: str, cfg: dict, flare: Path) -> Path:
    for prefix, folder in cfg["import"]["sources"].items():
        if sheet.startswith(prefix):
            return flare / folder / (sheet[len(prefix) :] + ".txt")
    raise KeyError(f"no import source for sheet '{sheet}' (see import.sources in sprites.json)")


def import_sheet(sheet: str, cfg: dict, flare: Path, out: Path) -> tuple[int, int]:
    import pygame

    imp = cfg["import"]
    scale, keep = imp["scale"], set(imp["anims"])
    image_rel, anims = parse_flare_animation(source_of(sheet, cfg, flare).read_text(encoding="utf-8"))
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
        rects = []
        for f in range(a.frames):
            row = []
            for d in range(DIRECTIONS):
                r = a.rects.get((f, d))
                row.append(placed.get(r, [0, 0, 0, 0, 0, 0]) if r else [0, 0, 0, 0, 0, 0])
            rects.append(row)
        out_anims[name] = {"frames": a.frames, "seconds": round(a.seconds, 3), "loop": a.loop, "rects": rects}

    target = out / f"{sheet}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_png(atlas, target.with_suffix(".png"), imp.get("colors", 256))
    target.write_text(json.dumps({"image": target.with_suffix(".png").name, "anims": out_anims}, separators=(",", ":")), encoding="utf-8")
    return len(keys), height


def save_png(surface, path: Path, colors: int) -> None:
    """Palette PNG with alpha (Pillow): about 5× smaller than full color and hard to tell apart at game size."""
    import pygame
    from PIL import Image

    img = Image.frombytes("RGBA", surface.get_size(), pygame.image.tobytes(surface, "RGBA"))
    if colors:
        img = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    img.save(path, optimize=True)


def write_attribution(flare: Path, out: Path, sheets: list[str]) -> None:
    try:
        commit = subprocess.run(["git", "-C", str(flare), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    folder = out / "flare"
    folder.mkdir(parents=True, exist_ok=True)
    license_src = flare / "LICENSE.txt"
    if license_src.exists():
        shutil.copyfile(license_src, folder / "LICENSE-CC-BY-SA-3.0.txt")
    credits = flare / "CREDITS.txt"
    if credits.exists():
        shutil.copyfile(credits, folder / "CREDITS-flare-game.txt")
    listing = "\n".join(f"- `{s}`" for s in sheets)
    (folder / "ATTRIBUTION.md").write_text(
        "# Flare art\n\n"
        "Character and monster sprites in this folder are derived from **Flare** "
        "(https://github.com/flareteam/flare-game, commit "
        f"`{commit}`), by Clint Bellanger, Justin Jacobs and the Flare contributors listed in "
        "`CREDITS-flare-game.txt`.\n\n"
        "License: **Creative Commons Attribution-ShareAlike 3.0** (`LICENSE-CC-BY-SA-3.0.txt`). "
        "Changes: each frame scaled to 1/3 (192×96 tiles → 64×32) and repacked into one atlas per sheet "
        "by `python -m aethelgard.tools.import_flare`. These derived sprites are under the same license.\n\n"
        f"Sheets:\n\n{listing}\n",
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flare", type=Path, help="a checkout of flare-game")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((1, 1))  # convert/smoothscale need a display
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    sheets = sheets_in_use(cfg)
    for s in sheets:
        n, h = import_sheet(s, cfg, args.flare, args.out)
        print(f"{s:34} {n:>4} frames  atlas {cfg['import']['atlasWidth']}×{h}")
    write_attribution(args.flare, args.out, sheets)
    pygame.quit()


if __name__ == "__main__":
    main()
