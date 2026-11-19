from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from vanthor.core.events import Event, EventBus
from vanthor.persistence.database import Database


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Nenhum teste pode tocar nos dados reais do usuário em %APPDATA%."""
    data = tmp_path / "appdata"
    monkeypatch.setenv("VANTHOR_DATA_DIR", str(data))
    return data


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


class Recorder:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(self.events.append)

    @property
    def kinds(self) -> list[str]:
        return [e.kind.value for e in self.events]


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def recorder(bus: EventBus) -> Recorder:
    return Recorder(bus)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
