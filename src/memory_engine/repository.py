# src/memory_engine/repository.py
"""Capture + dedup surface over the SQLite store. Clock injected for tests."""
import sqlite3
from typing import Callable

from memory_engine.fts_query import from_user_text
from memory_engine.models import (
    STATUS_ACTIVE, STATUS_ARCHIVED, Inserted, Memory, MergedByFuzzy, MergedByKey, Outcome,
)
from memory_engine.normalizer import normalized_key
from memory_engine.parser import Parsed

ARCHIVE_AFTER_DAYS = 365
_DAY_MS = 86_400_000


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
        """First same-scope+type FTS hit at the given status, or None. The type
        filter is in SQL (not a post-LIMIT-1 Python check) so the best bm25 row of
        the CORRECT type is returned even when a different-type row outranks it."""
        try:
            row = self._conn.execute(
                "SELECT m.id FROM memories m JOIN memories_fts f ON f.rowid=m.id "
                "WHERE memories_fts MATCH ? AND m.scope=? AND m.status=? AND m.type=? "
                "ORDER BY bm25(memories_fts) ASC LIMIT 1",
                (fuzzy, scope, status, type_),
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # malformed MATCH — treat as no candidate
        if row is None:
            return None
        return row["id"]

    def search(self, text: str, scopes: list[str], limit: int,
               status: str = STATUS_ACTIVE) -> list[Memory]:
        """Scope-filtered FTS search ranked by bm25. Empty list when text yields no
        usable query. (bm25 gating/threshold is Phase 2 — this returns raw matches.)"""
        fuzzy = from_user_text(text)
        if fuzzy is None or not scopes:
            return []
        placeholders = ",".join("?" for _ in scopes)
        sql = (
            "SELECT m.* FROM memories m JOIN memories_fts f ON f.rowid=m.id "
            f"WHERE memories_fts MATCH ? AND m.status=? AND m.scope IN ({placeholders}) "
            "ORDER BY bm25(memories_fts) ASC LIMIT ?"
        )
        try:
            rows = self._conn.execute(sql, (fuzzy, status, *scopes, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._to_memory(r) for r in rows]

    def bump_recall(self, ids: list[int]) -> None:
        """Bump recallHits + lastUsedAt and revive (status='active') served rows."""
        if not ids:
            return
        now = self._clock()
        placeholders = ",".join("?" for _ in ids)
        self._conn.execute(
            f"UPDATE memories SET recallHits=recallHits+1, lastUsedAt=?, status='active' "
            f"WHERE id IN ({placeholders})",
            (now, *ids),
        )
        self._conn.commit()

    def run_archival_sweep(self) -> int:
        """Flip active rows idle past the window to archived. Returns count."""
        cutoff = self._clock() - ARCHIVE_AFTER_DAYS * _DAY_MS
        cur = self._conn.execute(
            "UPDATE memories SET status='archived' WHERE status='active' AND lastUsedAt < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount

    @staticmethod
    def _to_memory(r: sqlite3.Row) -> Memory:
        return Memory(
            id=r["id"], scope=r["scope"], type=r["type"], name=r["name"],
            description=r["description"], body=r["body"], normalizedKey=r["normalizedKey"],
            captureHits=r["captureHits"], recallHits=r["recallHits"],
            lastUsedAt=r["lastUsedAt"], status=r["status"], createdAt=r["createdAt"],
            updatedAt=r["updatedAt"], source=r["source"],
        )
