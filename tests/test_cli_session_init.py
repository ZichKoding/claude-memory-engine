# tests/test_cli_session_init.py
import io, json, sqlite3
from pathlib import Path
import pytest
from memory_engine.cli import main


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    # session-init touches ~/.claude (backups_dir + kill-switch flag) regardless of --db,
    # so redirect HOME to tmp and clear the env flag — keep tests off the real home dir.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)


def _seed_old(db, lastUsedAt):
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "n",
          "--description", "d", "--body", "alpha bravo charlie"])
    c = sqlite3.connect(db); c.execute("UPDATE memories SET lastUsedAt=?", (lastUsedAt,)); c.commit(); c.close()


def _stdin(monkeypatch, obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(obj)))


def test_session_init_runs_archival_and_exits_zero(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)  # ancient → should archive
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    rc = main(["--db", db, "session-init"])
    assert rc == 0
    status = sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0]
    assert status == "archived"


def test_session_init_tolerates_garbage_stdin(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert main(["--db", db, "session-init"]) == 0  # never blocks session start
    # garbage stdin doesn't stop maintenance — archival still ran
    assert sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0] == "archived"


def test_session_init_noop_when_disabled(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    assert main(["--db", db, "session-init"]) == 0
    # disabled → archival did NOT run
    assert sqlite3.connect(db).execute("SELECT status FROM memories").fetchone()[0] == "active"


def test_inject_noop_when_disabled(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "Iowa",
          "--description", "d", "--body", "alpha bravo charlie"])
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "alpha bravo charlie", "cwd": str(tmp_path)})))
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--db", db, "inject"])
    assert rc == 0
    assert buf.getvalue().strip() == ""  # disabled → injects nothing


def test_session_init_recovers_corrupt_db_and_warns_on_stderr(tmp_path, monkeypatch, capsys):
    # End-to-end: session-init must actually invoke recovery (not just archival). A corrupt
    # DB with no backup → recreated empty + a stderr breadcrumb (stdout stays clean).
    db = str(tmp_path / "m.db")
    Path(db).write_bytes(b"this is not a sqlite database")
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    rc = main(["--db", db, "session-init"])
    assert rc == 0
    out = capsys.readouterr()
    assert "DB recovery -> recreated" in out.err
    assert out.out.strip() == ""  # nothing on stdout (reserved for the hook channel)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_session_init_creates_backup(tmp_path, monkeypatch):
    # session-init must actually back up (not just archive). A healthy DB with no prior
    # backup → a memory-<ms>.db snapshot appears under ~/.claude/memory/backups (home patched).
    db = str(tmp_path / "m.db")
    _seed_old(db, lastUsedAt=1)
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    assert main(["--db", db, "session-init"]) == 0
    backups = list((tmp_path / ".claude" / "memory" / "backups").glob("memory-*.db"))
    assert len(backups) == 1


def test_session_init_failopen_when_recovery_raises(tmp_path, monkeypatch, capsys):
    # The outer fail-open handler (not the inner stdin guard) must absorb a raising
    # recover_if_corrupt and still exit 0, leaving a traceback breadcrumb on stderr.
    def _boom(*a, **k):
        raise RuntimeError("simulated recovery failure")
    monkeypatch.setattr("memory_engine.cli.recover_if_corrupt", _boom)
    db = str(tmp_path / "m.db"); _seed_old(db, lastUsedAt=1)
    _stdin(monkeypatch, {"source": "startup", "cwd": str(tmp_path)})
    assert main(["--db", db, "session-init"]) == 0  # never blocks session start
    assert "simulated recovery failure" in capsys.readouterr().err


def test_inject_failopen_on_corrupt_db(tmp_path, monkeypatch, capsys):
    # inject against a corrupt DB must hit the outer except, exit 0, and inject nothing —
    # the prompt-erasing blocking exit is exactly what fail-open prevents.
    db = str(tmp_path / "m.db")
    Path(db).write_bytes(b"not a database")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "alpha bravo", "cwd": str(tmp_path)})))
    assert main(["--db", db, "inject"]) == 0
    assert capsys.readouterr().out.strip() == ""  # nothing injected
