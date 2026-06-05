# Claude Code Memory Engine — Design Spec

**Date:** 2026-06-05
**Status:** Approved design, pre-implementation
**Author:** Chris + Claude (brainstorming session)

## Summary

A local memory engine for Claude Code: a standalone SQLite layer that, at
each turn and on demand, runs fuzzy (FTS5/bm25) and exact searches for memories
relevant to the current context — at both **global** and **current-project** scope —
and injects the strong matches into context. It has its own capture, dedup, counters,
lifecycle, and backups.

It is **decoupled** from the existing `MEMORY.md` system. `MEMORY.md` continues to
work exactly as it does today (hand/Claude-curated, loaded once per session). The DB
engine is purely additive. If it misbehaves, it can be turned off with one flag and
the session reverts to today's behavior with zero data loss.

This design ports the parts of a proven on-device memory design we value:
`captureOrMerge` two-stage dedup, `captureHits`/`recallHits`/`lastUsedAt` counters,
FTS5/bm25 retrieval, and the active/archived lifecycle — adapted for Claude Code and
extended with a two-level (global + project) scope axis the reference design lacks.

## Goals

- Surface relevant long-tail memories per turn without bloating the always-loaded
  `MEMORY.md` hot-set.
- Deterministic, code-enforced counters and dedup (the signals the user specifically
  liked are unreliable as prompt instructions — they require real code).
- Clean global vs project separation; project memories never bleed across projects.
- Never block or break a session; instantly disableable.
- Keep every byte of memory out of any git repo.

## Non-Goals (explicitly deferred)

