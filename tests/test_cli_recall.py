# tests/test_cli_recall.py
from memory_engine.cli import main


def _seed(db, scope, body, type_="fact", name="n"):
    main(["--db", db, "add", "--scope", scope, "--type", type_,
          "--name", name, "--description", "d", "--body", body])


def test_recall_prints_matches(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie delta echo", name="Greeting")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie delta echo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "<memory>" in out and "Greeting" in out


def test_recall_includes_archived(tmp_path, capsys):
    import sqlite3
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie delta echo", name="OldFact")
    # Genuinely archive the row, then confirm recall STILL surfaces it (explicit recall
    # searches active + archived — this is the behavior that distinguishes it from the
    # auto-retrieve hook, so the fixture must actually contain an archived row).
    c = sqlite3.connect(db)
    c.execute("UPDATE memories SET status='archived'")
    c.commit()
    c.close()
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie delta echo"])
    assert rc == 0
    assert "OldFact" in capsys.readouterr().out


def test_recall_no_match_message(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    _seed(db, "global", "alpha bravo charlie")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "zulu yankee xray whiskey"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No matching memories" in out


def test_recall_scopes_to_cwd_project(tmp_path, capsys):
    db = str(tmp_path / "m.db")
    # global match + a match in an unrelated project scope; with no --cwd, only global.
    _seed(db, "global", "alpha bravo charlie", name="GlobalHit")
    _seed(db, "/some/other/proj", "alpha bravo charlie", name="OtherProjHit")
    capsys.readouterr()
    rc = main(["--db", db, "recall", "--query", "alpha bravo charlie"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GlobalHit" in out
    assert "OtherProjHit" not in out  # no --cwd → global only, other project excluded
