"""The client's texts by key (M7-06)."""

import json
import re
from importlib import resources
from pathlib import Path

from aethelgard.client.strings import Strings, languages

CLIENT = Path(resources.files("aethelgard")) / "client"


def test_every_key_the_client_uses_is_in_english():
    table = json.loads((Path(resources.files("aethelgard")) / "data" / "ui" / "strings" / "en.json").read_text(encoding="utf-8"))
    used = set()
    for f in CLIENT.glob("*.py"):
        used |= set(re.findall(r'\btr\("([^"]+)"', f.read_text(encoding="utf-8")))
    assert used and not used - set(table), sorted(used - set(table))


def test_formatting_and_fallbacks():
    tr = Strings("en")
    assert tr("save.loaded", name="Ana") == "Save loaded: Ana."
    assert tr("no.such.key") == "no.such.key"
    assert tr("save.loaded") == "Save loaded: {name}.", "a missing value shows the template, never crashes"
    assert Strings("xx")("save.saved") == "Game saved.", "an unknown language falls back to English"
    assert languages()[0] == "en"
