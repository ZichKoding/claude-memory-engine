# tests/test_db_recovery.py
import sqlite3
from pathlib import Path
from memory_engine.db import connect, recover_if_corrupt


def _good_db(path):
    c = connect(path)  # creates schema
    c.execute("INSERT INTO memories(scope,type,name,description,body,normalizedKey,"
              "captureHits,recallHits,lastUsedAt,status,createdAt,updatedAt,source) "
              "VALUES('global','fact','n','d','b','k',1,0,1,'active',1,1,'manual')")
    c.commit(); c.close()


def test_healthy_db_untouched(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    assert recover_if_corrupt(db, str(tmp_path / "backups")) == "ok"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_corrupt_db_restored_from_backup(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    bdir = tmp_path / "backups"; bdir.mkdir()
    con = sqlite3.connect(db); con.execute("VACUUM INTO ?", (str(bdir / "memory-1000.db"),)); con.close()
    Path(db).write_bytes(b"this is not a sqlite database at all")
    assert recover_if_corrupt(db, str(bdir)) == "restored"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    assert list(tmp_path.glob("m.db.corrupt-*"))  # quarantined, not deleted


def test_restore_skips_corrupt_newest_backup(tmp_path):
    # newest snapshot is corrupt, an older one is good → must restore from the GOOD one.
    db = str(tmp_path / "m.db"); _good_db(db)
    bdir = tmp_path / "backups"; bdir.mkdir()
    con = sqlite3.connect(db); con.execute("VACUUM INTO ?", (str(bdir / "memory-1000.db"),)); con.close()  # good
    (bdir / "memory-2000.db").write_bytes(b"corrupt snapshot, not a db")            # newest, bad
    Path(db).write_bytes(b"garbage")
    assert recover_if_corrupt(db, str(bdir)) == "restored"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_corrupt_db_no_backup_recreates_empty(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    Path(db).write_bytes(b"garbage")
    assert recover_if_corrupt(db, str(tmp_path / "backups")) == "recreated"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_missing_db_is_ok(tmp_path):
    assert recover_if_corrupt(str(tmp_path / "nope.db"), str(tmp_path / "b")) == "ok"
