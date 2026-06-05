# src/memory_engine/normalizer.py
"""Pure-logic dedup helpers. No DB, no Android/Claude deps — trivially testable."""
import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_WHITESPACE = re.compile(r"\s+")


def normalize_body(body: str) -> str:
    """Lowercase, strip non-alphanumerics to spaces, collapse whitespace, trim."""
    s = _NON_ALNUM.sub(" ", body.lower())
    s = _WHITESPACE.sub(" ", s)
    return s.strip()


def normalized_key(scope: str, type_: str, body: str) -> str:
    """Hard-dedup key: sha1(scope | type | normalized body). Scope+type folded in
    so a project capture never collides with a global row, and the same words under
    different facets stay distinct."""
    raw = f"{scope.lower()}|{type_.lower()}|{normalize_body(body)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
