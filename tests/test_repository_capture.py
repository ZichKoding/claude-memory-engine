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


def test_unrelated_same_type_bodies_do_not_merge(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Two UNRELATED preferences that share only the ubiquitous word "user" must NOT
    # merge — fuzzy dedup used to false-merge them, silently dropping the second.
    a = repo.capture_or_merge(Parsed("preference", "Langs", "d", "the user likes python and rust"), scope="global")
    b = repo.capture_or_merge(Parsed("preference", "Arch", "d", "the user favors lean decoupled architecture"), scope="global")
    assert isinstance(a, Inserted)
    assert isinstance(b, Inserted)
    assert a.id != b.id
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2


def test_exact_recapture_revives_archived(conn, clock):
    # Exact hard-key re-capture is now the SOLE capture-time revival path (the fuzzy
    # path was removed) — re-stating an identical fact must merge + revive, not insert.
    repo = MemoryRepository(conn, clock=clock)
    m = repo.capture_or_merge(Parsed("fact", "Home", "d", "the user lives in iowa"), scope="global")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    clock.now += 5
    out = repo.capture_or_merge(Parsed("fact", "Home", "d", "the user lives in iowa"), scope="global")
    assert isinstance(out, MergedByKey)
    assert out.id == m.id
    row = conn.execute("SELECT status, captureHits FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["status"] == "active"   # revived
    assert row["captureHits"] == 2     # bumped
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1


def test_same_body_different_type_does_not_merge(conn, clock):
    # Type is folded into the hard key, so same scope+body but different type → 2 rows
    # (repository-level coverage of the type dimension, complementing the unit test).
    repo = MemoryRepository(conn, clock=clock)
    a = repo.capture_or_merge(Parsed("fact", "X", "d", "the user lives in iowa"), scope="global")
    b = repo.capture_or_merge(Parsed("preference", "X", "d", "the user lives in iowa"), scope="global")
    assert isinstance(a, Inserted)
    assert isinstance(b, Inserted)
    assert a.id != b.id
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2
