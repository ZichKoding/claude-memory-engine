# src/memory_engine/db.py
"""SQLite connection + schema. The `.md` files are NOT involved here — the DB is
its own system of record (see spec). FTS5 external-content table mirrors the three
text columns; triggers keep it in sync."""
import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    normalizedKey TEXT NOT NULL,
    captureHits INTEGER NOT NULL DEFAULT 1,
    recallHits INTEGER NOT NULL DEFAULT 0,
    lastUsedAt INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    createdAt INTEGER NOT NULL,
    updatedAt INTEGER NOT NULL,
    source TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_normkey ON memories(normalizedKey);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_lastused ON memories(lastUsedAt);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    name, description, body, content='memories', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
    INSERT INTO memories_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
