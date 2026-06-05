# src/memory_engine/repository.py
"""Capture + dedup surface over the SQLite store. Clock injected for tests."""
import sqlite3
from typing import Callable

from memory_engine.fts_query import from_user_text
from memory_engine.models import (
    STATUS_ACTIVE, STATUS_ARCHIVED, Inserted, MergedByFuzzy, MergedByKey, Outcome,
)
from memory_engine.normalizer import normalized_key
from memory_engine.parser import Parsed


class MemoryRepository:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int]):
        self._conn = conn
        self._clock = clock

    def capture_or_merge(self, parsed: Parsed, scope: str) -> Outcome:
        now = self._clock()
        key = normalized_key(scope, parsed.type, parsed.body)

        # Stage 1: hard-key match.
        row = self._conn.execute(
            "SELECT id FROM memories WHERE normalizedKey=? LIMIT 1", (key,)
        ).fetchone()
        if row is not None:
            self._bump_capture_hit(row["id"], now)
            return MergedByKey(row["id"])

        # Stage 2: fuzzy FTS match, same scope+type. Active first, then archived
        # (an archived match is revived by _bump_capture_hit's status='active').
        fuzzy = from_user_text(parsed.body)
        if fuzzy is not None:
            hit = self._find_fuzzy_candidate(fuzzy, scope, parsed.type, STATUS_ACTIVE)
            if hit is not None:
                self._bump_capture_hit(hit, now)
                return MergedByFuzzy(hit, from_archived=False)
            hit = self._find_fuzzy_candidate(fuzzy, scope, parsed.type, STATUS_ARCHIVED)
            if hit is not None:
                self._bump_capture_hit(hit, now)
                return MergedByFuzzy(hit, from_archived=True)

        # Stage 3: insert new row.
        cur = self._conn.execute(
            "INSERT INTO memories(scope,type,name,description,body,normalizedKey,"
            "captureHits,recallHits,lastUsedAt,status,createdAt,updatedAt,source) "
            "VALUES(?,?,?,?,?,?,1,0,?,?,?,?,?)",
            (scope, parsed.type, parsed.name, parsed.description, parsed.body, key,
             now, STATUS_ACTIVE, now, now, "capture"),
        )
        self._conn.commit()
        return Inserted(cur.lastrowid)

    def _bump_capture_hit(self, id_: int, now: int) -> None:
        self._conn.execute(
            "UPDATE memories SET captureHits=captureHits+1, lastUsedAt=?, "
            "updatedAt=?, status='active' WHERE id=?",
            (now, now, id_),
        )
        self._conn.commit()

    def _find_fuzzy_candidate(self, fuzzy: str, scope: str, type_: str, status: str):
        """First same-scope+type FTS hit at the given status, or None."""
        try:
            row = self._conn.execute(
                "SELECT m.id, m.type FROM memories m JOIN memories_fts f ON f.rowid=m.id "
                "WHERE memories_fts MATCH ? AND m.scope=? AND m.status=? "
                "ORDER BY bm25(memories_fts) ASC LIMIT 1",
                (fuzzy, scope, status),
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # malformed MATCH — treat as no candidate
        if row is None or row["type"] != type_:
            return None
        return row["id"]
