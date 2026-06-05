# Memory Engine — Phase 2a (Auto-Retrieval + UserPromptSubmit Hook) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface relevant cold-store memories into each turn automatically: a `UserPromptSubmit` hook that queries the DB by the user's prompt (top-k bm25-ranked, scope-merged global + current project) and injects the matches as context — fail-open so it can never block a turn.

**Architecture:** Build on the Phase 1 engine (merged to `main`). Add DB-path self-resolution (`~/.claude/memory/memory.db`), scope resolution from the hook's `cwd` (git repo root, cwd fallback), a top-k bm25 `retrieve` policy on the repository (reusing Phase 1's tested `search`), a `<memory>` block formatter, and an `inject` CLI subcommand that speaks the Claude Code `UserPromptSubmit` hook contract. The engine is distributed as a global `uv` tool (`uv tool install --editable`), so the hook command `memory-engine inject` runs in any project. No Claude Code wiring is hardcoded in the engine — the hook config lives in `settings.json`.

**Tech Stack:** Python 3 (stdlib `sqlite3`, `subprocess`, `json`, `pathlib`); `pytest`; `uv`. Windows-clean (`uv run pytest`, `py`).

**Reference:** Spec `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md`. Conventions in `CLAUDE.md`. This plan covers the spec's **Phase 2** *retrieval + UserPromptSubmit* half only. The `recall_memory` MCP server is **Phase 2b**; `SessionStart` archival/backup wiring and the kill switch are **Phase 4**.

**Verified contract (from primary-source research + spike):**
- Hook config: `settings.json` `hooks.UserPromptSubmit[].hooks[]` with `type:"command"`, a `command` string (shell form) or `command`+`args` (exec form), optional `timeout` (UserPromptSubmit default 30s).
- `UserPromptSubmit` stdin JSON: `prompt`, `cwd`, `session_id`, `transcript_path`, `permission_mode`, `hook_event_name`.
- Inject context: exit 0 with JSON `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"<text>"}}` (or plain stdout). Exit 2 blocks+erases the prompt — we must NEVER exit 2.
- No `${HOME}` placeholder and no settings `env` block → the engine self-resolves its DB path.
- Distribution: global `uv tool install --editable` exposes `memory-engine` on PATH (spike-confirmed, runs from any dir).

---

## File Structure

```
src/memory_engine/
  paths.py        # NEW: default_db_path()
  scope.py        # NEW: resolve_scope_key(), scopes_for()
  formatting.py   # NEW: format_memory_block()
  repository.py   # MODIFY: RETRIEVE_K + retrieve (reuses Phase 1 search)
  cli.py          # MODIFY: --db optional (default default_db_path()); add `inject` subcommand
tests/
  test_paths.py            # NEW
  test_scope.py            # NEW
  test_formatting.py       # NEW
  test_repository_retrieve.py  # NEW
  test_cli_inject.py       # NEW
```

---

### Task 1: DB path self-resolution

**Files:** Create `src/memory_engine/paths.py`; Test `tests/test_paths.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py
from pathlib import Path
from memory_engine.paths import default_db_path


def test_default_db_path_under_home_claude(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = default_db_path()
    assert p == str(tmp_path / ".claude" / "memory" / "memory.db")
    # parent dir is created so sqlite can open the file
    assert (tmp_path / ".claude" / "memory").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.paths'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/paths.py
"""Filesystem locations the engine owns. The DB lives in the private global
~/.claude dir (never in any repo). Self-resolved because Claude Code hooks expose
no ${HOME} placeholder and no settings env block."""
from pathlib import Path


def default_db_path() -> str:
    """`~/.claude/memory/memory.db`. Ensures the parent dir exists so sqlite can
    open/create the file. Returns a string path for sqlite3.connect."""
    directory = Path.home() / ".claude" / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / "memory.db")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/paths.py tests/test_paths.py
git commit -m "feat: self-resolved DB path under ~/.claude/memory

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Scope resolution

**Files:** Create `src/memory_engine/scope.py`; Test `tests/test_scope.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scope.py
import os
import subprocess
from memory_engine.scope import resolve_scope_key, scopes_for


