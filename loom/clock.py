"""The world-clock: an autonomous, real-time heartbeat that gives the world its
own time, independent of any player.

The chronicle fills only from player-driven activity, and the director and the
reaction path both wait on a player-driven beat — so a still room (no one typing,
no director pulse) produces nothing and the world goes inert the moment you stop.
The clock is the missing autonomous *event source* (BACKLOG B9). It advances on the
game loop's pulse whether or not anyone is present, and when it crosses into a new
time-of-day it lands a standing condition and one ambient beat — which the minds we
already have (``NpcMind.react``, the director) answer, giving a change nobody caused
a life of its own.

This is the classic MUD weather-daemon shape (DikuMUD's ``weather.c``): a global
clock ticks from the loop heartbeat, decoupled from player action, and lands a
*deterministic* beat where it is seen. No model call for the beat itself — the life
comes from the reaction path and the director reacting to it, not from the clock
narrating cleverly. That also keeps it cheap, always-on, and — because a phase
turning is a real chronicle event — a natural *feed* for the director's restraint
floor (B8) rather than a thing that fights it.

Game-agnostic: the clock knows nothing about "dawn" or "night". It advances an
abstract minute-of-day and, on crossing into a new phase from a table the game
supplies as data, asks the engine to apply that phase. What the phases are, when
they begin, how they read, and how fast time runs are all content — authored in the
world's ``"clock"`` block (see ``game/world/world.json``) and wired via
``Engine.attach_clock``. The base engine has no clock; a game opts in.
"""
from __future__ import annotations
from dataclasses import dataclass

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class Phase:
    """One named span of the day.

    ``start`` is the minute-of-day in ``[0, 1440)`` at which this phase begins; it
    runs until the next phase's start, the last phase of the day wrapping across
    midnight until the first begins again. ``condition`` is the standing time-of-day
    fragment set when the phase begins (a look reads it until the next phase turns);
    ``ambient`` is the one-shot beat landed on entering the phase — the chronicle
    line, broadcast to each place where it is seen. Either may be empty (a phase
    that changes the standing text but says nothing, or vice versa)."""
    name: str
    start: int
    condition: str = ""
    ambient: str = ""


class WorldClock:
    """Advances an abstract day-clock on the game loop and, on crossing a phase
    boundary, drives the world's time-of-day through the engine.

    Mirrors ``Director``'s loop-system shape: ``install(loop)`` registers a
    ``tick(dt)`` callback the loop calls every pulse. Each pulse advances the clock
    by ``dt * factor`` game-minutes; a boundary crossing lands that phase's standing
    condition and ambient beat (deterministically, no model call). Tick-driven, not
    wall-clock — so a test drives it reproducibly by injecting pulses. Every phase
    shares one ``tag`` so a new time-of-day upserts the last (one standing time,
    never a pile).
    """

    def __init__(self, engine, phases: list[Phase], *, factor: float = 1.0,
                 start_minute: float | None = None, tag: str = "time") -> None:
        if not phases:
            raise ValueError("a world-clock needs at least one phase")
        # Sorted by start so the active phase is a simple ascending scan.
        self.phases = sorted(phases, key=lambda p: p.start)
        self.engine = engine
        self.factor = max(0.0, float(factor))
        self.tag = tag
        first = self.phases[0].start
        self._minute = float(first if start_minute is None
                             else start_minute) % MINUTES_PER_DAY
        self._phase = self._phase_at(self._minute)

    def _phase_at(self, minute: float) -> Phase:
        """The phase active at ``minute``: the last phase whose start is at or
        before it. Before the first phase's start — the small hours just past
        midnight — that is the last phase of the day, which runs across the wrap."""
        current = self.phases[-1]
        for p in self.phases:
            if p.start <= minute:
                current = p
            else:
                break
        return current

    @property
    def minute(self) -> float:
        """The current minute-of-day in ``[0, 1440)``."""
        return self._minute

    @property
    def phase(self) -> Phase:
        """The phase the clock is currently in."""
        return self._phase

    def install(self, loop) -> None:
        """Register on the loop and set the *initial* time-of-day silently — the
        world starts already in a phase (a look shows it from the first moment), but
        with no spurious 'night falls' beat announced at startup."""
        if self._phase.condition:
            self.engine.world.conditions.set_world(self.tag, self._phase.condition)
        loop.add_system(self.tick)

    async def tick(self, dt: float) -> None:
        """The loop callback. Advance the clock; if it crossed into a new phase,
        apply that phase's standing condition and ambient beat through the engine.

        The loop awaits each system to completion before the next pulse, so ``tick``
        never overlaps itself; the beat it lands is cheap (deterministic broadcasts
        plus fire-and-forget reactions), never a model call. If a single large pulse
        skips past a phase, the clock lands on whichever phase it is now in — one
        beat, the intermediate phase passed unremarked (won't happen at a sane
        ``factor``)."""
        self._minute = (self._minute + dt * self.factor) % MINUTES_PER_DAY
        phase = self._phase_at(self._minute)
        if phase.name == self._phase.name:
            return                       # still within the same phase — nothing to do
        self._phase = phase
        await self.engine.apply_world_condition(self.tag, phase.condition,
                                                phase.ambient)
