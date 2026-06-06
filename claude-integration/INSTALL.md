# Installing the Claude Code integration

The engine is a standalone CLI; this directory holds the **Claude Code wiring** that the
repo can't keep in your live `~/.claude/` for you. Setup is manual (copy a couple of
files, merge two config snippets). Everything here is portable across machines.

> Paths use `~` for your home dir. On Windows that's `C:\Users\<you>`.

## 1. Install the engine as a global tool

From the repo root:

```bash
uv tool install --editable .
```

This puts `memory-engine` on your PATH (e.g. `~/.local/bin/memory-engine`), so it runs in
any project. Confirm: `memory-engine --help`.

## 2. Install the skills

Copy both skill folders into your user-level skills dir:

```bash
cp -r claude-integration/skills/recall-memory   ~/.claude/skills/recall-memory
cp -r claude-integration/skills/capture-memory  ~/.claude/skills/capture-memory
```

- `recall-memory` — lets the model explicitly search memory on demand (Phase 2b).
- `capture-memory` — the proactive capture flow: recall → decide add/skip/edit → write (Phase 3).

## 3. Merge the settings snippet

Open `claude-integration/settings.snippet.json` and merge its two blocks into your
`~/.claude/settings.json` (**merge — don't overwrite the whole file**):

- `permissions.allow` → add `"Bash(memory-engine*)"` so the skills run without a prompt.
- `hooks.UserPromptSubmit` → the per-turn auto-retrieval hook (Phase 2a).
- `hooks.SessionStart` → boot-time maintenance: corruption-recovery + archival sweep +
  stale-gated backup, fail-open (Phase 4a).

The hook `command` uses the PATH-resolved `memory-engine`. If Claude Code can't resolve it
on Windows, use the absolute path to the uv-tool shim instead, e.g.
`"command": "C:/Users/<you>/.local/bin/memory-engine.exe"`.

Validate it still parses:
```bash
python -c "import json,pathlib; json.load(open(pathlib.Path.home()/'.claude'/'settings.json')); print('ok')"
```

## 4. Merge the standing instruction

Append the section in `claude-integration/CLAUDE.snippet.md` to your user-global
`~/.claude/CLAUDE.md` (create the file if it doesn't exist). This is what makes proactive
capture fire on ambient mentions (not just explicit "remember this").

## 5. Restart Claude Code

Hooks, skills, and the global `CLAUDE.md` instruction all load **at session start**, so
restart/resume for the wiring to take effect.

## 6. Verify

```bash
# write + read directly:
memory-engine add --type preference --name "Test" --description "t" --body "alpha bravo charlie"
memory-engine recall --query "alpha bravo charlie"
```

In a restarted session: state a durable fact in passing (no "remember") and confirm it's
captured, then ask the model to recall it.

## What each piece does (and the off switch)

| Piece | Effect | Disable |
|---|---|---|
| **Master kill switch** | makes the `UserPromptSubmit` **and** `SessionStart` hooks no-op instantly (no restart needed) | set env `MEMORY_ENGINE_DISABLED=1` (values `1/true/yes/on`) **or** create the file `~/.claude/memory/DISABLED`; re-enable by clearing the env var / deleting the file |
| `UserPromptSubmit` hook | auto-injects relevant memories every turn (fail-open) | remove the `hooks.UserPromptSubmit` block, or use the kill switch above |
| `SessionStart` hook | boot-time corruption-recovery + archival sweep + stale-gated backup (fail-open) | remove the `hooks.SessionStart` block, or use the kill switch above |
| `recall-memory` skill | explicit on-demand search | delete `~/.claude/skills/recall-memory` |
| `capture-memory` skill + `CLAUDE.md` instruction | proactive capture | delete the skill dir and/or remove the instruction |
| `Bash(memory-engine*)` allowlist | no permission prompts for the CLI | remove the allow entry |

## Notes

- The DB lives at `~/.claude/memory/memory.db` (self-resolved; never in any repo).
- Backups land in `~/.claude/memory/backups/` as `memory-<epoch_ms>.db` snapshots —
  written at most once per 24h by the `SessionStart` hook, newest 7 retained. If the live
  DB is ever corrupt, the next session restores from the newest **healthy** snapshot.
- This integration intentionally uses **no MCP server** — the model invokes the global
  CLI via Bash, surfaced through the skills.
- The `plan-redteam` agent referenced in this project's history is **general dev tooling**,
  not part of the memory engine, so it is intentionally not bundled here.
