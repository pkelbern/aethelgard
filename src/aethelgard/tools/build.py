"""Builds the stand-alone game for this system with PyInstaller (M9-07): ``python -m aethelgard.tools.build``.

The result is a folder ``dist/Aethelgard`` (``--onedir``: starts faster and trips fewer antivirus false positives
than one big self-extracting file) with the ``Aethelgard`` program, the game data (JSON, Tiled maps, strings) and
the libraries. The art and sounds are not inside: the game downloads them on first start, as from the sources.
``--smoke`` then runs the built program for a few frames without a window, as CI does.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "Aethelgard"
LAUNCHER = """from aethelgard.__main__ import main

main()
"""
# Packages whose data files (themes, fonts, compiled helpers) the frozen game needs.
COLLECT_DATA = ("aethelgard", "pygame_gui")
COLLECT_ALL = ("tcod",)
HIDDEN = ("pygame._sdl2.controller", "pytmx", "py7zr")


def pyinstaller_args(work: Path, dist: Path) -> list[str]:
    launcher = work / "launch_aethelgard.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER, encoding="utf-8")
    args = [str(launcher), "--name", NAME, "--onedir", "--noconfirm", "--clean",
            "--distpath", str(dist), "--workpath", str(work / "build"), "--specpath", str(work)]
    if platform.system() in ("Windows", "Darwin"):
        args.append("--windowed")  # no console window; Linux keeps stdout for bug reports
    for pkg in COLLECT_DATA:
        args += ["--collect-data", pkg]
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for mod in HIDDEN:
        args += ["--hidden-import", mod]
    return args


def executable(dist: Path) -> Path:
    folder = dist / NAME
    if platform.system() == "Darwin" and (dist / f"{NAME}.app").exists():
        return dist / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return folder / (f"{NAME}.exe" if platform.system() == "Windows" else NAME)


def smoke(exe: Path, frames: int = 60) -> None:
    """The built program opens, draws ``frames`` frames with no window and no sound card, and quits cleanly."""
    env = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"}
    with _temp_dir() as tmp:
        env["AETHELGARD_SAVE_DIR"] = str(tmp / "saves")
        env["AETHELGARD_ART_DIR"] = str(tmp / "art")
        subprocess.run([str(exe), "--seed", "1", "--frames", str(frames), "--offline", "--area", "plains"], env=env, check=True, timeout=300)


def _temp_dir():
    import tempfile

    class _Dir:
        def __enter__(self) -> Path:
            self.path = Path(tempfile.mkdtemp(prefix="aethelgard-smoke-"))
            return self.path

        def __exit__(self, *exc) -> None:
            shutil.rmtree(self.path, ignore_errors=True)

    return _Dir()


def archive(dist: Path) -> Path:
    """dist/Aethelgard-<system>-<machine>.zip, ready to upload."""
    system = {"Darwin": "macos"}.get(platform.system(), platform.system().lower())
    base = dist / f"{NAME}-{system}-{platform.machine().lower()}"
    root = dist / f"{NAME}.app" if (dist / f"{NAME}.app").exists() else dist / NAME
    return Path(shutil.make_archive(str(base), "zip", root_dir=root.parent, base_dir=root.name))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", type=Path, default=Path("dist"), help="where the game folder goes (default: dist)")
    ap.add_argument("--work", type=Path, default=Path("build"), help="PyInstaller's scratch folder (default: build)")
    ap.add_argument("--smoke", action="store_true", help="run the built game for a few frames afterwards")
    ap.add_argument("--zip", action="store_true", help="also pack it as dist/Aethelgard-<system>-<machine>.zip")
    args = ap.parse_args()
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError:
        sys.exit('PyInstaller is missing: pip install -e ".[build]"')
    pyinstaller.run(pyinstaller_args(args.work, args.dist))
    exe = executable(args.dist)
    print(f"Built: {exe}")
    if args.smoke:
        smoke(exe)
        print("Smoke run: OK")
    if args.zip:
        print(f"Archive: {archive(args.dist)}")


if __name__ == "__main__":
    main()
