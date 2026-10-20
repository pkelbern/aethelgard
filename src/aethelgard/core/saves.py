"""Save files on disk: one versioned JSON per character, written safely, with a backup.

Writing goes to a temporary file first and then replaces the save in one step, so a crash or
power cut never leaves a half-written save. The previous save is kept as ``<name>.bak.json``
and is used automatically if the main file cannot be read (backlog M6-07).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .data import GameData
from .session import GameSession

APP_DIR = "Aethelgard"
ENV_SAVE_DIR = "AETHELGARD_SAVE_DIR"  # override for tests, CI and portable installs


def default_save_dir() -> Path:
    """Per-user folder: %APPDATA%\\Aethelgard, ~/Library/Application Support/Aethelgard or ~/.local/share/aethelgard."""
    if os.environ.get(ENV_SAVE_DIR):
        return Path(os.environ[ENV_SAVE_DIR])
    home = Path.home()
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / APP_DIR / "saves"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR / "saves"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_DIR.lower() / "saves"


def slot_name(character_name: str) -> str:
    """File-safe name for a character: "Sir Ana!" → "sir_ana"."""
    return re.sub(r"[^a-z0-9]+", "_", character_name.lower()).strip("_") or "character"


class SaveStore:
    """Reads and writes one character's save file."""

    def __init__(self, directory: Path | str | None = None, slot: str = "character"):
        self.directory = Path(directory) if directory is not None else default_save_dir()
        self.slot = slot

    @property
    def path(self) -> Path:
        return self.directory / f"{self.slot}.json"

    @property
    def backup_path(self) -> Path:
        return self.directory / f"{self.slot}.bak.json"

    def exists(self) -> bool:
        return self.path.exists() or self.backup_path.exists()

    def write(self, text: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.directory / f"{self.slot}.tmp"
        tmp.write_text(text, encoding="utf-8")
        if self.path.exists():
            os.replace(self.path, self.backup_path)
        os.replace(tmp, self.path)
        return self.path

    def candidates(self) -> list[str]:
        """Texts to try, newest first: the save, then its backup."""
        return [p.read_text(encoding="utf-8") for p in (self.path, self.backup_path) if p.exists()]

    # ---- characters

    def save_session(self, session: GameSession) -> Path:
        return self.write(session.save())

    def load_session(self, data: GameData, new_world_seed: int | None = None, player_id: int = 0) -> GameSession | None:
        """The saved character in a fresh world, or None when there is no readable save."""
        errors = []
        for text in self.candidates():
            try:
                return GameSession.load(data, text, new_world_seed, player_id)
            except (ValueError, KeyError, TypeError) as ex:  # broken file: fall back to the backup
                errors.append(str(ex))
        if errors:
            raise ValueError("No readable save: " + "; ".join(errors))
        return None
