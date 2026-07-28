"""Resource pools — the gauge that hard mechanics deplete and the soft world perceives
qualitatively (Phase 9, Tier 0). See docs/spikes/rpg-systems.md §C and §E.

HP, mana, stamina, hunger, thirst are all the *same* mechanism differing only in data: a
gauge whose ``max`` and ``regen`` are :class:`~loom.rpg.expr.Expr` derivations over the
character's stats and level, whose ``current`` is the mutable value clamped to
``[floor, max]``, and whose only qualitative surface to an onlooker or an NPC mind is a
game-declared **condition cue** (Diku's ``diag_char_to_char`` threshold table) — never the raw
number. This is Evennia's *gauge* trait: ``max`` is read-only (recomputed from the formula),
``current`` is what moves. Hitting ``floor`` fires a generic *depleted* signal; what depletion
*means* (death, cannot-cast, a penalty) is a per-pool ``policy`` tag consumed by later tiers —
Tier 0 models only the pool, its regen, and the signal.

:class:`RegenSystem` is the regen half: a loop system mirroring :class:`~loom.clock.WorldClock`
— ``install(loop)`` registers a ``tick`` that advances every sheeted entity's pools by their
``regen`` derivation on the heartbeat. Deterministic, no model call, off unless a game attaches
it. Pure and offline-testable: drive the tick with injected pulses, seed nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .expr import Expr


# --- the authored pool definition -------------------------------------------

@dataclass(frozen=True)
class PoolSpec:
    """One pool's authored definition (shared vocabulary, from world data). ``max`` and
    ``regen`` are derivations evaluated against the sheet's namespace; ``floor`` is the low
    bound (HP floors at 0, hunger might floor at 0 = starving). ``policy`` labels what
    depletion means to later systems (``"vital"`` = the HP-like pool an onlooker reads for a
    health descriptor). ``cues`` is the qualitative table: ``(at_least_pct, label)`` bands,
    highest first, mapping ``100*current/max`` to a word the soft world perceives."""
    key: str
    max: Expr
    regen: Any = None                       # Expr | None (None ⇒ no regen)
    floor: int = 0
    policy: str = ""
    cues: tuple = ()                        # descending (float pct, str label)

    def cue_for(self, current: int, maximum: int) -> str:
        """The condition label for ``current/maximum`` — the highest band whose threshold the
        percentage meets. Empty when no cues are declared or the pool has no capacity."""
        if not self.cues or maximum <= 0:
            return ""
        pct = 100.0 * current / maximum
        for at_least, label in self.cues:
            if pct >= at_least:
                return label
        return self.cues[-1][1]

    @property
    def top_label(self) -> str:
        """The healthiest/fullest band's label (``unharmed``, ``fed``) — the one perception
        suppresses so only a *notable* condition (hurt, hungry) is ever surfaced."""
        return self.cues[0][1] if self.cues else ""

    @classmethod
    def from_config(cls, key: str, cfg: Mapping[str, Any]) -> "PoolSpec":
        cfg = cfg or {}
        regen = cfg.get("regen")
        cues = tuple(sorted(((float(c[0]), str(c[1])) for c in cfg.get("cues", [])),
                            key=lambda c: c[0], reverse=True))
        return cls(key=str(key), max=Expr(str(cfg["max"])),
                   regen=Expr(str(regen)) if regen not in (None, "") else None,
                   floor=int(cfg.get("floor", 0)), policy=str(cfg.get("policy", "")),
                   cues=cues)


# --- the live gauge ---------------------------------------------------------

@dataclass
class Pool:
    """A live gauge: the ``spec`` it follows, its derived ``max`` and ``regen``, and the
    mutable ``current``, always clamped to ``[spec.floor, max]``."""
    spec: PoolSpec
    current: int = 0
    max: int = 0
    regen: int = 0

    def recompute(self, namespace: Mapping[str, Any]) -> None:
        """Re-derive ``max`` and ``regen`` from ``namespace`` (called on load, level-up, or a
        stat/gear change) and re-clamp ``current`` into the possibly-changed bounds."""
        self.max = max(self.spec.floor, int(self.spec.max.evaluate(namespace)))
        self.regen = int(self.spec.regen.evaluate(namespace)) if self.spec.regen else 0
        self.current = self._clamp(self.current)

    def _clamp(self, value: int) -> int:
        return max(self.spec.floor, min(int(value), self.max))

    def set(self, value: int) -> bool:
        """Set ``current`` (clamped). Returns ``True`` iff this transition brought the pool to
        its floor from above — the generic *depleted* signal later tiers act on (a death, a
        can't-cast). No-op-returns-``False`` if it was already at the floor."""
        was_above = self.current > self.spec.floor
        self.current = self._clamp(value)
        return was_above and self.current <= self.spec.floor

    def apply_regen(self) -> bool:
        """Advance ``current`` by ``regen`` (which may be negative — hunger decays), clamped.
        Returns whether the value changed. The per-tick step the :class:`RegenSystem` calls."""
        if not self.regen:
            return False
        before = self.current
        self.current = self._clamp(self.current + self.regen)
        return self.current != before

    def is_depleted(self) -> bool:
        return self.current <= self.spec.floor

    def fraction(self) -> float:
        return (self.current / self.max) if self.max > 0 else 0.0

    def condition(self) -> str:
        """This pool's current qualitative cue (``wounded``, ``starving``)."""
        return self.spec.cue_for(self.current, self.max)

    def is_notable(self) -> bool:
        """True when the cue is worth surfacing — i.e. not the healthiest/fullest band."""
        c = self.condition()
        return bool(c) and c != self.spec.top_label


# --- the regen loop system --------------------------------------------------

class RegenSystem:
    """Advances every sheeted entity's pools by their ``regen`` on the game loop — the pool
    counterpart of :class:`~loom.clock.WorldClock`. ``install(loop)`` registers ``tick``; every
    ``period_pulses`` pulses each pool takes one regen step (deterministic, no model call).
    Tick-driven so a test drives it reproducibly by injecting pulses. Off unless a game
    attaches it; iterates by duck type (``entity.sheet``), so the engine stays uncoupled."""

    def __init__(self, engine, *, period_pulses: int = 1) -> None:
        self.engine = engine
        self.period = max(1, int(period_pulses))
        self._pulses = 0

    def install(self, loop) -> None:
        loop.add_system(self.tick)

    async def tick(self, dt: float) -> None:
        self._pulses += 1
        if self._pulses % self.period:
            return
        for ent in list(self.engine.world.entities.values()):
            sheet = getattr(ent, "sheet", None)
            if sheet is not None:
                sheet.regen_tick()
