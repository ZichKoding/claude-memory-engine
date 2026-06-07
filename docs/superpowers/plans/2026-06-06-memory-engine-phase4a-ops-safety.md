# Memory Engine — Phase 4a (Ops & Safety) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Red-team this plan with the `plan-redteam` agent before implementing.**

**Goal:** Make the engine safe to rely on day-to-day: a `SessionStart` hook that keeps the DB tidy and backed up on every (re)open, a first-class kill switch, and boot-time corruption recovery — all fail-open so they can never block a session.

**Architecture:** Build on the merged Phases 1–3. Add a `backup` module (stale-gated `VACUUM INTO` snapshots + pruning), a `control` module (kill switch), a boot-time corruption check/restore in `db`, and a `session-init` CLI subcommand that the `SessionStart` hook runs (kill-switch check → recover-if-corrupt → archival sweep → backup-if-stale). Per-turn paths (`inject`) gain only a cheap kill-switch check; the *expensive* maintenance (integrity check, backup) runs once at session start, not per turn.

**Tech Stack:** Python 3 (stdlib `sqlite3`, `pathlib`, `shutil`, `os`, `glob`); `pytest`; `uv`. Windows-clean.

**Reference:** Spec `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md` (Durability, Error Handling, Kill switch sections). Conventions: `CLAUDE.md`. This is **Phase 4 Half A (ops/safety)**; Half B (calibrated relevance gating, optional `consolidate`) is data-dependent and deferred until after a real-usage period.

**Verified contract (from Phase 2 research):** `SessionStart` hook config lives in `settings.json` `hooks.SessionStart`; `matcher` filters on `source` (`startup`/`resume`/`clear`/`compact`); stdin includes `source`, `cwd`, `session_id`, `transcript_path`, `hook_event_name`; default command timeout 600s (ample). It fires on **resume**, so maintenance runs when you reopen. Plain stdout / `hookSpecificOutput.additionalContext` can inject context, but this hook injects nothing — it's silent maintenance.

**Key design choices (call out for red-team):**
- Corruption recovery runs **only at boot** (`session-init`), NOT in `connect()` — keeps the per-turn `inject` path cheap. `inject`/`recall` stay fail-open if corruption appears mid-session.
- Backup is **stale-gated** (skip if a backup is younger than N hours) so frequent restarts don't spam backups.
- Kill switch is checked by **both** `inject` (per turn) and `session-init` (boot); when set, both no-op and exit 0.
- Time is **injected** (`now_ms` param) for testability, mirroring the repo's `clock` seam.

---

## File Structure

```
src/memory_engine/
  paths.py      # MODIFY: backups_dir(), disabled_flag_path()
  control.py    # NEW: is_disabled()
  backup.py     # NEW: backup_if_stale()
  db.py         # MODIFY: recover_if_corrupt()
  cli.py        # MODIFY: gate `inject` on is_disabled(); add `session-init` subcommand
tests/
  test_control.py            # NEW
  test_backup.py             # NEW
  test_db_recovery.py        # NEW
  test_cli_session_init.py   # NEW
```

---

### Task 1: Kill switch (`control.py`)

**Files:** Create `src/memory_engine/control.py`, modify `src/memory_engine/paths.py`; Test `tests/test_control.py`.

- [ ] **Step 1: Failing test** `tests/test_control.py`:
```python
# tests/test_control.py
from pathlib import Path
import memory_engine.control as control


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    assert control.is_disabled() is True


def test_disabled_via_sentinel_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)
    assert control.is_disabled() is False
    flag = tmp_path / ".claude" / "memory" / "DISABLED"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    assert control.is_disabled() is True


def test_enabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)
    assert control.is_disabled() is False
```

- [ ] **Step 2: Run, verify FAIL** — `uv run pytest tests/test_control.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement** `src/memory_engine/control.py`:
```python
# src/memory_engine/control.py
"""Kill switch. When set, all hooks/tools no-op so the engine instantly reverts to
'off' with zero side effects."""
import os
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}


def is_disabled() -> bool:
    """True if disabled via the MEMORY_ENGINE_DISABLED env var or a sentinel file at
    ~/.claude/memory/DISABLED."""
    if os.environ.get("MEMORY_ENGINE_DISABLED", "").strip().lower() in _TRUTHY:
        return True
    return (Path.home() / ".claude" / "memory" / "DISABLED").exists()
```

And add to `src/memory_engine/paths.py`:
```python
def backups_dir() -> str:
    """`~/.claude/memory/backups/`, created if missing."""
    directory = Path.home() / ".claude" / "memory" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)
