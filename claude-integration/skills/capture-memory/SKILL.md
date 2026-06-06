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
