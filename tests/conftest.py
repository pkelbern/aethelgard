import pytest

from aethelgard.core.data import GameData


@pytest.fixture(scope="session")
def data() -> GameData:
    return GameData.load()


@pytest.fixture
def fresh_data() -> GameData:
    """A private copy, for tests that change parameters."""
    return GameData.load()
