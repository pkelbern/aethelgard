"""Downloads the third-party art the game uses and converts it to the game's sprite format.

    python -m aethelgard.tools.fetch_art              # only the sheets that are missing
    python -m aethelgard.tools.fetch_art --all        # rebuild every sheet
    python -m aethelgard.tools.fetch_art --flare /path/to/flare-game   # Flare files from a local checkout

The art is **not** in the repository; the game runs the same code when it opens. Sources are listed in
``data/ui/sprites.json`` → ``import.sources``, one per sheet-id prefix:

- ``repo``: files fetched one by one from a repository (Flare: only the ``.txt``/``.png`` it needs, pinned commit);
- ``archive``: a ``.7z``/``.zip`` pack (OpenGameArt) downloaded once and opened; ``sheets`` says which animation
  file and which image make each sheet (globs on file names, so the pack's folders do not matter).

Each frame is scaled on its own (scaling the whole atlas would bleed neighbours), packed into one atlas per
sheet and written to ``<art dir>/sprites/<sheet>.png`` + ``.json``; the ``.json`` is written last, so its presence
means the sheet is ready. A source that fails (no network, site down, a file not in the pack) does not stop the
others: the error lists what went wrong and running again retries only what is still missing.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ..client.animation import DIRECTIONS, is_flare_tileset, pack_shelves, parse_flare_animation, parse_flare_tileset
from ..core.saves import default_art_dir

Fetch = Callable[[str], bytes]  # URL → file contents
Progress = Callable[[str, int, int], None]  # stage ("download" | "convert"), done, total


class ArtError(RuntimeError):
    """Some sheets could not be built. ``built`` = the ones that were; ``problems`` = one line per failure."""

    def __init__(self, problems: list[str], built: list[str]):
        super().__init__("; ".join(problems))
        self.problems, self.built = problems, built


def load_config() -> dict:
    path = resources.files("aethelgard") / "data" / "ui" / "sprites.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sprites_dir(art_dir: Path) -> Path:
    return Path(art_dir) / "sprites"


def sheets_in_use(cfg: dict) -> list[str]:
    """Every sheet named by a look, the gear table, a map theme or a map object, sorted."""
    found = {s for look in cfg["looks"].values() for s in look["layers"].values()}
    found |= {s for layers in cfg["gear"].values() for s in layers.values()}
    found |= {theme["sheet"] for theme in cfg.get("tiles", {}).get("themes", {}).values()}
    tiles = cfg.get("tiles", {})
    objects = [*tiles.get("objects", {}).values(), *(o for th in tiles.get("themes", {}).values() for o in th.get("objects", {}).values())]
    found |= {o["sheet"] for o in objects if "sheet" in o}
    loot = cfg.get("loot", {})
    found |= {*loot.get("items", {}).values(), *loot.get("gold", {}).values(), *loot.get("potions", {}).values()}
    found |= {loot["scroll"]} if loot.get("scroll") else set()
    found |= set(cfg.get("projectiles", {}).get("looks", {}).values())
    return sorted(found)


def images_of(text: str) -> list[str]:
    """Images named by a Flare animation (one) or tileset (one per section) file."""
    return parse_flare_tileset(text)[0] if is_flare_tileset(text) else [parse_flare_animation(text)[0]]


def sheet_ready(art_dir: Path, sheet: str, version: int = 0) -> bool:
    """Both files there and built by the current import (``import.version``): older sheets are rebuilt."""
    base = sprites_dir(art_dir) / sheet
    if not (base.with_suffix(".json").is_file() and base.with_suffix(".png").is_file()):
        return False
    if not version:
        return True
    try:
        return json.loads(base.with_suffix(".json").read_text(encoding="utf-8")).get("version", 1) == version
    except (OSError, ValueError):
        return False


def missing_sheets(cfg: dict, art_dir: Path) -> list[str]:
    version = cfg["import"].get("version", 0)
    return [s for s in sheets_in_use(cfg) if not sheet_ready(art_dir, s, version)]


def source_for(sheet: str, cfg: dict) -> tuple[str, dict, str]:
    """(prefix, source, rest of the id) for a sheet id like ``flare/enemies/zombie``."""
    for prefix, src in cfg["import"]["sources"].items():
        if sheet.startswith(prefix):
            return prefix, src, sheet[len(prefix) :]
    raise KeyError(f"no source for sheet '{sheet}' (see import.sources in sprites.json)")


def repo_path(src: dict, rest: str) -> str:
    """Animation file of a sheet inside a ``repo`` source: ``enemies/zombie`` → mods/…/enemies/zombie.txt."""
    for sub, folder in src["folders"].items():
        if rest.startswith(sub):
            return folder + rest[len(sub) :] + ".txt"
    raise KeyError(f"no folder for '{rest}' in the source's 'folders'")


# ---------------------------------------------------------------- where the files come from


def http_fetch(timeout: float = 60) -> Fetch:
    def get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "aethelgard-fetch-art (+https://github.com/pkelbern/aethelgard)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    return get


def local_repo_fetch(cfg: dict, prefix: str, root: Path, fallback: Fetch | None = None) -> Fetch:
    """Files of one ``repo`` source read from a local checkout; anything else goes to ``fallback`` (the web)."""
    base = cfg["import"]["sources"][prefix]["base"]
    other = fallback or http_fetch()

    def get(url: str) -> bytes:
        if url.startswith(base):
            return (Path(root) / url[len(base) :]).read_bytes()
        return other(url)

    return get


# ---------------------------------------------------------------- download + convert


@dataclass
class Job:
    sheet: str
    animation: Path | None = None
    image: Path | None = None
    scale: float = 1.0
    images: list[Path] = field(default_factory=list)  # tilesets with several sections: one image each


@dataclass
class _Run:
    cfg: dict
    raw: Path
    fetch: Fetch
    report: Progress
    workers: int
    problems: list[str] = field(default_factory=list)
    notes: dict[str, dict[str, bytes]] = field(default_factory=dict)  # source prefix → license files
    total: int = 1
    done: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def step(self) -> None:
        """One file downloaded (called from the download threads)."""
        with self.lock:
            self.done += 1
            self.report("download", min(self.done, self.total), self.total)


def fetch_art(cfg: dict, art_dir: Path, fetch: Fetch, *, rebuild: bool = False, progress: Progress | None = None, workers: int = 6) -> list[str]:
    """Fetches and converts the sheets that are missing (all with ``rebuild``). Returns the sheets built;
    raises :class:`ArtError` after doing everything it could when some sheet failed."""
    sheets = sheets_in_use(cfg) if rebuild else missing_sheets(cfg, art_dir)
    if not sheets:
        return []
    groups: dict[str, list[str]] = {}
    for s in sheets:
        groups.setdefault(source_for(s, cfg)[0], []).append(s)
    report = progress or (lambda stage, done, total: None)
    built: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aethelgard-art-") as tmp:
        run = _Run(cfg, Path(tmp), fetch, report, workers)
        sources = cfg["import"]["sources"]
        run.total = sum(2 * len(g) if sources[k]["kind"] == "repo" else len(_urls(sources[k])) for k, g in groups.items())  # repo: .txt + image(s)
        jobs: list[Job] = []
        for prefix, group in groups.items():
            src = sources[prefix]
            try:
                prepare = _prepare_repo if src["kind"] == "repo" else _prepare_archive
                jobs += prepare(run, prefix, src, group)
            except Exception as ex:  # this source is out; the others go on
                run.problems.append(f"{prefix} ({src.get('page', src.get('url', ''))}): {_why(ex)}")
        for i, job in enumerate(jobs, 1):
            try:
                convert_sheet(job, cfg, sprites_dir(art_dir))
                built.append(job.sheet)
            except Exception as ex:
                run.problems.append(f"{job.sheet}: {_why(ex)}")
            report("convert", i, len(jobs))
        for prefix in groups:
            write_attribution(cfg, prefix, art_dir, run.notes.get(prefix, {}))
    if run.problems:
        raise ArtError(run.problems, built)
    return built


def _why(ex: Exception) -> str:
    return f"{type(ex).__name__}: {ex}"


def _prepare_repo(run: _Run, prefix: str, src: dict, sheets: list[str]) -> list[Job]:
    """Downloads each sheet's animation file, then the images they name (mirrored under the temp folder)."""
    base = src["base"]
    folder = run.raw / prefix

    def get(rel: str) -> Path:
        dest = folder / rel
        if not dest.exists():
            data = run.fetch(base + quote(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        run.step()
        return dest

    texts = {s: repo_path(src, s[len(prefix) :]) for s in sheets}
    with ThreadPoolExecutor(max_workers=run.workers) as pool:
        paths = dict(zip(texts, pool.map(get, texts.values()), strict=True))
        images = {s: [src["imageRoot"] + i for i in images_of(p.read_text(encoding="utf-8"))] for s, p in paths.items()}
        wanted = sorted({i for imgs in images.values() for i in imgs})
        files = dict(zip(wanted, pool.map(get, wanted), strict=True))
    for name, out in src.get("licenseFiles", {}).items():
        try:
            run.notes.setdefault(prefix, {})[out] = run.fetch(base + quote(name))
        except Exception:  # ATTRIBUTION.md still names the source and the license
            pass
    scale = src.get("scale", run.cfg["import"]["scale"])
    scales = src.get("scales", {})  # per folder: tilesets must land exactly on our 64×32 cells

    def scale_of(sheet: str) -> float:
        rest = sheet[len(prefix) :]
        return next((v for k, v in scales.items() if rest.startswith(k)), scale)

    return [Job(s, paths[s], files[images[s][0]], scale_of(s), [files[i] for i in images[s]]) for s in sheets]


def _urls(src: dict) -> list[str]:
    return list(src.get("urls") or [src["url"]])


def _prepare_archive(run: _Run, prefix: str, src: dict, sheets: list[str]) -> list[Job]:
    """Downloads the pack's files once (archives are opened, loose files kept) and finds each sheet's
    animation file and image by name."""
    folder = run.raw / prefix
    unpacked = folder / "files"
    unpacked.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(_urls(src)):
        name = PurePosixPath(url.split("?")[0]).name or f"file{i}"
        data = run.fetch(url)
        run.step()
        if name.lower().endswith((".7z", ".zip", ".tar", ".tar.gz", ".tgz")):
            archive = folder / name
            archive.write_bytes(data)
            extract(archive, unpacked / f"{i}")
        else:
            (unpacked / name).write_bytes(data)
    files = sorted(p for p in unpacked.rglob("*") if p.is_file())
    pngs = [p for p in files if p.suffix.lower() == ".png"]
    for p in files:  # the pack's own license/credits travel with the art
        if any(k in p.name.lower() for k in ("licen", "credit", "readme", "copying")):
            run.notes.setdefault(prefix, {})[p.name] = p.read_bytes()
    scale = src.get("scale", run.cfg["import"]["scale"])
    jobs = []
    for sheet in sheets:
        spec = src["sheets"].get(sheet[len(prefix) :])
        if spec is None:
            run.problems.append(f"{sheet}: not listed in the source's 'sheets'")
            continue
        try:
            anim = _pick_animation(files, spec)
            image = _pick_image(pngs, spec, anim)
        except LookupError as ex:
            run.problems.append(f"{sheet}: {ex}")
            continue
        jobs.append(Job(sheet, anim, image, spec.get("scale", scale)))
    return jobs


def _patterns(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def _matches(path: Path, patterns, exclude=()) -> bool:
    name = path.name.lower()
    if any(fnmatch.fnmatch(name, x.lower()) for x in _patterns(exclude)):
        return False
    return any(fnmatch.fnmatch(name, p.lower()) for p in _patterns(patterns))


def _first(files: list[Path], patterns, exclude=()) -> list[Path]:
    """Files matching the first pattern that matches anything (patterns are in order of preference)."""
    for pat in _patterns(patterns):
        found = [p for p in files if _matches(p, pat, exclude)]
        if found:
            return found
    return []


def _pick_animation(files: list[Path], spec: dict) -> Path:
    patterns = spec.get("animation", "*.txt")
    texts = [p for p in files if p.suffix.lower() == ".txt"]
    for p in _first(texts, patterns, spec.get("exclude", ())) or []:
        try:
            _, anims = parse_flare_animation(p.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, IndexError):
            continue
        if anims:
            return p
    raise LookupError(f"no Flare animation file matching {_patterns(patterns)} in the pack ({', '.join(p.name for p in files) or 'empty'})")


def _pick_image(pngs: list[Path], spec: dict, anim: Path) -> Path:
    """``image`` globs when given (a recolor), else the image the animation file names."""
    patterns = _patterns(spec.get("image"))
    if not patterns:
        named = PurePosixPath(parse_flare_animation(anim.read_text(encoding="utf-8", errors="replace"))[0]).name
        patterns = [named] if named else ["*.png"]
    found = _first(pngs, patterns, spec.get("exclude", ()))
    if not found:
        raise LookupError(f"no image matching {patterns} in the pack ({', '.join(p.name for p in pngs) or 'no PNG'})")
    return found[0]


def extract(archive: Path, dest: Path) -> None:
    """Opens a .7z (py7zr), .zip or .tar.*; refuses entries that would land outside ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()

    def safe(names: list[str]) -> None:
        for n in names:
            if not (root / n).resolve().is_relative_to(root):
                raise ValueError(f"unsafe path in the pack: {n}")

    if archive.suffix.lower() == ".7z":
        import py7zr

        with py7zr.SevenZipFile(archive, "r") as z:
            safe(z.getnames())
            z.extractall(path=dest)
        return
    if archive.suffix.lower() == ".zip":
        import zipfile

        with zipfile.ZipFile(archive) as z:
            safe(z.namelist())
            z.extractall(dest)
        return
    import tarfile

    with tarfile.open(archive) as z:
        safe(z.getnames())
        z.extractall(dest, filter="data")


def convert_sheet(job: Job, cfg: dict, out: Path) -> tuple[int, int]:
    """One Flare animation file (or tileset) + image → ``<out>/<sheet>.png`` and ``.json``."""
    import pygame

    text = job.animation.read_text(encoding="utf-8", errors="replace")
    if is_flare_tileset(text):
        return convert_tileset(job, cfg, out, text)
    imp = cfg["import"]
    keep = set(imp["anims"])
    _, anims = parse_flare_animation(job.animation.read_text(encoding="utf-8", errors="replace"))
    src = pygame.image.load(str(job.image))
    bounds = src.get_rect()

    frames: dict[tuple, pygame.Surface] = {}  # source rect → scaled frame (Flare repeats frames across animations)
    offsets: dict[tuple, tuple[int, int]] = {}
    for anim in (a for name, a in anims.items() if name in keep):
        for x, y, w, h, ox, oy in anim.rects.values():
            key = (x, y, w, h, ox, oy)
            if w <= 0 or h <= 0 or key in frames or not bounds.contains((x, y, w, h)):
                continue
            sw, sh = max(1, round(w * job.scale)), max(1, round(h * job.scale))
            frame = pygame.transform.smoothscale(src.subsurface((x, y, w, h)), (sw, sh))
            box = frame.get_bounding_rect(min_alpha=8)  # trim the empty margin: grid packs have a lot of it
            if box.w == 0 or box.h == 0:
                continue
            frames[key] = frame.subsurface(box).copy()
            offsets[key] = (round(ox * job.scale) - box.x, round(oy * job.scale) - box.y)
    if not frames:
        raise ValueError("no usable frames (animation names or image size do not match)")

    keys = list(frames)
    width = max(imp["atlasWidth"], max(f.get_width() for f in frames.values()))
    pos, height = pack_shelves([frames[k].get_size() for k in keys], width)
    atlas = pygame.Surface((width, max(1, height)), pygame.SRCALPHA)
    placed = {}
    for k, (px, py) in zip(keys, pos, strict=True):
        atlas.blit(frames[k], (px, py))
        placed[k] = [px, py, *frames[k].get_size(), *offsets[k]]

    out_anims = {}
    empty = [0, 0, 0, 0, 0, 0]
    for name, a in anims.items():
        if name not in keep:
            continue
        rects = [[placed.get(a.rects.get((f, d)), empty) for d in range(DIRECTIONS)] for f in range(a.frames)]
        out_anims[name] = {"frames": a.frames, "seconds": round(a.seconds, 3), "loop": a.loop, "rects": rects}

    target = out / f"{job.sheet}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_png(atlas, target.with_suffix(".png"), imp.get("colors", 256))
    tmp = target.with_suffix(".json.tmp")
    meta = {"version": imp.get("version", 1), "image": target.with_suffix(".png").name, "anims": out_anims}
    tmp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)  # the .json appears last: the sheet is complete
    return len(keys), height


def convert_tileset(job: Job, cfg: dict, out: Path, text: str) -> tuple[int, int]:
    """Flare tileset → atlas + ``{"tiles": {id: [x, y, w, h, ox, oy]}}``. Not trimmed (floors must meet edge to
    edge) and kept in full color (textures suffer most from a palette)."""
    import pygame

    _, tiles = parse_flare_tileset(text)
    sources = [pygame.image.load(str(p)) for p in (job.images or [job.image])]
    frames, offsets = {}, {}
    for tid, (section, x, y, w, h, ox, oy) in tiles.items():
        src = sources[min(section, len(sources) - 1)]
        if w <= 0 or h <= 0 or not src.get_rect().contains((x, y, w, h)):
            continue
        sw, sh = max(1, round(w * job.scale)), max(1, round(h * job.scale))
        frames[tid] = pygame.transform.smoothscale(src.subsurface((x, y, w, h)), (sw, sh))
        offsets[tid] = (round(ox * job.scale), round(oy * job.scale))
    if not frames:
        raise ValueError("empty tileset")
    keys = sorted(frames)
    width = max(cfg["import"]["atlasWidth"], max(f.get_width() for f in frames.values()))
    pos, height = pack_shelves([frames[k].get_size() for k in keys], width)
    atlas = pygame.Surface((width, max(1, height)), pygame.SRCALPHA)
    placed = {}
    for k, (px, py) in zip(keys, pos, strict=True):
        atlas.blit(frames[k], (px, py))
        placed[str(k)] = [px, py, *frames[k].get_size(), *offsets[k]]
    target = out / f"{job.sheet}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_png(atlas, target.with_suffix(".png"), 0)
    tmp = target.with_suffix(".json.tmp")
    meta = {"version": cfg["import"].get("version", 1), "image": target.with_suffix(".png").name, "tiles": placed}
    tmp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)
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


def write_attribution(cfg: dict, prefix: str, art_dir: Path, notes: dict[str, bytes]) -> None:
    src = cfg["import"]["sources"][prefix]
    folder = sprites_dir(art_dir) / prefix.rstrip("/")
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in notes.items():
        if data:
            (folder / name).write_bytes(data)
    listing = "\n".join(f"- `{s}`" for s in sheets_in_use(cfg) if s.startswith(prefix))
    origin = src.get("page") or src.get("url", "")
    pinned = f" (commit `{src['commit']}`)" if src.get("commit") else ""
    (folder / "ATTRIBUTION.md").write_text(
        f"# {src.get('title', prefix)}\n\n"
        f"Derived from {origin}{pinned}, by {src.get('credit', 'see the source page')}.\n\n"
        f"License: **{src.get('license', 'see the source page')}**. Changes: frames scaled to fit 64×32 tiles and "
        "repacked into one atlas per sheet by `python -m aethelgard.tools.fetch_art`. "
        "These derived sprites are under the same license.\n\n"
        f"Sheets:\n\n{listing}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------- command line


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flare", "--from", dest="flare", type=Path, help="read the Flare files from a local flare-game checkout")
    ap.add_argument("--art-dir", type=Path, default=None, help="where the art goes (default: the per-user data folder)")
    ap.add_argument("--all", action="store_true", help="rebuild every sheet, not only the missing ones")
    args = ap.parse_args()
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    cfg = load_config()
    art_dir = args.art_dir or default_art_dir()
    fetch = local_repo_fetch(cfg, "flare/", args.flare) if args.flare else http_fetch()
    last = [""]

    def show(stage: str, done: int, total: int) -> None:
        line = f"{'Downloading' if stage == 'download' else 'Converting'} {done}/{total}"
        if line != last[0]:
            print(line, flush=True)
            last[0] = line

    missing = missing_sheets(cfg, art_dir)
    print(f"Art folder: {sprites_dir(art_dir)}  ·  {len(sheets_in_use(cfg)) - len(missing)}/{len(sheets_in_use(cfg))} sheets ready")
    try:
        built = fetch_art(cfg, art_dir, fetch, rebuild=args.all, progress=show)
    except ArtError as ex:
        print(f"{len(ex.built)} sheet(s) built; {len(ex.problems)} problem(s):")
        for p in ex.problems:
            print(f"  - {p}")
        raise SystemExit(1) from None
    finally:
        pygame.quit()
    print(f"{len(built)} sheet(s) built." if built else "No sheet missing.")
    from .fetch_sounds import fetch_sounds, load_icon_config, load_sound_config  # same source (M7-02, M7-04)

    for extra in (load_sound_config(), load_icon_config()):
        failed = fetch_sounds(extra, cfg, art_dir, fetch)
        what = extra.get("folder", "sounds")
        print(f"{what}: {len(failed)} could not be downloaded ({', '.join(failed)})." if failed else f"{what}: ready.")


if __name__ == "__main__":
    main()
