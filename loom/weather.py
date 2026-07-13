"""Probabilistic weather — the world-clock's sibling (BACKLOG B9).

Where the clock advances time-of-day on a fixed schedule, weather *wanders*: a
**bounded random walk** over a small chain of sky-states (clear ↔ cloudy ↔ rain ↔
storm), stepping now and then and never jumping, so the sky can sit in a state for a
while before it shifts. This is DikuMUD's weather-daemon model (`weather.c`'s pressure
walk) — change reads with continuity, not as random noise.

Like the clock it rides the game loop decoupled from any player, and lands each step
through the *same* engine bridge (`apply_world_condition`): a standing world-scope
condition (tag ``"weather"``, coexisting with the clock's ``"time"``) plus one
deterministic ambient beat that the minds answer of their own volition. The beat is
deterministic — no model call — so the life comes from the reaction path, and a
sky-turn is a real chronicle event that *feeds* the director's restraint (B8).

Deterministic and testable: the walk is driven by an injected ``random.Random`` (a
test seeds it for a reproducible sequence) and rolls are counted in loop *pulses*, not
wall-clock. Game-agnostic — the states, their text, and the pace are all content (the
world's ``"weather"`` block); the base engine has no weather until a game attaches one.
"""
from __future__ import annotations
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Weather:
    """One sky-state in the chain.

    ``condition`` is the standing world-scope fragment while this state holds — empty
    means *clear* (nothing worth noting), which lifts the weather tag rather than
    showing a line. ``ambient`` is the one-shot beat announced on entering the state
    (the chronicle line, broadcast to each place where it is seen)."""
    name: str
    condition: str = ""
    ambient: str = ""


class WeatherSystem:
    """A bounded random walk over a chain of ``Weather`` states, on the game loop.

    Mirrors ``WorldClock``: ``install(loop)`` registers a ``tick(dt)`` callback the
    loop calls each pulse; every ``period_pulses`` pulses it *rolls* — with
    probability ``change_chance`` it steps one place along the chain (up toward storm
    or down toward clear, bounded at the ends), and on a real step applies the new
    state's standing condition + ambient beat through the engine. The walk is driven
    by ``rng`` (inject a seeded ``random.Random`` for a reproducible test). Opt-in;
    the base engine has no weather.
    """

    def __init__(self, engine, states: list[Weather], *, period_pulses: int = 12,
                 change_chance: float = 0.4, start_index: int = 0,
                 tag: str = "weather", rng: random.Random | None = None) -> None:
        if not states:
            raise ValueError("weather needs at least one state")
        self.engine = engine
        self.states = states
        self.period_pulses = max(1, period_pulses)
        self.change_chance = min(1.0, max(0.0, change_chance))
        self.tag = tag
        self._rng = rng or random.Random()
        self._i = min(max(0, start_index), len(states) - 1)
        self._pulses = 0

    @property
    def state(self) -> Weather:
        """The current sky-state."""
        return self.states[self._i]

    def install(self, loop) -> None:
        """Register on the loop and set the *initial* weather silently — a look reads
        the sky from the first moment, but with no spurious 'a storm begins' beat at
        startup (and clear weather sets nothing)."""
        s = self.state
        if s.condition:
            self.engine.world.conditions.set_world(self.tag, s.condition)
        loop.add_system(self.tick)

    async def tick(self, dt: float) -> None:
        """Count pulses; every ``period_pulses`` roll the walk. On a step, land the
        new state's standing condition and ambient beat through the shared engine
        bridge (the same one the clock uses)."""
        self._pulses += 1
        if self._pulses < self.period_pulses:
            return
        self._pulses = 0
        if len(self.states) == 1:
            return                        # a single state never walks
        if self._rng.random() >= self.change_chance:
            return                        # the sky holds this roll
        # Step one place along the chain, bounded at the ends: at a boundary the only
        # way is inward, otherwise up or down by a coin.
        if self._i == 0:
            step = 1
        elif self._i == len(self.states) - 1:
            step = -1
        else:
            step = 1 if self._rng.random() < 0.5 else -1
        self._i += step
        s = self.state
        await self.engine.apply_world_condition(self.tag, s.condition, s.ambient)