def test_resolve_uses_git_toplevel(tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "src"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # From a subdir of the repo, the scope key is the repo root (normcased abspath).
    key = resolve_scope_key(str(sub))
    expected = os.path.normcase(os.path.abspath(str(repo)))
    assert key == expected


def test_resolve_falls_back_to_cwd_when_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    key = resolve_scope_key(str(plain))
    assert key == os.path.normcase(os.path.abspath(str(plain)))


def test_scopes_for_prepends_global(tmp_path):
    plain = tmp_path / "p2"
    plain.mkdir()
    scopes = scopes_for(str(plain))
    assert scopes[0] == "global"
    assert scopes[1] == resolve_scope_key(str(plain))
    assert len(scopes) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scope.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.scope'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/scope.py
"""Resolve the project scope key for a working directory. The key is the git repo
root (so shenron and senku are distinct scopes), normcased+absolute so it's stable;
falls back to the normalized cwd when there's no repo. Reading the path never writes
to the repo."""
import os
import subprocess

GLOBAL_SCOPE = "global"


def resolve_scope_key(cwd: str) -> str:
    """Git repo root for `cwd`, else the normalized `cwd`. Always normcase+abspath
    so the same location yields a byte-stable key across captures and retrievals."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        top = result.stdout.strip()
        if result.returncode == 0 and top:
            return os.path.normcase(os.path.abspath(top))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.normcase(os.path.abspath(cwd))


def scopes_for(cwd: str) -> list[str]:
    """The scopes a retrieval searches when working in `cwd`: global + this project."""
    return [GLOBAL_SCOPE, resolve_scope_key(cwd)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scope.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/scope.py tests/test_scope.py
git commit -m "feat: scope key resolution (git repo root, cwd fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Repository `retrieve` (top-k bm25, scope-merged, revive)

**Files:** Modify `src/memory_engine/repository.py`; Test `tests/test_repository_retrieve.py`.

**Design note:** an absolute bm25 cutoff was rejected during plan red-team — bm25
magnitude scales with corpus size (IDF), so no fixed threshold is stable (measured: a
1-token match scores `0` on a 1-row fixture but `-3.35` on a 51-row corpus). Phase 2a
returns the best-k matches; FTS `MATCH` already requires token overlap and `k` caps the
count. Calibrated relevance gating is deferred to a Phase 4 tuning pass against the real
corpus. This task therefore reuses Phase 1's already-tested `search` — no score plumbing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repository_retrieve.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed


def _add(repo, scope, body, name="n", type_="fact"):
    return repo.capture_or_merge(Parsed(type_, name, "d", body), scope=scope)


def test_retrieve_merges_scopes_and_excludes_others(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    g = _add(repo, "global", "alpha bravo charlie delta echo")
    p = _add(repo, "repoA", "alpha bravo charlie delta echo")
    _add(repo, "repoB", "alpha bravo charlie delta echo")  # competing row, must be excluded
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global", "repoA"], k=10)
    ids = {m.id for m in out}
    assert ids == {g.id, p.id}  # repoB excluded by scope


def test_retrieve_returns_best_k_ordered(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Three matching rows with DISTINCT types so capture-time fuzzy dedup (which is
    # scope+type-constrained) keeps them as separate rows. Retrieval filters on
    # scope+status only (not type), so all three are still searched and bm25-ranked.
    _add(repo, "global", "alpha bravo charlie delta echo foxtrot", type_="fact")  # fullest overlap
    _add(repo, "global", "alpha bravo charlie", type_="preference")
    _add(repo, "global", "alpha", type_="person")
    out = repo.retrieve("alpha bravo charlie delta echo foxtrot", scopes=["global"], k=2)
    assert len(out) == 2                 # capped at k
    assert "foxtrot" in out[0].body      # best (all-6-token) match ranks first


def test_retrieve_bumps_recall_on_served(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    clock.now += 5
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1
    row = conn.execute("SELECT recallHits, lastUsedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["recallHits"] == 1
    assert row["lastUsedAt"] == clock.now


def test_retrieve_active_takes_precedence_over_archived(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Distinct types so BOTH rows survive dedup — else they'd merge into one and this
    # would silently exercise the fallback path instead of active-precedence.
    active = _add(repo, "global", "alpha bravo charlie delta echo", type_="fact")
    archived = _add(repo, "global", "alpha bravo charlie delta echo foxtrot golf", type_="preference")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (archived.id,))
    conn.commit()
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert {m.id for m in out} == {active.id}  # active non-empty → archived never consulted


def test_retrieve_falls_back_to_archived_and_revives(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    out = repo.retrieve("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1
    assert out[0].status == "active"          # returned entity reflects revive
    db = conn.execute("SELECT status FROM memories WHERE id=?", (m.id,)).fetchone()
    assert db["status"] == "active"           # and the row was revived


def test_retrieve_empty_query_returns_empty(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    _add(repo, "global", "alpha bravo charlie")
    assert repo.retrieve("a", scopes=["global"], k=10) == []   # no usable tokens
    assert repo.retrieve("alpha", scopes=[], k=10) == []       # no scopes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repository_retrieve.py -q`
Expected: FAIL — `AttributeError: 'MemoryRepository' object has no attribute 'retrieve'`.

- [ ] **Step 3: Modify `src/memory_engine/repository.py`**

(a) Add this constant next to the existing `ARCHIVE_AFTER_DAYS` / `_DAY_MS`:

```python
RETRIEVE_K = 5
```

(b) Add `import dataclasses` to the top of the file (with the other imports).

(c) Add this method to `MemoryRepository` (after `search`). It reuses the existing
`search(text, scopes, limit, status)` from Phase 1 — no new SQL:

```python
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
```

Do NOT change `capture_or_merge`, `_find_fuzzy_candidate`, `search`, `bump_recall`, `run_archival_sweep`, or `_to_memory`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repository_retrieve.py -q`
Expected: PASS (6 passed). Then `uv run pytest -q` — full suite still green.

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_retrieve.py
git commit -m "feat: top-k bm25 retrieve (scope-merged, archived fallback + revive)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `<memory>` block formatter

**Files:** Create `src/memory_engine/formatting.py`; Test `tests/test_formatting.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_formatting.py
from memory_engine.formatting import format_memory_block
from memory_engine.models import Memory


def _mem(id_, scope, type_, name, body):
    return Memory(id=id_, scope=scope, type=type_, name=name, description="d",
                  body=body, normalizedKey="k", captureHits=1, recallHits=0,
                  lastUsedAt=0, status="active", createdAt=0, updatedAt=0, source=None)


def test_empty_list_returns_empty_string():
    assert format_memory_block([]) == ""


def test_formats_wrapped_block():
    out = format_memory_block([
        _mem(1, "global", "fact", "Lives in Iowa", "The user lives in Iowa"),
        _mem(2, "repoA", "preference", "Tabs", "Prefers tabs"),
    ])
    assert out == (
        "<memory>\n"
        "- [global/fact] Lives in Iowa: The user lives in Iowa\n"
        "- [repoA/preference] Tabs: Prefers tabs\n"
        "</memory>"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_formatting.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_engine.formatting'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory_engine/formatting.py
"""Render retrieved memories into the context block injected by the hook."""
from memory_engine.models import Memory


def format_memory_block(memories: list[Memory]) -> str:
    """A `<memory>`-wrapped bullet list, or "" when there's nothing to inject
    (caller emits no additionalContext on empty)."""
    if not memories:
        return ""
    lines = [f"- [{m.scope}/{m.type}] {m.name}: {m.body}" for m in memories]
    return "<memory>\n" + "\n".join(lines) + "\n</memory>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_formatting.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/formatting.py tests/test_formatting.py
git commit -m "feat: <memory> context block formatter

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: CLI `inject` subcommand (UserPromptSubmit hook entry, fail-open)

**Files:** Modify `src/memory_engine/cli.py`; Test `tests/test_cli_inject.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_inject.py
import io
import json
from memory_engine.cli import main


def _seed(db):
    main(["--db", db, "add", "--scope", "global", "--type", "fact",
          "--name", "Iowa", "--description", "where", "--body",
          "alpha bravo charlie delta echo"])


def _run_inject(monkeypatch, capsys, db, stdin_obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    rc = main(["--db", db, "inject"])
    return rc, capsys.readouterr().out


def test_inject_emits_additional_context_on_match(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {
        "prompt": "alpha bravo charlie delta echo", "cwd": str(tmp_path)})
    assert rc == 0
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "<memory>" in payload["hookSpecificOutput"]["additionalContext"]
    assert "Iowa" in payload["hookSpecificOutput"]["additionalContext"]


def test_inject_emits_nothing_on_no_match(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {
        "prompt": "zulu yankee xray whiskey victor", "cwd": str(tmp_path)})
    assert rc == 0
    assert out.strip() == ""   # no additionalContext when nothing matched


def test_inject_failopen_on_malformed_stdin(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("this is not json"))
    rc = main(["--db", db, "inject"])
    out = capsys.readouterr().out
    assert rc == 0            # NEVER non-zero (exit 2 would erase the prompt)
    assert out.strip() == ""  # emit nothing rather than crash


def test_inject_failopen_on_empty_prompt(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {"prompt": "   ", "cwd": str(tmp_path)})
    assert rc == 0
    assert out.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_inject.py -q`
Expected: FAIL — `inject` is not a recognized subcommand (argparse SystemExit).

- [ ] **Step 3: Modify `src/memory_engine/cli.py`**

(a) Add imports at the top (with the existing imports):

```python
import sys

from memory_engine.formatting import format_memory_block
from memory_engine.paths import default_db_path
from memory_engine.repository import MemoryRepository, RETRIEVE_K
from memory_engine.scope import scopes_for
```
(Keep the existing `from memory_engine.repository import MemoryRepository` — merge it into the line above so `RETRIEVE_K` is also imported; do not import `MemoryRepository` twice.)

(b) Make `--db` optional with the self-resolved default. Change the existing line:
```python
    parser.add_argument("--db", required=True)
```
to:
```python
    parser.add_argument("--db", default=None,
                        help="SQLite path; defaults to ~/.claude/memory/memory.db")
```

(c) Register the `inject` subcommand (add near the other `sub.add_parser(...)` calls):
```python
    sub.add_parser("inject")  # UserPromptSubmit hook entry; reads JSON on stdin
```

(d) Resolve the DB path before connecting. Change:
```python
    conn = connect(args.db)
```
to:
```python
    conn = connect(args.db or default_db_path())
```

(e) Add the `inject` branch. It must run BEFORE the generic `connect(...)` line so a
malformed/empty payload never even opens the DB, and it must be fully fail-open.
Insert this as the FIRST command check, right after `args = parser.parse_args(argv)`:
```python
    if args.cmd == "inject":
        return _run_inject(args.db)
```

(f) Add the `_run_inject` helper function (module level, after `main`):
```python
def _run_inject(db: str | None) -> int:
    """UserPromptSubmit hook body. Reads the hook JSON from stdin, injects relevant
    memories as additionalContext, and ALWAYS returns 0 — a non-zero/blocking exit
    on this event would erase the user's prompt. Any failure → emit nothing."""
    try:
        data = json.loads(sys.stdin.read())
        prompt = (data.get("prompt") or "").strip()
        cwd = data.get("cwd") or "."
        if not prompt:
            return 0
        conn = connect(db or default_db_path())
        try:
            repo = MemoryRepository(conn, clock=_real_clock)
            memories = repo.retrieve(prompt, scopes=scopes_for(cwd), k=RETRIEVE_K)
        finally:
            conn.close()
        block = format_memory_block(memories)
        if block:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": block,
            }}))
        return 0
    except Exception:
        return 0  # fail-open: never block a turn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_inject.py -q`
