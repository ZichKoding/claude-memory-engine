# Memory Engine — Phase 3 (Inline Capture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the engine capture on its own: the in-loop agent proactively saves durable, directly-stated user facts to memory (and corrects them when they change) — driven by a standing instruction + a `capture-memory` skill over the existing `add`/`edit` CLI, all flowing through the deterministic `capture_or_merge`. No end-of-session sweep.

**Architecture:** Builds on Phases 1/2a/2b. Adds an `edit`-by-id path to the repository + CLI (for the "fact changed / was wrong" case), a `--cwd` ergonomics enhancement to `add` (so the agent specifies project scope by passing its working dir instead of computing a normalized key), and the agent-facing surface: a user-level `capture-memory` skill (the recall→decide→add/edit flow at a **conservative** bar) plus a short standing instruction in the user-global `~/.claude/CLAUDE.md` so proactive capture fires on ambient mentions (not just explicit "remember this"). Capture judgement is the model's job; the *mechanics* (dedup, counters, key) stay code-enforced via `capture_or_merge`/`edit`.

**Tech Stack:** Python 3 (stdlib only — reuses existing modules); `pytest`; `uv`. Skill + instruction are markdown. Windows-clean.

**Reference:** Spec `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md` — **NOTE the spec is now partially stale**: it lists capture as "inline tool + one session-end sweep pass." Task 3 updates the spec to reflect the decided design (inline-first; sweep deferred to an optional Phase 4 fallback). Conventions: `CLAUDE.md`.

**Decisions locked:** updates = `edit` by id; capture bar = conservative; trigger = global `~/.claude/CLAUDE.md` instruction + `capture-memory` skill.

---

## File Structure

```
src/memory_engine/
  repository.py   # MODIFY: add edit()
  cli.py          # MODIFY: add --cwd to `add` (+ make --scope optional, default global); add `edit` subcommand
tests/
  test_repository_edit.py   # NEW
  test_cli_edit.py          # NEW  (edit + add-scope cases)
# user-level / repo (Task 3):
~/.claude/skills/capture-memory/SKILL.md   # NEW
~/.claude/CLAUDE.md                        # ADD a standing capture instruction (merge/create)
docs/.../spec...md                         # UPDATE capture section
CLAUDE.md                                  # note Phase 3 wiring
```

---

### Task 1: `repository.edit` (update fields by id)

**Files:** Modify `src/memory_engine/repository.py`; Test `tests/test_repository_edit.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repository_edit.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed
from memory_engine.normalizer import normalized_key


def _add(repo, scope, body, name="n", type_="fact", desc="d"):
    return repo.capture_or_merge(Parsed(type_, name, desc, body), scope=scope)


def test_edit_updates_body_and_recomputes_key_and_fts(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "the user lives in iowa")
    clock.now += 5
    result = repo.edit(m.id, body="the user lives in texas")
    assert result == "updated"
    row = conn.execute("SELECT body, normalizedKey, updatedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["body"] == "the user lives in texas"
    assert row["normalizedKey"] == normalized_key("global", "fact", "the user lives in texas")
    assert row["updatedAt"] == clock.now
    # FTS reflects the change: new term found, old term gone (in body)
    assert conn.execute("SELECT 1 FROM memories_fts WHERE memories_fts MATCH ?", ('"texas"*',)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM memories_fts WHERE memories_fts MATCH ?", ('"iowa"*',)).fetchone() is None


def test_edit_partial_keeps_other_fields(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "body one", name="Original", desc="orig desc", type_="fact")
    assert repo.edit(m.id, name="Renamed") == "updated"
    row = conn.execute("SELECT name, body, description, type FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["name"] == "Renamed"
    assert row["body"] == "body one"          # unchanged
    assert row["description"] == "orig desc"  # unchanged
    assert row["type"] == "fact"              # unchanged


def test_edit_missing_id_returns_not_found(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    assert repo.edit(999, body="x") == "not_found"


def test_edit_conflict_when_key_would_collide(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    a = _add(repo, "global", "alpha fact body", type_="fact")
    b = _add(repo, "global", "beta fact body", type_="fact")
    # Editing b's body to equal a's (same scope+type) would duplicate a's normalizedKey.
    result = repo.edit(b.id, body="alpha fact body")
    assert result == "conflict"
    # b is unchanged
    row = conn.execute("SELECT body FROM memories WHERE id=?", (b.id,)).fetchone()
    assert row["body"] == "beta fact body"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repository_edit.py -q`
