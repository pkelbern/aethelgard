"""Keyboard map from ``data/ui/keys.json``: which key triggers which action and which action-bar slot.

The core never sees keys: the client turns a key into a slot, the slot into a skill id
(``GameSession.skill_bar``) and sends ``UseSkill`` with that id.
"""

from __future__ import annotations

import json
from importlib import resources

import pygame

ACTIONS = ("guard", "skillTrees", "map", "console", "save", "cancel")


class KeyMap:
    def __init__(self, raw: dict, bar_slots: int):
        names = raw["skillBar"]
        if len(names) != bar_slots:
            raise ValueError(f"keys.json: skillBar has {len(names)} keys, the bar has {bar_slots} slots (skills.json barSlots)")
        self.slot_keys: list[int] = [pygame.key.key_code(n) for n in names]
        self.action_keys: dict[str, int] = {a: pygame.key.key_code(raw[a]) for a in ACTIONS}
        every = self.slot_keys + list(self.action_keys.values())
        if len(set(every)) != len(every):
            raise ValueError("keys.json: the same key is used twice")
        self._slot_of = {k: i for i, k in enumerate(self.slot_keys)}
        self._action_of = {k: a for a, k in self.action_keys.items()}

    @classmethod
    def load(cls, bar_slots: int) -> KeyMap:
        path = resources.files("aethelgard") / "data" / "ui" / "keys.json"
        return cls(json.loads(path.read_text(encoding="utf-8")), bar_slots)

    def slot_for(self, key: int) -> int | None:
        return self._slot_of.get(key)

    def action_for(self, key: int) -> str | None:
        return self._action_of.get(key)

    def key(self, action: str) -> int:
        return self.action_keys[action]

    @staticmethod
    def label(key: int) -> str:
        """Short text for a key on screen: "1", "Space", "F5"."""
        name = pygame.key.name(key)
        function_key = name[:1] == "f" and name[1:].isdigit()
        return name.upper() if len(name) == 1 or function_key else name.title()

    def slot_label(self, slot: int) -> str:
        return self.label(self.slot_keys[slot])

    def action_label(self, action: str) -> str:
        return self.label(self.action_keys[action])

    @property
    def bar_labels(self) -> str:
        """"1–6" when the bar keys are a run of digits, otherwise "Q W E R"."""
        labels = [self.slot_label(i) for i in range(len(self.slot_keys))]
        digits = all(x.isdigit() for x in labels) and [int(x) for x in labels] == list(range(int(labels[0]), int(labels[0]) + len(labels)))
        return f"{labels[0]}–{labels[-1]}" if digits and len(labels) > 2 else " ".join(labels)
