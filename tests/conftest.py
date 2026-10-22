import pytest

from aethelgard.core.data import GameData


@pytest.fixture(scope="session")
def data() -> GameData:
    return GameData.load()


@pytest.fixture
def fresh_data() -> GameData:
    """A private copy, for tests that change parameters."""
    return GameData.load()


@pytest.fixture(autouse=True)
def first_point_at_level_one(request, data):
    """Most tests check a skill's rules, not how points are earned: there the first tree point comes at level 1
    (as before v9), so one level of XP is enough to take a node. Tests marked ``real_points`` use the game's rule
    (skills.json firstPointLevel)."""
    if request.node.get_closest_marker("real_points"):
        yield
        return
    old, data.skills.first_point_level = data.skills.first_point_level, 1
    yield
    data.skills.first_point_level = old