Expected: FAIL — `AttributeError: 'MemoryRepository' object has no attribute 'edit'`.

- [ ] **Step 3: Modify `src/memory_engine/repository.py`**

Add this method to `MemoryRepository` (after `capture_or_merge` or near the other write methods). `normalized_key` and `sqlite3` are already imported.

```python
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
```

Do NOT change any other method.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repository_edit.py -q`
Expected: PASS (4 passed). Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_edit.py
git commit -m "feat: repository.edit (update fields by id, recompute key, conflict-safe)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: CLI — `add --cwd` ergonomics + `edit` subcommand

**Files:** Modify `src/memory_engine/cli.py`; Test `tests/test_cli_edit.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_edit.py
import sqlite3
from memory_engine.cli import main
from memory_engine.scope import resolve_scope_key


def _rows(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id, scope, name, body FROM memories ORDER BY id").fetchall()
    c.close()
    return rows


def test_add_defaults_to_global_scope(tmp_path):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--type", "fact", "--name", "n", "--description", "d",
          "--body", "alpha bravo"])  # no --scope, no --cwd
    assert _rows(db)[0]["scope"] == "global"


def test_add_cwd_resolves_project_scope(tmp_path):
    db = str(tmp_path / "m.db")
    proj = tmp_path / "proj"
    proj.mkdir()
    main(["--db", db, "add", "--cwd", str(proj), "--type", "fact", "--name", "n",
          "--description", "d", "--body", "alpha bravo"])
    assert _rows(db)[0]["scope"] == resolve_scope_key(str(proj))


def test_edit_updates_via_cli(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "n",
          "--description", "d", "--body", "lives in iowa"])
    mid = _rows(db)[0]["id"]
    capsys.readouterr()
    rc = main(["--db", db, "edit", "--id", str(mid), "--body", "lives in texas"])
    assert rc == 0
    assert "updated" in capsys.readouterr().out.lower()
    assert _rows(db)[0]["body"] == "lives in texas"