```

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_control.py -q` (3 passed); full suite green.
- [ ] **Step 5: Commit** — `feat: kill switch (env var / sentinel file) + backups_dir` (with trailer).

---

### Task 2: Backup (`backup.py`)

**Files:** Create `src/memory_engine/backup.py`; Test `tests/test_backup.py`.

- [ ] **Step 1: Failing test** `tests/test_backup.py`:
```python
# tests/test_backup.py
import sqlite3
from pathlib import Path
from memory_engine.backup import backup_if_stale

HOUR_MS = 3_600_000


def _make_db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()


def test_creates_backup_when_none_exists(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    made = backup_if_stale(db, str(bdir), now_ms=1_000 * HOUR_MS)
    assert made is True
    backups = list(Path(bdir).glob("memory-*.db"))
    assert len(backups) == 1
    # the backup is a valid, queryable copy
    assert sqlite3.connect(str(backups[0])).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_skips_when_recent_backup_exists(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    assert backup_if_stale(db, bdir, now_ms=1_000 * HOUR_MS) is True
    # 1h later (< 24h stale window) → skip
    assert backup_if_stale(db, bdir, now_ms=1_001 * HOUR_MS) is False
    assert len(list(Path(bdir).glob("memory-*.db"))) == 1


def test_makes_new_backup_when_stale(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    backup_if_stale(db, bdir, now_ms=1_000 * HOUR_MS)
    # 25h later → stale → new backup
    assert backup_if_stale(db, bdir, now_ms=1_025 * HOUR_MS) is True
    assert len(list(Path(bdir).glob("memory-*.db"))) == 2


def test_prunes_to_retention(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    # 10 stale-spaced backups, retention default 7 → keep the NEWEST 7 (not the oldest)
    for i in range(10):
        backup_if_stale(db, bdir, now_ms=(1_000 + i * 25) * HOUR_MS)
    stamps = sorted(int(p.stem.split("memory-")[1]) for p in Path(bdir).glob("memory-*.db"))
    assert stamps == [(1_000 + i * 25) * HOUR_MS for i in range(3, 10)]


def test_missing_db_is_noop(tmp_path):
    assert backup_if_stale(str(tmp_path / "nope.db"), str(tmp_path / "b"), now_ms=HOUR_MS) is False
```

- [ ] **Step 2: Run, verify FAIL** — ModuleNotFoundError.

- [ ] **Step 3: Implement** `src/memory_engine/backup.py`:
```python
# src/memory_engine/backup.py
"""Stale-gated SQLite backups: VACUUM INTO a timestamped copy at most once per window,
keeping the newest N. Time is injected (now_ms) for testability."""
import os
import sqlite3
from pathlib import Path

BACKUP_STALE_HOURS = 24
BACKUP_RETENTION = 7
_HOUR_MS = 3_600_000


def backup_if_stale(db_path: str, backups_dir: str, *, now_ms: int,
                    stale_hours: int = BACKUP_STALE_HOURS,
                    retention: int = BACKUP_RETENTION) -> bool:
    """If `db_path` exists and the newest backup is older than `stale_hours` (or there is
    none), write a fresh `memory-<now_ms>.db` snapshot via VACUUM INTO and prune to the
    newest `retention`. Returns True if a backup was made. Never raises (best-effort)."""
    try:
        if not os.path.exists(db_path):
            return False
        bdir = Path(backups_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        existing = sorted(bdir.glob("memory-*.db"), key=lambda p: (_stamp_of(p) or 0))
        if existing:
            newest_ms = _stamp_of(existing[-1])
            if newest_ms is not None and (now_ms - newest_ms) < stale_hours * _HOUR_MS:
                return False
        dest = bdir / f"memory-{now_ms}.db"
        src = sqlite3.connect(db_path)
        try:
            src.execute("VACUUM INTO ?", (str(dest),))
        finally:
            src.close()
        _prune(bdir, retention)
        return True
    except (OSError, sqlite3.Error):
        return False  # backups are best-effort; never block boot


def _stamp_of(path: Path):
    try:
        return int(path.stem.split("memory-", 1)[1])
    except (ValueError, IndexError):
        return None


def _prune(bdir: Path, retention: int) -> None:
    backups = sorted(bdir.glob("memory-*.db"), key=lambda p: (_stamp_of(p) or 0))
    for old in backups[:-retention] if retention > 0 else backups:
        old.unlink(missing_ok=True)
```

- [ ] **Step 4: Run, verify PASS** (5 passed); full suite green.
- [ ] **Step 5: Commit** — `feat: stale-gated VACUUM INTO backups with retention pruning`.

---

### Task 3: Corruption recovery (`db.recover_if_corrupt`)

**Files:** Modify `src/memory_engine/db.py`; Test `tests/test_db_recovery.py`.

