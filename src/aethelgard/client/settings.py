"""Player options (M7-05), kept in ``settings.json`` beside the saves: volumes, window size, full screen, and the
keys the player changed (``keys`` overrides data/ui/keys.json by action name). Not part of the save: the same
options for every character."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.saves import user_data_dir

RESOLUTIONS = ((1280, 720), (1600, 900), (1920, 1080))


@dataclass
class Settings:
    music_volume: float = 0.5
    sfx_volume: float = 0.8
    resolution: list[int] = field(default_factory=lambda: [1280, 720])
    fullscreen: bool = False
    keys: dict[str, str | list[str]] = field(default_factory=dict)  # action (or skillBar) → key name(s)
    language: str = "en"  # data/ui/strings/<language>.json (M7-06)

    @staticmethod
    def path(directory: Path | None = None) -> Path:
        return (directory or user_data_dir()) / "settings.json"

    @classmethod
    def load(cls, directory: Path | None = None) -> Settings:
        """The saved options, or the defaults (a missing or broken file is not an error)."""
        try:
            raw = json.loads(cls.path(directory).read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            s = cls(**known)
        except (OSError, ValueError, TypeError):
            s = cls()
        s.music_volume = min(1.0, max(0.0, float(s.music_volume)))
        s.sfx_volume = min(1.0, max(0.0, float(s.sfx_volume)))
        if tuple(s.resolution) not in RESOLUTIONS:
            s.resolution = list(RESOLUTIONS[0])
        return s

    def save(self, directory: Path | None = None) -> None:
        p = self.path(directory)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
