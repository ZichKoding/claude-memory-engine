# tests/test_scope.py
import os
import subprocess
from memory_engine.scope import resolve_scope_key, scopes_for


def test_resolve_uses_git_toplevel(tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "src"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # From a subdir of the repo, the scope key is the repo root (normcased abspath).
    key = resolve_scope_key(str(sub))
    expected = os.path.normcase(os.path.abspath(str(repo)))
    assert key == expected


def test_resolve_falls_back_to_cwd_when_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    key = resolve_scope_key(str(plain))
    assert key == os.path.normcase(os.path.abspath(str(plain)))


def test_scopes_for_prepends_global(tmp_path):
    plain = tmp_path / "p2"
    plain.mkdir()
    scopes = scopes_for(str(plain))
    assert scopes[0] == "global"
    assert scopes[1] == resolve_scope_key(str(plain))
    assert len(scopes) == 2
