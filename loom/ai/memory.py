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

    # ---- persistence (Phase 5) ----
    # A plain list of the entries' fields, so a mind's memory survives a restart
    # (an NPC that remembers across reboots). The eventual importance/embedding
    # fields slot into the same records with no format change — the whole reason
    # this stays a JSON list now rather than moving to SQLite before it must.
    def state(self) -> list:
        """A JSON-ready snapshot: each memory as ``{text, kind, t}`` in order."""
        return [{"text": e.text, "kind": e.kind, "t": e.t} for e in self.entries]

    def load_state(self, data: list) -> None:
        """Replace the stream from a ``state()`` snapshot, restoring each entry's
        original timestamp (never ``time.time()`` at load)."""
        self.entries = [MemoryEntry(text=e.get("text", ""),
                                    kind=e.get("kind", "observation"),
                                    t=float(e.get("t", 0.0)))
                        for e in (data or [])]
