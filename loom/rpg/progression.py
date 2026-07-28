"""Progression — XP, levels, and the stat-points growth economy (Phase 9, Tier 1). See
docs/spikes/rpg-systems.md Tier 1 "Progression".

Two deliberate growth currencies meet the sheet here (the third, use-based skills, is
``skills.py``): **XP → levels**, and **levels → build points → trained attributes**. Both are
rules-as-data over the Tier 0 evaluator, so the framework hardcodes no curve and no cost:

- The **XP curve** ``xp_to_level(level)`` is an authored formula giving the running XP needed to
  *reach* a level (DikuMUD's ``exp_to_level`` table / D&D's advancement table, as an expression).
  A single grant may cross several thresholds at once (catch-up), and each level crossed grants
  ``points_per_level`` build points. Because ``level`` is already a variable in the pool
  derivations, HP/mana grow on level-up for free — no new wiring.
- **Training** spends those build points to raise an attribute through the **same point-buy cost
  table** used at authoring (§H) — one escalating curve spans character creation and play-growth,
  exactly Diku's practice-points lineage.

This module ships only the :class:`Progression` rule (parse + curve); the operations live on the
:class:`~loom.rpg.sheet.Sheet` as ``grant_xp`` / ``train`` so the engine drives growth by duck
type and never imports this package. Pure and deterministic — offline-testable end to end.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .expr import Expr


@dataclass
class Progression:
    """The game's declared advancement rule: the XP ``curve`` (running XP to reach a level),
    the ``points_per_level`` build-point grant, and an optional ``max_level`` cap."""
    curve: Any = None                                   # Expr: xp_to_level(level)
    points_per_level: int = 1
    max_level: int = 0                                  # 0 = uncapped

    _LEVEL_CAP = 9999                                   # runaway-loop backstop

    def xp_to_level(self, level: int) -> int:
        """Running XP required to *reach* ``level`` (level 1 is free)."""
        if level <= 1 or self.curve is None:
            return 0
        return int(self.curve.evaluate({"level": level}))

    def cap(self) -> int:
        return min(self.max_level, self._LEVEL_CAP) if self.max_level else self._LEVEL_CAP

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None) -> "Progression | None":
        """Parse a ``"progression"`` block: ``curve`` formula, ``points_per_level``,
        ``max_level``. ``None`` when no block is declared (a world with sheets but no leveling)."""
        if not cfg:
            return None
        return cls(curve=Expr(str(cfg.get("curve", "100 * (level - 1) * level"))),
                   points_per_level=int(cfg.get("points_per_level", 1)),
                   max_level=int(cfg.get("max_level", 0)))
