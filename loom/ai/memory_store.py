"""The SQLite backing for memory streams (Phase 5, slice 1b).

Slice 1a lifted memory past recency with importance + embedding-relevance retrieval,
still holding each stream as an in-memory list persisted through the JSON overlay.
This is the storage move the persistence spike staged: **one SQLite table keyed by
``agent_id``**, each memory a row, the embedding a **float32 BLOB packed with the
stdlib ``array``** (no numpy). It earns two things the JSON overlay could not:

  * **Incremental append** — a new memory is a single ``INSERT``, O(1), no whole-file
    rewrite of every mind's entire stream on each autosave.
  * **Durable embeddings** — the vector persists as a BLOB, so an NPC that remembers
    across a reboot does not have to re-embed its whole history on the next retrieval.

The stream stays authoritative for *reads*: each ``MemoryStream`` loads its agent's
rows into memory once and scans that list (brute-force cosine over an agent's few
hundred memories is sub-millisecond — see the spike), while writes go through to the
DB. When no store is given a stream is a plain in-memory list exactly as before, so
the offline suite and any store-less deployment are unchanged. Stdlib ``sqlite3`` and
``array`` only — no new dependency.
"""
from __future__ import annotations
import array
import sqlite3

from .memory import MemoryEntry


def _pack(vec: list | None) -> bytes | None:
    """A vector -> a float32 byte blob (4 bytes/dim), or None for an unembedded row."""
    return array.array("f", vec).tobytes() if vec is not None else None


def _unpack(blob: bytes | None) -> list | None:
    if blob is None:
        return None
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


class MemoryStore:
    """A single SQLite table holding every agent's memories, keyed by ``agent_id``.

    One connection, one table, one index — simpler than a table per agent, and every
    query filters ``WHERE agent_id = ?`` so a scan only ever touches the one agent's
    rows. ``path`` is a file (a durable store beside the world save) or ``":memory:"``
    for tests. Writes commit immediately: a memory is durable the moment it is formed.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id         INTEGER PRIMARY KEY,
                agent_id   TEXT NOT NULL,
                text       TEXT NOT NULL,
                kind       TEXT NOT NULL,
                t          REAL NOT NULL,
                importance INTEGER NOT NULL,
                embedding  BLOB
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS memory_agent ON memory(agent_id)")
        self._conn.commit()

    def load(self, agent_id: str) -> list[MemoryEntry]:
        """Every memory for an agent, oldest first, embeddings unpacked from BLOB —
        the stream's in-memory read set, rebuilt at construction."""
        rows = self._conn.execute(
            "SELECT id, text, kind, t, importance, embedding FROM memory "
            "WHERE agent_id = ? ORDER BY id", (agent_id,)).fetchall()
        return [MemoryEntry(text=r[1], kind=r[2], t=r[3], importance=r[4],
                            embedding=_unpack(r[5]), rowid=r[0]) for r in rows]

    def insert(self, agent_id: str, entry: MemoryEntry) -> int:
        """Append one memory; return its new rowid (kept on the entry so a later
        embedding fill can UPDATE the exact row)."""
        cur = self._conn.execute(
            "INSERT INTO memory (agent_id, text, kind, t, importance, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, entry.text, entry.kind, entry.t, entry.importance,
             _pack(entry.embedding)))
        self._conn.commit()
        return cur.lastrowid

    def update_embedding(self, rowid: int, vec: list) -> None:
        """Persist a lazily-computed embedding onto its row, so it need not be
        re-derived after a restart."""
        self._conn.execute("UPDATE memory SET embedding = ? WHERE id = ?",
                            (_pack(vec), rowid))
        self._conn.commit()

    def count(self, agent_id: str) -> int:
        """How many memories an agent holds — the migration gate (import the JSON
        overlay's memory only into an empty store; the DB is authoritative once seeded)."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM memory WHERE agent_id = ?", (agent_id,)).fetchone()[0]

    def close(self) -> None:
        self._conn.close()
