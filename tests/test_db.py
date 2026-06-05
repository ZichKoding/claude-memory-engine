# tests/test_db.py
def test_schema_creates_tables_and_fts(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
    )}
    assert "memories" in names
    assert "memories_fts" in names
    assert {"memories_ai", "memories_ad", "memories_au"} <= names


def test_fts_trigger_indexes_inserts(conn):
    conn.execute(
        "INSERT INTO memories(scope,type,name,description,body,normalizedKey,"
        "captureHits,recallHits,lastUsedAt,status,createdAt,updatedAt,source) "
        "VALUES('global','fact','Iowa','where','lives in iowa','k1',1,0,1,'active',1,1,'manual')"
    )
    hit = conn.execute(
        "SELECT m.id FROM memories m JOIN memories_fts f ON f.rowid=m.id "
        "WHERE memories_fts MATCH ?", ('"iowa"*',)
    ).fetchall()
    assert len(hit) == 1


def test_init_db_is_idempotent(conn):
    from memory_engine.db import init_db
    init_db(conn)  # second call must not raise
