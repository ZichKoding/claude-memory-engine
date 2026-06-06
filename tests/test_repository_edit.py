# tests/test_repository_edit.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed
from memory_engine.normalizer import normalized_key


def _add(repo, scope, body, name="n", type_="fact", desc="d"):
    return repo.capture_or_merge(Parsed(type_, name, desc, body), scope=scope)


def test_edit_updates_body_and_recomputes_key_and_fts(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "the user lives in iowa")
    clock.now += 5
    result = repo.edit(m.id, body="the user lives in texas")
    assert result == "updated"
    row = conn.execute("SELECT body, normalizedKey, updatedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["body"] == "the user lives in texas"
    assert row["normalizedKey"] == normalized_key("global", "fact", "the user lives in texas")
    assert row["updatedAt"] == clock.now
    # FTS reflects the change: new term found, old term gone (in body)
    assert conn.execute("SELECT 1 FROM memories_fts WHERE memories_fts MATCH ?", ('"texas"*',)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM memories_fts WHERE memories_fts MATCH ?", ('"iowa"*',)).fetchone() is None


def test_edit_partial_keeps_other_fields(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "body one", name="Original", desc="orig desc", type_="fact")
    assert repo.edit(m.id, name="Renamed") == "updated"
    row = conn.execute("SELECT name, body, description, type FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["name"] == "Renamed"
    assert row["body"] == "body one"          # unchanged
    assert row["description"] == "orig desc"  # unchanged
    assert row["type"] == "fact"              # unchanged


def test_edit_missing_id_returns_not_found(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    assert repo.edit(999, body="x") == "not_found"


def test_edit_conflict_when_key_would_collide(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Two rows with DIFFERENT bodies (both persist — dedup is exact hard-key), then
    # editing b's body to equal a's triggers the normalizedKey UNIQUE collision = conflict.
    a = _add(repo, "global", "alpha", type_="fact")
    b = _add(repo, "global", "bravo", type_="fact")
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2  # both persist
    # Editing b's body to equal a's (same scope+type) would duplicate a's normalizedKey.
    result = repo.edit(b.id, body="alpha")
    assert result == "conflict"
    row = conn.execute("SELECT body FROM memories WHERE id=?", (b.id,)).fetchone()
    assert row["body"] == "bravo"  # unchanged