- [ ] **Step 1: Failing test** `tests/test_db_recovery.py`:
```python
# tests/test_db_recovery.py
import sqlite3
from pathlib import Path
from memory_engine.db import connect, recover_if_corrupt


def _good_db(path):
    c = connect(path)  # creates schema
    c.execute("INSERT INTO memories(scope,type,name,description,body,normalizedKey,"
              "captureHits,recallHits,lastUsedAt,status,createdAt,updatedAt,source) "
              "VALUES('global','fact','n','d','b','k',1,0,1,'active',1,1,'manual')")
    c.commit(); c.close()


def test_healthy_db_untouched(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    result = recover_if_corrupt(db, str(tmp_path / "backups"))
    assert result == "ok"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_corrupt_db_restored_from_backup(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    bdir = tmp_path / "backups"; bdir.mkdir()
    # take a good backup, then corrupt the live DB
    sqlite3.connect(db).execute("VACUUM INTO ?", (str(bdir / "memory-1000.db"),))
    Path(db).write_bytes(b"this is not a sqlite database at all")
    result = recover_if_corrupt(db, str(bdir))
    assert result == "restored"
    # live DB is healthy again with the backup's row
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    # the corrupt file was quarantined, not deleted
    assert list(tmp_path.glob("m.db.corrupt-*"))


def test_restore_skips_corrupt_newest_backup(tmp_path):
    # newest snapshot is corrupt, an older one is good → must restore from the GOOD one,
    # not blindly copy the newest (which would leave the live DB corrupt).
    db = str(tmp_path / "m.db"); _good_db(db)
    bdir = tmp_path / "backups"; bdir.mkdir()
    sqlite3.connect(db).execute("VACUUM INTO ?", (str(bdir / "memory-1000.db"),))  # good
    (bdir / "memory-2000.db").write_bytes(b"corrupt snapshot, not a db")            # newest, bad
    Path(db).write_bytes(b"garbage")
    assert recover_if_corrupt(db, str(bdir)) == "restored"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_corrupt_db_no_backup_recreates_empty(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    Path(db).write_bytes(b"garbage")
    result = recover_if_corrupt(db, str(tmp_path / "backups"))  # no backups dir/content
    assert result == "recreated"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0  # fresh schema


def test_missing_db_is_ok(tmp_path):
    assert recover_if_corrupt(str(tmp_path / "nope.db"), str(tmp_path / "b")) == "ok"
```

- [ ] **Step 2: Run, verify FAIL** — `ImportError: cannot import name 'recover_if_corrupt'`.

- [ ] **Step 3: Implement** — add to `src/memory_engine/db.py` (`import os, shutil` at top):
```python
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
```

- [ ] **Step 4: Run, verify PASS** (4 passed); full suite green.
- [ ] **Step 5: Commit** — `feat: boot-time corruption recovery (quarantine + restore/recreate)`.

---

### Task 4: `session-init` subcommand + gate `inject` on the kill switch

**Files:** Modify `src/memory_engine/cli.py`; Test `tests/test_cli_session_init.py`.

- [ ] **Step 1: Failing test** `tests/test_cli_session_init.py`:
```python
# tests/test_cli_session_init.py
import io, json, sqlite3
from pathlib import Path
import pytest
from memory_engine.cli import main


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    # session-init touches ~/.claude (backups_dir + kill-switch flag) regardless of --db,
    # so redirect HOME to tmp and clear the env flag — keep tests off the real home dir.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)


def _seed_old(db, lastUsedAt):
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "n",
          "--description", "d", "--body", "alpha bravo charlie"])
    c = sqlite3.connect(db); c.execute("UPDATE memories SET lastUsedAt=?", (lastUsedAt,)); c.commit(); c.close()


def _stdin(monkeypatch, obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(obj)))


def test_session_init_runs_archival_and_exits_zero(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)  # ancient → should archive
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    rc = main(["--db", db, "session-init"])
    assert rc == 0
    status = sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0]
    assert status == "archived"


def test_session_init_tolerates_garbage_stdin(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert main(["--db", db, "session-init"]) == 0  # never blocks session start
    # garbage stdin doesn't stop maintenance — archival still ran
    assert sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0] == "archived"


def test_session_init_noop_when_disabled(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    assert main(["--db", db, "session-init"]) == 0
    # disabled → archival did NOT run
    assert sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0] == "active"


def test_inject_noop_when_disabled(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "Iowa",
          "--description", "d", "--body", "alpha bravo charlie"])
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "alpha bravo charlie", "cwd": str(tmp_path)})))
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--db", db, "inject"])
    assert rc == 0
    assert buf.getvalue().strip() == ""  # disabled → injects nothing
```