Expected: PASS (4 passed). Then `uv run pytest -q` — full suite still green (existing `tests/test_cli.py` still passes because it always passes `--db` explicitly).

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/cli.py tests/test_cli_inject.py
git commit -m "feat: inject subcommand (UserPromptSubmit hook entry, fail-open)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Reinstall global tool + live hook registration & end-to-end verification

This task wires the engine into Claude Code and confirms injection actually reaches
the model. It is a manual verification task (no new unit tests).

**Files:** Modify `~/.claude/settings.json` (user-level hook config).

- [ ] **Step 1: Refresh the global tool**

The editable install tracks the working tree, but reinstall to be certain the new
subcommand is registered:
Run: `uv tool install --editable --force "C:/Users/zichk/Desktop/projects/claude-memory-engine"`
Then verify the subcommand exists:
Run: `memory-engine inject --help`
Expected: argparse shows `inject` (no error).

- [ ] **Step 2: Smoke the hook body by hand (simulate Claude Code)**

Seed a memory in the REAL default DB, then pipe a hook-shaped payload to `inject`:
```bash
memory-engine add --scope global --type preference --name "Pizza topping" --description "fav" --body "the user's favorite pizza topping is pineapple"
echo '{"prompt":"what is my favorite pizza topping","cwd":"C:/Users/zichk/Desktop/projects","hook_event_name":"UserPromptSubmit"}' | memory-engine inject
```
Expected: a single line of JSON with `hookSpecificOutput.additionalContext` containing
`<memory>` and `pineapple`. Confirm exit code is 0: `echo $?` → `0`.

