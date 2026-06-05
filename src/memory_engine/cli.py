# src/memory_engine/cli.py
"""Manual driver for Phase 1 — no Claude Code wiring. Real-clock production use."""
import argparse
import json
import time

from memory_engine.db import connect
from memory_engine.parser import Parsed
from memory_engine.repository import MemoryRepository


def _real_clock() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", required=True)
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

    args = parser.parse_args(argv)
    conn = connect(args.db)
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


if __name__ == "__main__":
    raise SystemExit(main())
