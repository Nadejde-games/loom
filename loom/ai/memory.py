"""The memory substrate shared by NPCs and (later) players.

The minimal useful subset of the Generative-Agents memory stream, now with the two
signals that lift it past pure recency: an **importance** score (how salient the
memory is) and, when an embedder is present, **relevance** (how close the memory is
to the current moment). Retrieval combines the three — the paper's score, verbatim:

    score = recency + importance + relevance

each component min-max normalized across the candidate pool, weights all 1.0, recency
an exponential decay. So an old-but-salient memory (a promise, a threat) can outrank
the flood of recent trivia, and a memory *relevant to what is happening now* surfaces
even after it has fallen out of the last-k window — the whole reason to go past
``recent(k)``.

Importance is scored by a **cheap deterministic heuristic** at write time (the one
thing every affordable system avoids is an LLM call per memory); an LLM rating and
reflection are the next, deferred layers. Embeddings are a **derived cache** on each
entry, filled lazily off the loop at first retrieval and re-computed after a restart
rather than persisted (this slice). See ``docs/spikes/memory.md``. Everything slots
behind this one interface; the sole caller-visible change is that relevance needs a
*query*, so callers pass the current utterance/scene into ``retrieve`` where
``recent`` needed nothing.
"""
from __future__ import annotations
import math
import time
from dataclasses import dataclass, field

# Importance heuristic. A memory's base salience comes from its kind (a reflection or
# a world event matters more than idle observation), bumped by salience cue-words a
# person would remember — a promise, a threat, a gift, a death, a named quest object.
# Deterministic and free, so every memory is scored on write with no model call.
_KIND_WEIGHT = {"reflection": 8, "event": 5, "speech": 4, "action": 3,
                "observation": 2}
_SALIENCE_CUES = (
    "swore", "swear", "promise", "vow", "oath", "must", "never", "always",
    "betray", "threat", "danger", "warn", "kill", "death", "die", "dead",
    "blade", "sword", "attack", "flee", "curse", "gift", "reward", "gold",
    "treasure", "key", "secret", "quest", "love", "hate", "fear", "save",
)


def score_importance(text: str, kind: str = "observation") -> int:
    """A memory's importance on a 1-10 scale, by kind plus salience cues — cheap,
    deterministic, offline. The LLM-rated poignancy of the Generative-Agents paper is
    a deferred fidelity upgrade behind this same number."""
    base = _KIND_WEIGHT.get(kind, 2)
    low = text.lower()
    hits = sum(1 for cue in _SALIENCE_CUES if cue in low)
    return max(1, min(10, base + min(3, hits)))


@dataclass
class MemoryEntry:
    text: str
    kind: str = "observation"   # observation | speech | event | reflection | ...
    t: float = field(default_factory=time.time)
    # Salience, 1-10, set at write time by the heuristic above (LLM-rated later, same
    # field). Drives the importance term of retrieval.
    importance: int = 1
    # A derived cache of the text's embedding vector, filled lazily off the loop at
    # first retrieval; None until embedded. Persisted as a BLOB when the stream is
    # SQLite-backed (slice 1b), so it need not be re-derived after a restart.
    embedding: list | None = None
    # The SQLite rowid when this memory is stored (slice 1b), so a later embedding
    # fill can UPDATE the exact row. Not part of the memory's identity or its
    # persisted state; None for a store-less (in-memory) stream.
    rowid: int | None = field(default=None, compare=False)


