"""Reflection — the second memory cognition (Phase 5, slice 2).

Slice 1 gave an agent a memory that surfaces an old-but-salient or old-but-relevant
memory past the recent window (`loom/ai/memory.py`). Reflection is the deferred half of
that spike (`docs/spikes/memory.md` §5/Q5, designed in `docs/spikes/reflection.md`): the
Generative-Agents step where an agent, on a cadence, *distills* its raw memories into
higher-level insights and writes them back as memories — so a long-lived NPC forms a
durable belief ("the player's promises mean nothing") instead of only holding the raw
lines, and that belief then colours every later turn because it is retrieved like any
memory.

It is a **second cognition** — two model calls on a slow, opt-in cadence — but it needs
**no memory-seam change**: a reflection is just another ``MemoryEntry(kind="reflection")``
(importance 8 by the kind weight, so it outranks trivia), embedded lazily and persisted to
SQLite like everything else. Two pieces live here, mirroring ``director``:

  * ``reflect`` — the cognition. Provider-agnostic: given a ``MemoryStream``, an
    ``LLMProvider``, and a persona preamble, it generates questions, retrieves evidence,
    synthesizes insights, and writes them back. Returns the added entries.
  * ``Reflector`` — the orchestrator that hangs reflection off the game loop: slow,
    threshold-gated (an agent reflects only once its accumulated memory-importance crosses
    a bar), one agent per pulse on a background task, non-overlapping. **Depth-1**: the
    per-agent watermark advances past the just-written reflections, so a reflection never
    reflects on itself — no tree this slice.
"""
from __future__ import annotations
import asyncio

from .. import log
from .mind import _extract_json

# The two constrained schemas. Small — the reflection calls ride the same
# structured-decoding seam as every other mind call, with the tolerant
# ``_extract_json`` parse as defense-in-depth for backends that do not constrain.
_QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["questions"],
    "additionalProperties": False,
}
_INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "because_of": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "because_of"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insights"],
    "additionalProperties": False,
}

_CITE_SNIPPET = 80   # how much of a cited memory's text to echo into "(because of: …)"


def _numbered(entries: list) -> str:
    """The memories rendered as a numbered list the model can cite by number."""
    return "\n".join(f"[{i}] {e.text}" for i, e in enumerate(entries, 1))


def _questions_prompt(subject: str, recent: list, max_questions: int) -> str:
    return "\n\n".join([
        subject,
        "You are stepping back from the moment to reflect on your own memories — to "
        "notice what matters across them, not just what happened last. Here are things "
        "you remember, numbered, oldest first:",
        _numbered(recent),
        f"Looking across these memories, what are the most salient high-level questions "
        f"they raise — about the people around you, your situation, or yourself? Ask at "
        f"most {max_questions}, each a single clear sentence. Ask about what recurs or "
        f"what weighs on you, not passing trivia.",
        'Respond with a single JSON object and nothing else, in exactly this shape:\n'
        '{"questions": ["<a question>", "<another question>"]}',
    ])


def _insights_prompt(subject: str, evidence: list, max_insights: int) -> str:
    return "\n\n".join([
        subject,
        "You are reflecting on your own memories — drawing durable conclusions that go "
        "beyond any single moment. Here is the evidence you have gathered, numbered:",
        _numbered(evidence),
        f"From this evidence, what higher-level insights can you draw about the people "
        f"around you, your situation, or yourself? Give at most {max_insights}, each a "
        f"single clear sentence stating a belief or realization you will carry forward. "
        f"Ground each one in the evidence: list the numbers of the memories it rests on.",
        'Respond with a single JSON object and nothing else, in exactly this shape:\n'
        '{"insights": [{"text": "<an insight>", "because_of": [<memory numbers>]}]}',
    ])


def _cited_texts(because_of, evidence: list) -> list:
    """Map an insight's cited numbers (1-based, into ``evidence``) to short renderings
    of those memories' texts — the GA-style provenance echoed into the stored insight.
    Out-of-range citations are dropped, a non-list yields nothing, and identical cited
    texts collapse to one (an NPC that said the same line thrice cites it once), so the
    ``(because of: …)`` clause reads clean instead of repeating itself."""
    if not isinstance(because_of, list):
        return []
    out, seen = [], set()
    for n in because_of:
        if not isinstance(n, int):
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
        if not (1 <= n <= len(evidence)):
            continue
        t = evidence[n - 1].text
        snippet = (t if len(t) <= _CITE_SNIPPET
                   else t[:_CITE_SNIPPET - 1].rstrip() + "…")
        if snippet in seen:           # dedup by the rendered text, not the index
            continue
        seen.add(snippet)
        out.append(snippet)
    return out


