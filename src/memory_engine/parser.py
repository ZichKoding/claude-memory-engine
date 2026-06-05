# src/memory_engine/parser.py
"""Parse a model extraction pass into validated memory candidates. Strict and
failure-tolerant: one bad line is dropped, never fatal."""
from dataclasses import dataclass

ALLOWED_TYPES = {"fact", "preference", "person", "goal", "project", "other"}
MAX_MEMORIES_PER_SWEEP = 5
MAX_NAME_LEN = 80
MAX_DESCRIPTION_LEN = 200
MAX_BODY_LEN = 500


@dataclass(frozen=True)
class Parsed:
    type: str
    name: str
    description: str
    body: str


def parse_candidates(output: str) -> list[Parsed]:
    if output.strip().upper() == "NONE":
        return []
    results: list[Parsed] = []
    for raw in output.splitlines():
        if len(results) >= MAX_MEMORIES_PER_SWEEP:
            break
        line = raw.strip()
        if not line.startswith("MEMORY|"):
            continue
        parts = line[len("MEMORY|"):].split("|")
        if len(parts) != 4:
            continue
        type_ = parts[0].strip().lower()
        name, description, body = parts[1].strip(), parts[2].strip(), parts[3].strip()
        if type_ not in ALLOWED_TYPES:
            continue
        if not name or len(name) > MAX_NAME_LEN:
            continue
        if not description or len(description) > MAX_DESCRIPTION_LEN:
            continue
        if not body or len(body) > MAX_BODY_LEN:
            continue
        results.append(Parsed(type_, name, description, body))
    return results
