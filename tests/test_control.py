# tests/test_control.py
from pathlib import Path
import pytest
import memory_engine.control as control


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", "1")
    assert control.is_disabled() is True


# Truthy: every accepted value, plus case-insensitivity and surrounding whitespace.
@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On", "Yes", " 1 ", "  true  "])
def test_truthy_env_values_disable(monkeypatch, tmp_path, val):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # no sentinel file
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", val)
    assert control.is_disabled() is True


# Falsy: set-but-not-a-truthy-value MUST stay enabled. This is the regression catcher —
# a refactor to a naive `if val:` truthiness check would wrongly disable on any of these.
@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "banana", "disabled"])
def test_falsy_env_values_stay_enabled(monkeypatch, tmp_path, val):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # no sentinel file
    monkeypatch.setenv("MEMORY_ENGINE_DISABLED", val)
    assert control.is_disabled() is False


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
