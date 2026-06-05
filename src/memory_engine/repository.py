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

        # Stage 2 added in Task 7.

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
