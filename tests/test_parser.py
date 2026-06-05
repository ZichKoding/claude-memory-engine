# tests/test_parser.py
from memory_engine.parser import parse_candidates, Parsed


def test_none_returns_empty():
    assert parse_candidates("none") == []
    assert parse_candidates("  NONE  ") == []


def test_parses_valid_line():
    out = "MEMORY|fact|Lives in Iowa|Where the user lives|The user lives in Iowa."
    assert parse_candidates(out) == [
        Parsed("fact", "Lives in Iowa", "Where the user lives", "The user lives in Iowa.")
    ]


def test_ignores_preamble_and_bad_lines():
    out = "Sure!\nMEMORY|fact|A|B|C\n- a bullet\nMEMORY|bogus|x|y|z\nMEMORY|fact|only|three"
    assert parse_candidates(out) == [Parsed("fact", "A", "B", "C")]


def test_caps_at_five():
    out = "\n".join(f"MEMORY|fact|n{i}|d{i}|b{i}" for i in range(10))
    assert len(parse_candidates(out)) == 5


def test_rejects_overlong_fields():
    out = "MEMORY|fact|" + ("x" * 81) + "|d|b"
    assert parse_candidates(out) == []
