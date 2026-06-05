# src/memory_engine/cli.py
"""Manual driver for Phase 1 — no Claude Code wiring. Real-clock production use."""
import argparse
import json
import sys
import time

from memory_engine.db import connect
from memory_engine.formatting import format_memory_block
from memory_engine.parser import Parsed
from memory_engine.paths import default_db_path
from memory_engine.repository import MemoryRepository, RETRIEVE_K
from memory_engine.scope import scopes_for


def _real_clock() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", default=None,
                        help="SQLite path; defaults to ~/.claude/memory/memory.db")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    for f in ("scope", "type", "name", "description", "body"):
        p_add.add_argument(f"--{f}", required=True)

    p_search = sub.add_parser("search")
    p_search.add_argument("--scopes", required=True, help="comma-separated")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=5)

    sub.add_parser("list")
    sub.add_parser("stats")
    sub.add_parser("sweep")
    sub.add_parser("inject")  # UserPromptSubmit hook entry; reads JSON on stdin

    args = parser.parse_args(argv)
    if args.cmd == "inject":
        return _run_inject(args.db)
    conn = connect(args.db or default_db_path())
    repo = MemoryRepository(conn, clock=_real_clock)

    if args.cmd == "add":
        outcome = repo.capture_or_merge(
            Parsed(args.type, args.name, args.description, args.body), scope=args.scope)
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
    conn.close()
    return 0


def _run_inject(db: str | None) -> int:
    """UserPromptSubmit hook body. Reads the hook JSON from stdin, injects relevant
    memories as additionalContext, and ALWAYS returns 0 — a non-zero/blocking exit
    on this event would erase the user's prompt. Any failure → emit nothing."""
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