def _cosine(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _minmax(xs: list[float]) -> list[float]:
    """Scale a list to [0,1]; an all-equal pool contributes nothing (all zeros), so a
    component with no spread cannot skew the sum — the paper's normalization."""
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi - lo <= 1e-12:
        return [0.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]


class MemoryStream:
    # Retrieval weights and recency decay — exposed as class constants so a game can
    # retune them (the paper notes the weights "should potentially be learned"). The
    # decay is per game-hour since a memory's creation.
    RECENCY_DECAY = 0.995
    W_RECENCY = 1.0
    W_IMPORTANCE = 1.0
    W_RELEVANCE = 1.0

    def __init__(self, embedder=None, store=None, agent_id: str | None = None):
        # The optional embedding provider (an ``EmbeddingProvider``). None = no
        # relevance signal; retrieval degrades to recency+importance, and ``retrieve``
        # returns exactly what ``recent`` would, so an embedder-less world behaves as
        # before. Injected by the engine from ``get_default_embedder()``.
        self._embedder = embedder
        # The optional SQLite backing (a ``MemoryStore``) and this stream's agent key
        # (slice 1b). With a store, writes go through to the DB and reads come from it
        # at construction; without one, the stream is a plain in-memory list exactly as
        # before — so the offline suite and any store-less deployment are unchanged.
        self._store = store
        self._agent_id = agent_id
        self.entries: list[MemoryEntry] = (
            store.load(agent_id) if (store is not None and agent_id) else [])

    def add(self, text: str, kind: str = "observation") -> MemoryEntry:
        e = MemoryEntry(text=text, kind=kind, importance=score_importance(text, kind))
        if self._store is not None and self._agent_id:
            e.rowid = self._store.insert(self._agent_id, e)   # incremental append
        self.entries.append(e)
        return e

    def recent(self, k: int = 8) -> list[MemoryEntry]:
        return self.entries[-k:]

    async def retrieve(self, query: str, k: int = 8) -> list[MemoryEntry]:
        """The Generative-Agents retrieval: rank memories by
        ``recency + importance + relevance`` (each min-max normalized, weights 1.0)
        and return the top ``k``, most salient first. Relevance is the cosine of the
        query's embedding against each memory's; embeddings are filled lazily here
        (one batched call for the query plus any unembedded memories), off the loop.

        Degrades gracefully: with no embedder — or if the embedder is unreachable —
        it returns exactly ``recent(k)``, so a turn never breaks on a memory lookup
        and an embedder-less world is unchanged."""
        if self._embedder is None or not self.entries:
            return self.recent(k)
        cands = self.entries
        to_embed = [e for e in cands if e.embedding is None]
        try:
            batch = await self._embedder.embed([query] + [e.text for e in to_embed])
        except Exception:                      # any embedder failure -> recency degrade
            return self.recent(k)
        qvec, rest = batch[0], batch[1:]
        for e, vec in zip(to_embed, rest):
            e.embedding = vec
            if self._store is not None and e.rowid is not None:
                self._store.update_embedding(e.rowid, vec)   # persist the vector
        now = time.time()
        rec = _minmax([self.RECENCY_DECAY ** (max(0.0, now - e.t) / 3600.0)
                       for e in cands])
        imp = _minmax([float(e.importance) for e in cands])
        rel = _minmax([_cosine(qvec, e.embedding) for e in cands])
        order = sorted(
            range(len(cands)),
            key=lambda i: (rec[i] * self.W_RECENCY + imp[i] * self.W_IMPORTANCE
                           + rel[i] * self.W_RELEVANCE),
            reverse=True)
        return [cands[i] for i in order[:k]]

    # ---- persistence (Phase 5) ----
    # A plain list of the entries' durable fields, so a mind's memory survives a
    # restart. The embedding is NOT persisted — it is a derived cache re-computed
    # lazily on the next retrieval (this slice); importance IS, so a restored memory
    # keeps its salience without re-deriving. The SQLite backing that will store
    # embeddings as BLOBs is a later slice; the JSON shape here is forward-compatible.
    def state(self) -> list:
        """A JSON-ready snapshot: each memory as ``{text, kind, t, importance}``."""
        return [{"text": e.text, "kind": e.kind, "t": e.t,
                 "importance": e.importance} for e in self.entries]

    def load_state(self, data: list) -> None:
        """Restore the stream from a ``state()`` snapshot — and, when SQLite-backed,
        act as the one-time JSON->SQLite migration bridge.

        Each entry restores its original timestamp and importance (an older save
        without ``importance`` re-derives it from the heuristic; embeddings restore
        empty and re-embed lazily). With no store this simply replaces the in-memory
        list, exactly as before. With a store it imports the JSON memory **only into an
        empty store** (a first boot after the 1b upgrade); once the DB holds the
        agent's memory it is authoritative and the JSON block is ignored — so a stale
        overlay can never overwrite or double-import a live store."""
        out = []
        for e in (data or []):
            text = e.get("text", "")
            kind = e.get("kind", "observation")
            imp = e.get("importance")
            out.append(MemoryEntry(
                text=text, kind=kind, t=float(e.get("t", 0.0)),
                importance=int(imp) if imp is not None else score_importance(text, kind)))
        if self._store is not None and self._agent_id:
            if self._store.count(self._agent_id) == 0:        # migrate once
                for entry in out:
                    entry.rowid = self._store.insert(self._agent_id, entry)
                self.entries = out
            # else: the DB is authoritative; keep what was loaded at construction.
            return
        self.entries = out
