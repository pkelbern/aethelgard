"""The client's texts by key (M7-06): ``data/ui/strings/<language>.json``.

``Strings("en")("save.loaded", name="Ana")`` → "Save loaded: Ana.". A key missing in a language falls back to
English, and a key missing everywhere shows the key itself, so a half-done translation never breaks the game.
"""

from __future__ import annotations

import json
from importlib import resources

DEFAULT = "en"


def _table(language: str) -> dict[str, str]:
    path = resources.files("aethelgard") / "data" / "ui" / "strings" / f"{language}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def languages() -> list[str]:
    """The languages there is a file for (English first)."""
    folder = resources.files("aethelgard") / "data" / "ui" / "strings"
    found = sorted(p.name.removesuffix(".json") for p in folder.iterdir() if p.name.endswith(".json"))
    return [DEFAULT, *(x for x in found if x != DEFAULT)]


class Strings:
    def __init__(self, language: str = DEFAULT):
        self.language = language
        self._table = {**_table(DEFAULT), **(_table(language) if language != DEFAULT else {})}

    def __call__(self, string_id: str, /, **values) -> str:
        template = self._table.get(string_id, string_id)
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):  # a translation with a wrong {name}: show it raw
            return template

    def has(self, key: str) -> bool:
        return key in self._table