async def reflect(memory, provider, *, subject: str, recent_n: int = 40,
                  max_questions: int = 3, retrieve_k: int = 4,
                  max_insights: int = 3, min_memories: int = 3) -> list:
    """Run one reflection over ``memory`` using ``provider`` — the two-call
    Generative-Agents synthesis, depth-1 — and write the insights back as
    ``kind="reflection"`` memories. Returns the added ``MemoryEntry`` list (possibly
    empty). ``subject`` is the agent's persona preamble ("You are X, …"), so the
    reflection speaks in the agent's own frame.

    Degrades gracefully at every step: too few memories, no questions, no evidence, or
    no insights each return ``[]`` and touch nothing — a reflection can never break a
    turn (it runs off the loop) and never corrupts the stream."""
    if len(memory.entries) < min_memories:
        return []
    recent = memory.recent(recent_n)

    # Call 1 — the salient high-level questions the recent memories raise.
    raw = await provider.complete(
        _questions_prompt(subject, recent, max_questions),
        [{"role": "user", "content": "Reflect on what you remember."}],
        schema=_QUESTIONS_SCHEMA)
    obj = _extract_json(raw) or {}
    questions = [q.strip() for q in (obj.get("questions") or [])
                 if isinstance(q, str) and q.strip()][:max_questions]
    if not questions:
        log.debug("reflect: the model raised no questions — nothing to distill")
        return []
    log.debug(f"reflect: {len(questions)} question(s): "
              + " | ".join(questions))

    # Retrieve evidence per question — the step that reaches back past recent() into
    # old-but-relevant memory (with an embedder; else it degrades to recent(k)). Union,
    # deduped by object identity, order preserved.
    evidence: dict = {}
    for q in questions:
        for e in await memory.retrieve(q, k=retrieve_k):
            evidence.setdefault(id(e), e)
    ev_list = list(evidence.values())
    if not ev_list:
        return []
    log.debug(f"reflect: gathered {len(ev_list)} evidence memories from "
              f"{len(questions)} question(s)")

    # Call 2 — synthesize grounded insights over the numbered evidence.
    raw2 = await provider.complete(
        _insights_prompt(subject, ev_list, max_insights),
        [{"role": "user", "content": "What do you now believe?"}],
        schema=_INSIGHTS_SCHEMA)
    obj2 = _extract_json(raw2) or {}
    added = []
    for ins in (obj2.get("insights") or [])[:max_insights]:
        if not isinstance(ins, dict):
            continue
        text = ins.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        cited = _cited_texts(ins.get("because_of"), ev_list)
        full = f"{text} (because of: {'; '.join(cited)})" if cited else text
        added.append(memory.add(full, kind="reflection"))
    return added


