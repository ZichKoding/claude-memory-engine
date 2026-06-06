# src/memory_engine/control.py
"""Kill switch. When set, all hooks/tools no-op so the engine instantly reverts to
'off' with zero side effects."""
import os
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}


def is_disabled() -> bool:
    """True if disabled via the MEMORY_ENGINE_DISABLED env var or a sentinel file at
    ~/.claude/memory/DISABLED."""
    if os.environ.get("MEMORY_ENGINE_DISABLED", "").strip().lower() in _TRUTHY:
        return True
    return (Path.home() / ".claude" / "memory" / "DISABLED").exists()
