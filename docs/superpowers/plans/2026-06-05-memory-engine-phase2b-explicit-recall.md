# Memory Engine — Phase 2b (Explicit Recall via CLI + Skill) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the model an explicit, on-demand way to search long-term memory (deeper than the per-turn auto-injection): a `memory-engine recall` CLI subcommand that searches active **and** archived memories across global + the current project, surfaced to the model via a discoverable skill — no MCP server.

**Architecture:** Builds on Phase 1 + 2a. Adds `retrieve_explicit` to the repository (active+archived union, bm25-ranked, ungated, revives + bumps recall), a `recall` CLI subcommand that resolves scope from `--cwd` (reusing `scope.scopes_for`), and a user-level `recall-memory` skill that tells the model when/how to invoke it via Bash. The model already has the Bash tool and `memory-engine` is already a global `uv` tool on PATH, so "a tool call" = the model running the CLI; the skill provides discoverability and an allowlist entry removes permission prompts. **No MCP server, no new runtime dependency.**

**Tech Stack:** Python 3 (stdlib only — reuses existing modules); `pytest`; `uv`. The skill is markdown. Windows-clean.

**Reference:** Spec `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md` (the `recall_memory` touchpoint — note: implemented as a CLI+skill, not the MCP server the spec sketched; this was a deliberate simplification). Conventions: `CLAUDE.md`.

**Why CLI+skill instead of MCP (decided during planning):** an MCP server would add the `mcp` dependency, a FastMCP stdio process, user-scope registration, restart-to-reload, and Windows MCP quirks — to wrap a query the existing CLI already performs. The only thing MCP adds is native tool-list discoverability, which a skill replicates with zero infrastructure. Research also confirmed an MCP server **cannot** auto-detect the session's project (unreliable cwd, no project-dir env, non-functional roots), so it would need an explicit scope argument *anyway* — same as the CLI.

---

## File Structure

```
src/memory_engine/
  repository.py   # MODIFY: add _search_any_status + retrieve_explicit
  cli.py          # MODIFY: add `recall` subcommand
tests/
  test_repository_explicit.py   # NEW
  test_cli_recall.py            # NEW
# user-level (outside repo, created in Task 3):
~/.claude/skills/recall-memory/SKILL.md
~/.claude/settings.json         # add Bash(memory-engine*) allowlist
```

---

### Task 1: `retrieve_explicit` (active + archived union, ungated, revive)

**Files:** Modify `src/memory_engine/repository.py`; Test `tests/test_repository_explicit.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repository_explicit.py
from memory_engine.repository import MemoryRepository
from memory_engine.parser import Parsed


def _add(repo, scope, body, name="n", type_="fact"):
    return repo.capture_or_merge(Parsed(type_, name, "d", body), scope=scope)


def test_explicit_returns_active_and_archived_together(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    active = _add(repo, "global", "alpha bravo charlie delta echo", type_="fact")
    archived = _add(repo, "global", "alpha bravo charlie delta echo", type_="preference")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (archived.id,))
    conn.commit()
    out = repo.retrieve_explicit("alpha bravo charlie delta echo", scopes=["global"], k=10)
    ids = {m.id for m in out}
    assert ids == {active.id, archived.id}  # BOTH pools, unlike auto-retrieve


def test_explicit_revives_archived_and_bumps_recall(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    m = _add(repo, "global", "alpha bravo charlie delta echo")
    conn.execute("UPDATE memories SET status='archived' WHERE id=?", (m.id,))
    conn.commit()
    clock.now += 5
    out = repo.retrieve_explicit("alpha bravo charlie delta echo", scopes=["global"], k=10)
    assert len(out) == 1 and out[0].status == "active"
    row = conn.execute("SELECT status, recallHits, lastUsedAt FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row["status"] == "active"      # revived
    assert row["recallHits"] == 1         # bumped
    assert row["lastUsedAt"] == clock.now


def test_explicit_respects_scope(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    g = _add(repo, "global", "alpha bravo charlie")
    p = _add(repo, "repoA", "alpha bravo charlie")
    _add(repo, "repoB", "alpha bravo charlie")  # competing row, must be excluded
    out = repo.retrieve_explicit("alpha bravo charlie", scopes=["global", "repoA"], k=10)
    assert {m.id for m in out} == {g.id, p.id}


def test_explicit_caps_at_k(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    # Distinct types so all three survive scope+type fuzzy dedup.
    _add(repo, "global", "alpha bravo charlie delta echo foxtrot", type_="fact")
    _add(repo, "global", "alpha bravo charlie", type_="preference")
    _add(repo, "global", "alpha", type_="person")
    out = repo.retrieve_explicit("alpha bravo charlie delta echo foxtrot", scopes=["global"], k=2)
    assert len(out) == 2
    assert "foxtrot" in out[0].body  # best bm25 match first


def test_explicit_empty(conn, clock):
    repo = MemoryRepository(conn, clock=clock)
    _add(repo, "global", "alpha bravo charlie")
    assert repo.retrieve_explicit("a", scopes=["global"], k=10) == []   # no usable tokens
    assert repo.retrieve_explicit("alpha", scopes=[], k=10) == []       # no scopes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repository_explicit.py -q`
