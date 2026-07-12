"""The world chronicle: a bounded, game-agnostic feed of what has happened.

The engine records salient beats here as they broadcast — a player arriving, an
utterance, an NPC's action, a move. On its slow cadence the game-master director
(Phase 3) reads the chronicle as its perception of *what changed* since it last
looked (the turn-digest pattern), alongside a current-state snapshot. Cheap and
inspectable: a rolling window plus a monotonic sequence number, no embeddings.

Dependency-free and unaware of any game. The engine decides what is worth
recording; this only remembers it. Reusable later as the substrate for
persistence (Phase 5) and any other system that needs to know what just happened.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ChronicleEvent:
    """One recorded beat. ``seq`` is a monotonic id (1-based) so a reader can ask
    'what is new since the last id I saw?' without re-reading the whole window."""
    text: str
    location_id: str | None = None
    kind: str = "event"          # arrival | speech | action | move | event | ...
    seq: int = 0


class Chronicle:
    """A rolling record of recent world events with a monotonic cursor.

    ``seq`` only ever grows (even as old events fall out of the bounded window),
    so ``seq == last_seen`` is a reliable 'nothing happened since' test — the
    director's laziness gate, so an idle world costs it no model call.
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._events: deque[ChronicleEvent] = deque(maxlen=maxlen)
        self._seq = 0

    @property
    def seq(self) -> int:
        """The id of the most recent event (0 if nothing has happened yet)."""
        return self._seq

    def record(self, text: str, location_id: str | None = None,
               kind: str = "event") -> int:
        """Append one beat and return its new sequence id. Empty text is ignored
        (nothing to remember) and does not advance the cursor."""
        text = (text or "").strip()
        if not text:
            return self._seq
        self._seq += 1
        self._events.append(ChronicleEvent(text=text, location_id=location_id,
                                           kind=kind, seq=self._seq))
        return self._seq

    def recent(self, n: int | None = None) -> list[ChronicleEvent]:
        """The last ``n`` events in chronological order (all of them if None)."""
        events = list(self._events)
        return events if n is None else events[-n:] if n > 0 else []

    def since(self, marker: int) -> list[ChronicleEvent]:
        """Events recorded after sequence id ``marker`` (still in the window)."""
        return [e for e in self._events if e.seq > marker]

    def render(self, n: int | None = 20) -> str:
        """The recent events as prose lines for a prompt. Empty feed → a plain
        note, so the reader always gets a well-formed section."""
        events = self.recent(n)
        if not events:
            return "(nothing has happened yet)"
        return "\n".join(f"- {e.text}" for e in events)
