"""Save files on disk: one versioned JSON per character, written safely, with a backup.

Writing goes to a temporary file first and then replaces the save in one step, so a crash or
power cut never leaves a half-written save. The previous save is kept as ``<name>.bak.json``
and is used automatically if the main file cannot be read (backlog M6-07).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .data import GameData
from .items import SharedStash
from .session import GameSession

STASH_SLOT = "shared_stash"  # one file for every character (GDD 10.5)


@dataclass(frozen=True)
class CharacterSummary:
    """One line of the character selection."""

    slot: str
    name: str
    level: int
    title: str
    gold: int
    played: float  # the save's modified time

APP_DIR = "Aethelgard"
ENV_SAVE_DIR = "AETHELGARD_SAVE_DIR"  # override for tests, CI and portable installs
ENV_ART_DIR = "AETHELGARD_ART_DIR"


def user_data_dir() -> Path:
    """Per-user folder: %APPDATA%\\Aethelgard, ~/Library/Application Support/Aethelgard or ~/.local/share/aethelgard."""
    home = Path.home()
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / APP_DIR
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_DIR.lower()


def default_save_dir() -> Path:
    if os.environ.get(ENV_SAVE_DIR):
        return Path(os.environ[ENV_SAVE_DIR])
    return user_data_dir() / "saves"


def default_art_dir() -> Path:
    """Where downloaded art goes (it is not in the repository): <user data>/art."""
    if os.environ.get(ENV_ART_DIR):
        return Path(os.environ[ENV_ART_DIR])
    return user_data_dir() / "art"


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

    @classmethod
    def characters(cls, directory: Path | str | None = None) -> list[tuple[str, str, float]]:
        """Every saved character in the folder, most recently played first: (slot, name, modified time)."""
        folder = Path(directory) if directory is not None else default_save_dir()
        out = []
        for p in folder.glob("*.json") if folder.is_dir() else []:
            slot = p.stem
            if slot == STASH_SLOT or slot.endswith((".bak", ".deleted")):
                continue
            try:
                name = json.loads(p.read_text(encoding="utf-8")).get("name", slot)
            except (OSError, ValueError):
                continue
            out.append((slot, name, p.stat().st_mtime))
        return sorted(out, key=lambda c: -c[2])

    @classmethod
    def summaries(cls, data: GameData, directory: Path | str | None = None) -> list[CharacterSummary]:
        """What the character selection shows, most recently played first (unreadable saves are left out)."""
        out = []
        for slot, _name, mtime in cls.characters(directory):
            try:
                s = cls(directory, slot).load_session(data)
            except ValueError:
                continue
            if s is not None:
                out.append(CharacterSummary(slot, s.name, s.character.level, s.character.title, s.gold, mtime))
        return out

    def delete(self) -> None:
        """Deletes this character: its save (and backup) become ``<slot>.deleted.json``, which the game never lists
        but a player can rename back by hand."""
        for src in (self.path, self.backup_path):
            if src.exists():
                src.replace(src.with_name(src.name.replace(".json", ".deleted.json")))

    # ---- the shared stash: same safe writing and backup, its own file

    @classmethod
    def for_stash(cls, directory: Path | str | None = None) -> SaveStore:
        return cls(directory, STASH_SLOT)

    def save_stash(self, stash: SharedStash) -> Path:
        return self.write(stash.to_json())

    def load_stash(self, data: GameData) -> SharedStash:
        """The stash on disk, or an empty one the first time (a broken file falls back to its backup)."""
        for text in self.candidates():
            try:
                return SharedStash.from_json(data, text)
            except (ValueError, KeyError, TypeError):
                continue
        return SharedStash()
