"""XP log for playtests (M2-05): one CSV per play session, one row per XP grant.

Columns: seconds since the session started, player, proficiency, its level after the grant, source (offense,
block, armor, absorb, practice), monster level and instance (when the XP came from a fight), XP. Open it in the
spreadsheet to find farms: a source or a monster level that gives far more than the simulation expects.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from .events import EventBus, XpGained

HEADER = ("seconds", "player", "proficiency", "level", "source", "monster_level", "monster_id", "xp")


class XpLog:
    def __init__(self, bus: EventBus, path: Path | str, levels=None, clock=time.monotonic):
        """``levels``: proficiency id → current level (the session's ProficiencySet), for the level column."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._levels = levels
        self._clock = clock
        self._start = clock()
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._file)
        self._csv.writerow(HEADER)
        bus.subscribe(XpGained, self.on_xp)

    def on_xp(self, e: XpGained) -> None:
        level = self._levels[e.proficiency_id].level if self._levels is not None and e.proficiency_id in self._levels else ""
        m = e.monster
        self._csv.writerow((f"{self._clock() - self._start:.1f}", e.player_id, e.proficiency_id, level, e.source,
                            m.level if m else "", m.instance_id if m else "", f"{e.amount:.2f}"))
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