- [ ] **Step 3: Register the UserPromptSubmit hook (user-level)**

Add to `~/.claude/settings.json` (merge into existing `hooks` if present; create the
file with just this block if it doesn't exist). Use the `args` exec form (spike-proven
robust on Windows) and a short timeout:
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "memory-engine",
            "args": ["inject"],
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```
Validate the file parses: `py -c "import json,pathlib; json.load(open(pathlib.Path.home()/'.claude'/'settings.json'))"` → no error.

- [ ] **Step 4: Live end-to-end test (requires a session restart — hooks load at start)**

Report DONE_WITH_CONCERNS with explicit instructions for the human, because a
subagent cannot restart the app:
- "Restart/resume Claude Code, then ask: *what's my favorite pizza topping?*"
- Expected: the assistant answers **pineapple** because the hook injected the memory
  (the user never told it in-session).
- If it does NOT work, check: `~/.claude/settings.json` parses, `memory-engine inject`
  runs standalone (Step 2), and the hook fired (no errors in the session).

- [ ] **Step 5: Clean up the demo memory (after the human confirms)**

```bash
memory-engine list   # find the id of the pineapple preference, if a forget-by-id exists
```
NOTE: Phase 1 has no `forget` CLI; deleting the demo row is optional and can wait for
Phase 2b/3 management tools. Leave a note rather than hand-editing the DB.

- [ ] **Step 6: Commit the hook config note**

The hook lives in `~/.claude/settings.json` (outside the repo). Add a short note to
the repo so the wiring is documented:
```bash
# (controller does this) append a "Phase 2a wiring" section to CLAUDE.md describing
# the UserPromptSubmit hook entry and settings.json block, then:
git add CLAUDE.md
git commit -m "docs: note Phase 2a UserPromptSubmit hook wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 2a Done — Definition of Done

