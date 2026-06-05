# Memory Engine — Phase 1 (Core + DB) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone storage + capture + dedup core of the Claude Code memory engine — a SQLite DB with FTS5, scope-aware `captureOrMerge` dedup, the pure-logic helpers, and a CLI to exercise it — with zero Claude Code wiring.

**Architecture:** Dependency-free Python modules over stdlib `sqlite3` + FTS5. Pure-logic helpers (normalizer, FTS query sanitizer, candidate parser) are isolated and unit-tested in isolation. A `MemoryRepository` wraps a `sqlite3.Connection` with an injected clock and implements the two-stage dedup (hard-key, then fuzzy-FTS constrained to same scope+type). A small argparse CLI (`add`/`list`/`search`/`sweep`/`stats`) drives it manually.

**Tech Stack:** Python 3 (stdlib `sqlite3`, `hashlib`, `re`, `argparse`, `dataclasses`); `pytest` for tests. Windows-clean (`python -m pytest`).

**Reference:** Spec at `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md`. This plan covers the spec's **Build Phasing → Phase 1** only. Retrieval policy/bm25 gating, hooks, capture wiring, backups, and kill switch are Phases 2–4.

---

## File Structure

```
claude-memory-engine/
  pyproject.toml                  # package + pytest config
  requirements-dev.txt            # pytest
  .gitignore
  src/memory_engine/
    __init__.py
    db.py                         # connect(), SCHEMA_SQL, init_db()
    normalizer.py                 # normalize_body(), normalized_key()
    fts_query.py                  # from_user_text()
    parser.py                     # Parsed, parse_candidates()
    models.py                     # Memory, Outcome
    repository.py                 # MemoryRepository (capture_or_merge, search, sweep, bumps)
    cli.py                        # argparse entrypoint
  tests/
    conftest.py                   # in-memory DB fixture, fixed clock
    test_normalizer.py
    test_fts_query.py
    test_parser.py
    test_db.py
    test_repository_capture.py
    test_repository_fuzzy.py
    test_repository_lifecycle.py
    test_cli.py
```

Each file has one responsibility. `repository.py` is the only place SQL meets policy; the pure helpers never touch the DB.

---

### Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `.gitignore`, `src/memory_engine/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
*.db
*.db-wal
*.db-shm
backups/
.venv/
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```text
pytest>=8.0
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "memory-engine"
version = "0.1.0"
description = "Shenron-style memory engine for Claude Code"
requires-python = ">=3.10"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Create empty `src/memory_engine/__init__.py` and `tests/__init__.py`**

```python
# src/memory_engine/__init__.py
```

```python
# tests/__init__.py
```

- [ ] **Step 5: Install dev deps and verify pytest runs (collects nothing)**

Run: `python -m pip install -r requirements-dev.txt; python -m pytest -q`
Expected: `no tests ran` (exit 5) — confirms pytest + pythonpath wiring.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold memory-engine package and pytest config"
```

---

### Task 1: Normalizer (pure logic)

**Files:**
- Create: `src/memory_engine/normalizer.py`
- Test: `tests/test_normalizer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalizer.py
from memory_engine.normalizer import normalize_body, normalized_key


def test_normalize_collapses_punctuation_case_whitespace():
    assert normalize_body("  Lives in IOWA!! ") == "lives in iowa"


def test_normalize_empty():
    assert normalize_body("   ") == ""


def test_key_is_stable_and_hex_40():
    k = normalized_key("global", "fact", "Lives in Iowa")
    assert k == normalized_key("global", "FACT", "lives in iowa.")
    assert len(k) == 40 and all(c in "0123456789abcdef" for c in k)


def test_key_differs_by_scope():
    assert normalized_key("global", "fact", "x y z") != normalized_key("repoA", "fact", "x y z")


def test_key_differs_by_type():
    assert normalized_key("global", "fact", "x y z") != normalized_key("global", "preference", "x y z")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_normalizer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.normalizer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/normalizer.py
"""Pure-logic dedup helpers. No DB, no Android/Claude deps — trivially testable."""
import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_WHITESPACE = re.compile(r"\s+")


def normalize_body(body: str) -> str:
    """Lowercase, strip non-alphanumerics to spaces, collapse whitespace, trim."""
    s = _NON_ALNUM.sub(" ", body.lower())
    s = _WHITESPACE.sub(" ", s)
    return s.strip()


def normalized_key(scope: str, type_: str, body: str) -> str:
    """Hard-dedup key: sha1(scope | type | normalized body). Scope+type folded in
    so a project capture never collides with a global row, and the same words under
    different facets stay distinct."""
    raw = f"{scope.lower()}|{type_.lower()}|{normalize_body(body)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_normalizer.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/normalizer.py tests/test_normalizer.py
git commit -m "feat: scope-aware memory normalizer and dedup key"
```

