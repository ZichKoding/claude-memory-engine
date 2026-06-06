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

## Flow — ALWAYS recall before you add

This recall-then-decide step is how duplicates are avoided: the engine only auto-merges
*exact* re-captures, so a reworded near-duplicate would create a NEW row unless you catch
it here. **You are the semantic-dedup layer.**

1. **Search for an existing memory first:**
   ```bash
   memory-engine recall --query "<key terms of the fact>" --cwd "<current working directory>"
   ```
2. **Decide based on what recall returns:**
   - Already captured and still accurate → **do nothing.**
   - Same fact but it CHANGED or was wrong → **update** the existing row by id:
     ```bash
     memory-engine edit --id <id> --body "<corrected fact>"
     ```
   - Genuinely new (recall shows nothing equivalent) → **add** it (scope + type below).

3. **Add a new memory:**
   ```bash
   # about the user / cross-project → global:
   memory-engine add --type <type> --name "<3-5 word label>" --description "<one line>" --body "<self-contained fact>"
   # specific to the current project → pass --cwd so it's scoped to this repo:
   memory-engine add --cwd "<current working directory>" --type <type> --name "..." --description "..." --body "..."
   ```

- `--type` is one of: fact, preference, person, goal, project, other.
- Keep `body` self-contained (don't rely on conversation context to interpret it).
- Dedup is EXACT (scope + type + normalized body): an identical re-add just reinforces the
  existing memory; a paraphrase makes a new row. So don't blind-add — step 1 (recall +
  judge) is what prevents near-duplicates.

Do this quietly as part of the conversation; don't announce every save.
