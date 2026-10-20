"""One character's session: wires the systems, enters areas, handles death, loot pickup and saving."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .combat import CombatMath
from .data import GameData
from .events import EventBus
from .items import GoldDrop, Inventory, ItemDrop, Loadout, LootGenerator, PotionDrop, ScrollDrop
from .monsters import MonsterFactory, MonsterPack, PackKind, PackSpawner
from .progression import (
    Allocate,
    CharacterSheet,
    LevelFactor,
    ProficiencySet,
    SkillBar,
    SkillTrees,
    XpCalculator,
)
from .rng import Rng
from .world_gen import AreaGenerator, AreaLayout

# v1: gold, XP and skill points. v2: + name, equipment, backpack and consumables. v3: + action bar. v4: + waypoints.
SAVE_VERSION = 4
DEFAULT_NAME = "Wanderer"


class Equip(Enum):
    OK = "ok"
    NO_ITEM = "no such item"
    REQUIREMENTS = "requirements not met"
    BACKPACK_FULL = "backpack full"
    WRONG_SLOT = "does not fit there"


@dataclass(frozen=True)
class DeathResult:
    gold_lost: int
    xp_lost: dict[str, float]
    leaves_corpse: bool


class GameSession:
    """Single assembly point for one character's systems.

    ``player_id`` is 0 in single player; in multiplayer each character has its own session
    attached to the same bus, and each one only listens to its own events.
    """

    def __init__(self, data: GameData, master_seed: int, player_id: int = 0, bus: EventBus | None = None, name: str = DEFAULT_NAME):
        self.data = data
        self.master_seed = master_seed
        self.player_id = player_id
        self.name = name
        self.bus = bus or EventBus()
        self.world_rng = Rng.for_label(master_seed, "world")
        self.combat_rng = Rng.for_label(master_seed, f"combat:{player_id}")
        self.loot_rng = Rng.for_label(master_seed, "loot")
        self.spawn_rng = Rng.for_label(master_seed, "spawn")

        self.xp = XpCalculator(LevelFactor(data.balance.level_factor.table), data.balance.xp)
        self.proficiencies = ProficiencySet(data, self.xp, self.bus, player_id)
        self.character = CharacterSheet(data, self.proficiencies)
        self.skills = SkillTrees(data.skills, self.proficiencies)
        self.skill_bar = SkillBar(self.skills, data.skills.bar_slots)
        self.combat = CombatMath(data.balance.combat, data.balance.guard)
        self.monster_factory = MonsterFactory(data)
        self.spawner = PackSpawner(data, self.monster_factory)
        self.loot = LootGenerator(data)
        self.area_gen = AreaGenerator(data)
        self.gear = Loadout.starting(data)
        self.inventory = Inventory.for_data(data)
        self.consumables: Counter[str] = Counter()
        self.waypoints: set[str] = {data.town_id}  # areas whose waypoint this character has touched
        self.gold = 0

    def enter_area(self, area_id: str, floor: int = 0) -> tuple[AreaLayout, list[MonsterPack]]:
        area = self.data.area(area_id)
        rng = Rng.for_label(self.master_seed, f"spawn:{area_id}:{floor}")
        packs = [] if area.kind == "town" else self.spawner.spawn_area(area, floor, rng)
        layout = self.area_gen.generate(area, floor, self.master_seed, sum(1 for p in packs if p.kind != PackKind.BOSS))
        return layout, packs

    def allocate_skill(self, node_id: str) -> Allocate:
        """Spends a tree point; a newly learned active goes to the first free slot of the bar."""
        r = self.skills.allocate(node_id)
        if r is Allocate.OK:
            self.skill_bar.auto_bind(node_id)
        return r

    def missing_requirements(self, item) -> dict[str, int]:
        """Attribute → how much is required, for each requirement the character does not meet yet."""
        a = self.character.attributes
        return {k: v for k, v in item.base.req.items() if v > getattr(a, k, 0)}

    def equip_from_backpack(self, index: int, slot: str | None = None) -> tuple[Equip, str]:
        """Wears the backpack item at ``index`` in ``slot`` (or its automatic slot); what was there takes its
        place in the backpack. A one-hand weapon in the off hand means two weapons."""
        if not 0 <= index < len(self.inventory.items):
            return Equip.NO_ITEM, "no such item"
        item = self.inventory.items[index]
        if slot is not None and slot not in self.gear.slots_for(item):
            names = self.data.items.equipment
            fits = " or ".join(dict.fromkeys(names[k].name for k in self.gear.slots_for(item))) or "nowhere"
            return Equip.WRONG_SLOT, f"{item.name} goes in {fits}, not {names[slot].name if slot in names else slot}"
        missing = self.missing_requirements(item)
        if missing:
            need = ", ".join(f"{v} {self.data.stat(k).label}" for k, v in missing.items())
            return Equip.REQUIREMENTS, f"{item.name} needs {need}"
        previous = self.gear.equip(item, slot)
        if previous is None:
            self.inventory.items.pop(index)
        else:
            self.inventory.items[index] = previous  # a swap never needs a free cell
        return Equip.OK, f"Equipped {item.name}"

    def unequip(self, slot: str) -> tuple[Equip, str]:
        item = self.gear.get(slot)
        if item is None:
            return Equip.NO_ITEM, "nothing equipped there"
        if self.inventory.full:
            return Equip.BACKPACK_FULL, "Backpack full"
        del self.gear.slots[slot]
        self.inventory.add(item)
        return Equip.OK, f"Took off {item.name}"

    def collect(self, drop) -> bool:
        """Puts a drop where it belongs. False when it does not fit (backpack full)."""
        if isinstance(drop, GoldDrop):
            self.gold += drop.amount
            return True
        if isinstance(drop, PotionDrop | ScrollDrop):
            self.consumables[drop.consumable_id] += 1
            return True
        if isinstance(drop, ItemDrop):
            return self.inventory.add(drop.item)
        return False

    def apply_death(self) -> DeathResult:
        """Loses gold and, if enabled in balance.json, XP inside the current level (GDD 7.6)."""
        p = self.data.balance.death
        gold = int(self.gold * p.gold_loss_pct)
        self.gold -= gold
        xp = {}
        for prof in self.proficiencies:
            lost = prof.lose_xp_within_level(p.xp_loss_pct_of_level)
            if lost > 0:
                xp[prof.id] = lost
        return DeathResult(gold, xp, p.drop_corpse)

    # ---------------------------------------------------------------- save

    def save(self) -> str:
        """The character, not the world: the world is regenerated every game (GDD 9.4)."""
        return json.dumps(
            {
                "version": SAVE_VERSION,
                "name": self.name,
                "seed": self.master_seed,
                "gold": self.gold,
                "proficiencyXp": {p.id: p.xp for p in self.proficiencies},
                "skillPoints": dict(self.skills.points),
                "skillBar": self.skill_bar.to_list(),
                "gear": self.gear.to_dict(),
                "inventory": self.inventory.to_list(),
                "consumables": dict(self.consumables),
                "waypoints": sorted(self.waypoints),
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def load(
        cls, data: GameData, text: str, new_world_seed: int | None = None, player_id: int = 0, bus: EventBus | None = None
    ) -> GameSession:
        """Rebuilds a character from ``save()``. Pass ``new_world_seed`` to get a fresh world (GDD 9.4)."""
        raw = json.loads(text)
        if raw.get("version", 1) > SAVE_VERSION:
            raise ValueError(f"Save version {raw['version']} is newer than the game.")
        seed = new_world_seed if new_world_seed is not None else raw["seed"]
        s = cls(data, seed, player_id, bus, raw.get("name", DEFAULT_NAME))
        s.gold = raw.get("gold", 0)
        for pid, xp in raw.get("proficiencyXp", {}).items():
            if pid in s.proficiencies:
                s.proficiencies.set_xp(pid, xp)
        s.skills.restore(raw.get("skillPoints", {}))
        if "skillBar" in raw:
            s.skill_bar.restore(raw["skillBar"])
        else:  # v1/v2 saves: learned actives fill the bar in tree order
            s.skill_bar.sync()
        if "gear" in raw:  # v1 saves keep the starting gear
            s.gear = Loadout.from_dict(data, raw["gear"])
        s.inventory.load_list(data, raw.get("inventory", []))
        s.consumables.update(raw.get("consumables", {}))
        known = {a.id for a in data.all_areas if a.waypoint}
        s.waypoints |= {w for w in raw.get("waypoints", []) if w in known}
        return s
