"""Monsters: scaling by level × rank (GDD 8.1), packs per area (8.2) and unique names."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .data import (
    AreaDef,
    BossDef,
    GameData,
    MonsterModifierDef,
    MonsterRankDef,
    MonsterTypeDef,
)
from .events import MonsterXpInfo
from .rng import Rng


@dataclass
class MonsterInstance:
    instance_id: int
    type: MonsterTypeDef
    rank: MonsterRankDef
    name: str
    level: int
    max_hp: float
    hp: float
    dmg_min: float
    dmg_max: float
    damage_bonus_pct: float
    accuracy: float
    defense: float
    xp_base: float
    modifiers: list[MonsterModifierDef] = field(default_factory=list)
    leader_id: int | None = None

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def xp_info(self) -> MonsterXpInfo:
        return MonsterXpInfo(self.instance_id, self.level, self.xp_base, self.max_hp)

    def stat(self, name: str) -> float:
        return sum(m.stats.get(name, 0.0) for m in self.modifiers)

    def take_damage(self, amount: float) -> float:
        """Applies damage and returns the effective damage (no overkill), the base of offensive XP."""
        if not self.alive or amount <= 0:
            return 0.0
        eff = min(self.hp, amount)
        self.hp -= eff
        return eff


class MonsterFactory:
    def __init__(self, data: GameData):
        self._d = data
        self._next_id = 1

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def create(
        self,
        mtype: MonsterTypeDef,
        area_level: int,
        rank_id: str,
        rng: Rng,
        modifiers: list[MonsterModifierDef] | None = None,
        name: str | None = None,
        leader_id: int | None = None,
    ) -> MonsterInstance:
        rank = self._d.rank(rank_id)
        level = area_level + rank.level_bonus
        lv = self._d.monster_level(level)
        mods = list(modifiers or [])
        hp = max(1.0, round(mtype.hp * lv.hp_pct / 100 * rank.hp_mult * rng.uniform(0.9, 1.1)))
        scale = lv.dmg_pct / 100
        return MonsterInstance(
            instance_id=self._new_id(),
            type=mtype,
            rank=rank,
            name=name or mtype.name,
            level=level,
            max_hp=hp,
            hp=hp,
            dmg_min=max(1.0, mtype.dmg_min * scale),
            dmg_max=max(1.0, mtype.dmg_max * scale),
            damage_bonus_pct=rank.dmg_bonus + sum(m.stats.get("damagePct", 0.0) for m in mods),
            accuracy=lv.accuracy * (1 + rank.acc_bonus),
            defense=lv.defense,
            xp_base=lv.xp * mtype.exp_pct / 100 * rank.xp_mult,
            modifiers=mods,
            leader_id=leader_id,
        )

    def create_boss(self, boss: BossDef, rng: Rng) -> MonsterInstance:
        mtype = self._d.monster_type(boss.base_type)
        rank = self._d.rank("boss")
        lv = self._d.monster_level(boss.level)
        return MonsterInstance(
            instance_id=self._new_id(),
            type=mtype,
            rank=rank,
            name=boss.name,
            level=boss.level,
            max_hp=boss.hp,
            hp=boss.hp,
            dmg_min=boss.dmg_min,
            dmg_max=boss.dmg_max,
            damage_bonus_pct=0.0,
            accuracy=lv.accuracy,
            defense=lv.defense,
            xp_base=lv.xp * boss.exp_pct / 100 * rank.xp_mult,
        )


class PackKind(Enum):
    NORMAL = "normal"
    CHAMPION = "champion"
    UNIQUE = "unique"
    BOSS = "boss"


@dataclass
class MonsterPack:
    kind: PackKind
    members: list[MonsterInstance] = field(default_factory=list)

    @property
    def leader(self) -> MonsterInstance | None:
        return self.members[0] if self.kind in (PackKind.UNIQUE, PackKind.BOSS) and self.members else None


def unique_name(data: GameData, rng: Rng) -> str:
    """Names like "Bonemaw the Hungry"; the parts come from monsters.json → uniqueNames."""
    n = data.monsters.unique_names
    return f"{rng.choice(n.prefixes)}{rng.choice(n.suffixes)} {rng.choice(n.epithets)}".strip()


class PackSpawner:
    def __init__(self, data: GameData, factory: MonsterFactory):
        self._d = data
        self._f = factory

    def spawn_area(self, area: AreaDef, floor: int, rng: Rng) -> list[MonsterPack]:
        s = self._d.balance.spawn
        level = area.level_for_floor(floor)
        types = [t for t in self._d.monsters.types if t.family in area.families]
        packs: list[MonsterPack] = []
        if not types:
            return packs

        for _ in range(area.normal_packs):
            mtype = rng.choice(types)
            pack = MonsterPack(PackKind.NORMAL)
            for _ in range(rng.randint(s.normal_pack_size_min, s.normal_pack_size_max)):
                pack.members.append(self._f.create(mtype, level, "normal", rng))
            packs.append(pack)

        for _ in range(rng.randint(s.special_packs_min, s.special_packs_max)):
            unique = rng.chance(s.unique_pack_chance)
            packs.append(self._unique(types, level, rng) if unique else self._champions(types, level, rng))

        last_floor = floor == max(1, area.floors) - 1
        if area.boss and last_floor:
            boss = next(b for b in self._d.monsters.bosses if b.id == area.boss)
            packs.append(MonsterPack(PackKind.BOSS, [self._f.create_boss(boss, rng)]))
        return packs

    def _champions(self, types, level, rng) -> MonsterPack:
        s = self._d.balance.spawn
        mtype = rng.choice(types)
        mods = self._pick_modifiers(1, False, rng)
        n = rng.randint(s.champion_pack_size_min, s.champion_pack_size_max)
        pack = MonsterPack(PackKind.CHAMPION)
        for k in range(n):
            berserk = k == n - 1 and rng.chance(self._d.ranks.berserker_chance_in_champion_pack)
            rank = "berserker" if berserk else "champion"
            label = "Berserker" if berserk else "Champion"
            pack.members.append(self._f.create(mtype, level, rank, rng, mods, f"{mtype.name} {label}"))
        return pack

    def _unique(self, types, level, rng) -> MonsterPack:
        s = self._d.balance.spawn
        mtype = rng.choice(types)
        mods = self._pick_modifiers(self._d.rank("unique").modifiers, True, rng)
        leader = self._f.create(mtype, level, "unique", rng, mods, unique_name(self._d, rng))
        pack = MonsterPack(PackKind.UNIQUE, [leader])
        for _ in range(rng.randint(s.minions_min, s.minions_max)):
            pack.members.append(self._f.create(mtype, level, "minion", rng, leader_id=leader.instance_id))
        return pack

    def _pick_modifiers(self, count: int, unique_allowed: bool, rng: Rng) -> list[MonsterModifierDef]:
        pool = [m for m in self._d.modifiers.modifiers if unique_allowed or not m.unique_only]
        rng.shuffle(pool)
        return pool[:count]
