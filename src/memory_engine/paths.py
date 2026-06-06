# src/memory_engine/paths.py
"""Filesystem locations the engine owns. The DB lives in the private global
~/.claude dir (never in any repo). Self-resolved because Claude Code hooks expose
no ${HOME} placeholder and no settings env block."""
from pathlib import Path


def default_db_path() -> str:
    """`~/.claude/memory/memory.db`. Ensures the parent dir exists so sqlite can
    open/create the file. Returns a string path for sqlite3.connect."""
    directory = Path.home() / ".claude" / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / "memory.db")


def backups_dir() -> str:
    """`~/.claude/memory/backups/`, created if missing."""
    directory = Path.home() / ".claude" / "memory" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)
