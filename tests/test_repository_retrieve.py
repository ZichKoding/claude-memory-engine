# tests/test_repository_retrieve.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed


def _add(repo, scope, body, name="n", type_="fact"):
    return repo.capture_or_merge(Parsed(type_, name, "d", body), scope=scope)


def test_retrieve_merges_scopes_and_excludes_others(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    g = _add(repo, "global", "alpha bravo charlie delta echo")
    p = _add(repo, "repoA", "alpha bravo charlie delta echo")
    _add(repo, "repoB", "alpha bravo charlie delta echo")  # competing row, must be excluded
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global", "repoA"], k=10)
    ids = {m.id for m in out}
    assert ids == {g.id, p.id}  # repoB excluded by scope


def test_retrieve_returns_best_k_ordered(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Three matching rows with DISTINCT types so capture-time fuzzy dedup (which is
    # scope+type-constrained) keeps them as separate rows. Retrieval filters on
    # scope+status only (not type), so all three are still searched and bm25-ranked.
    _add(repo, "global", "alpha bravo charlie delta echo foxtrot", type_="fact")  # fullest overlap
    _add(repo, "global", "alpha bravo charlie", type_="preference")
    _add(repo, "global", "alpha", type_="person")
    out = repo.retrieve("alpha bravo charlie delta echo foxtrot", scopes=["global"], k=2)
    assert len(out) == 2                 # capped at k
    assert "foxtrot" in out[0].body      # best (all-6-token) match ranks first


def test_retrieve_bumps_recall_on_served(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    clock.now += 5
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1
    row = conn.execute("SELECT recallHits, lastUsedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["recallHits"] == 1
    assert row["lastUsedAt"] == clock.now


def test_retrieve_active_takes_precedence_over_archived(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Distinct types so BOTH rows survive dedup — else they'd merge into one and this
    # would silently exercise the fallback path instead of active-precedence.
    active = _add(repo, "global", "alpha bravo charlie delta echo", type_="fact")
    archived = _add(repo, "global", "alpha bravo charlie delta echo foxtrot golf", type_="preference")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (archived.id,))
    conn.commit()
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert {m.id for m in out} == {active.id}  # active non-empty → archived never consulted


def test_retrieve_falls_back_to_archived_and_revives(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1
    assert out[0].status == "active"          # returned entity reflects revive
    db = conn.execute("SELECT status FROM memories WHERE id=?", (m.id,)).fetchone()
    assert db["status"] == "active"           # and the row was revived


def test_retrieve_empty_query_returns_empty(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    _add(repo, "global", "alpha bravo charlie")
    assert repo.retrieve("a", scopes=["global"], k=10) == []   # no usable tokens
    assert repo.retrieve("alpha", scopes=[], k=10) == []       # no scopes
