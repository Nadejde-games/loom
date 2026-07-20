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
from .mind import Turn, parse_turn, _extract_json

# How many recent chronicle lines the director reads as its digest of "what
# changed" each beat. Bounded so the prompt stays lean even on a busy world.
DIGEST_LINES = 24

# The act-gate (BACKLOG B8/B5), opt-in. A cheap, low-temperature, tightly
# constrained pass — "does this exact moment need a beat, or wait?" — run before
# the full compose is paid for. The local model, asked to *compose*, stages on
# nearly every pulse (it under-weights 'do nothing'); asked this one narrow
# question at low temperature with 'wait' the primed default, it may restrain.
# RESEARCH-Y and unproven — measured live before it is trusted (behavior_probe
# director.restraint); ships off until it demonstrably discriminates.
ACT_GATE_TEMPERATURE = 0.2
_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        # "wait" first so the low-temperature default token is restraint, not act.
        "decision": {"type": "string", "enum": ["wait", "act"]},
        "reason": {"type": "string"},
    },
    "required": ["decision", "reason"],
    "additionalProperties": False,
}


class DirectorMind:
    """The game-master mind. Persona + memory + the action seam, on the director's
    own offered action subset. Reads perception, returns a validated ``Turn``."""

    def __init__(self, persona: dict | None = None,
                 provider: LLMProvider | None = None,
                 registry: ActionRegistry | None = None,
                 offered: list | None = None,
                 memory: MemoryStream | None = None,
                 name: str = "the Director",
                 embedder=None, store=None) -> None:
        self.persona = persona or {}
        self.provider = provider
        # Retrieval over the director's own past beats ranks by relevance to what is
        # happening now (via the embedder), not only by recency — so it recalls a
        # fitting earlier touch for a similar scene. None = recency+importance only. An
        # optional SQLite ``store`` backs the stream (slice 1b) under the reserved
        # "director" agent key.
        self.memory = memory or MemoryStream(embedder=embedder, store=store,
                                             agent_id="director")
        self.registry = registry
        # The subset of the registry this mind may act through — its own
        # director-only actions. Both the prompt catalogue and the constrained
        # grammar are narrowed to this, exactly as an NPC's are.
        self.offered = offered
        self.name = name

    # ---- prompt ----
    async def _recall(self, query: str, k: int = 8) -> list:
        """The director's own past beats that bear on the present — relevance +
        importance + recency when an embedder is present, else the recent ones. The
        query is the chronicle digest (what is happening), so a similar scene recalls
        the fitting earlier touch and the director does not repeat itself."""
        return await self.memory.retrieve(query, k=k)

    def _system_prompt(self, chronicle: str, snapshot: str,
                       foreshadow: bool = False,
                       memories: list | None = None) -> str:
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
        mems = memories if memories is not None else self.memory.recent()
        if mems:
            # Its own past touches that bear on this moment — so it does not repeat a
            # beat it has set for a scene like this one.
            parts.append("Beats you have set that bear on this moment:\n"
                         + "\n".join(f"- {m.text}" for m in mems))
        parts.append("What has happened recently, oldest first:\n" + chronicle)
        label = ("The world right now (places with someone present, and any empty "
                 "places just ahead of them):" if foreshadow
                 else "The world right now (only places with someone present):")
        parts.append(label + "\n" + snapshot)
        if foreshadow:
            parts.append(
                "A place marked [ahead, empty] is next to the wanderers but has no "
                "one in it yet — the way they may walk next. You may quietly "
                "foreshadow there, rarely, to reward the step: prefer something "
                "that lasts (a standing condition) over a one-off line, since no "
                "one is there yet to hear a passing beat. Shaping the place the "
                "wanderers are already in still comes first.")
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
            'This is a moment that may want shaping. Give it the single touch that '
            'best fits from the actions open to you — a passing atmospheric beat, a '
            'standing change that lasts, a small real thing to find, or a purpose to '
            'draw the wanderers onward — and never more than one at a time. Commit '
            'to the touch you choose; do not hedge into doing nothing when the scene '
            'hands you something to answer. Only if nothing genuinely fits should '
            'you hold your hand and reply with an empty actions list, {"speech": "", '
            '"actions": []}. Name a place by the id shown in your view of the world '
            '(e.g. "clearing"), never by its title. Never invent actions or '
            'arguments beyond those listed.\n'
            'Example (setting a scene): {"speech": "let the wood breathe", '
            '"actions": [{"name": "stage_event", "args": {"location": "clearing", '
            '"text": "A cold wind moves through the pines, and the lanterns '
            'gutter."}}]}\n'
            'Example (watching, doing nothing): {"speech": "", "actions": []}\n'
            'Available actions:\n' + catalogue
        )

    # ---- turn ----
    async def observe(self, chronicle: str, snapshot: str,
                      lull: bool = False, foreshadow: bool = False) -> Turn:
        """Read the world and return a validated ``Turn`` — usually silent (an
        empty turn = watch and do nothing), occasionally one staged beat.

        Mirrors ``NpcMind.converse``: a constrained call (a malformed envelope is
        impossible at the token level), a tolerant parse, and one bounded retry
        feeding the exact validation error back before dropping anything invalid.

        ``lull`` marks a beat prompted by the world having gone *quiet* (B9) rather
        than by fresh activity — the nudge then asks for a gentler, lower-key touch,
        since there is nothing that just happened to answer. ``foreshadow`` tells
        the prompt the snapshot includes the empty rooms just ahead of the players,
        which the director may shape before they arrive (B9).
        """
        mems = await self._recall(chronicle)
        system = self._system_prompt(chronicle, snapshot, foreshadow=foreshadow,
                                     memories=mems)
        if lull:
            nudge = ("The scene has gone quiet — no one has stirred it for a "
                     "while. If a single small, low-key beat would keep it from "
                     "going lifeless, set it now; otherwise do nothing. Keep it "
                     "gentle — a sound, a shift of light, a small sign — never a "
                     "dramatic intrusion.")
        else:
            nudge = ("A quiet moment passes over the world. Decide whether a "
                     "single small beat would make it feel more alive right now; "
                     "if not, do nothing.")
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

    # ---- the act-gate: judge *whether* to beat before composing one ----
    def _decision_prompt(self, chronicle: str, snapshot: str,
                         memories: list | None = None) -> str:
        """A deliberately *lean* prompt for the act-gate — persona tone and goals, the
        same perception the compose call reads, and one narrow instruction. It carries
        NO action catalogue and no envelope examples: the gate decides only
        wait-or-act, so it stays cheap. Used only on the *activity* path (the lull is a
        deterministic floor, not a thing to restrain — see ``Director._run_beat``)."""
        p = self.persona
        parts = [
            f"You are {self.name}, the unseen game-master of a living text world — a "
            "watcher who shapes the scene only rarely, and only ever its atmosphere. "
            "You are not composing anything now; you are making one small judgment: "
            "would this exact moment be deepened by a touch from you, or is it better "
            "left alone?"
        ]
        if p.get("tone"):
            parts.append("Tone of the world: " + str(p["tone"]))
        if p.get("goals"):
            # The gate judges against what the director is *for* — atmosphere and
            # small omens — not a generic 'intervene only if something is broken' bar.
            parts.append("What you are shaping toward: " + ", ".join(p["goals"]))
        mems = memories if memories is not None else self.memory.recent()
        if mems:
            parts.append("Beats you have set that bear on this moment (do not crowd "
                         "them):\n" + "\n".join(f"- {m.text}" for m in mems))
        parts.append("What has happened recently, oldest first:\n" + chronicle)
        parts.append("The world right now:\n" + snapshot)
        parts.append(
            "Judge whether one small beat would deepen THIS moment. You shape "
            "atmosphere and small omens — you never fix problems or move the "
            "story, the characters do that. The test is whether the scene gives "
            "you something specific and present to answer: words exchanged, a "
            "question left hanging, characters reacting to one another, a tension "
            "held between them. When it does, a single atmospheric touch — a "
            "sound, a shift of light, an omen — can answer it: act. When it does "
            "not — only an arrival, a pause, nothing yet stirring — wait; someone "
            "merely entering a place is ordinary, not a cue, and an omen laid on "
            "an empty moment is noise, not atmosphere. Prefer to wait unless the "
            "scene hands you something to answer.")
        parts.append(
            'Reply with a single JSON object and nothing else: '
            '{"decision": "wait" or "act", "reason": "<a few words>"}. '
            '"wait" means do nothing this moment; "act" means one beat is warranted now.')
        return "\n\n".join(parts)

    async def decide(self, chronicle: str, snapshot: str) -> tuple[bool, str]:
        """The act-gate (B8): a cheap, low-temperature, constrained wait/act pass run
        *before* the full ``observe`` compose, so only a warranted moment pays for
        generation. Returns ``(act, reason)``.

        Scoped to the *activity* path (the ``Director`` does not call it on a lull —
        the lull is a liveliness floor, and the model judges a quiet scene 'leave it',
        which would kill the floor). On any parse failure the gate returns
        ``(False, ...)`` — it fails *toward silence*, the whole point of a restraint gate.
        """
        mems = await self._recall(chronicle)
        system = self._decision_prompt(chronicle, snapshot, memories=mems)
        nudge = "Make the call for this moment: wait, or act?"
        messages = [{"role": "user", "content": nudge}]
        raw = await self.provider.complete(system, messages,
                                           schema=_DECISION_SCHEMA,
                                           temperature=ACT_GATE_TEMPERATURE)
        obj = _extract_json(raw) or {}
        decision = str(obj.get("decision", "")).strip().lower()
        reason = str(obj.get("reason", "")).strip()
        return (decision == "act"), reason


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
    With the lull trigger enabled (B9, opt-in), it will *also* stir a quiet room
    now and then — a gentle beat on a slow floor — so a still world does not go
    dead between the sparser things that stir it (a player, the world-clock). Each
    beat runs on a background task so a slow reading never stalls the loop, and it
    never overlaps itself. A broken beat is logged, never fatal; the loop's own
    guard is the final backstop.
    """

    def __init__(self, engine, mind: DirectorMind, *, period_ticks: int = 12,
                 name: str = "the Director", digest_lines: int = DIGEST_LINES,
                 min_new_events: int = 3, cooldown_pulses: int = 2,
                 lull_pulses: int = 0, foreshadow: bool = False,
                 act_gate: bool = False) -> None:
        self.engine = engine
        self.mind = mind
        self.period_ticks = max(1, period_ticks)
        self.digest_lines = digest_lines
        # Restraint (BACKLOG B8): the model, offered a beat, takes one on nearly
        # every pulse. Until a model-side "should I act?" pass lands, hold the
        # frequency down deterministically in the orchestrator — most pulses the
        # director simply does nothing, without spending a call. A beat needs BOTH
        # enough to have happened (min_new_events new chronicle events since its
        # last beat) AND enough breathing room (cooldown_pulses pulses since it).
        self.min_new_events = max(1, min_new_events)
        self.cooldown_pulses = max(1, cooldown_pulses)
        # The lull trigger (B9), opt-in (0 = off). If the scene stays quiet for
        # this many director pulses since the last beat — far longer than the
        # cooldown — allow one gentle, low-key beat anyway, so a still room does not
        # go dead. A liveliness *floor* that complements the activity *ceiling*
        # (min_new_events / cooldown) above, on its own slow cadence. The game opts
        # in (LOOM_DIRECTOR_LULL); off keeps the exact prior B8 restraint behaviour.
        self.lull_pulses = max(0, lull_pulses)
        # Off-screen staging (B9), opt-in: when set, the director's snapshot also
        # carries the empty rooms just ahead of the players, and it may foreshadow
        # into them (a standing condition that outlasts the walk). Off keeps the
        # snapshot to occupied rooms only, exactly as before.
        self.foreshadow = bool(foreshadow)
        # The model-side act-gate (B8), opt-in (default off). When set, a warranted
        # pulse first pays for a cheap, low-temp wait/act decision (mind.decide) and
        # composes only on 'act' — the model judging the specific moment, layered
        # AFTER the deterministic ceiling/floor above. Off keeps the exact prior
        # behaviour (every warranted pulse composes). Unproven until measured live.
        self.act_gate = bool(act_gate)
        self.actor = _DirectorActor(name=name)
        self._ticks = 0
        self._running = False
        self._last_seq = 0          # chronicle seq at the last beat it acted on
        # Start "warmed up" so the first beat is not needlessly delayed by cooldown.
        self._pulses_since_beat = self.cooldown_pulses

    def install(self, loop) -> None:
        """Register this director as a system on the game loop."""
        loop.add_system(self.tick)

    async def tick(self, dt: float) -> None:
        """The loop callback. Counts ticks; on the period, decides whether to run
        a beat. Cheap: the model is only consulted when a beat is warranted —
        because enough has changed (the activity path) or the scene has gone quiet
        long enough (the lull path) — and someone is present to see the result."""
        self._ticks += 1
        if self._ticks < self.period_ticks or self._running:
            return
        self._ticks = 0
        self._pulses_since_beat += 1        # a pulse: an opportunity to act
        reason = self._beat_reason()
        if reason is None:
            return
        self._running = True
        task = asyncio.create_task(self._guarded_beat(reason))
        # Track on the engine so shutdown can await/cancel in-flight work, mirroring
        # how NPC replies are tracked; harmless if the engine has no such set.
        tasks = getattr(self.engine, "_tasks", None)
        if tasks is not None:
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    def _beat_reason(self) -> str | None:
        """Why (and whether) to beat this pulse: ``"activity"`` (enough new events
        to warrant a beat), ``"lull"`` (the scene has stayed quiet long enough that
        a gentle beat keeps it alive — opt-in via ``lull_pulses``), or ``None`` (do
        nothing, spending no model call). Both paths still require an audience and
        the cooldown's breathing room."""
        chron = getattr(self.engine, "chronicle", None)
        if chron is None:
            return None
        if not getattr(self.engine, "players", None):
            return None             # no audience — no reason to spend a call
        if self._pulses_since_beat < self.cooldown_pulses:
            return None             # too soon since the last beat — let it breathe
        if len(chron.since(self._last_seq)) >= self.min_new_events:
            return "activity"       # enough has happened to warrant a beat
        # The lull path (B9), off unless lull_pulses > 0: the scene has gone quiet
        # for long enough that a small low-key beat keeps it from going dead.
        if self.lull_pulses and self._pulses_since_beat >= self.lull_pulses:
            return "lull"
        return None                 # too little has happened, and not yet a lull

    async def _guarded_beat(self, reason: str) -> None:
        try:
            await self._run_beat(reason)
        except Exception as exc:    # a bad beat must never break the loop
            print(f"[loom] director beat failed: {exc!r}")
        finally:
            self._running = False

    async def _run_beat(self, reason: str) -> None:
        chron = self.engine.chronicle
        acting_on = chron.seq       # what this beat is a response to
        digest = chron.render(self.digest_lines)
        snapshot = self.engine.world_snapshot(include_adjacent=self.foreshadow)
        # The act-gate (B8), opt-in, scoped to the ACTIVITY path: before paying for a
        # full beat when something has happened, ask the model the one narrow question
        # — is a beat warranted right now? — at low temperature with 'wait' primed. On
        # 'wait' the director stays its hand. The lull path is deliberately NOT gated:
        # it is a liveliness *floor* (B9), and the model judges a quiet scene 'leave it'
        # (measured 8/8) — the very deadness the floor exists to prevent — so a restraint
        # gate must not sit on it.
        if self.act_gate and reason == "activity":
            act, _reason = await self.mind.decide(digest, snapshot)
            if not act:
                # The model judged this moment needs nothing. Mark the events seen so
                # the same ones do not re-trigger, but do NOT count this as a beat:
                # leave the cooldown untouched so a genuinely new moment is not delayed
                # by a decision to wait (a 'wait' is restraint, not an intervention).
                self._last_seq = max(acting_on, chron.seq)
                return
        turn = await self.mind.observe(digest, snapshot, lull=(reason == "lull"),
                                       foreshadow=self.foreshadow)
        # The director's "speech" is a private note, never broadcast; only its
        # validated actions touch the world, through the same seam as everyone.
        for intent in turn.actions:
            await self.engine._perform(None, self.actor, self.mind, intent)
        # Mark everything up to now as seen — including this beat's own staged
        # events — so the director does not react to its own touch next pulse, and
        # restart the cooldown from this beat.
        self._last_seq = max(acting_on, chron.seq)
        self._pulses_since_beat = 0
