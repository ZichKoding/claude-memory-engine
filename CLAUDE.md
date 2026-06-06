# claude-memory-engine

A local memory engine for Claude Code: a standalone SQLite layer (FTS5 + bm25
+ counters + lifecycle) that surfaces relevant memories per turn at global and
project scope. **Decoupled** from Claude Code's `MEMORY.md` — purely additive.

- Spec: `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md`
- Plan (Phase 1): `docs/superpowers/plans/2026-06-05-memory-engine-phase1-core-db.md`
- Claude Code setup (skills, hook, standing instruction): `claude-integration/INSTALL.md`
- Status: **Phases 1, 2a (auto-retrieval hook), 2b (explicit recall CLI+skill) merged**
  on `main`; **Phase 3 (inline capture) implemented** on `phase-3-inline-capture`.
  Remaining: Phase 4 (SessionStart archival/backup, kill switch, calibrated relevance
  gating, counter-snapshot hardening; optional session-end capture sweep fallback).

## Tooling (Windows)

- **Use `uv` for everything.** Never invoke a bare `python` — use `uv run …`, or the
  Windows `py` launcher for a one-off bare interpreter call.
- Run tests: `uv run pytest -q`. Run the CLI: `uv run python -m memory_engine.cli …`.
- The package is an editable install (hatchling, `src/` layout) created by `uv sync`;
  this is why imports resolve under `uv run` without `PYTHONPATH` hacks.
- `uv.lock` is committed. `.venv/` and `*.db` are gitignored.
- End every commit message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Architecture invariants (do not violate)

- **The DB engine is DECOUPLED from `MEMORY.md`.** They are two independent systems,
  not two views of one. Do not add derivation, regeneration, reconcile, or a
  promotion pipeline between them. The DB is system-of-record for its own data;
  `MEMORY.md` is system-of-record for its own.
- **Two-level scope:** every row has a `scope` — `'global'` or a project key (the git
  repo root, via `git rev-parse --show-toplevel`, cwd fallback). Retrieval merges
  `global` + current project only.
- **Dedup is scope+type aware.** The `normalizedKey` UNIQUE key folds in scope and
  type; fuzzy-FTS dedup is constrained to the same scope AND type *in SQL*. A project
  capture must never merge into a global row; different-type same-text captures stay
  separate.
- **Counters mutate only through code** (`captureHits`/`recallHits`/`lastUsedAt`/
  `status`) — never via prompt instructions. That determinism is the whole point.

## Testing conventions

- **TDD:** write the failing test, see it fail for the right reason, then implement.
- **Negative/exclusion/isolation tests MUST include the competing rows in the
  fixture.** A test asserting "X is not merged" / "Y is excluded" proves nothing if
  the table has only one row — it would pass even with the filter deleted. Populate
  the adversarial case. (This is a Phase 1 lesson: a one-row fixture hid a real
  dedup bug that only the final holistic review caught.)
- **Same scope+type multi-row fixtures MUST use token-disjoint bodies.** Capture-time
  fuzzy dedup merges same-scope+type rows that share any ≥3-char token, silently
  collapsing the fixture to one row (this bit Phase 2a twice and Phase 3 once). Use
  disjoint tokens (e.g. `"alpha"`/`"bravo"`), or distinct types/scopes, so the rows
  actually persist.
- **Before implementing a plan with non-trivial logic, run the `plan-redteam` agent**
  (user-level) over it. Spec-compliance review cannot catch a wrong spec.
- Use the in-memory SQLite `conn` fixture and the injected `clock` fixture
  (`tests/conftest.py`) for deterministic DB + time tests.
- Pure-logic modules (`normalizer`, `fts_query`, `parser`) carry no DB deps — keep
  them that way so they stay trivially unit-testable.

## Review tiering (subagent-driven development)

- **Logic-bearing tasks** (repository, DB, dedup, retrieval): full two-stage review
  (spec compliance, then code quality) per task, plus a holistic final review of the
  whole phase.
- **Config-only tasks** (scaffold, pure data shapes): controller self-verifies; no
  review subagent needed.

## Module map (`src/memory_engine/`)

- `normalizer.py` — `normalize_body`, `normalized_key` (scope+type+body → sha1).
- `fts_query.py` — `from_user_text` → FTS5 MATCH expression (or None).
- `parser.py` — `Parsed`, `parse_candidates` (strict pipe-delimited extraction).
- `models.py` — `Memory` row, `Inserted`/`MergedByKey`/`MergedByFuzzy` outcomes.
- `db.py` — `SCHEMA_SQL`, `connect`, `init_db` (FTS5 external-content + triggers).
- `repository.py` — `MemoryRepository`: `capture_or_merge` (2-stage dedup), `search`,
  `retrieve` (auto top-k), `retrieve_explicit` (active+archived), `edit`, `bump_recall`,
  `run_archival_sweep` (clock injected for tests).
- `cli.py` — driver + hook entry: `add` (`--cwd` scope), `edit`, `list`/`search`/`stats`/
  `sweep`, `recall` (explicit), plus `inject` (the UserPromptSubmit hook body, fail-open).
  `--db` defaults to the self-resolved path.
- `paths.py` — `default_db_path()` → `~/.claude/memory/memory.db`.
- `scope.py` — `resolve_scope_key`/`scopes_for` (git repo root, cwd fallback).
- `formatting.py` — `format_memory_block` → the injected `<memory>` block.

## Claude Code wiring (Phase 2a)

- The engine is installed globally as a `uv` tool (`uv tool install --editable .`),
  exposing `memory-engine` on PATH so the hook runs in any project.
- Auto-retrieval is wired via a **`UserPromptSubmit` hook** in `~/.claude/settings.json`
  (outside this repo) that runs `memory-engine inject` — the global uv-tool shim,
  resolved on PATH.
  On each prompt it injects scope-merged top-k memories as
  `hookSpecificOutput.additionalContext`, and is fail-open (always exit 0) so it can
  never block a turn.
- Hooks load at session start — config changes require a restart/resume to take effect.
- Kill switch (interim): remove the `UserPromptSubmit` block from
  `~/.claude/settings.json`. A flag-based switch is Phase 4.

## Explicit recall (Phase 2b)

- `memory-engine recall --query "…" [--cwd "…"]` runs an explicit, on-demand search
  (active **+** archived, ungated) across global + the given project; omit `--cwd` for
  global only.
- Surfaced to the model by a user-level skill `~/.claude/skills/recall-memory/SKILL.md`
  (discoverable + auto-triggering) that shells out to the CLI. `Bash(memory-engine*)`
  is allowlisted in `~/.claude/settings.json` so it runs without prompts.
- No MCP server — the model invokes the existing CLI via Bash. To disable: remove the
  skill dir and/or the allowlist entry.

## Inline capture (Phase 3)

- Capture is **inline + agent-driven** (no session-end sweep). A standing instruction in
  user-global `~/.claude/CLAUDE.md` + the `~/.claude/skills/capture-memory/SKILL.md` skill
  make the agent proactively save durable, directly-stated user facts (conservative bar)
  via `memory-engine add` (global) / `add --cwd <dir>` (project) — through `capture_or_merge`.
- Corrections use `memory-engine edit --id N --body "…"` (recomputes the dedup key, FTS
  re-syncs; conflict-safe — returns `updated`/`not_found`/`conflict`).
- The session-end sweep is a deferred optional Phase 4 fallback, not built.
