"""The game-master director: a world-observing mind on a slow cadence.

Where an ``NpcMind`` is one character answering one utterance, the director is
the unseen hand over the whole stage. It has no body and speaks as no character;
on a slow pulse it reads what has happened (the chronicle) and how things stand
(a snapshot), and *now and then* shapes the scene — for this first slice, an
ambient beat narrated into a room (``stage_event``).

It rides the same seam as everyone else. Its actions are registered on the one
``ActionRegistry`` and offered to it (and only it) via the per-mind ``offered``
subset, so it inherits constrained decoding, validation, and the bounded retry
for free — and NPCs are never offered the director's actions, nor it theirs. The
golden rule is unbroken: the director *proposes*; the engine *disposes*.

Two pieces live here:
  * ``DirectorMind`` — the mind (persona + memory + the action seam), mirroring
    ``NpcMind``: one constrained turn where an empty ``actions`` list means
    'watch and do nothing'.
  * ``Director`` — the orchestrator that hangs the mind off the game loop: slow,
    lazy (no model call when nothing has happened or no one is present),
    non-blocking (each beat runs on a background task), and non-overlapping.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field

from ..action import ActionRegistry
from .provider import LLMProvider
from .memory import MemoryStream
from .mind import Turn, parse_turn

# How many recent chronicle lines the director reads as its digest of "what
# changed" each beat. Bounded so the prompt stays lean even on a busy world.
DIGEST_LINES = 24


class DirectorMind:
    """The game-master mind. Persona + memory + the action seam, on the director's
    own offered action subset. Reads perception, returns a validated ``Turn``."""

    def __init__(self, persona: dict | None = None,
                 provider: LLMProvider | None = None,
                 registry: ActionRegistry | None = None,
                 offered: list | None = None,
                 memory: MemoryStream | None = None,
                 name: str = "the Director") -> None:
        self.persona = persona or {}
        self.provider = provider
        self.memory = memory or MemoryStream()
        self.registry = registry
        # The subset of the registry this mind may act through — its own
        # director-only actions. Both the prompt catalogue and the constrained
        # grammar are narrowed to this, exactly as an NPC's are.
        self.offered = offered
        self.name = name

    # ---- prompt ----
    def _system_prompt(self, chronicle: str, snapshot: str) -> str:
        p = self.persona
        parts = [
            f"You are {self.name}, the unseen game-master of a living text world. "
            "You have no body and you speak as no character. You watch what "
            "unfolds and, sparingly, shape the scene to make the world feel alive "
            "and to draw play forward. You never move, speak, or decide for the "
            "player or the characters — you only set the stage around them."
        ]
        if p.get("backstory"):
            parts.append(str(p["backstory"]))
        if p.get("tone"):
            parts.append("Tone of the world: " + str(p["tone"]))
        if p.get("goals"):
            parts.append("What you are shaping toward: " + ", ".join(p["goals"]))
        mems = self.memory.recent()
        if mems:
            # Its own recent touches — so it does not repeat a beat it just set.
            parts.append("Beats you have recently set:\n"
                         + "\n".join(f"- {m.text}" for m in mems))
        parts.append("What has happened recently, oldest first:\n" + chronicle)
        parts.append("The world right now (only places with someone present):\n"
                     + snapshot)
        parts.append(self._action_instructions())
        return "\n\n".join(parts)

    def _action_instructions(self) -> str:
        catalogue = (self.registry.describe(self.offered)
                     if self.registry is not None else "(no actions available)")
        return (
            'Respond with a single JSON object and nothing else, in exactly this '
            'shape:\n'
            '{"speech": "<a brief private note to yourself on your intent, shown '
            'to no one>", "actions": [{"name": "<action>", "args": {<named '
            'arguments>}}]}\n'
            'Intervene sparingly: most beats the world needs nothing from you — '
            'then reply with an empty actions list, {"speech": "", "actions": []}. '
            'Act only when a small touch genuinely improves the moment, and never '
            'more than one beat at a time. Name a place by the id shown in your '
            'view of the world (e.g. "clearing"), never by its title. Never invent '
            'actions or arguments beyond those listed.\n'
            'Example (setting a scene): {"speech": "let the wood breathe", '
            '"actions": [{"name": "stage_event", "args": {"location": "clearing", '
            '"text": "A cold wind moves through the pines, and the lanterns '
            'gutter."}}]}\n'
            'Example (watching, doing nothing): {"speech": "", "actions": []}\n'
            'Available actions:\n' + catalogue
        )

    # ---- turn ----
    async def observe(self, chronicle: str, snapshot: str) -> Turn:
        """Read the world and return a validated ``Turn`` — usually silent (an
        empty turn = watch and do nothing), occasionally one staged beat.

        Mirrors ``NpcMind.converse``: a constrained call (a malformed envelope is
        impossible at the token level), a tolerant parse, and one bounded retry
        feeding the exact validation error back before dropping anything invalid.
        """
        system = self._system_prompt(chronicle, snapshot)
        nudge = ("A quiet moment passes over the world. Decide whether a single "
                 "small beat would make it feel more alive right now; if not, do "
                 "nothing.")
        messages = [{"role": "user", "content": nudge}]
        schema = (self.registry.json_schema(self.offered)
                  if self.registry is not None else None)

        raw = await self.provider.complete(system, messages, schema=schema)
        speech, actions, errors = parse_turn(self.registry, raw)

        if errors and self.registry is not None:
            correction = ("Your previous reply proposed invalid actions:\n"
                          + "\n".join(f"- {e}" for e in errors)
                          + "\nReply again as a single JSON object; fix or omit "
                            "those actions.")
            retry = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ]
            raw2 = await self.provider.complete(system, retry, schema=schema)
            speech2, actions2, _ = parse_turn(self.registry, raw2)
            speech = speech2 or speech
            actions = actions2

        if speech:
            self.memory.add(f"I mused: {speech}", kind="director")
        return Turn(speech=speech, actions=actions)


@dataclass(frozen=True)
class _DirectorActor:
    """A bodiless actor stub for the seam: the director is present everywhere and
    nowhere. It carries no ``location_id`` — its actions name their target room
    explicitly (``stage_event``'s ``location`` arg), so ``_perform`` broadcasts
    exactly where the beat was aimed rather than to the actor's own room."""
    id: str = "director"
    name: str = "the Director"


class Director:
    """Hangs a ``DirectorMind`` off the game loop on a slow, lazy cadence.

    A tick system (register via ``install``): every ``period_ticks`` ticks it
    considers a beat, but only if something has actually happened since its last
    beat and at least one player is present — otherwise it spends no model call.
    Each beat runs on a background task so a slow reading never stalls the loop,
    and it never overlaps itself. A broken beat is logged, never fatal; the loop's
    own guard is the final backstop.
    """

    def __init__(self, engine, mind: DirectorMind, *, period_ticks: int = 12,
                 name: str = "the Director", digest_lines: int = DIGEST_LINES) -> None:
        self.engine = engine
        self.mind = mind
        self.period_ticks = max(1, period_ticks)
        self.digest_lines = digest_lines
        self.actor = _DirectorActor(name=name)
        self._ticks = 0
        self._running = False
        self._last_seq = 0          # chronicle seq at the last beat it acted on

    def install(self, loop) -> None:
        """Register this director as a system on the game loop."""
        loop.add_system(self.tick)

    async def tick(self, dt: float) -> None:
        """The loop callback. Counts ticks; on the period, decides whether to run
        a beat. Cheap: the model is only consulted when the world has changed and
        someone is present to see the result."""
        self._ticks += 1
        if self._ticks < self.period_ticks or self._running:
            return
        self._ticks = 0
        if not self._should_beat():
            return
        self._running = True
        task = asyncio.create_task(self._guarded_beat())
        # Track on the engine so shutdown can await/cancel in-flight work, mirroring
        # how NPC replies are tracked; harmless if the engine has no such set.
        tasks = getattr(self.engine, "_tasks", None)
        if tasks is not None:
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    def _should_beat(self) -> bool:
        chron = getattr(self.engine, "chronicle", None)
        if chron is None or chron.seq == self._last_seq:
            return False            # nothing has happened since the last beat
        if not getattr(self.engine, "players", None):
            return False            # no audience — no reason to spend a call
        return True

    async def _guarded_beat(self) -> None:
        try:
            await self._run_beat()
        except Exception as exc:    # a bad beat must never break the loop
            print(f"[loom] director beat failed: {exc!r}")
        finally:
            self._running = False

    async def _run_beat(self) -> None:
        chron = self.engine.chronicle
        acting_on = chron.seq       # what this beat is a response to
        digest = chron.render(self.digest_lines)
        snapshot = self.engine.world_snapshot()
        turn = await self.mind.observe(digest, snapshot)
        # The director's "speech" is a private note, never broadcast; only its
        # validated actions touch the world, through the same seam as everyone.
        for intent in turn.actions:
            await self.engine._perform(None, self.actor, self.mind, intent)
        # Mark everything up to now as seen — including this beat's own staged
        # events — so the director does not react to its own touch next pulse.
        self._last_seq = max(acting_on, chron.seq)
