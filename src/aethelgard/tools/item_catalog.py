"""Prints the hand-made items (uniques and sets) as a Markdown catalog: ``python -m aethelgard.tools.item_catalog``.

docs/catalogo_itens.md is made with it; run it again after changing items.json.
"""

from __future__ import annotations

from ..core.data import GameData, PropDef


def prop_text(data: GameData, p: PropDef) -> str:
    value = f"{p.value:g}" if p.value is not None else f"{p.min:g}–{p.max:g}"
    return f"{p.stat} {value}"


def catalog(data: GameData) -> str:
    bases = {b.id: b for b in data.items.bases}
    out = ["# Itens únicos e de set", "", f"{len(data.items.uniques)} únicos e {len(data.items.sets)} sets (items.json). "
           "Gerado por `python -m aethelgard.tools.item_catalog`.", "", "## Únicos", "", "| Nível | Nome | Base | Linhas |", "|---|---|---|---|"]
    for u in sorted(data.items.uniques, key=lambda u: (u.level, u.name)):
        out.append(f"| {u.level} | {u.name} | {bases[u.base].name} | {'; '.join(prop_text(data, p) for p in u.props)} |")
    out += ["", "## Sets", ""]
    for s in data.items.sets:
        out += [f"### {s.name}", "", "| Nível | Peça | Base | Linhas |", "|---|---|---|---|"]
        out += [f"| {p.level} | {p.name} | {bases[p.base].name} | {'; '.join(prop_text(data, x) for x in p.props)} |" for p in s.pieces]
        out += [""] + [f"- **{b.pieces} peças:** " + ", ".join(f"{k} {v:g}" for k, v in b.stats.items()) for b in s.bonuses] + [""]
    return "\n".join(out) + "\n"


def main() -> None:
    print(catalog(GameData.load()), end="")


if __name__ == "__main__":
    main()
