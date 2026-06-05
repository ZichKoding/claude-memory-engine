# src/memory_engine/fts_query.py
"""Turn free-form text into a safe FTS5 MATCH expression. Shared by capture-time
fuzzy dedup (Phase 1) and retrieval (Phase 2) so both normalize identically."""
import re

_NON_ALNUM_WS = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")
MIN_TOKEN_LEN = 3
MAX_TOKENS = 8


def from_user_text(raw: str) -> str | None:
    """OR-joined prefix tokens, e.g. '"lives"* OR "iowa"*'. None when nothing usable."""
    cleaned = _NON_ALNUM_WS.sub(" ", raw.lower())
    tokens: list[str] = []
    for tok in _WHITESPACE.split(cleaned):
        if len(tok) >= MIN_TOKEN_LEN and tok not in tokens:
            tokens.append(tok)
        if len(tokens) >= MAX_TOKENS:
            break
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)
