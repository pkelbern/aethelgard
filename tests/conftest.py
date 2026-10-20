import pytest

from aethelgard.core.data import GameData


@pytest.fixture(scope="session")
def data() -> GameData:
    return GameData.load()


@pytest.fixture
def fresh_data() -> GameData:
    """Cópia própria, para testes que alteram parâmetros."""
    return GameData.load()