- [ ] **Step 2: Run, verify FAIL** — `session-init` unknown; `inject` still injects when disabled.

- [ ] **Step 3: Implement** in `src/memory_engine/cli.py`:

(a) Imports: `from memory_engine.control import is_disabled`; `from memory_engine.backup import backup_if_stale`; `from memory_engine.db import recover_if_corrupt`; `from memory_engine.paths import default_db_path, backups_dir`.

(b) Register the subcommand near the others: `sub.add_parser("session-init")`.

(c) At the TOP of `_run_inject` (first line of the `try`), add the kill-switch short-circuit:
```python
        if is_disabled():
            return 0
```

(d) Add the `session-init` dispatch as an early return (next to the `inject` short-circuit, before the generic `connect`):
```python
    if args.cmd == "session-init":
        return _run_session_init(args.db)
```

(e) Add the helper (module level), fully fail-open:
```python
def _run_session_init(db: str | None) -> int:
    """SessionStart hook body: kill-switch check → recover-if-corrupt → archival sweep →
    backup-if-stale. ALWAYS returns 0 — must never block a session from starting."""
    try:
        if is_disabled():
            return 0
        try:
            _ = sys.stdin.read()  # drain stdin (source/cwd available but unused here)
        except Exception:
            pass
        path = db or default_db_path()
        recover_if_corrupt(path, backups_dir())
        conn = connect(path)
        try:
            MemoryRepository(conn, clock=_real_clock).run_archival_sweep()
        finally:
            conn.close()
        backup_if_stale(path, backups_dir(), now_ms=_real_clock())
        return 0
    except Exception:
        return 0  # fail-open: never block session start
```

Do NOT change other branches.

- [ ] **Step 4: Run, verify PASS** (4 passed); full suite green.
- [ ] **Step 5: Commit** — `feat: session-init SessionStart hook (recover+sweep+backup) + kill-switch gate on inject`.

---

### Task 5: Wiring — SessionStart hook, snippet, docs, live test

Controller/manual task.

- [ ] **Step 1:** `uv tool install --editable --force .`; `memory-engine session-init --help` shows the subcommand.
- [ ] **Step 2: Smoke** by hand: pipe `{"source":"startup","cwd":"<dir>"}` to `memory-engine session-init`; `echo $?` → 0; confirm a backup appears under `~/.claude/memory/backups/`.
- [ ] **Step 3:** Add the `SessionStart` hook to `~/.claude/settings.json` AND `claude-integration/settings.snippet.json`:
```json
"SessionStart": [
  { "matcher": "*", "hooks": [ { "type": "command", "command": "memory-engine", "args": ["session-init"], "timeout": 60 } ] }
]
```
- [ ] **Step 4:** Kill-switch doc: add to `claude-integration/INSTALL.md` off-switch table — set `MEMORY_ENGINE_DISABLED=1` or `touch ~/.claude/memory/DISABLED` to disable everything instantly; update repo `CLAUDE.md` Phase 4 status.
- [ ] **Step 5: Live test** (DONE_WITH_CONCERNS → human restarts): restart Claude Code; confirm a fresh backup appears under `~/.claude/memory/backups/` and the session works normally. Then `touch ~/.claude/memory/DISABLED`, restart, confirm no injection fires (kill switch); remove the flag to re-enable.
- [ ] **Step 6: Commit** the snippet + docs.

---

## Phase 4a Done — Definition of Done

- Full unit suite green.
- `SessionStart` hook runs archival sweep + stale-gated backup on every (re)open, fail-open (exit 0 always); a fresh backup appears under `~/.claude/memory/backups/`.
- Kill switch (`MEMORY_ENGINE_DISABLED` env or `~/.claude/memory/DISABLED` file) makes `inject` and `session-init` no-op instantly; documented.
- Boot-time corruption recovery: a corrupt DB is quarantined and restored from the newest
  **healthy** backup, or recreated empty if none — never crashes a session. (Detects gross
  corruption — unreadable / "not a database" / malformed / truncated — via `PRAGMA
  integrity_check`; subtle in-page corruption may pass and is out of scope.)
- Per-turn `inject` stays cheap (only a kill-switch check added; integrity/backup run at boot, not per turn).
- No new runtime dependency.

## Deferred to Phase 4b (data-dependent — after a real-usage period)
- Calibrated relevance gating for auto-retrieve (needs a real corpus to tune; corpus-normalized, not an absolute bm25 number).
- Optional `consolidate` near-duplicate cleanup; optional session-end capture sweep fallback.
- Counter-snapshot tidy (active-path pre-bump returned objects) — cosmetic.
