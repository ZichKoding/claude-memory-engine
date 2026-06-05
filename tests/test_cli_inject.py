# tests/test_cli_inject.py
import io
import json
from memory_engine.cli import main


def _seed(db):
    main(["--db", db, "add", "--scope", "global", "--type", "fact",
          "--name", "Iowa", "--description", "where", "--body",
          "alpha bravo charlie delta echo"])


def _run_inject(monkeypatch, capsys, db, stdin_obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    rc = main(["--db", db, "inject"])
    return rc, capsys.readouterr().out


def test_inject_emits_additional_context_on_match(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {
        "prompt": "alpha bravo charlie delta echo", "cwd": str(tmp_path)})
    assert rc == 0
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "<memory>" in payload["hookSpecificOutput"]["additionalContext"]
    assert "Iowa" in payload["hookSpecificOutput"]["additionalContext"]


def test_inject_emits_nothing_on_no_match(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {
        "prompt": "zulu yankee xray whiskey victor", "cwd": str(tmp_path)})
    assert rc == 0
    assert out.strip() == ""   # no additionalContext when nothing matched


def test_inject_failopen_on_malformed_stdin(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("this is not json"))
    rc = main(["--db", db, "inject"])
    out = capsys.readouterr().out
    assert rc == 0            # NEVER non-zero (exit 2 would erase the prompt)
    assert out.strip() == ""  # emit nothing rather than crash


def test_inject_failopen_on_empty_prompt(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "m.db")
    _seed(db)
    capsys.readouterr()
    rc, out = _run_inject(monkeypatch, capsys, db, {"prompt": "   ", "cwd": str(tmp_path)})
    assert rc == 0
    assert out.strip() == ""
