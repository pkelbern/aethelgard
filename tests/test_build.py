"""The stand-alone build (M9-07): what PyInstaller is asked to pack. The build itself runs in CI (build.yml)."""

from pathlib import Path

from aethelgard.tools.build import NAME, executable, pyinstaller_args


def test_the_build_packs_the_game_data_and_the_libraries_it_loads_late(tmp_path):
    args = pyinstaller_args(tmp_path, tmp_path / "dist")
    launcher = Path(args[0])
    assert launcher.read_text(encoding="utf-8").strip().endswith("main()")
    pairs = {(a, b) for a, b in zip(args, args[1:], strict=False)}
    assert ("--collect-data", "aethelgard") in pairs and ("--collect-data", "pygame_gui") in pairs
    assert ("--hidden-import", "pygame._sdl2.controller") in pairs, "the gamepad module is imported inside a function"
    assert "--onedir" in args and ("--name", NAME) in pairs
    assert executable(tmp_path / "dist").name.startswith(NAME)
