"""The memory substrate shared by NPCs and (later) players.

Right now this is an append-only log with recency retrieval — the minimal
useful subset of the Generative-Agents memory stream. Importance scoring,
embedding + relevance retrieval, and reflection are deliberately deferred;
each slots in behind this same interface without touching callers.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    text: str
    kind: str = "observation"   # observation | speech | reflection | ...
    t: float = field(default_factory=time.time)


class MemoryStream:
    def __init__(self):
        self.entries: list[MemoryEntry] = []

    def add(self, text: str, kind: str = "observation") -> MemoryEntry:
        e = MemoryEntry(text=text, kind=kind)
        self.entries.append(e)
        return e

    def recent(self, k: int = 8) -> list[MemoryEntry]:
        return self.entries[-k:]
