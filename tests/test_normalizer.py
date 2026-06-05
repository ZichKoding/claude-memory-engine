# tests/test_normalizer.py
from memory_engine.normalizer import normalize_body, normalized_key


def test_normalize_collapses_punctuation_case_whitespace():
    assert normalize_body("  Lives in IOWA!! ") == "lives in iowa"


def test_normalize_empty():
    assert normalize_body("   ") == ""


def test_key_is_stable_and_hex_40():
    k = normalized_key("global", "fact", "Lives in Iowa")
    assert k == normalized_key("global", "FACT", "lives in iowa.")
    assert len(k) == 40 and all(c in "0123456789abcdef" for c in k)


def test_key_differs_by_scope():
    assert normalized_key("global", "fact", "x y z") != normalized_key("repoA", "fact", "x y z")


def test_key_differs_by_type():
    assert normalized_key("global", "fact", "x y z") != normalized_key("global", "preference", "x y z")