Expected: FAIL — `AttributeError: 'MemoryRepository' object has no attribute 'retrieve_explicit'`.

- [ ] **Step 3: Modify `src/memory_engine/repository.py`**

Add these two methods to `MemoryRepository` (after the `retrieve` method). `_search_any_status` is a sibling of the existing `search` but with **no status filter**, so a single bm25-ranked query naturally unions active+archived. (`import dataclasses`, `STATUS_ACTIVE`, `from_user_text`, `RETRIEVE_K`, `_to_memory` already exist in this file.)

```python
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
```

Do NOT change `capture_or_merge`, `_find_fuzzy_candidate`, `search`, `retrieve`, `bump_recall`, `run_archival_sweep`, or `_to_memory`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repository_explicit.py -q`
Expected: PASS (5 passed). Then `uv run pytest -q` — full suite still green.

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/repository.py tests/test_repository_explicit.py
git commit -m "feat: retrieve_explicit (active+archived union, ungated, revive)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `recall` CLI subcommand

**Files:** Modify `src/memory_engine/cli.py`; Test `tests/test_cli_recall.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_recall.py
from memory_engine.cli import main


def _seed(db, scope, body, type_="fact", name="n"):
    main(["--db", db, "add", "--scope", scope, "--type", type_,
          "--name", name, "--description", "d", "--body", body])