- Full unit suite green (`uv run pytest -q`).
- `memory-engine inject` reads the UserPromptSubmit stdin JSON, injects bm25-gated,
  scope-merged memories as `hookSpecificOutput.additionalContext`, and **always exits
  0** (fail-open verified for malformed stdin, empty prompt, and no-match).
- Scope resolution distinguishes repos (git root) with cwd fallback; retrieval merges
  `global` + current project only.
- Retrieval returns the best-k bm25 matches (no absolute cutoff), falls back to
  archived with revive, and bumps `recallHits`. (Calibrated relevance gating deferred
  to a Phase 4 tuning pass against the real corpus.)
- Live end-to-end injection confirmed by the human after restart (the pineapple test).

## What Phase 2b / later add (not in this plan)

- **Phase 2b:** `recall_memory` stdio MCP server (explicit, ungated, active+archived
  deep search) + `retrieve_explicit` repository method + `.mcp.json`/user-level
  registration.
- **Phase 3:** capture wiring (`memory_add` tool, session-end sweep), management tools.
- **Phase 4:** `SessionStart` archival-sweep + backup-on-boot-if-stale, kill switch,
  corruption recovery / deeper fail-open hardening, and **calibrated relevance gating**
  for retrieval (measured against the real `~/.claude/memory` corpus, replacing the
  Phase 2a top-k-only behavior).
