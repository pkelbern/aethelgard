"""Co-op rules (M10-03): D2 defaults, and single player unchanged."""

import pytest

from aethelgard.core.party import monster_hp_mult, monster_xp_mult, no_drop_chance, split_kill_xp


def test_one_player_is_single_player(data):
    p = data.balance.party
    assert monster_hp_mult(p, 1) == monster_xp_mult(p, 1) == 1.0
    assert split_kill_xp(p, 100, {0: 10}) == {0: 100}
    assert no_drop_chance(p, 0.55, 1) == pytest.approx(0.55)


def test_d2_scaling_with_more_players(data):
    p = data.balance.party
    assert monster_hp_mult(p, 2) == pytest.approx(1.5) and monster_hp_mult(p, 4) == pytest.approx(2.5)
    assert monster_xp_mult(p, 3) == pytest.approx(2.0)
    assert no_drop_chance(p, 0.5, 2) == pytest.approx(0.5) and no_drop_chance(p, 0.5, 3) == pytest.approx(0.25)


def test_party_xp_gets_a_bonus_and_is_split_by_level(data):
    p = data.balance.party
    shares = split_kill_xp(p, 100, {0: 20, 1: 10})
    assert sum(shares.values()) == pytest.approx(135)
    assert shares[0] == pytest.approx(90) and shares[1] == pytest.approx(45)
    assert data.balance.party.loot in ("shared", "individual")