---

### Task 2: FTS query sanitizer (pure logic)

**Files:**
- Create: `src/memory_engine/fts_query.py`
- Test: `tests/test_fts_query.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fts_query.py
from memory_engine.fts_query import from_user_text


def test_builds_or_prefix_expression():
    assert from_user_text("Lives in Iowa") == '"lives"* OR "iowa"*'  # "in" dropped (<3 chars)


def test_strips_punctuation_and_dedupes():
    assert from_user_text("cat, cat? CAT!") == '"cat"*'


def test_caps_at_eight_tokens():
    expr = from_user_text("alpha bravo charlie delta echo foxtrot golf hotel india juliet")
    assert expr.count(" OR ") == 7  # 8 tokens => 7 separators


def test_returns_none_when_nothing_usable():
    assert from_user_text("a, b? c!") is None
    assert from_user_text("   ") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fts_query.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.fts_query'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/fts_query.py
"""Turn free-form text into a safe FTS5 MATCH expression. Shared by capture-time
fuzzy dedup (Phase 1) and retrieval (Phase 2) so both normalize identically."""
import re

_NON_ALNUM_WS = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")
MIN_TOKEN_LEN = 3
MAX_TOKENS = 8


def from_user_text(raw: str) -> str | None:
    """OR-joined prefix tokens, e.g. '"lives"* OR "iowa"*'. None when nothing usable."""
    cleaned = _NON_ALNUM_WS.sub(" ", raw.lower())
    tokens: list[str] = []
    for tok in _WHITESPACE.split(cleaned):
        if len(tok) >= MIN_TOKEN_LEN and tok not in tokens:
            tokens.append(tok)
        if len(tokens) >= MAX_TOKENS:
            break
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fts_query.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/fts_query.py tests/test_fts_query.py
git commit -m "feat: FTS5 MATCH expression sanitizer"
```

---

### Task 3: Candidate parser (pure logic)

**Files:**
- Create: `src/memory_engine/parser.py`
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parser.py
from memory_engine.parser import parse_candidates, Parsed


def test_none_returns_empty():
    assert parse_candidates("none") == []
    assert parse_candidates("  NONE  ") == []


def test_parses_valid_line():
    out = "MEMORY|fact|Lives in Iowa|Where the user lives|The user lives in Iowa."
    assert parse_candidates(out) == [
        Parsed("fact", "Lives in Iowa", "Where the user lives", "The user lives in Iowa.")
    ]


def test_ignores_preamble_and_bad_lines():
    out = "Sure!\nMEMORY|fact|A|B|C\n- a bullet\nMEMORY|bogus|x|y|z\nMEMORY|fact|only|three"
    assert parse_candidates(out) == [Parsed("fact", "A", "B", "C")]


def test_caps_at_five():
    out = "\n".join(f"MEMORY|fact|n{i}|d{i}|b{i}" for i in range(10))
    assert len(parse_candidates(out)) == 5


def test_rejects_overlong_fields():
    out = "MEMORY|fact|" + ("x" * 81) + "|d|b"
    assert parse_candidates(out) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.parser'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/parser.py
"""Parse a model extraction pass into validated memory candidates. Strict and
failure-tolerant: one bad line is dropped, never fatal."""
from dataclasses import dataclass

ALLOWED_TYPES = {"fact", "preference", "person", "goal", "project", "other"}
MAX_MEMORIES_PER_SWEEP = 5
MAX_NAME_LEN = 80
MAX_DESCRIPTION_LEN = 200
MAX_BODY_LEN = 500


@dataclass(frozen=True)
class Parsed:
    type: str
    name: str
    description: str
    body: str


def parse_candidates(output: str) -> list[Parsed]:
    if output.strip().upper() == "NONE":
        return []
    results: list[Parsed] = []
    for raw in output.splitlines():
        if len(results) >= MAX_MEMORIES_PER_SWEEP:
            break
        line = raw.strip()
        if not line.startswith("MEMORY|"):
            continue
        parts = line[len("MEMORY|"):].split("|")
        if len(parts) != 4:
            continue
        type_ = parts[0].strip().lower()
        name, description, body = parts[1].strip(), parts[2].strip(), parts[3].strip()
        if type_ not in ALLOWED_TYPES:
            continue
        if not name or len(name) > MAX_NAME_LEN:
            continue
        if not description or len(description) > MAX_DESCRIPTION_LEN:
            continue
        if not body or len(body) > MAX_BODY_LEN:
            continue
        results.append(Parsed(type_, name, description, body))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parser.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/parser.py tests/test_parser.py
