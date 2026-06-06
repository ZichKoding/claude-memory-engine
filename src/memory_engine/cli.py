# src/memory_engine/cli.py
"""Manual driver for Phase 1 — no Claude Code wiring. Real-clock production use."""
import argparse
import json
import sys
import time

from memory_engine.backup import backup_if_stale
from memory_engine.control import is_disabled
from memory_engine.db import connect, recover_if_corrupt
from memory_engine.formatting import format_memory_block
from memory_engine.parser import Parsed
from memory_engine.paths import default_db_path, backups_dir
from memory_engine.repository import MemoryRepository, RETRIEVE_K
from memory_engine.scope import resolve_scope_key, scopes_for


def _real_clock() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", default=None,
                        help="SQLite path; defaults to ~/.claude/memory/memory.db")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--scope", default="global")
    p_add.add_argument("--cwd", default=None,
                       help="if given, scope = this project's key (overrides --scope)")
    for f in ("type", "name", "description", "body"):
        p_add.add_argument(f"--{f}", required=True)

    p_search = sub.add_parser("search")
    p_search.add_argument("--scopes", required=True, help="comma-separated")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=5)

    sub.add_parser("list")
    sub.add_parser("stats")
    sub.add_parser("sweep")
    sub.add_parser("inject")  # UserPromptSubmit hook entry; reads JSON on stdin
    sub.add_parser("session-init")  # SessionStart hook entry; reads JSON on stdin

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--id", type=int, required=True)
    for f in ("type", "name", "description", "body"):
        p_edit.add_argument(f"--{f}", default=None)

    p_recall = sub.add_parser("recall")
    p_recall.add_argument("--query", required=True)
    p_recall.add_argument("--cwd", default=None,
                          help="project dir to scope to; omit for global-only")
    p_recall.add_argument("--limit", type=int, default=RETRIEVE_K)

    args = parser.parse_args(argv)
    if args.cmd == "inject":
        return _run_inject(args.db)
    if args.cmd == "session-init":
        return _run_session_init(args.db)
    conn = connect(args.db or default_db_path())
    repo = MemoryRepository(conn, clock=_real_clock)

    if args.cmd == "add":
        scope = resolve_scope_key(args.cwd) if args.cwd else args.scope
        outcome = repo.capture_or_merge(
            Parsed(args.type, args.name, args.description, args.body), scope=scope)
        print(type(outcome).__name__, getattr(outcome, "id", ""))
    elif args.cmd == "search":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        for m in repo.search(args.query, scopes=scopes, limit=args.limit):
            print(f"[{m.scope}/{m.type}] {m.name} :: {m.body}")
    elif args.cmd == "list":
        for r in conn.execute("SELECT scope,type,name,status FROM memories ORDER BY id"):
            print(f"[{r['scope']}/{r['type']}] {r['name']} ({r['status']})")
    elif args.cmd == "stats":
        total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) c FROM memories WHERE status='active'").fetchone()["c"]
        print(json.dumps({"total": total, "active": active, "archived": total - active}))
    elif args.cmd == "sweep":
        print(json.dumps({"archived": repo.run_archival_sweep()}))
    elif args.cmd == "edit":
        result = repo.edit(args.id, type=args.type, name=args.name,
                           description=args.description, body=args.body)
        messages = {"updated": f"Updated memory {args.id}",
                    "not_found": f"Memory {args.id} not found",
                    "conflict": f"Edit would duplicate an existing memory; memory {args.id} unchanged"}
        print(messages[result])
    elif args.cmd == "recall":
        scopes = scopes_for(args.cwd) if args.cwd else ["global"]
        memories = repo.retrieve_explicit(args.query, scopes=scopes, k=args.limit)
        block = format_memory_block(memories)
        print(block if block else "No matching memories found.")
    conn.close()
    return 0


def _run_inject(db: str | None) -> int:
    """UserPromptSubmit hook body. Reads the hook JSON from stdin, injects relevant
    memories as additionalContext, and ALWAYS returns 0 — a non-zero/blocking exit
    on this event would erase the user's prompt. Any failure → emit nothing."""
    try:
        if is_disabled():
            return 0
        data = json.loads(sys.stdin.read())
        prompt = (data.get("prompt") or "").strip()
        cwd = data.get("cwd") or "."
        if not prompt:
            return 0
        conn = connect(db or default_db_path())
        try:
            repo = MemoryRepository(conn, clock=_real_clock)
            memories = repo.retrieve(prompt, scopes=scopes_for(cwd), k=RETRIEVE_K)
        finally:
            conn.close()
        block = format_memory_block(memories)
        if block:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": block,
            }}))
        return 0
    except Exception:
        return 0  # fail-open: never block a turn


def _run_session_init(db: str | None) -> int:
    """SessionStart hook body: kill-switch check → recover-if-corrupt → archival sweep →
    backup-if-stale. ALWAYS returns 0 — must never block a session from starting."""
    try:
        if is_disabled():
            return 0
        try:
            _ = sys.stdin.read()  # drain stdin (source/cwd available but unused here)
        except Exception:
            pass
        path = db or default_db_path()
        recover_if_corrupt(path, backups_dir())
        conn = connect(path)
        try:
            MemoryRepository(conn, clock=_real_clock).run_archival_sweep()
        finally:
            conn.close()
        backup_if_stale(path, backups_dir(), now_ms=_real_clock())
        return 0
    except Exception:
        return 0  # fail-open: never block session start


if __name__ == "__main__":
    raise SystemExit(main())
