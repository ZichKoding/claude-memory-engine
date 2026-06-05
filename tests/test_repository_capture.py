# tests/test_repository_capture.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed
from memory_engine.models import Inserted, MergedByKey


def test_insert_then_hardkey_merge(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    p = Parsed("fact", "Iowa", "where", "The user lives in Iowa.")

    first = repo.capture_or_merge(p, scope="global")
    assert isinstance(first, Inserted)

    # Same body modulo punctuation/case => same hard key => merge, no new row.
    p2 = Parsed("fact", "Iowa", "where", "the user lives in iowa!!")
    clock.now += 5
    second = repo.capture_or_merge(p2, scope="global")
    assert isinstance(second, MergedByKey)
    assert second.id == first.id

    row = conn.execute("SELECT captureHits, lastUsedAt FROM memories WHERE id=?", (first.id,)).fetchone()
    assert row["captureHits"] == 2
    assert row["lastUsedAt"] == clock.now
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1


def test_different_scope_does_not_merge(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    p = Parsed("fact", "Iowa", "where", "Lives in Iowa")
    a = repo.capture_or_merge(p, scope="global")
    b = repo.capture_or_merge(p, scope="repoA")
    assert a.id != b.id
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2
