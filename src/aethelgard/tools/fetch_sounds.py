"""Downloads plain files from the art sources: the sounds and music of ``data/ui/sounds.json`` (M7-04) into
``<art folder>/sounds`` and the icon sheet of ``data/ui/icons.json`` (M7-02) into ``<art folder>/icons``.

Each config names a ``source`` of sprites.json ``import.sources`` (the same pinned commit, license and credit as
the art), a ``root`` inside it, the ``folder`` to fill and ``files`` (name → path; the file keeps the path's
extension). The game starts this together with the art download; by hand: ``python -m aethelgard.tools.fetch_sounds``.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ..core.saves import default_art_dir
from .fetch_art import http_fetch, load_config


def load_ui_config(name: str) -> dict:
    return json.loads((resources.files("aethelgard") / "data" / "ui" / f"{name}.json").read_text(encoding="utf-8"))


def load_sound_config() -> dict:
    return load_ui_config("sounds")


def load_icon_config() -> dict:
    return load_ui_config("icons")


def sounds_dir(art_dir: Path, cfg: dict | None = None) -> Path:
    """The folder of a config's files (the sounds by default)."""
    return Path(art_dir) / (cfg or {}).get("folder", "sounds")


def file_path(cfg: dict, art_dir: Path, name: str) -> Path:
    return sounds_dir(art_dir, cfg) / f"{name}{Path(cfg['files'][name]).suffix}"


def missing_sounds(cfg: dict, art_dir: Path) -> list[str]:
    """The config's files not downloaded yet (sounds or icons)."""
    return sorted(name for name in cfg["files"] if not file_path(cfg, art_dir, name).is_file())


def fetch_sounds(cfg: dict, art_cfg: dict, art_dir: Path, fetch=None, progress=None) -> list[str]:
    """Fetches the missing files of ``cfg``. Returns the names that failed (silence, or no icons, for those)."""
    fetch = fetch or http_fetch()
    src = art_cfg["import"]["sources"][cfg["source"]]
    folder = sounds_dir(art_dir, cfg)
    folder.mkdir(parents=True, exist_ok=True)
    todo, failed = missing_sounds(cfg, art_dir), []
    for k, name in enumerate(todo):
        try:
            data = fetch(src["base"] + cfg["root"] + cfg["files"][name])
            file_path(cfg, art_dir, name).write_bytes(data)
        except Exception:  # no network, file moved: that one stays missing
            failed.append(name)
        if progress:
            progress(cfg.get("folder", "sounds"), k + 1, len(todo))
    (folder / "ATTRIBUTION.md").write_text(
        f"# {cfg.get('title', 'Sounds and music')}\n\nFrom {src.get('page', src['base'])} (commit `{src.get('commit', '?')}`), by "
        f"{src.get('credit', 'see the source page')}. License: **{src.get('license', 'see the source page')}**, unchanged files.\n\n"
        + "\n".join(f"- `{file_path(cfg, art_dir, n).name}` ← `{cfg['root']}{p}`" for n, p in cfg["files"].items()) + "\n",
        encoding="utf-8",
    )
    return failed


def main() -> None:
    art_dir = default_art_dir()
    for cfg in (load_sound_config(), load_icon_config()):
        missing = missing_sounds(cfg, art_dir)
        print(f"{sounds_dir(art_dir, cfg)}  ·  {len(cfg['files']) - len(missing)}/{len(cfg['files'])} ready")
        failed = fetch_sounds(cfg, load_config(), art_dir)
        print(f"{len(missing) - len(failed)} downloaded" + (f"; failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
