"""Sessão de um personagem: monta os sistemas, entra em áreas, morte e save."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .combat import CombatMath, Loadout
from .data import GameData
from .events import EventBus
from .items import LootGenerator
from .monsters import MonsterFactory, MonsterPack, PackKind, PackSpawner
from .progression import (
    CharacterSheet,
    LevelFactor,
    ProficiencySet,
    SkillTrees,
    XpCalculator,
)
from .rng import Rng
from .world_gen import AreaGenerator, AreaLayout

SAVE_VERSION = 1


@dataclass(frozen=True)
class DeathResult:
    gold_lost: int
    xp_lost: dict[str, float]
    leaves_corpse: bool


class GameSession:
    """Ponto único de montagem dos sistemas de um personagem.

    ``player_id`` é 0 no single-player; no multiplayer, cada personagem tem a sua sessão
    ligada ao mesmo barramento, e cada uma só ouve os próprios eventos.
    """

    def __init__(self, data: GameData, master_seed: int, player_id: int = 0, bus: EventBus | None = None):
        self.data = data
        self.master_seed = master_seed
        self.player_id = player_id
        self.bus = bus or EventBus()
        self.world_rng = Rng.for_label(master_seed, "world")
        self.combat_rng = Rng.for_label(master_seed, f"combat:{player_id}")
        self.loot_rng = Rng.for_label(master_seed, "loot")
        self.spawn_rng = Rng.for_label(master_seed, "spawn")

        self.xp = XpCalculator(LevelFactor(data.balance.level_factor.table), data.balance.xp)
        self.proficiencies = ProficiencySet(data, self.xp, self.bus, player_id)
        self.character = CharacterSheet(data, self.proficiencies)
        self.skills = SkillTrees(data.skills, self.proficiencies)
        self.combat = CombatMath(data.balance.combat, data.balance.guard)
        self.monster_factory = MonsterFactory(data)
        self.spawner = PackSpawner(data, self.monster_factory)
        self.loot = LootGenerator(data)
        self.area_gen = AreaGenerator(data)
        self.gear = Loadout.starting(data)
        self.gold = 0

    def enter_area(self, area_id: str, floor: int = 0) -> tuple[AreaLayout, list[MonsterPack]]:
        area = self.data.area(area_id)
        rng = Rng.for_label(self.master_seed, f"spawn:{area_id}:{floor}")
        packs = [] if area.kind == "town" else self.spawner.spawn_area(area, floor, rng)
        layout = self.area_gen.generate(area, floor, self.master_seed, sum(1 for p in packs if p.kind != PackKind.BOSS))
        return layout, packs

    def apply_death(self) -> DeathResult:
        """Perde ouro e, se ligado em balance.json, XP dentro do nível (GDD 7.6)."""
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
        return json.dumps(
            {
                "version": SAVE_VERSION,
                "seed": self.master_seed,
                "gold": self.gold,
                "proficiencyXp": {p.id: p.xp for p in self.proficiencies},
                "skillPoints": dict(self.skills.points),
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def load(cls, data: GameData, text: str, new_world_seed: int | None = None) -> GameSession:
        """O mundo é regenerado a cada jogo (GDD 9.4): passe ``new_world_seed``."""
        raw = json.loads(text)
        if raw.get("version", 1) > SAVE_VERSION:
            raise ValueError(f"Save da versão {raw['version']} é mais novo que o jogo.")
        s = cls(data, new_world_seed if new_world_seed is not None else raw["seed"])
        s.gold = raw.get("gold", 0)
        for pid, xp in raw.get("proficiencyXp", {}).items():
            s.proficiencies.set_xp(pid, xp)
        for node, pts in raw.get("skillPoints", {}).items():
            for _ in range(pts):
                s.skills.allocate(node)
        return s