def test_edit_missing_id_message(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    capsys.readouterr()
    rc = main(["--db", db, "edit", "--id", "999", "--body", "x"])
    assert rc == 0
    assert "not found" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_edit.py -q`
Expected: FAIL — `add` rejects missing `--scope` (currently required) and `edit` is an unknown subcommand.

- [ ] **Step 3: Modify `src/memory_engine/cli.py`**

(a) `resolve_scope_key` is needed; the file already imports `scopes_for` from `memory_engine.scope`. Change that import to also bring in `resolve_scope_key`:
```python
from memory_engine.scope import resolve_scope_key, scopes_for
```

(b) Make `add`'s `--scope` optional (default `"global"`) and add `--cwd`. Find the `add` subparser setup. It currently does:
```python
    p_add = sub.add_parser("add")
    for f in ("scope", "type", "name", "description", "body"):
        p_add.add_argument(f"--{f}", required=True)
```
Replace it with:
```python
    p_add = sub.add_parser("add")
    p_add.add_argument("--scope", default="global")
    p_add.add_argument("--cwd", default=None,
                       help="if given, scope = this project's key (overrides --scope)")
    for f in ("type", "name", "description", "body"):
        p_add.add_argument(f"--{f}", required=True)
```

(c) Register the `edit` subcommand near the other `sub.add_parser(...)` calls:
```python
    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--id", type=int, required=True)
    for f in ("type", "name", "description", "body"):
        p_edit.add_argument(f"--{f}", default=None)
```

(d) In the `add` branch, resolve scope from `--cwd` when provided. Change the existing add branch:
```python
    if args.cmd == "add":
        outcome = repo.capture_or_merge(
            Parsed(args.type, args.name, args.description, args.body), scope=args.scope)
        print(type(outcome).__name__, getattr(outcome, "id", ""))
```
to:
```python
    if args.cmd == "add":
        scope = resolve_scope_key(args.cwd) if args.cwd else args.scope
        outcome = repo.capture_or_merge(
            Parsed(args.type, args.name, args.description, args.body), scope=scope)
        print(type(outcome).__name__, getattr(outcome, "id", ""))
```

(e) Add an `edit` branch to the if/elif dispatch (after the other commands, before `conn.close()`):
```python
    elif args.cmd == "edit":
        result = repo.edit(args.id, type=args.type, name=args.name,
                           description=args.description, body=args.body)
        messages = {"updated": f"Updated memory {args.id}",
                    "not_found": f"Memory {args.id} not found",
                    "conflict": f"Edit would duplicate an existing memory; memory {args.id} unchanged"}
        print(messages[result])
```

Do NOT change `_run_inject`, the `inject` short-circuit, or other branches.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_edit.py -q`
Expected: PASS (4 passed). Then `uv run pytest -q` — full suite green (existing `test_cli.py` still passes: it always passes `--scope`, which still works as the default-overridable arg).

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/cli.py tests/test_cli_edit.py
git commit -m "feat: add --cwd scope ergonomics + edit subcommand

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Capture skill + global standing instruction + spec update + live test

Controller/manual task — wires the agent-facing surface and verifies end-to-end.

**Files:** `~/.claude/skills/capture-memory/SKILL.md` (new), `~/.claude/CLAUDE.md` (add instruction; merge/create), the spec (update capture section), repo `CLAUDE.md` (note).

- [ ] **Step 1: Refresh the global tool + smoke `edit`**

Run (repo root): `uv tool install --editable --force .`
```bash
memory-engine add --type fact --name "Home" --description "where" --body "the user lives in iowa"
memory-engine recall --query "where does the user live"          # confirm it's stored
# (note the printed id from `add` or via `memory-engine list`), then:
memory-engine edit --id <ID> --body "the user lives in texas"     # expect "Updated memory <ID>"
memory-engine recall --query "where does the user live"          # now shows texas
```
Then clean up: delete that demo row (it's in the real DB) via the sqlite one-liner used in earlier phases, OR leave for the live test in Step 4 and clean in Step 6.

- [ ] **Step 2: Create the `capture-memory` skill** at `~/.claude/skills/capture-memory/SKILL.md`:
```markdown
---
name: capture-memory
description: Proactively save durable, directly-stated facts about the user or their work to long-term memory — preferences, goals, people, project details, decisions, stable personal facts. Trigger whenever the user states such a fact (no need for them to ask), and always when they say "remember this". Also use to correct a stored memory the user says has changed or is wrong.
---

# Capturing long-term memory

`memory-engine` (a global CLI on PATH) persists durable memories in SQLite. Capture is
PROACTIVE: when the user states something durable about themselves or their work, save it
without being asked. Keep a CONSERVATIVE bar — capture clear, directly-stated, durable
facts; skip transient chitchat, one-off task details, and your own inferences/guesses.
(If the user explicitly says "remember this", always save it.)

## Flow

1. **Check for an existing memory first** (avoid semantic duplicates / find the row to update):
   ```bash
   memory-engine recall --query "<key terms of the fact>" --cwd "<current working directory>"
   ```
2. **Decide:**
   - Already captured and still accurate → do nothing.
   - The fact CHANGED or was wrong → update the existing row by its id:
     ```bash
     memory-engine edit --id <id> --body "<corrected fact>"
     ```
   - Genuinely new → add it (see scope + type below).

3. **Add a new memory:**
   ```bash
   # about the user / cross-project → global:
   memory-engine add --type <type> --name "<3-5 word label>" --description "<one line>" --body "<self-contained fact>"
   # specific to the current project → pass --cwd so it's scoped to this repo:
   memory-engine add --cwd "<current working directory>" --type <type> --name "..." --description "..." --body "..."
   ```

- `--type` is one of: fact, preference, person, goal, project, other.
- Keep `body` self-contained (don't rely on conversation context to interpret it).
- `add` is dedup-safe — re-adding the same fact just reinforces it, so when unsure, prefer
  adding over losing the fact.

Do this quietly as part of the conversation; don't announce every save.
```

- [ ] **Step 3: Add the standing instruction to `~/.claude/CLAUDE.md`**

If `~/.claude/CLAUDE.md` exists, append the block below (don't disturb existing content);
if not, create it with this block. Keep it short — it's always-in-context:
```markdown
## Long-term memory (memory-engine)

Proactively save durable, directly-stated facts about me or my work (preferences, goals,
people, project details, decisions, stable personal facts) to long-term memory using the
`memory-engine` CLI — you don't need to be asked. Keep a conservative bar (skip transient
chitchat and guesses). Before saving, check for an existing memory and update it if the
fact changed. See the `capture-memory` skill for the exact flow. Always honor an explicit
"remember this".
```

- [ ] **Step 4: Live end-to-end test (requires session restart — skill + CLAUDE.md load at start)**

Report DONE_WITH_CONCERNS with instructions for the human (a subagent can't restart):
- "Restart/resume. Then, WITHOUT saying 'remember', state a durable fact in passing, e.g. *'by the way, I always use 2-space indentation.'*"
- Expected: the assistant proactively captures it (recall → add) without being asked.
- Then in a later turn ask *'what indentation do I use?'* → it should recall **2-space** (via the 2a auto-hook or the recall skill).
- Correction test: say *'actually I switched to tabs'* → it should `edit` the existing memory, and a later recall shows tabs.
- If proactive capture doesn't fire: confirm `~/.claude/CLAUDE.md` has the block and the `capture-memory` skill exists/parses; `memory-engine add` works standalone (Step 1).

- [ ] **Step 5: Update the spec to match the decided design**

In `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md`:
- Key Decisions table, row 5 (Capture model): change to
  `Inline, agent-driven capture (conservative, proactive) via a capture skill + add/edit through capture_or_merge. Session-end sweep deferred (optional Phase 4 fallback).`
- In the Runtime "④ Capture" section, replace the session-end sweep description with the
  inline flow (skill recall→decide→add/edit; standing instruction for proactive trigger),
  and note the sweep is a deferred optional fallback.

- [ ] **Step 6: Clean up demo + document + commit**

After the human confirms, delete any demo rows from the real DB (sqlite one-liner). Then
add a "Phase 3 wiring" note to the repo `CLAUDE.md` (capture skill + global instruction +
`edit`/`add --cwd`), and commit the spec update + CLAUDE.md note:
```bash
git add docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md CLAUDE.md
git commit -m "docs: capture is inline-first (skill + add/edit); update spec + note wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 3 Done — Definition of Done

- Full unit suite green (`uv run pytest -q`).
- `repository.edit` updates fields by id, recomputes `normalizedKey`, re-syncs FTS, bumps
  `updatedAt`, and returns `updated`/`not_found`/`conflict` (no silent duplicate).
- `memory-engine add` defaults to global scope and accepts `--cwd` for project scope;
  `memory-engine edit --id …` updates a memory and reports the outcome.
- The `capture-memory` skill + the global `~/.claude/CLAUDE.md` instruction drive proactive,
  conservative capture; live test confirms the agent saves a passing fact unprompted and
  corrects it via `edit`.
- Spec updated to reflect inline-first capture; sweep documented as deferred.
- No session-end sweep, no new runtime dependency.

## What later phases add (not in this plan)

- **Phase 4:** `SessionStart` archival-sweep + backup-on-boot-if-stale, kill switch,
  calibrated relevance gating, counter-snapshot hardening, and (optional) the session-end
  capture sweep as a fallback if inline capture proves leaky.
- Possible: `forget` (delete by id) if removal — not just correction — proves needed.
