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


def test_restore_picks_numerically_newest_healthy_backup(tmp_path):
    # Two HEALTHY backups with UNEQUAL-width stamps and different row counts. The numeric
    # newest (1000) is the freshest snapshot and must win. Lexicographically, "memory-999.db"
    # sorts AFTER "memory-1000.db", so a string sort would wrongly restore the older 999 one
    # — this test only passes when recover_if_corrupt sorts by the numeric epoch-ms stamp.
    db = str(tmp_path / "m.db"); _good_db(db)
    bdir = tmp_path / "backups"; bdir.mkdir()

    # older snapshot (stamp 999): TWO rows
    older = str(bdir / "memory-999.db"); _good_db(older)
    c = sqlite3.connect(older)
    c.execute("INSERT INTO memories(scope,type,name,description,body,normalizedKey,"
              "captureHits,recallHits,lastUsedAt,status,createdAt,updatedAt,source) "
              "VALUES('global','fact','n2','d','b2','k2',1,0,1,'active',1,1,'manual')")
    c.commit(); c.close()
    assert sqlite3.connect(older).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2

    # newer snapshot (stamp 1000): ONE row (the canonical freshest state)
    con = sqlite3.connect(db); con.execute("VACUUM INTO ?", (str(bdir / "memory-1000.db"),)); con.close()

    Path(db).write_bytes(b"garbage")
    assert recover_if_corrupt(db, str(bdir)) == "restored"
    # must restore the numerically-newest (1000 → 1 row), NOT the lexicographic-newest (999 → 2 rows)
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_corrupt_db_no_backup_recreates_empty(tmp_path):
    db = str(tmp_path / "m.db"); _good_db(db)
    Path(db).write_bytes(b"garbage")
    assert recover_if_corrupt(db, str(tmp_path / "backups")) == "recreated"
    assert connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_missing_db_is_ok(tmp_path):
    assert recover_if_corrupt(str(tmp_path / "nope.db"), str(tmp_path / "b")) == "ok"


def test_quarantine_does_not_clobber_prior_corrupt_file(tmp_path):
    # A previous corruption already left a .corrupt-0 quarantine. A new corruption must
    # NOT overwrite it (that would be silent data loss) — it gets the next free index.
    db = str(tmp_path / "m.db"); _good_db(db)
    prior = Path(db + ".corrupt-0"); prior.write_bytes(b"earlier quarantined corrupt db")
    Path(db).write_bytes(b"newly corrupt garbage")
    assert recover_if_corrupt(db, str(tmp_path / "backups")) == "recreated"
    assert prior.read_bytes() == b"earlier quarantined corrupt db"  # untouched
    assert Path(db + ".corrupt-1").exists()                          # new quarantine, fresh index
