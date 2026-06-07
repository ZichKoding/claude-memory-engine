# src/memory_engine/backup.py
"""Stale-gated SQLite backups: VACUUM INTO a timestamped copy at most once per window,
keeping the newest N. Time is injected (now_ms) for testability."""
import os
import sqlite3
from pathlib import Path

BACKUP_STALE_HOURS = 24
BACKUP_RETENTION = 7
_HOUR_MS = 3_600_000


def backup_if_stale(db_path: str, backups_dir: str, *, now_ms: int,
                    stale_hours: int = BACKUP_STALE_HOURS,
                    retention: int = BACKUP_RETENTION) -> bool:
    """If `db_path` exists and the newest backup is older than `stale_hours` (or there is
    none), write a fresh `memory-<now_ms>.db` snapshot via VACUUM INTO and prune to the
    newest `retention`. Returns True if a backup was made. Never raises (best-effort)."""
    try:
        if not os.path.exists(db_path):
            return False
        bdir = Path(backups_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        existing = sorted(bdir.glob("memory-*.db"), key=lambda p: (_stamp_of(p) or 0))
        if existing:
            newest_ms = _stamp_of(existing[-1])
            if newest_ms is not None and (now_ms - newest_ms) < stale_hours * _HOUR_MS:
                return False
        dest = bdir / f"memory-{now_ms}.db"
        src = sqlite3.connect(db_path)
        try:
            src.execute("VACUUM INTO ?", (str(dest),))
        finally:
            src.close()
        _prune(bdir, retention)
        return True
    except (OSError, sqlite3.Error):
        return False  # backups are best-effort; never block boot


def _stamp_of(path: Path):
    try:
        return int(path.stem.split("memory-", 1)[1])
    except (ValueError, IndexError):
        return None


def _prune(bdir: Path, retention: int) -> None:
    backups = sorted(bdir.glob("memory-*.db"), key=lambda p: (_stamp_of(p) or 0))
    for old in (backups[:-retention] if retention > 0 else backups):
        old.unlink(missing_ok=True)
