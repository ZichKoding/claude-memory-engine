# src/memory_engine/formatting.py
"""Render retrieved memories into the context block injected by the hook."""
from memory_engine.models import Memory


def format_memory_block(memories: list[Memory]) -> str:
    """A `<memory>`-wrapped bullet list, or "" when there's nothing to inject
    (caller emits no additionalContext on empty)."""
    if not memories:
        return ""
    lines = [f"- [{m.scope}/{m.type}] {m.name}: {m.body}" for m in memories]
    return "<memory>\n" + "\n".join(lines) + "\n</memory>"
