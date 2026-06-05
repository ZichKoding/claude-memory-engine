# tests/test_fts_query.py
from memory_engine.fts_query import from_user_text


def test_builds_or_prefix_expression():
    assert from_user_text("Lives in Iowa") == '"lives"* OR "iowa"*'  # "in" dropped (<3 chars)


def test_strips_punctuation_and_dedupes():
    assert from_user_text("cat, cat? CAT!") == '"cat"*'


def test_caps_at_eight_tokens():
    expr = from_user_text("alpha bravo charlie delta echo foxtrot golf hotel india juliet")
    assert expr.count(" OR ") == 7  # 8 tokens => 7 separators


def test_returns_none_when_nothing_usable():
    assert from_user_text("a, b? c!") is None
    assert from_user_text("   ") is None
