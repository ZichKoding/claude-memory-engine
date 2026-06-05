# tests/test_formatting.py
from memory_engine.formatting import format_memory_block
from memory_engine.models import Memory


def _mem(id_, scope, type_, name, body):
    return Memory(id=id_, scope=scope, type=type_, name=name, description="d",
                  body=body, normalizedKey="k", captureHits=1, recallHits=0,
                  lastUsedAt=0, status="active", createdAt=0, updatedAt=0, source=None)


def test_empty_list_returns_empty_string():
    assert format_memory_block([]) == ""


def test_formats_wrapped_block():
    out = format_memory_block([
        _mem(1, "global", "fact", "Lives in Iowa", "The user lives in Iowa"),
        _mem(2, "repoA", "preference", "Tabs", "Prefers tabs"),
    ])
    assert out == (
        "<memory>\n"
        "- [global/fact] Lives in Iowa: The user lives in Iowa\n"
        "- [repoA/preference] Tabs: Prefers tabs\n"
        "</memory>"
    )
