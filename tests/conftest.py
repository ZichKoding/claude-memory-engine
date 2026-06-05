# tests/conftest.py
import sqlite3
import pytest
from memory_engine.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def clock():
    """Mutable fake clock returning milliseconds. Tests bump .now to advance time."""
    class Clock:
        now = 1_000_000_000_000  # fixed epoch ms
        def __call__(self) -> int:
            return self.now
    return Clock()
