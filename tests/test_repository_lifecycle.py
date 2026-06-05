# tests/test_repository_lifecycle.py
from memory_engine.repository import MemoryRepository, ARCHIVE_AFTER_DAYS
from memory_engine.parser import Parsed

DAY_MS = 86_400_000


def test_archival_sweep_archives_stale_only(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    repo.capture_or_merge(Parsed("fact", "old", "d", "alpha bravo charlie"), scope="global")
    repo.capture_or_merge(Parsed("fact", "new", "d", "delta echo foxtrot"), scope="global")
    ids = [r["id"] for r in conn.execute("SELECT id FROM memories ORDER BY id")]
    # Make the first row stale, keep the second fresh.
    conn.execute("UPDATE memories SET lastUsedAt=? WHERE id=?",
                 (clock.now - (ARCHIVE_AFTER_DAYS + 1) * DAY_MS, ids[0]))
    conn.commit()

    n = repo.run_archival_sweep()
    assert n == 1
    statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM memories")}
    assert statuses[ids[0]] == "archived"
    assert statuses[ids[1]] == "active"


def test_search_scoped_and_bumps_recall(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    g = repo.capture_or_merge(Parsed("fact", "g", "d", "shared keyword global"), scope="global")
    a = repo.capture_or_merge(Parsed("fact", "a", "d", "shared keyword repoA"), scope="repoA")
    repo.capture_or_merge(Parsed("fact", "b", "d", "shared keyword repoB"), scope="repoB")

    rows = repo.search("shared keyword", scopes=["global", "repoA"], limit=10)
    found = {r.id for r in rows}
    assert found == {g.id, a.id}  # repoB excluded by scope

    clock.now += 5
    repo.bump_recall(list(found))
    bumped = conn.execute(
        "SELECT recallHits,lastUsedAt FROM memories WHERE id=?", (g.id,)).fetchone()
    assert bumped["recallHits"] == 1
    assert bumped["lastUsedAt"] == clock.now


def test_bump_recall_revives_archived(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = repo.capture_or_merge(Parsed("fact", "x", "d", "lonely token zebra"), scope="global")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    repo.bump_recall([m.id])
    assert conn.execute("SELECT status FROM memories WHERE id=?", (m.id,)).fetchone()["status"] == "active"
