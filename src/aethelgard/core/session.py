"""One character's session: wires the systems, enters areas, handles death, loot pickup and saving."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .combat import CombatMath
from .data import GameData
from .events import EventBus
from .items import GoldDrop, Inventory, Item, ItemDrop, Loadout, LootGenerator, PotionDrop, ScrollDrop, item_from_dict, item_to_dict
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
# v5: + spells learned and in training. v6: actives unlocked with a point and levelled by use (unlockedSkills, skillXp).
# v7: backpack items keep their place in the grid ("pos"); older saves are placed in order. v8: + corpses with their gear.
SAVE_VERSION = 8
DEFAULT_NAME = "Wanderer"


class Equip(Enum):
    OK = "ok"
    NO_ITEM = "no such item"
    REQUIREMENTS = "requirements not met"
    BACKPACK_FULL = "backpack full"
    WRONG_SLOT = "does not fit there"


@dataclass
class Corpse:
    """Where the character died, with everything it wore (GDD 7.6, as in Diablo 2 Normal): walking over it
    puts the gear back on. ``x``/``y`` None = in town beside the waypoint (a corpse from an earlier game)."""

    gear: dict[str, Item]
    area_id: str
    floor: int = 0
    x: float | None = None
    y: float | None = None


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
        self.character = CharacterSheet(data, self.proficiencies, lambda stat: self.stat(stat))
        self.skills = SkillTrees(data.skills, self.proficiencies, lambda: self.gear.granted_spells(),
                                 lambda tree: self.gear.stat_total(f"{tree}Skills"))
        self.skill_bar = SkillBar(self.skills, data.skills.bar_slots)
        self.combat = CombatMath(data.balance.combat, data.balance.guard)
        self.monster_factory = MonsterFactory(data)
        self.spawner = PackSpawner(data, self.monster_factory)
        self.loot = LootGenerator(data)
        self.area_gen = AreaGenerator(data)
        self.gear = Loadout.starting(data)
        self.inventory = Inventory.for_data(data)
        for base_id in data.items.starting_backpack:  # a bow and a second sword to try other weapons
            self.inventory.add(Item.plain(data.base(base_id)))
        self.consumables: Counter[str] = Counter()
        self.waypoints: set[str] = {data.town_id}  # areas whose waypoint this character has touched
        self.corpses: list[Corpse] = []  # bodies left with gear on them, until walked over
        self.gold = 0

    def stat(self, name: str) -> float:
        """A bonus summed over the passives with a level and everything worn (affixes, uniques, set bonuses):
        the one place the rules read generic stats such as attackSpeedPct, fasterHitRecovery or fireDamagePct."""
        return self.skills.stat_total(name) + self.gear.stat_total(name)

    def enter_area(self, area_id: str, floor: int = 0) -> tuple[AreaLayout, list[MonsterPack]]:
        area = self.data.area(area_id)
        rng = Rng.for_label(self.master_seed, f"spawn:{area_id}:{floor}")
        packs = [] if area.kind == "town" else self.spawner.spawn_area(area, floor, rng)
        layout = self.area_gen.generate(area, floor, self.master_seed, sum(1 for p in packs if p.kind != PackKind.BOSS))
        return layout, packs

    def allocate_skill(self, node_id: str) -> Allocate:
        """Spends a tree point; a newly unlocked active goes to the first free slot of the bar."""
        r = self.skills.allocate(node_id)
        if r is Allocate.OK:
            self.skill_bar.auto_bind(node_id)
        return r

    def grant_skill(self, node_id: str, level: int = 1) -> None:
        """Debug console and tests: the node at ``level`` without the rules (see ``SkillTrees.set_level``)."""
        self.skills.set_level(node_id, level)
        self.skill_bar.drop_unusable()
        if self.skills.points_in(node_id) > 0 and self.skills.is_active(node_id):
            self.skill_bar.auto_bind(node_id)

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
        slot = slot or self.gear.auto_slot(item)
        # two hands: a bow pushes out the off hand; a shield or second weapon pushes out a bow
        main = self.gear.main_hand
        bumped = None
        if item.base.two_handed and slot == "mainhand":
            bumped = "offhand"
        elif slot == "offhand" and main is not None and main.base.two_handed:
            bumped = "mainhand"
        extra = self.gear.get(bumped) if bumped else None
        # try it on a copy of the backpack first: the old item takes the new one's place (or any free one)
        trial, previous = self.inventory.copy(), self.gear.get(slot)
        ok = trial.replace(index, previous) if previous is not None else trial.take(index) is not None
        if ok and extra is not None:
            ok = trial.add(extra)
        if not ok:
            names = " and ".join(x.name for x in (previous, extra) if x is not None)
            return Equip.BACKPACK_FULL, f"No room in the backpack for your {names}"
        self.gear.equip(item, slot)
        if extra is not None:
            del self.gear.slots[bumped]
        self.inventory.items, self.inventory.pos = trial.items, trial.pos
        self._gear_changed()
        return Equip.OK, f"Equipped {item.name}"

    def forget_tree(self, tree: str) -> str | None:
        """Reads a Scroll of Forgetting: every point of ``tree`` comes back (GDD 6.1). Refusal message, or None."""
        if self.consumables["respec_scroll"] <= 0:
            return "You need a Scroll of Forgetting"
        if self.skills.spent(tree) <= 0:
            return "No points to give back in that tree"
        self.consumables["respec_scroll"] -= 1
        self.skills.respec(tree)
        self.skill_bar.drop_unusable()
        return None

    def unequip(self, slot: str) -> tuple[Equip, str]:
        item = self.gear.get(slot)
        if item is None:
            return Equip.NO_ITEM, "nothing equipped there"
        if not self.inventory.can_add(item):
            return Equip.BACKPACK_FULL, f"No room in the backpack for {item.name}"
        del self.gear.slots[slot]
        self.inventory.add(item)
        self._gear_changed()
        return Equip.OK, f"Took off {item.name}"

    def _gear_changed(self) -> None:
        """A wand's spell joins the bar while it is worn and leaves it when no wand (or point) keeps it."""
        self.skill_bar.drop_unusable()
        for node_id in self.gear.granted_spells():
            self.skill_bar.auto_bind(node_id)

    def carry_limit(self, cid: str) -> int | None:
        """How many of a consumable can be carried: potions by the belt (none worn: items.json beltWithout),
        scrolls without limit (None)."""
        c = self.data.items.consumables.get(cid)
        if c is None or not c.belt:
            return None
        return self.data.items.belt_without + sum(i.base.belt_slots for i in self.gear.slots.values())

    def has_room_for(self, cid: str) -> bool:
        limit = self.carry_limit(cid)
        return limit is None or self.consumables[cid] < limit

    def collect(self, drop) -> bool:
        """Puts a drop where it belongs. False when it does not fit (backpack full, belt full)."""
        if isinstance(drop, GoldDrop):
            self.gold += drop.amount
            return True
        if isinstance(drop, PotionDrop | ScrollDrop):
            if not self.has_room_for(drop.consumable_id):
                return False
            self.consumables[drop.consumable_id] += 1
            return True
        if isinstance(drop, ItemDrop):
            return self.inventory.add(drop.item)
        return False

    def leave_corpse(self, area_id: str, floor: int, x: float, y: float) -> Corpse | None:
        """On death (balance.json death.dropCorpse): the worn gear stays on the body; you get up without it."""
        if not self.data.balance.death.drop_corpse or not self.gear.slots:
            return None
        c = Corpse(dict(self.gear.slots), area_id, floor, x, y)
        self.corpses.append(c)
        self.gear.slots.clear()
        self._gear_changed()
        return c

    def recover_corpse(self, c: Corpse) -> list[Item]:
        """Puts the corpse's gear back on. What you wore meanwhile goes to the backpack; what does not fit there
        is returned (the world drops it on the floor)."""
        overflow: list[Item] = []
        for slot, item in c.gear.items():
            now = self.gear.get(slot)
            if now is not None:
                del self.gear.slots[slot]
                if not self.inventory.add(now):
                    overflow.append(now)
            self.gear.equip(item, slot)
        self.corpses.remove(c)
        self._gear_changed()
        return overflow

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
                "unlockedSkills": sorted(self.skills.unlocked),
                "skillXp": dict(self.skills.xp),
                "skillBar": self.skill_bar.to_list(),
                "rightSkill": self.skill_bar.right,
                "gear": self.gear.to_dict(),
                "inventory": self.inventory.to_list(),
                "consumables": dict(self.consumables),
                "waypoints": sorted(self.waypoints),
                "corpses": [{slot: item_to_dict(i) for slot, i in c.gear.items()} for c in self.corpses],
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
        if "gear" in raw:  # v1 saves keep the starting gear; worn wands count for the bar below
            s.gear = Loadout.from_dict(data, raw["gear"])
        s.skills.restore(*_skills_from_save(s, raw))
        if "skillBar" in raw:
            s.skill_bar.restore(raw["skillBar"])
            s.skill_bar.set_right(raw.get("rightSkill"))  # older saves: none
        else:  # v1/v2 saves: learned actives fill the bar in tree order
            s.skill_bar.sync()
        s.inventory.load_list(data, raw.get("inventory", []))
        s.consumables.update(raw.get("consumables", {}))
        known = {a.id for a in data.all_areas if a.waypoint}
        s.waypoints |= {w for w in raw.get("waypoints", []) if w in known}
        for gear in raw.get("corpses", []):  # a new world: the bodies wait in town, beside the waypoint
            s.corpses.append(Corpse({slot: item_from_dict(data, i) for slot, i in gear.items()}, data.town_id))
        return s


def _skills_from_save(s: GameSession, raw: dict) -> tuple[dict[str, int], list[str], dict[str, float]]:
    """(passive points, unlocked actives, active XP) from any save version.

    Before v6 an active's level was the points put in it (+1 for a spell learned from a wand): it comes back
    unlocked, with the XP of that level, and the other points return to the tree to be spent again."""
    points = dict(raw.get("skillPoints", {}))
    if raw.get("version", 1) >= 6:
        return points, raw.get("unlockedSkills", []), raw.get("skillXp", {})
    unlocked, xp = [], {}
    for node_id in set(points) | set(raw.get("learnedSpells", [])):
        if s.skills.is_active(node_id):
            level = points.pop(node_id, 0) + (node_id in raw.get("learnedSpells", []))
            unlocked.append(node_id)
            xp[node_id] = s.proficiencies.curve.total_for_level(level)
    return points, unlocked, xp