class Reflector:
    """Hangs ``reflect`` off the game loop on a slow, threshold-gated, opt-in cadence.

    A tick system (register via ``install``): every ``period_ticks`` ticks it considers
    a reflection, but only for an agent whose accumulated memory-importance since its last
    reflection has crossed ``importance_threshold`` — so a model call is spent only once an
    agent has lived enough salient memory to have something to distill. The agent most over
    the bar reflects; one at a time, on a background task, non-overlapping. Runs for every
    NPC and (by default) the director — one memory substrate, uniform treatment.

    Depth-1: after an agent reflects, its watermark advances past the just-written
    reflections, so a reflection never re-triggers itself. The watermark is in-memory and
    lazily initialized to an agent's current length on first sight, so a store-backed
    restart does not re-reflect the whole loaded backlog (a slice-2 simplification — see
    ``docs/spikes/reflection.md``).
    """

    def __init__(self, engine, *, period_ticks: int = 40,
                 importance_threshold: int = 30, include_director: bool = True,
                 recent_n: int = 40, max_questions: int = 3, retrieve_k: int = 4,
                 max_insights: int = 3, tell: bool = True) -> None:
        self.engine = engine
        self.period_ticks = max(1, period_ticks)
        self.importance_threshold = max(1, importance_threshold)
        self.include_director = bool(include_director)
        # Whether a completed NPC reflection shows a perceptible *tell* to a present
        # player (a quiet ambient beat via ``engine.narrate_reflection``) — so the moment
        # of insight is felt, not only logged. The belief stays private; only the pause
        # shows. Off makes reflection wholly silent (memory-only).
        self.tell = bool(tell)
        self.recent_n = recent_n
        self.max_questions = max_questions
        self.retrieve_k = retrieve_k
        self.max_insights = max_insights
        # agent_id -> the entry count at that agent's last reflection (its watermark).
        self._watermark: dict[str, int] = {}
        self._ticks = 0
        self._running = False

    def install(self, loop) -> None:
        """Register this reflector as a system on the game loop."""
        loop.add_system(self.tick)

    def _minds(self):
        """(agent_id, mind) for every in-scope mind — the NPCs, and the director when
        included. Each mind exposes ``.memory``, ``.provider``, and
        ``reflection_subject()``; that small structural contract is all reflection needs,
        so it never reaches into a mind's internals."""
        for agent_id, mind in self.engine.minds.items():
            yield agent_id, mind
        if self.include_director and getattr(self.engine, "director", None) is not None:
            yield "director", self.engine.director.mind

    def _accumulated(self, agent_id: str, entries: list) -> int:
        """The summed importance of an agent's memories since its last reflection. On
        first sight the watermark is set to the current length (start from now, no
        backlog re-reflection) and 0 is returned."""
        if agent_id not in self._watermark:
            self._watermark[agent_id] = len(entries)
            return 0
        return sum(e.importance for e in entries[self._watermark[agent_id]:])

    def _pick(self):
        """The in-scope agent most over the importance threshold, or ``None`` — the one
        with the most salient accumulation to distill this pulse."""
        best, best_acc = None, 0
        for agent_id, mind in self._minds():
            acc = self._accumulated(agent_id, mind.memory.entries)
            if acc >= self.importance_threshold and acc > best_acc:
                best, best_acc = (agent_id, mind), acc
        return best

    async def tick(self, dt: float) -> None:
        """The loop callback. Counts ticks; on the period, reflects the agent most over
        the threshold (if any) on a background task. Cheap: no model call unless an agent
        has crossed the bar, and only one reflection runs at a time."""
        self._ticks += 1
        if self._ticks < self.period_ticks or self._running:
            return
        self._ticks = 0
        target = self._pick()
        if target is None:
            log.debug("reflect pulse: no agent past the importance bar "
                      f"(threshold {self.importance_threshold})")
            return
        log.event(f"{target[0]} is reflecting "
                  f"(accumulated memory past the bar of {self.importance_threshold})")
        self._running = True
        task = asyncio.create_task(self._guarded_reflect(*target))
        # Track on the engine so shutdown can await/cancel in-flight work, mirroring how
        # NPC replies and director beats are tracked; harmless if there is no such set.
        tasks = getattr(self.engine, "_tasks", None)
        if tasks is not None:
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _guarded_reflect(self, agent_id: str, mind) -> None:
        try:
            await self._reflect_agent(agent_id, mind)
        except Exception as exc:     # a bad reflection must never break the loop
            log.event(f"reflection failed for {agent_id}: {exc!r}")
        finally:
            self._running = False

    async def _reflect_agent(self, agent_id: str, mind) -> None:
        added = await reflect(
            mind.memory, mind.provider, subject=mind.reflection_subject(),
            recent_n=self.recent_n, max_questions=self.max_questions,
            retrieve_k=self.retrieve_k, max_insights=self.max_insights)
        # Advance the watermark past the just-written reflections (depth-1: a reflection
        # never re-triggers itself), whether or not any insight landed — a barren
        # reflection still resets the accumulator so it is not retried every pulse.
        self._watermark[agent_id] = len(mind.memory.entries)
        if added:
            # Show the distilled belief(s), not just a count — the console is the
            # developer's window on the agents' emergent inner life, so surface what was
            # actually concluded (the in-world tell stays private to a present player).
            log.event(f"{agent_id} reflected — {len(added)} insight(s):")
            for e in added:
                log.event(f"    · {e.text}")
            if self.tell and hasattr(self.engine, "narrate_reflection"):
                await self.engine.narrate_reflection(agent_id)
        else:
            log.debug(f"{agent_id} reflected but distilled nothing this pass")
