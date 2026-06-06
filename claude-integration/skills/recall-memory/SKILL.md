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

- `--query`: a few content words (the search is keyword/FTS-based; short and specific
  beats a full sentence).
- `--cwd`: the absolute path of the current working directory, so results include this
  project's memories plus global ones. Omit `--cwd` to search global memories only.

Read the returned `<memory>` block and use it to inform your answer. If it prints
"No matching memories found.", there's nothing stored for that query — say so rather
than guessing.