git commit -m "feat: strict pipe-delimited candidate parser"
```

---

### Task 4: Models

**Files:**
- Create: `src/memory_engine/models.py`

- [ ] **Step 1: Write the implementation (no test — pure data shapes, exercised by repository tests)**

```python
# src/memory_engine/models.py
"""Row + outcome shapes for the memory store."""
from dataclasses import dataclass

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


@dataclass(frozen=True)
class Memory:
    id: int
    scope: str
    type: str
    name: str
    description: str
    body: str
    normalizedKey: str
    captureHits: int
    recallHits: int
    lastUsedAt: int
    status: str
    createdAt: int
    updatedAt: int
    source: str | None


@dataclass(frozen=True)
class Inserted:
    id: int


@dataclass(frozen=True)
class MergedByKey:
    id: int


@dataclass(frozen=True)
class MergedByFuzzy:
    id: int
    from_archived: bool = False


Outcome = Inserted | MergedByKey | MergedByFuzzy
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "import sys; sys.path.insert(0,'src'); import memory_engine.models; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/memory_engine/models.py
git commit -m "feat: Memory row and capture Outcome models"
```

---

### Task 5: DB schema & connection

**Files:**
- Create: `src/memory_engine/db.py`
- Test: `tests/test_db.py`, `tests/conftest.py`

- [ ] **Step 1: Write the failing test + fixture**

```python
# tests/conftest.py
import sqlite3
import pytest
from memory_engine.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def clock():
    """Mutable fake clock returning milliseconds. Tests bump .now to advance time."""
    class Clock:
        now = 1_000_000_000_000  # fixed epoch ms
        def __call__(self) -> int:
            return self.now
    return Clock()
```

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.db'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: SQLite schema with FTS5 external-content table and sync triggers"
```

---

### Task 6: Repository — insert + hard-key dedup

**Files:**
- Create: `src/memory_engine/repository.py`
- Test: `tests/test_repository_capture.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repository_capture.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed
from memory_engine.models import Inserted, MergedByKey


def test_insert_then_hardkey_merge(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    p = Parsed("fact", "Iowa", "where", "The user lives in Iowa.")

    first = repo.capture_or_merge(p, scope="global")
    assert isinstance(first, Inserted)

    # Same body modulo punctuation/case => same hard key => merge, no new row.
    p2 = Parsed("fact", "Iowa", "where", "the user lives in iowa!!")
    clock.now += 5
    second = repo.capture_or_merge(p2, scope="global")
    assert isinstance(second, MergedByKey)
    assert second.id == first.id

    row = conn.execute("SELECT captureHits, lastUsedAt FROM memories WHERE id=?", (first.id,)).fetchone()
    assert row["captureHits"] == 2
    assert row["lastUsedAt"] == clock.now
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1


def test_different_scope_does_not_merge(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    p = Parsed("fact", "Iowa", "where", "Lives in Iowa")
    a = repo.capture_or_merge(p, scope="global")
    b = repo.capture_or_merge(p, scope="repoA")
    assert a.id != b.id
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repository_capture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.repository'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repository_capture.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_capture.py
git commit -m "feat: capture_or_merge stage 1 (hard-key dedup) + insert"
```

---

### Task 7: Repository — fuzzy-FTS dedup (scope+type aware, active then archived)

**Files:**
- Modify: `src/memory_engine/repository.py`
- Test: `tests/test_repository_fuzzy.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repository_fuzzy.py -q`
Expected: FAIL — both `MergedByFuzzy` assertions fail (currently inserts a 2nd row)

- [ ] **Step 3: Add the fuzzy stage + search helper to `repository.py`**

Replace the `# Stage 2 added in Task 7.` comment in `capture_or_merge` with:

```python
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
```

Then add these methods to the class:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repository_fuzzy.py tests/test_repository_capture.py -q`
Expected: PASS (5 passed) — fuzzy tests pass, Task 6 tests still green

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_fuzzy.py
git commit -m "feat: capture_or_merge stage 2 (fuzzy FTS dedup, scope+type aware, archived revive)"
```

---

### Task 8: Repository — archival sweep + search + recall bump

**Files:**
- Modify: `src/memory_engine/repository.py`
- Test: `tests/test_repository_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repository_lifecycle.py -q`
Expected: FAIL — `ImportError: cannot import name 'ARCHIVE_AFTER_DAYS'` / missing methods

- [ ] **Step 3: Add constants, `search`, `bump_recall`, `run_archival_sweep` to `repository.py`**

Add at module top (below imports):

```python
ARCHIVE_AFTER_DAYS = 365
_DAY_MS = 86_400_000
```

