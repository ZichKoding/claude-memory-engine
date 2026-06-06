# tests/test_backup.py
import sqlite3
from pathlib import Path
from memory_engine.backup import backup_if_stale

HOUR_MS = 3_600_000


def _make_db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()


def test_creates_backup_when_none_exists(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    made = backup_if_stale(db, bdir, now_ms=1_000 * HOUR_MS)
    assert made is True
    backups = list(Path(bdir).glob("memory-*.db"))
    assert len(backups) == 1
    assert sqlite3.connect(str(backups[0])).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_skips_when_recent_backup_exists(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    assert backup_if_stale(db, bdir, now_ms=1_000 * HOUR_MS) is True
    assert backup_if_stale(db, bdir, now_ms=1_001 * HOUR_MS) is False  # 1h later (<24h) → skip
    assert len(list(Path(bdir).glob("memory-*.db"))) == 1


def test_makes_new_backup_when_stale(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    backup_if_stale(db, bdir, now_ms=1_000 * HOUR_MS)
    assert backup_if_stale(db, bdir, now_ms=1_025 * HOUR_MS) is True  # 25h later → stale
    assert len(list(Path(bdir).glob("memory-*.db"))) == 2


def test_prunes_to_retention(tmp_path):
    db = str(tmp_path / "m.db"); _make_db(db)
    bdir = str(tmp_path / "backups")
    # 10 stale-spaced backups, retention default 7 → keep the NEWEST 7 (not the oldest)
    for i in range(10):
        backup_if_stale(db, bdir, now_ms=(1_000 + i * 25) * HOUR_MS)
    stamps = sorted(int(p.stem.split("memory-")[1]) for p in Path(bdir).glob("memory-*.db"))
    assert stamps == [(1_000 + i * 25) * HOUR_MS for i in range(3, 10)]


def test_missing_db_is_noop(tmp_path):
    assert backup_if_stale(str(tmp_path / "nope.db"), str(tmp_path / "b"), now_ms=HOUR_MS) is False
