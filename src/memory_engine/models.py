# src/memory_engine/models.py
"""Row + outcome shapes for the memory store."""
from dataclasses import dataclass

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


@dataclass(frozen=True)
class Memory:
    id: int
    scope: str
    type: str
    name: str
    description: str
    body: str
    normalizedKey: str
    captureHits: int
    recallHits: int
    lastUsedAt: int
    status: str
    createdAt: int
    updatedAt: int
    source: str | None


@dataclass(frozen=True)
class Inserted:
    id: int


@dataclass(frozen=True)
class MergedByKey:
    id: int


@dataclass(frozen=True)
class MergedByFuzzy:
    id: int
    from_archived: bool = False


Outcome = Inserted | MergedByKey | MergedByFuzzy
