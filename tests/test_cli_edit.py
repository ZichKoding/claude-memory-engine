# tests/test_cli_edit.py
import sqlite3
from memory_engine.cli import main
from memory_engine.scope import resolve_scope_key


def _rows(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id, scope, name, body FROM memories ORDER BY id").fetchall()
    c.close()
    return rows


def test_add_defaults_to_global_scope(tmp_path):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--type", "fact", "--name", "n", "--description", "d",
          "--body", "alpha bravo"])  # no --scope, no --cwd
    assert _rows(db)[0]["scope"] == "global"


def test_add_cwd_resolves_project_scope(tmp_path):
    db = str(tmp_path / "m.db")
    proj = tmp_path / "proj"
    proj.mkdir()
    main(["--db", db, "add", "--cwd", str(proj), "--type", "fact", "--name", "n",
          "--description", "d", "--body", "alpha bravo"])
    assert _rows(db)[0]["scope"] == resolve_scope_key(str(proj))


def test_edit_updates_via_cli(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact", "--name", "n",
          "--description", "d", "--body", "lives in iowa"])
    mid = _rows(db)[0]["id"]
    capsys.readouterr()
    rc = main(["--db", db, "edit", "--id", str(mid), "--body", "lives in texas"])
    assert rc == 0
    assert "updated" in capsys.readouterr().out.lower()
    assert _rows(db)[0]["body"] == "lives in texas"


def test_edit_missing_id_message(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    capsys.readouterr()
    rc = main(["--db", db, "edit", "--id", "999", "--body", "x"])
    assert rc == 0
    assert "not found" in capsys.readouterr().out.lower()
