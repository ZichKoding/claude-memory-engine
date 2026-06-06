# memory-engine — long-term memory for Claude Code

**Persistent, local, per-project long-term memory for [Claude Code](https://www.anthropic.com/claude-code).**
A small SQLite + FTS5 engine that (1) **auto-injects** relevant memories into every turn,
(2) lets the agent **explicitly recall** them on demand, and (3) **captures** durable
facts about you and your work — automatically, as you talk. It runs entirely on your
machine, is decoupled from Claude Code's built-in `MEMORY.md`, and needs **no MCP server**.

> Keywords: Claude Code memory · persistent agent memory · long-term memory for AI agents ·
> SQLite FTS5 memory store · Claude Code hook + skill · local/offline memory · RAG over personal facts

---

## Why

Claude Code forgets between sessions, and the built-in `MEMORY.md` hot-set gets bloated if
you put everything in it. `memory-engine` adds the **searchable cold long-tail**: a local
database of durable facts (preferences, goals, people, project details, decisions) that
surface *only when relevant*, so they help without eating context. It's additive and
**fully decoupled** — `MEMORY.md` keeps working exactly as it does today.

## What it does

| Capability | How | Surface |
|---|---|---|
| **Auto-retrieval** | A `UserPromptSubmit` hook fuzzy-searches (FTS5/bm25) your prompt and injects the top matches as a `<memory>` block. **Fail-open** — never blocks a turn. | per turn, automatic |
| **Explicit recall** | The agent runs `memory-engine recall` (active **+** archived; global + the current project when given `--cwd`) when it needs to dig deeper. | `recall-memory` skill |
| **Proactive capture** | The agent saves durable, directly-stated facts on its own judgment — recall first, then add/skip/edit. | `capture-memory` skill + a standing instruction |

Plus: **two-level scope** (a `global` store + one per git project, merged at read time),
deterministic **counters/lifecycle** (`captureHits`/`recallHits`/`lastUsedAt`, idle →
archived → revived on hit), and an `edit` path for corrections.

## How dedup works (important design point)

- **Search is fuzzy** (FTS5 OR-token + bm25) — memories surface on partial/paraphrased queries.
- **Capture dedup is *exact*** (scope + type + normalized body) — identical re-captures
  reinforce a memory; they never silently merge unrelated ones.
- **Semantic dedup is the agent's job**: the capture skill *recalls first*, then decides
  skip / edit / add. This is more accurate than lexical auto-merge — and, unlike the
  earlier fuzzy auto-merge (removed), it can't lose data.

## Install

The engine is a `uv` tool plus a little Claude Code wiring (skills, a hook, a standing
instruction). Full steps — including the snippets to merge into `~/.claude/settings.json`
and `~/.claude/CLAUDE.md` — are in **[`claude-integration/INSTALL.md`](claude-integration/INSTALL.md)**.

```bash
uv tool install --editable .          # exposes `memory-engine` on PATH
# then copy the two skills + merge the settings/CLAUDE.md snippets (see INSTALL.md), and restart Claude Code
```

## Usage (CLI)

```bash
memory-engine add --type preference --name "Editor" --description "pref" --body "prefers tabs over spaces"
memory-engine add --cwd "/path/to/repo" --type project --name "..." --description "..." --body "..."   # project-scoped
memory-engine recall --query "editor preference" --cwd "/path/to/repo"   # explicit search
memory-engine edit --id 7 --body "prefers spaces now"                    # correct a fact
memory-engine list ; memory-engine stats ; memory-engine sweep           # inspect / archive idle
memory-engine inject                                                     # UserPromptSubmit hook body (reads JSON on stdin)
```

In a Claude Code session the agent drives these for you via the `recall-memory` and
`capture-memory` skills — you generally just talk.

## Architecture

```
Always loaded by Claude Code (UNCHANGED):  global + project MEMORY.md   ← lean hot-set
Injected on demand by this engine:         SQLite memory.db (FTS5/bm25) ← searchable cold long-tail
```

- Pure-logic core (`normalizer`, `fts_query`, `parser`) with zero DB deps; a
  `MemoryRepository` over `sqlite3` with FTS5 external-content + sync triggers; a thin CLI.
- The DB lives at `~/.claude/memory/memory.db` (private, path-keyed) — **never in any repo
  or git history**.
- **No MCP server**: the model invokes the existing CLI via Bash, surfaced through skills —
  leaner, discoverable, nothing to register or keep running.
- Stdlib-only runtime (Python ≥3.10); built and tested with `uv` on Windows.

Design rationale, trade-offs, and the full data model are in
**[`docs/superpowers/specs/`](docs/superpowers/specs/)**; the phase-by-phase implementation
plans are in **[`docs/superpowers/plans/`](docs/superpowers/plans/)**.

## Status & roadmap

- ✅ **Phase 1** — core: schema, FTS5, exact dedup, counters, lifecycle, CLI
- ✅ **Phase 2a** — auto-retrieval `UserPromptSubmit` hook (top-k, scope-merged, fail-open)
- ✅ **Phase 2b** — explicit `recall` (CLI + skill; active + archived)
- ✅ **Phase 3** — inline proactive capture (skill + standing instruction) + `edit`
- ⏭️ **Phase 4** — `SessionStart` archival/backup, kill switch, calibrated relevance gating,
  optional near-duplicate `consolidate` pass
- 🔭 **Later** — associative (HDC) recall layer (research)

## Privacy

All memory is stored locally under `~/.claude/` and is never written into a repo or pushed.
The hook and skills can be disabled at any time (see INSTALL.md → off-switch table).

## Tech

Python (stdlib `sqlite3` + FTS5) · `uv` · pytest. Cross-platform; developed on Windows.
