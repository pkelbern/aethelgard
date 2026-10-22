"""Downloads the sounds and music named in ``data/ui/sounds.json`` (M7-04) into ``<art folder>/sounds``.

They come from the same pinned source as the art (sprites.json ``import.sources``), so the license and credit
are the same: CC-BY-SA 3.0, Flare contributors. The game starts this together with the art download; by hand:
``python -m aethelgard.tools.fetch_sounds``.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ..core.saves import default_art_dir
from .fetch_art import http_fetch, load_config


def load_sound_config() -> dict:
    return json.loads((resources.files("aethelgard") / "data" / "ui" / "sounds.json").read_text(encoding="utf-8"))


def sounds_dir(art_dir: Path) -> Path:
    return Path(art_dir) / "sounds"


def missing_sounds(cfg: dict, art_dir: Path) -> list[str]:
    folder = sounds_dir(art_dir)
    return sorted(name for name in cfg["files"] if not (folder / f"{name}.ogg").is_file())


def fetch_sounds(cfg: dict, art_cfg: dict, art_dir: Path, fetch=None, progress=None) -> list[str]:
    """Fetches the missing files. Returns the names that failed (the game is silent for those)."""
    fetch = fetch or http_fetch()
    src = art_cfg["import"]["sources"][cfg["source"]]
    folder = sounds_dir(art_dir)
    folder.mkdir(parents=True, exist_ok=True)
    todo, failed = missing_sounds(cfg, art_dir), []
    for k, name in enumerate(todo):
        try:
            data = fetch(src["base"] + cfg["root"] + cfg["files"][name])
            (folder / f"{name}.ogg").write_bytes(data)
        except Exception:  # no network, file moved: that sound stays silent
            failed.append(name)
        if progress:
            progress("sounds", k + 1, len(todo))
    (folder / "ATTRIBUTION.md").write_text(
        f"# Sounds and music\n\nFrom {src.get('page', src['base'])} (commit `{src.get('commit', '?')}`), by "
        f"{src.get('credit', 'see the source page')}. License: **{src.get('license', 'see the source page')}**, unchanged files.\n\n"
        + "\n".join(f"- `{n}.ogg` ← `{cfg['root']}{p}`" for n, p in cfg["files"].items()) + "\n",
        encoding="utf-8",
    )
    return failed


def main() -> None:
    cfg, art_dir = load_sound_config(), default_art_dir()
    missing = missing_sounds(cfg, art_dir)
    print(f"Sound folder: {sounds_dir(art_dir)}  ·  {len(cfg['files']) - len(missing)}/{len(cfg['files'])} ready")
    failed = fetch_sounds(cfg, load_config(), art_dir)
    print(f"{len(missing) - len(failed)} downloaded" + (f"; failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
