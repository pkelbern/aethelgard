"""Weapons grow with their proficiency (weapon mastery) and the higher bases hit much harder (out/2026)."""

import pytest

from aethelgard.core.combat import player_attack
from aethelgard.core.session import GameSession


def bonus(s):
    return player_attack(s.character, s.skills, s.gear).damage_bonus_pct


def test_blades_level_raises_sword_damage(data):
    s = GameSession(data, 1)
    before = bonus(s)
    s.proficiencies.set_xp("blades", data.xp_total_by_level[9])  # level 10
    per = data.balance.attributes.weapon_mastery_per_level
    gained = bonus(s) - before
    str_part = (s.character.attributes.str - GameSession(data, 1).character.attributes.str) * data.balance.attributes.damage_per_str
    assert gained == pytest.approx(9 * per + str_part)


def test_mastery_follows_the_weapon_in_hand(data):
    s = GameSession(data, 1)
    s.proficiencies.set_xp("bows", data.xp_total_by_level[9])
    sword = bonus(s)
    idx = next(i for i, it in enumerate(s.inventory.items) if it.base.id == "short_bow")
    s.equip_from_backpack(idx, None)
    assert bonus(s) == pytest.approx(sword + 9 * data.balance.attributes.weapon_mastery_per_level), "the bow uses Archery"


def test_each_weapon_tier_hits_clearly_harder(data):
    for family in (["short_sword", "gladius", "sabre", "long_sword", "bastard_sword"], ["club", "spiked_club", "mace", "war_hammer"],
                   ["short_bow", "hunters_bow", "long_bow", "composite_bow"], ["battle_axe", "zweihander", "maul", "claymore"]):
        avgs = [(data.base(b).dmg_min + data.base(b).dmg_max) / 2 for b in family]
        assert all(b >= a * 1.25 for a, b in zip(avgs, avgs[1:], strict=False)), family
        assert avgs[-1] >= avgs[0] * 3, f"{family[-1]} should hit at least 3x the first {family[0]}"
