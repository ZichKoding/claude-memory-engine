# tests/test_paths.py
from pathlib import Path
from memory_engine.paths import default_db_path


def test_default_db_path_under_home_claude(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = default_db_path()
    assert p == str(tmp_path / ".claude" / "memory" / "memory.db")
    # parent dir is created so sqlite can open the file
    assert (tmp_path / ".claude" / "memory").is_dir()
