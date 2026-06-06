# src/memory_engine/repository.py
"""Capture + dedup surface over the SQLite store. Clock injected for tests."""
import dataclasses
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
RETRIEVE_K = 5


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

    def edit(self, id_: int, *, type: str | None = None, name: str | None = None,
             description: str | None = None, body: str | None = None) -> str:
        """Update fields of an existing memory by id. Only the provided fields change;
        scope is immutable (it's identity). Recomputes normalizedKey from the (unchanged)
        scope + the resulting type/body, and touches updatedAt. The FTS index re-syncs
        via the UPDATE trigger. Returns 'updated', 'not_found', or 'conflict' (the new
        normalizedKey would duplicate another row's UNIQUE key)."""
        row = self._conn.execute("SELECT * FROM memories WHERE id=?", (id_,)).fetchone()
        if row is None:
            return "not_found"
        new_type = type if type is not None else row["type"]
        new_name = name if name is not None else row["name"]
        new_desc = description if description is not None else row["description"]
        new_body = body if body is not None else row["body"]
        new_key = normalized_key(row["scope"], new_type, new_body)
        now = self._clock()
        try:
            self._conn.execute(
                "UPDATE memories SET type=?, name=?, description=?, body=?, "
                "normalizedKey=?, updatedAt=? WHERE id=?",
                (new_type, new_name, new_desc, new_body, new_key, now, id_),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()  # clear the failed UPDATE's open transaction
            return "conflict"  # new key collides with an existing row's UNIQUE key
        return "updated"

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

    def retrieve(self, text: str, scopes: list[str], k: int = RETRIEVE_K) -> list[Memory]:
        """Auto-retrieval: the best-k bm25 matches across the given scopes, active
        first. If the active set is empty, fall back to archived and revive the hits.
        Bumps recallHits on everything served, best-first.

        No absolute score cutoff: bm25 magnitude is corpus-dependent (rejected in plan
        red-team), so calibrated relevance gating is deferred to a Phase 4 tuning pass.
        FTS MATCH already requires token overlap and k caps the count."""
        active = self.search(text, scopes, k, STATUS_ACTIVE)
        if active:
            self.bump_recall([m.id for m in active])
            return active
        archived = self.search(text, scopes, k, STATUS_ARCHIVED)
        if not archived:
            return []
        now = self._clock()
        self.bump_recall([m.id for m in archived])  # also flips status='active'
        return [dataclasses.replace(m, status=STATUS_ACTIVE,
                                    recallHits=m.recallHits + 1, lastUsedAt=now)
                for m in archived]

    def _search_any_status(self, text: str, scopes: list[str], k: int) -> list[Memory]:
        """Scope-filtered FTS search across ALL statuses (active + archived),
        bm25-ranked, best first. Empty when text yields no query or scopes is empty."""
        fuzzy = from_user_text(text)
        if fuzzy is None or not scopes:
            return []
        placeholders = ",".join("?" for _ in scopes)
        sql = (
            "SELECT m.* FROM memories m JOIN memories_fts f ON f.rowid=m.id "
            f"WHERE memories_fts MATCH ? AND m.scope IN ({placeholders}) "
            "ORDER BY bm25(memories_fts) ASC LIMIT ?"
        )
        try:
            rows = self._conn.execute(sql, (fuzzy, *scopes, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._to_memory(r) for r in rows]

    def retrieve_explicit(self, text: str, scopes: list[str], k: int = RETRIEVE_K) -> list[Memory]:
        """Explicit on-demand recall: active + archived union, bm25-ranked, ungated
        (the model asked, so no relevance pre-filter beyond k). Revives archived hits
        and bumps recallHits on everything served; returns post-revive state."""
        rows = self._search_any_status(text, scopes, k)
        if not rows:
            return []
        now = self._clock()
        self.bump_recall([m.id for m in rows])  # also flips status='active' on archived hits
        return [dataclasses.replace(m, status=STATUS_ACTIVE,
                                    recallHits=m.recallHits + 1, lastUsedAt=now)
                for m in rows]

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