- **HDC associative layer.** Theoretical/under test elsewhere; not in this build.
- **MEMORY.md changes of any kind.** Left completely alone.
- **MEMORY.md ↔ DB derivation/sync/promotion pipeline.** The two systems are
  independent. (An optional "skip injecting rows already substantially present in
  MEMORY.md" filter is a *later* tuning, not structural.)
- **Automated promotion suggestions** from DB into MEMORY.md.

## Key Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Faithfulness of port | Tier 3 — full engine (SQLite + FTS5 + bm25 + counters + lifecycle) |
| 2 | Coupling to MEMORY.md | **Decoupled.** Two independent systems; MEMORY.md unchanged |
| 3 | DB physical location | Private global `~/.claude` path-keyed dir. **Never in any repo / git history** |
| 4 | Source of truth | The DB is system-of-record for its own data. MEMORY.md is system-of-record for its own data. No derivation between them |
| 5 | Capture model | Hybrid: inline tool (Claude judgment) + one session-end sweep pass; both through `captureOrMerge` |
| 6 | Retrieval model | Auto per-turn (bm25-gated) **+** explicit `recall_memory` tool (ungated, includes archived) |
| 7 | Scope model | Merged, project-scoped: retrieval searches `global` + current project only |
| 8 | Scope-key granularity | **Git repo root** (`git rev-parse --show-toplevel`), fallback to cwd. Identifier only — repo is never written to |
| 9 | Editing | Tool-driven everyday (`memory_add/edit/forget`); rare hand-edit of exports as safety valve |
| 10 | Durability | Scheduled DB backups (backup-on-boot-if-stale primary; OS scheduled task optional) |
| 11 | Kill switch | First-class. One flag disables all hooks/tools → instant revert to today |
| 12 | Tech stack | Python (stdlib `sqlite3` + FTS5); Windows-clean paths/invocation |

## Architecture

Three conceptual layers (the user's vision); only the middle one is built here:

```
┌─ Always loaded by harness (UNCHANGED) ──────────────────┐
│  GLOBAL MEMORY.md  +  PROJECT MEMORY.md   (hot-set)     │  lean, curated
└─────────────────────────────────────────────────────────┘
┌─ THIS BUILD: injected on demand by hook/tool ───────────┐
│  SQLite memory.db: global rows + current-project rows   │  cold long-tail
│  FTS5 · bm25 · captureHits/recallHits/lastUsedAt/status │  scope-aware
└─────────────────────────────────────────────────────────┘
   HDC associative layer — DEFERRED, not in this build
```

The two stores are complementary, not derived from each other. Editorial guideline
only: high-level always-relevant facts → MEMORY.md (as today); granular/long-tail/
searchable facts → DB. Mild duplication is harmless.

## Data Model

Single DB, single table, a `scope` column (not per-scope files): one FTS index, one
backup target, merged retrieval is `WHERE scope IN ('global', '<repo-key>')`. Stored
as `memory.db` in the global path-keyed `~/.claude` dir.

### `memories` table

| column | type | purpose |
|--------|------|---------|
| `id` | INTEGER PK | |
| `scope` | TEXT NOT NULL | `'global'` or `'<repo-key>'` — the two-level axis |
| `type` | TEXT NOT NULL | facet: fact / preference / person / goal / project / other |
| `name` | TEXT NOT NULL | short label (3–5 words) |
| `description` | TEXT NOT NULL | one-line summary |
| `body` | TEXT NOT NULL | self-contained memory content |
| `normalizedKey` | TEXT NOT NULL | `sha1(scope │ type │ normalizedBody)`, **UNIQUE** — hard-dedup key |
| `captureHits` | INTEGER DEFAULT 1 | times re-derived → confidence signal |
| `recallHits` | INTEGER DEFAULT 0 | times served into context → relevance signal |
| `lastUsedAt` | INTEGER NOT NULL | latest of create/capture/recall → drives archival |
| `status` | TEXT DEFAULT 'active' | `active` / `archived` |
| `createdAt` | INTEGER NOT NULL | |
| `updatedAt` | INTEGER NOT NULL | |
| `source` | TEXT NULL | provenance: `inline` / `sweep` / `manual` (diagnostic only) |

**Indices:** UNIQUE(`normalizedKey`), plus `scope`, `type`, `status`, `lastUsedAt`.

**FTS5:** external-content virtual table `memories_fts` over (`name`, `description`,
`body`), kept in sync by insert/update/delete triggers. (Standard FTS5 external-content setup.)

### Normalization & dedup (scope-aware)

- `normalizeBody`: lowercase, strip non-alphanumerics to spaces, collapse whitespace,
  trim.
- `normalizedKey = sha1(scope | type | normalizeBody(body))`. Folding `scope` and
  `type` in means: a project capture can never silently merge into a global row, and
  "lives in Iowa" as a global `fact` stays distinct from a same-worded project note.
- `FtsQuery.fromUserText`: tokens ≥ 3 chars, distinct, max 8, OR-joined prefix
  matches (`"tok"*`). Returns null when nothing usable → caller skips the query.

## Runtime Flow & Claude Code Integration

Four touchpoints:

### ① `SessionStart` hook — boot (no model call)
- Open/migrate `memory.db`.
- Compute scope key: `git rev-parse --show-toplevel`, fallback to cwd.
- Run **archival sweep**: active rows with `lastUsedAt < now - ARCHIVE_AFTER_DAYS` →
  `archived`. Idempotent.
- Run **backup-if-stale** (see Durability).

### ② `UserPromptSubmit` hook — auto-retrieve, per turn (no model call)
- Prompt text → `FtsQuery` → FTS5 search
  `WHERE scope IN ('global','<repo-key>') AND status='active'`, ranked by bm25,
  gated by `RAG_INJECT_THRESHOLD`.
- Inject top-k strong matches as a `<memory>` context block.
- Bump `recallHits` + `lastUsedAt` on served rows.
- Negligible latency (one SQLite query).

### ③ `recall_memory` tool — explicit deep search, as needed
- Searches **active + archived**, **no bm25 gate**, both scopes.
- Auto-revives archived hits (bumps recall + flips to active).
- Provides an explicit-recall path.

### ④ Capture — hybrid, all through `captureOrMerge`
- **Inline:** `memory_add` tool Claude calls mid-session when it judges something
  durable. Claude sets `scope` (project-specific → `<repo-key>`; about-user /
  cross-project → `global`) and `type`.
- **Session-end sweep:** `Stop`/`SessionEnd` hook fires **one** extraction pass over
  the transcript (the only recurring per-session model call), emits candidates in a
  strict pipe-delimited format, each parsed and run through `captureOrMerge`.
- `captureOrMerge`: (1) hard-key match → bump `captureHits`; (2) fuzzy-FTS match
  constrained to **same scope + type**, active then archived (archived match
  auto-revives); (3) else insert new row.

### Management tools (everyday editing path)
`memory_edit` / `memory_forget` write the DB directly. Hand-editing an export is the
rare safety valve, not the primary path.

### Counter semantics
- `captureHits` +1 per re-derive/merge (new insert starts at 1).
- `recallHits` +1 per serve (auto-injection or explicit recall).
- `lastUsedAt` = latest of create / capture-merge / recall.
- `status`: archival flips active→archived on idle; any capture or recall hit revives.

## Error Handling & Graceful Degradation

- **Fail-open everywhere.** Every hook wraps its work; any error (DB locked, missing,
  corrupt, FTS throws) → log and return empty. A failing engine **never blocks a
  turn**; the session proceeds on MEMORY.md alone, exactly like today.
- **One-flag kill switch.** A config flag (env var / settings) disables all hooks +
  tools → instant revert to current behavior. Nothing else depends on the engine.
- **Corruption recovery.** Boot integrity-check; on failure, quarantine the bad file
  and restore from the latest backup (or recreate empty). Never crash the session.

## Durability — Backups

- **Primary: backup-on-boot-if-stale.** `SessionStart` checks "last backup > 24h?";
  if so, `VACUUM INTO` a timestamped copy in `~/.claude/.../backups/`, keep last N.
  Self-contained, cross-platform, no OS scheduler dependency.
- **Optional upgrade:** OS scheduled task (Windows Task Scheduler) for cadence
  independent of when Claude is launched.

## Tech Stack

- **Python**, stdlib `sqlite3` + FTS5. Hooks are plain executables.
- Pure-logic modules (normalizer, FtsQuery, candidate parser, captureOrMerge core)
  kept dependency-free and trivially unit-testable — the same dependency-isolation discipline used by
  isolating its pure-Kotlin helpers from Android.
- Windows-clean paths and invocation (user is on Windows).

## Testing

- **Unit (pure logic):** `normalizeBody`/`normalizedKey`, `FtsQuery` sanitizer,
  candidate `parse`, `captureOrMerge` outcomes (insert / merge-by-key /
  merge-by-fuzzy), **scope isolation** (project capture never merges into global),
  archival→revive, counter bumps. In-memory SQLite for DAO tests; injected clock for
  time-based logic (injected-clock pattern).
- **Integration:** hook scripts invoked with sample Claude Code payloads.

## Tunable Constants (initial defaults)

All are constants in code to start; surfaced as settings only if real usage shows the
static value is wrong (constants-first approach).

| constant | default | meaning |
|----------|---------|---------|
| `ARCHIVE_AFTER_DAYS` | 365 | idle window before active → archived (chosen default) |
| `RAG_INJECT_THRESHOLD` | -1.0 | bm25 gate for auto-injection; more-negative = stricter. Conservative start — tune to -3.0/-6.0 as the pile grows |
| `RETRIEVE_K` | 5 | max rows injected per turn (auto) and per explicit recall |
| `MAX_MEMORIES_PER_SWEEP` | 5 | cap on candidates from one session-end extraction pass |
| `BACKUP_STALE_HOURS` | 24 | min age before backup-on-boot fires |
| `BACKUP_RETENTION` | 7 | timestamped backups kept (oldest pruned) |
| field length caps | name ≤ 80, description ≤ 200, body ≤ 500 | bound a runaway memory (chosen caps) |

## Build Phasing

Each phase independently testable.

1. **Core + DB** — schema, indexer/DAO, `captureOrMerge`, normalizer/FtsQuery, small
   CLI to exercise it. Zero Claude Code wiring required to test.
2. **Retrieval** — auto (bm25-gated) + explicit, scope resolution, `UserPromptSubmit`
   hook + `recall_memory` tool.
3. **Capture** — `memory_add` inline tool + session-end sweep hook +
   `memory_edit`/`memory_forget`.
4. **Lifecycle & safety** — archival sweep, backups, kill switch, degradation
   hardening.

**Deferred:** HDC associative layer; MEMORY.md-disjoint injection filter; automated
promotion suggestions.

## Open Risks / Notes

- Per-turn auto-injection runs alongside the harness's own native file recall;
  because the systems are decoupled, overlap is mild redundancy, not a correctness
  issue. Disjoint filter can be added later if it proves noisy.
- The session-end sweep is the only recurring token cost. If it proves not worth it,
  inline-only capture still works and the sweep can be disabled independently.
- Scope-key from `git rev-parse` assumes work happens inside repos; cwd fallback
  covers non-repo dirs but could fragment if the user launches from varying subdirs.
```
