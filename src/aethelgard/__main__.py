"""Entry point: ``python -m aethelgard`` or the ``aethelgard`` command."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="aethelgard", description="Aethelgard – isometric action RPG.")
    ap.add_argument("--seed", type=int, help="world seed (default: random)")
    ap.add_argument("--new", action="store_true", help="start a new character instead of loading the save")
    ap.add_argument("--save-dir", type=Path, help="folder for save files (default: the per-user data folder)")
    ap.add_argument("--frames", type=int, help="run N frames and quit, without saving (tests/CI)")
    ap.add_argument("--screenshot", help="save the last frame to this PNG file")
    ap.add_argument("--area", help="starting area (default: the town)")
    args = ap.parse_args()

    from .client.app import Game  # pygame is only imported here

    game = Game(seed=args.seed, new_game=args.new, save_dir=args.save_dir)
    if args.area:
        game.load_area(args.area)
    game.run(frames=args.frames, screenshot=args.screenshot)


if __name__ == "__main__":
    main()