Add `from memory_engine.models import Memory` to the models import line (so it becomes:
`from memory_engine.models import (STATUS_ACTIVE, STATUS_ARCHIVED, Inserted, Memory, MergedByFuzzy, MergedByKey, Outcome,)`).

Add these methods to the class:

```python
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
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `python -m pytest -q`
Expected: PASS (all tests green across every file)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_lifecycle.py
git commit -m "feat: archival sweep, scope-filtered search, recall bump with revive"
```

---

### Task 9: CLI to exercise the engine

**Files:**
- Create: `src/memory_engine/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import json
from memory_engine.cli import main


def test_add_then_search_roundtrip(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    rc = main(["--db", db, "add", "--scope", "global", "--type", "fact",
               "--name", "Iowa", "--description", "where", "--body", "Lives in Iowa"])
    assert rc == 0

    capsys.readouterr()
    rc = main(["--db", db, "search", "--scopes", "global", "--query", "iowa"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Iowa" in out


def test_stats_reports_counts(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact",
          "--name", "n", "--description", "d", "--body", "alpha beta gamma"])
    capsys.readouterr()
    rc = main(["--db", db, "stats"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 1
    assert data["active"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/cli.py
"""Manual driver for Phase 1 — no Claude Code wiring. Real-clock production use."""
import argparse
import json
import time

from memory_engine.db import connect
from memory_engine.parser import Parsed
from memory_engine.repository import MemoryRepository


def _real_clock() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    for f in ("scope", "type", "name", "description", "body"):
        p_add.add_argument(f"--{f}", required=True)

    p_search = sub.add_parser("search")
    p_search.add_argument("--scopes", required=True, help="comma-separated")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=5)

    sub.add_parser("list")
    sub.add_parser("stats")
    sub.add_parser("sweep")

    args = parser.parse_args(argv)
    conn = connect(args.db)
    repo = MemoryRepository(conn, clock=_real_clock)

    if args.cmd == "add":
        outcome = repo.capture_or_merge(
            Parsed(args.type, args.name, args.description, args.body), scope=args.scope)
        print(type(outcome).__name__, getattr(outcome, "id", ""))
    elif args.cmd == "search":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        for m in repo.search(args.query, scopes=scopes, limit=args.limit):
            print(f"[{m.scope}/{m.type}] {m.name} :: {m.body}")
    elif args.cmd == "list":
        for r in conn.execute("SELECT scope,type,name,status FROM memories ORDER BY id"):
            print(f"[{r['scope']}/{r['type']}] {r['name']} ({r['status']})")
    elif args.cmd == "stats":
        total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) c FROM memories WHERE status='active'").fetchone()["c"]
        print(json.dumps({"total": total, "active": active, "archived": total - active}))
    elif args.cmd == "sweep":
        print(json.dumps({"archived": repo.run_archival_sweep()}))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `python -m pytest -q`
Expected: PASS (all tests green)

- [ ] **Step 5: Manual smoke test**

Run:
```bash
python -m memory_engine.cli --db ./smoke.db add --scope global --type fact --name "Iowa" --description "where" --body "The user lives in Iowa"
python -m memory_engine.cli --db ./smoke.db search --scopes global --query "iowa"
python -m memory_engine.cli --db ./smoke.db stats
```
Expected: add prints `Inserted 1`; search prints the Iowa line; stats prints `{"total": 1, "active": 1, "archived": 0}`. Then delete `smoke.db`.

- [ ] **Step 6: Commit**

```bash
git add src/memory_engine/cli.py tests/test_cli.py
git commit -m "feat: CLI driver (add/list/search/stats/sweep)"
```

---

## Phase 1 Done — Definition of Done

- `python -m pytest -q` is fully green.
- CLI smoke test round-trips an add → search → stats.
- No Claude Code wiring exists yet (correct — that's Phase 2).
- `captureHits`/`recallHits`/`lastUsedAt`/`status` all mutate through code, never instructions.
- Scope isolation verified: project captures never merge into global; search respects scope filter.

## What Phases 2–4 add (not in this plan)

- **Phase 2 — Retrieval & wiring:** bm25 threshold gating (`RAG_INJECT_THRESHOLD=-1.0`), `retrieve` (auto) + `retrieve_explicit`, scope resolution via `git rev-parse`, `UserPromptSubmit` hook, `recall_memory` tool.
- **Phase 3 — Capture wiring:** `memory_add` inline tool, session-end sweep hook (the one model call), `memory_edit`/`memory_forget`.
- **Phase 4 — Lifecycle & safety:** boot archival sweep wiring, backup-on-boot-if-stale, kill switch, fail-open hardening, corruption recovery.
