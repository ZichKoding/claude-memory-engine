# tests/test_control.py
from pathlib import Path
import memory_engine.control as control


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    assert control.is_disabled() is True


def test_disabled_via_sentinel_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)
    assert control.is_disabled() is False
    flag = tmp_path / ".claude" / "memory" / "DISABLED"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    assert control.is_disabled() is True


def test_enabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MEMORY_ENGINE_DISABLED", raising=False)
    assert control.is_disabled() is False
