# src/memory_engine/scope.py
"""Resolve the project scope key for a working directory. The key is the git repo
root (so different repos are distinct scopes), normcased+absolute so it's stable;
falls back to the normalized cwd when there's no repo. Reading the path never writes
to the repo."""
import os
import subprocess

GLOBAL_SCOPE = "global"


def resolve_scope_key(cwd: str) -> str:
    """Git repo root for `cwd`, else the normalized `cwd`. Always normcase+abspath
    so the same location yields a byte-stable key across captures and retrievals."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        top = result.stdout.strip()
        if result.returncode == 0 and top:
            return os.path.normcase(os.path.abspath(top))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.normcase(os.path.abspath(cwd))


def scopes_for(cwd: str) -> list[str]:
    """The scopes a retrieval searches when working in `cwd`: global + this project."""
    return [GLOBAL_SCOPE, resolve_scope_key(cwd)]
