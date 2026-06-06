# src/memory_engine/db.py
"""SQLite connection + schema. The `.md` files are NOT involved here — the DB is
its own system of record (see spec). FTS5 external-content table mirrors the three
text columns; triggers keep it in sync."""
import os
import shutil
import sqlite3
from pathlib import Path

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


def _is_healthy(path: str) -> bool:
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def recover_if_corrupt(path: str, backups_dir: str) -> str:
    """Boot-time integrity guard. If `path` is missing or healthy → 'ok'. If corrupt:
    quarantine it to `<path>.corrupt-<n>`, then restore from the newest HEALTHY backup
    ('restored') or, if none works, recreate an empty schema ('recreated'). Best-effort —
    the caller (`session-init`) wraps it in fail-open; do not assume it never raises."""
    if not os.path.exists(path):
        return "ok"
    if _is_healthy(path):
        return "ok"
    # quarantine the corrupt file under a non-colliding name (never delete user data)
    n = 0
    while os.path.exists(f"{path}.corrupt-{n}"):
        n += 1
    os.replace(path, f"{path}.corrupt-{n}")
    # restore from the newest HEALTHY backup (a corrupt newest snapshot must not win),
    # and re-verify the restored copy; else fall through to recreate.
    if os.path.isdir(backups_dir):
        for b in sorted(Path(backups_dir).glob("memory-*.db"), reverse=True):
            if _is_healthy(str(b)):
                shutil.copyfile(str(b), path)
                if _is_healthy(path):
                    return "restored"
    connect(path).close()  # no healthy backup → recreate empty schema
    return "recreated"
