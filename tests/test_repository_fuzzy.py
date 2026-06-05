# tests/test_repository_fuzzy.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed
from memory_engine.models import Inserted, MergedByFuzzy


def test_paraphrase_merges_by_fuzzy(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    first = repo.capture_or_merge(
        Parsed("fact", "Iowa home", "where", "The user lives in Iowa"), scope="global")
    assert isinstance(first, Inserted)

    # Different wording (different hard key) but overlapping tokens => fuzzy merge.
    clock.now += 5
    second = repo.capture_or_merge(
        Parsed("fact", "Iowa", "loc", "User currently lives in Iowa today"), scope="global")
    assert isinstance(second, MergedByFuzzy)
    assert second.id == first.id
    assert second.from_archived is False
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1


def test_fuzzy_respects_type_boundary(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    repo.capture_or_merge(Parsed("fact", "Iowa", "where", "lives in Iowa"), scope="global")
    out = repo.capture_or_merge(
        Parsed("preference", "Iowa", "pref", "lives in Iowa"), scope="global")
    assert isinstance(out, Inserted)  # different type => no merge
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2


def test_fuzzy_revives_archived_row(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    first = repo.capture_or_merge(
        Parsed("fact", "Iowa", "where", "The user lives in Iowa"), scope="global")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (first.id,))
    conn.commit()

    clock.now += 5
    out = repo.capture_or_merge(
        Parsed("fact", "Iowa", "where2", "user lives in Iowa now"), scope="global")
    assert isinstance(out, MergedByFuzzy)
    assert out.from_archived is True
    row = conn.execute("SELECT status FROM memories WHERE id=?", (first.id,)).fetchone()
    assert row["status"] == "active"  # revived


def test_fuzzy_merges_same_type_even_when_other_type_outranks(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # A short FACT that outranks on bm25, and a longer PREFERENCE, same scope + tokens.
    fact = repo.capture_or_merge(
        Parsed("fact", "zm fact", "d", "zebra mango"), scope="global")
    pref = repo.capture_or_merge(
        Parsed("preference", "zm pref", "d", "zebra mango lantern velvet copper ridge"),
        scope="global")
    assert isinstance(fact, Inserted)
    assert isinstance(pref, Inserted)
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2

    # New PREFERENCE capture must merge into the preference row, not insert a third,
    # even though the fact row ranks higher on bm25 for these tokens.
    clock.now += 5
    out = repo.capture_or_merge(
        Parsed("preference", "zm pref2", "d", "zebra mango"), scope="global")
    assert isinstance(out, MergedByFuzzy)
    assert out.id == pref.id
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2
