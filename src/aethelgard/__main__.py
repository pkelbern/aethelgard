"""Ponto de entrada: ``python -m aethelgard`` ou o comando ``aethelgard``."""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="aethelgard", description="Aethelgard – RPG de ação isométrico.")
    ap.add_argument("--seed", type=int, help="seed do mundo (padrão: aleatória)")
    ap.add_argument("--frames", type=int, help="roda N quadros e sai (testes/CI)")
    ap.add_argument("--screenshot", help="salva a última tela neste arquivo PNG")
    ap.add_argument("--area", help="área inicial (padrão: vila)")
    args = ap.parse_args()

    from .client.app import Game  # importa pygame só aqui

    game = Game(seed=args.seed)
    if args.area:
        game.load_area(args.area)
    game.run(frames=args.frames, screenshot=args.screenshot)


if __name__ == "__main__":
    main()
