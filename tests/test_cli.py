# tests/test_cli.py
import json
from memory_engine.cli import main


def test_add_then_search_roundtrip(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    rc = main(["--db", db, "add", "--scope", "global", "--type", "fact",
               "--name", "Iowa", "--description", "where", "--body", "Lives in Iowa"])
    assert rc == 0

    capsys.readouterr()
    rc = main(["--db", db, "search", "--scopes", "global", "--query", "iowa"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Iowa" in out


def test_stats_reports_counts(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    main(["--db", db, "add", "--scope", "global", "--type", "fact",
          "--name", "n", "--description", "d", "--body", "alpha beta gamma"])
    capsys.readouterr()
    rc = main(["--db", db, "stats"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 1
    assert data["active"] == 1