def test_recall_prints_matches(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie delta echo", name="Greeting")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie delta echo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "<memory>" in out and "Greeting" in out


def test_recall_includes_archived(tmp_path, capsys):
    import sqlite3
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie delta echo", name="OldFact")
    # Genuinely archive the row, then confirm recall STILL surfaces it (explicit recall
    # searches active + archived — this is the behavior that distinguishes it from the
    # auto-retrieve hook, so the fixture must actually contain an archived row).
    c = sqlite3.connect(db)
    c.execute("UPDATE memories SET status='archived'")
    c.commit()
    c.close()
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie delta echo"])
    assert rc == 0
    assert "OldFact" in capsys.readouterr().out


def test_recall_no_match_message(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "zulu yankee xray whiskey"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No matching memories" in out


def test_recall_scopes_to_cwd_project(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    # global match + a match in an unrelated project scope; with no --cwd, only global.
    _seed(db, "global", "alpha bravo charlie", name="GlobalHit")
    _seed(db, "/some/other/proj", "alpha bravo charlie", name="OtherProjHit")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GlobalHit" in out
    assert "OtherProjHit" not in out  # no --cwd → global only, other project excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_recall.py -q`
Expected: FAIL — `recall` is not a recognized subcommand (argparse error).

- [ ] **Step 3: Modify `src/memory_engine/cli.py`**

(a) Add the import for explicit-recall scope resolution (the file already imports `scopes_for` and `format_memory_block` from Phase 2a; reuse them).

(b) Register the `recall` subcommand near the other `sub.add_parser(...)` calls:
```python
    p_recall = sub.add_parser("recall")
    p_recall.add_argument("--query", required=True)
    p_recall.add_argument("--cwd", default=None,
                          help="project dir to scope to; omit for global-only")
    p_recall.add_argument("--limit", type=int, default=RETRIEVE_K)
```

(c) Add the `recall` branch in `main`'s command dispatch (in the if/elif chain, after the existing commands, BEFORE `conn.close()`):
```python
    elif args.cmd == "recall":
        scopes = scopes_for(args.cwd) if args.cwd else ["global"]
        memories = repo.retrieve_explicit(args.query, scopes=scopes, k=args.limit)
        block = format_memory_block(memories)
        print(block if block else "No matching memories found.")
```

(Note: `recall` uses the generic `connect(args.db or default_db_path())` + `repo` already built in `main`, exactly like `add`/`search`/`stats`. Only `inject` short-circuits earlier.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_recall.py -q`
Expected: PASS (4 passed). Then `uv run pytest -q` — full suite green (existing `test_cli.py` / `test_cli_inject.py` unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/memory_engine/cli.py tests/test_cli_recall.py
git commit -m "feat: recall subcommand (explicit memory search, cwd-scoped)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `recall-memory` skill + allowlist + live test

Discoverability + permissions wiring + end-to-end check. Controller/manual task (no new unit tests).

**Files:** `~/.claude/skills/recall-memory/SKILL.md` (new, user-level), `~/.claude/settings.json` (add allowlist), `CLAUDE.md` (note).

- [ ] **Step 1: Refresh the global tool**

Run (from repo root): `uv tool install --editable --force .`
Verify the subcommand exists: `memory-engine recall --help` → argparse shows `recall`.

- [ ] **Step 2: Smoke recall by hand against the real DB**

```bash
memory-engine add --scope global --type preference --name "Pizza topping" --description "fav" --body "the user's favorite pizza topping is pineapple"
memory-engine recall --query "favorite pizza topping"
```
Expected: a `<memory>` block containing `pineapple`. (Leave the demo row for the live test in Step 5; clean up after.)

- [ ] **Step 3: Create the user-level skill**

Create `~/.claude/skills/recall-memory/SKILL.md`:
```markdown
---
name: recall-memory
description: Search the user's long-term memory store for relevant past context — facts, preferences, people, goals, project details — that is NOT in the current conversation and was NOT auto-injected this turn. Use when the user refers to something they told you before, asks what you remember, or when you need durable context to answer well. Searches global + the current project, including archived memories.
---

# Recalling long-term memory

The `memory-engine` CLI (a global tool on PATH) searches a persistent SQLite memory
store that lives outside this conversation.

When this skill triggers, run:

```bash
memory-engine recall --query "<concise search terms>" --cwd "<the current working directory>"
```

- `--query`: a few content words (the search is keyword/FTS-based; short and specific beats a full sentence).
- `--cwd`: the absolute path of the current working directory, so results include this
  project's memories plus global ones. Omit `--cwd` to search global memories only.

Read the returned `<memory>` block and use it to inform your answer. If it prints
"No matching memories found.", there's nothing stored for that query — say so rather
than guessing.
```

- [ ] **Step 4: Allowlist the CLI so recall runs without prompts**

Add `"Bash(memory-engine*)"` to the `permissions.allow` array in `~/.claude/settings.json`
(merge into the existing array; don't remove existing entries). Validate it parses:
`py -c "import json,pathlib; json.load(open(pathlib.Path.home()/'.claude'/'settings.json'))"`.

- [ ] **Step 5: Live end-to-end test (requires session restart — skills/allowlist load at start)**

Report DONE_WITH_CONCERNS with instructions for the human (a subagent can't restart):
- "Restart/resume Claude Code. In a NEW session, ask: *do you remember my favorite pizza topping? use your memory.*"
- Expected: the assistant triggers the `recall-memory` skill, runs `memory-engine recall`, and answers **pineapple** — without a permission prompt (allowlisted).
- If it doesn't trigger: confirm `~/.claude/skills/recall-memory/SKILL.md` exists and parses, and that `memory-engine recall --query pineapple` works standalone.

- [ ] **Step 6: Clean up demo memory + document**

After the human confirms, delete the demo row:
`py -c "import sqlite3,pathlib; p=pathlib.Path.home()/'.claude'/'memory'/'memory.db'; c=sqlite3.connect(p); c.execute(\"DELETE FROM memories WHERE name='Pizza topping'\"); c.commit(); c.close(); print('cleaned')"`

Then (controller) add a "Phase 2b wiring" note to `CLAUDE.md` describing the `recall`
subcommand, the user-level `recall-memory` skill, and the allowlist entry, and commit:
```bash
git add CLAUDE.md
git commit -m "docs: note Phase 2b recall CLI + skill wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 2b Done — Definition of Done

- Full unit suite green (`uv run pytest -q`).
- `retrieve_explicit` returns active **+** archived (unlike auto-`retrieve` which prefers active), bm25-ranked, capped at k, scope-filtered, revives archived + bumps recall.
- `memory-engine recall --query … [--cwd …]` prints matches (or a clear no-match message), scoping to global (+ project when `--cwd` given).
- The `recall-memory` skill is discoverable and the CLI is allowlisted; live test confirms the model recalls a stored fact on demand.
- No MCP server, no new runtime dependency added.

## What later phases add (not in this plan)

- **Phase 3:** capture wiring — `memory_add` (inline capture tool/skill) + session-end sweep hook.
- **Phase 4:** `SessionStart` archival-sweep + backup-on-boot-if-stale, kill switch, calibrated relevance gating, counter-snapshot hardening (the pre-bump returned-object nicety).
