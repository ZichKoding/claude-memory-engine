# tests/test_repository_explicit.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed


def _add(repo, scope, body, name="n", type_="fact"):
    return repo.capture_or_merge(Parsed(type_, name, "d", body), scope=scope)


def test_explicit_returns_active_and_archived_together(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    active = _add(repo, "global", "alpha bravo charlie delta echo", type_="fact")
    archived = _add(repo, "global", "alpha bravo charlie delta echo", type_="preference")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (archived.id,))
    conn.commit()
    out = repo.retrieve_explicit("alpha bravo charlie delta echo", scopes=["global"], k=10)
    ids = {m.id for m in out}
    assert ids == {active.id, archived.id}  # BOTH pools, unlike auto-retrieve


def test_explicit_revives_archived_and_bumps_recall(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    clock.now += 5
    out = repo.retrieve_explicit("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1 and out[0].status == "active"
    row = conn.execute("SELECT status, recallHits, lastUsedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["status"] == "active"      # revived
    assert row["recallHits"] == 1         # bumped
    assert row["lastUsedAt"] == clock.now


def test_explicit_respects_scope(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    g = _add(repo, "global", "alpha bravo charlie")
    p = _add(repo, "repoA", "alpha bravo charlie")
    _add(repo, "repoB", "alpha bravo charlie")  # competing row, must be excluded
    out = repo.retrieve_explicit("alpha bravo charlie", scopes=["global", "repoA"], k=10)
    assert {m.id for m in out} == {g.id, p.id}


def test_explicit_caps_at_k(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Distinct types so all three survive scope+type fuzzy dedup.
    _add(repo, "global", "alpha bravo charlie delta echo foxtrot", type_="fact")
    _add(repo, "global", "alpha bravo charlie", type_="preference")
    _add(repo, "global", "alpha", type_="person")
    out = repo.retrieve_explicit("alpha bravo charlie delta echo foxtrot", scopes=["global"], k=2)
    assert len(out) == 2
    assert "foxtrot" in out[0].body  # best bm25 match first


def test_explicit_empty(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    _add(repo, "global", "alpha bravo charlie")
    assert repo.retrieve_explicit("a", scopes=["global"], k=10) == []   # no usable tokens
    assert repo.retrieve_explicit("alpha", scopes=[], k=10) == []       # no scopes
