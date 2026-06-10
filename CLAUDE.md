# claude-memory-engine

**CRAM — Capture & Retrieval-Augmented Memory for Claude Code.** A local memory engine
(standalone SQLite layer: FTS5 + bm25 + counters + lifecycle) that **captures** durable
facts and surfaces the relevant ones per turn at global and project scope. **Decoupled**
from Claude Code's `MEMORY.md` — purely additive. (CRAM is the concept/brand; the package
and CLI stay named `memory-engine`.)

- Overview / usage: `README.md`
- Spec: `docs/superpowers/specs/2026-06-05-claude-code-memory-engine-design.md`
- Plan (Phase 1): `docs/superpowers/plans/2026-06-05-memory-engine-phase1-core-db.md`
- Claude Code setup (skills, hook, standing instruction): `claude-integration/INSTALL.md`
- Status: **Phases 1–3 + the Claude Code integration bundle merged** on `main`.
  **Phase 4a (ops safety: kill switch, backups, corruption recovery, SessionStart hook)**
  is built on branch `phase-4a-ops-safety`. Remaining: Phase 4b (calibrated relevance
  gating; optional near-dup `consolidate` pass) — deferred until a real-usage period.

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
- **Dedup is exact + scope+type aware.** The `normalizedKey` UNIQUE key folds in
  scope + type + normalized body; capture merges ONLY on an exact hard-key match.
  There is **no code-level fuzzy/semantic dedup** — it false-merged unrelated rows that
  shared a common word, so it was removed; paraphrase/semantic dedup is the agent's
  recall-then-decide job (the capture skill). A project capture never merges into global.
- **Counters mutate only through code** (`captureHits`/`recallHits`/`lastUsedAt`/
  `status`) — never via prompt instructions. That determinism is the whole point.

## Testing conventions

- **TDD:** write the failing test, see it fail for the right reason, then implement.
- **Negative/exclusion/isolation tests MUST include the competing rows in the
  fixture.** A test asserting "X is not merged" / "Y is excluded" proves nothing if
  the table has only one row — it would pass even with the filter deleted. Populate
  the adversarial case. (This is a Phase 1 lesson: a one-row fixture hid a real
  dedup bug that only the final holistic review caught.)
- **(Resolved) The fuzzy-dedup fixture trap.** Earlier, capture-time fuzzy dedup merged
  same-scope+type rows sharing any ≥3-char token, silently collapsing fixtures (bit
  Phase 2a ×2, Phase 3 ×1). That fuzzy dedup was **removed** (it false-merged real data),
  so dedup is now exact-only and the trap no longer applies here — the general rule above
  still does.
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
- `models.py` — `Memory` row, `Inserted`/`MergedByKey` outcomes.
- `db.py` — `SCHEMA_SQL`, `connect`, `init_db` (FTS5 external-content + triggers).
- `repository.py` — `MemoryRepository`: `capture_or_merge` (exact hard-key dedup), `search`,
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
- Kill switch: see Phase 4a below.

## Ops safety (Phase 4a)

- **Master kill switch** (`control.is_disabled()`): env `MEMORY_ENGINE_DISABLED` in
  `{1,true,yes,on}` **or** the file `~/.claude/memory/DISABLED`. Checked by BOTH `inject`
  (per turn) and `session-init` (boot); when set, both no-op and exit 0 — no restart needed.
- **`SessionStart` hook** runs `memory-engine session-init` (in `~/.claude/settings.json`
  AND `claude-integration/settings.snippet.json`): kill-switch check → `recover_if_corrupt`
  → archival sweep → `backup_if_stale`. ALWAYS exits 0 (fail-open) — never blocks a session.
- **Backups** (`backup.py`): `VACUUM INTO ~/.claude/memory/backups/memory-<epoch_ms>.db`,
  stale-gated (≤ once/24h), newest 7 retained. The expensive maintenance runs once at boot,
  NOT per turn — `inject` only gained a cheap kill-switch check.
- **Corruption recovery** (`db.recover_if_corrupt`, boot-only): `PRAGMA integrity_check`;
  if corrupt, quarantine the live file to `<path>.corrupt-<n>` (never deleted), restore from
  the newest **healthy** backup (re-verified), else recreate an empty schema. Detects gross
  corruption only; subtle in-page corruption may pass and is out of scope.
- Recovery runs ONLY at boot (`session-init`), never in `connect()` — keeps `inject` cheap.

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
