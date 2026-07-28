"""Stats — the character sheet's number vocabulary (Phase 9, Tier 0). See
docs/spikes/rpg-systems.md §A and §H.

The framework hardcodes **no** attributes — no ``STR``/``DEX`` enum, no modifier formula.
A game declares its attribute vocabulary as world data (the ``"stats"`` block in world.json)
exactly the way the clock declares phases and the loot forge declares tiers; the demo world
happens to declare the classic six (STR/DEX/CON/INT/WIS/CHA) with the D&D 5e maths, but that
choice lives entirely in the data. This module ships the machinery only:

- :class:`StatDef` — one named integer attribute with ``min`` / ``max`` / ``default``.
- :class:`StatSet` — the declared vocabulary: the attributes, plus **derived** values
  (``con_mod = (con - 10) // 2``, ``prof = 2 + (level - 1) // 4``) as sandboxed
  :class:`~loom.rpg.expr.Expr` formulas exposed by name in the evaluator namespace, plus the
  optional point-buy rule. It validates a ``{name: value}`` block against the vocabulary and
  assembles the full derivation namespace (base values → derived values → caller ``extra``
  such as ``level`` / ``hit_die`` / skill levels) that pools and combat formulas read from.
- :class:`PointBuy` — the character-creation / balancing budget: a total budget and a
  cumulative per-score cost table (§H — used as the authoring discipline now, ready to power
  a player-facing creation flow and the Tier 1 ``train`` growth economy later).

Pure and deterministic — no engine, no I/O — so the whole vocabulary is offline-testable:
bounds, tolerant coercion vs strict validation, derived evaluation, and point-buy costing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .expr import Expr, ExprError


# --- one attribute ----------------------------------------------------------

@dataclass(frozen=True)
class StatDef:
    """A single named integer attribute. ``min``/``max`` bound it; ``default`` is the value a
    sheet takes when it declares nothing (the enum-free analog of a class's base array)."""
    name: str
    min: int = 1
    max: int = 30
    default: int = 10

    def clamp(self, value: int) -> int:
        return max(self.min, min(self.max, int(value)))

    def in_bounds(self, value: int) -> bool:
        return self.min <= value <= self.max


# --- the point-buy budget ---------------------------------------------------

@dataclass(frozen=True)
class PointBuy:
    """A point-buy rule: a total ``budget`` and a **cumulative** cost table mapping a score to
    the points it costs to reach it (D&D 5e's ``{8:0, 9:1, … 14:7, 15:9}``). ``floor``/``ceil``
    bound each buyable score. The framework ships the mechanism; the numbers are game data.

    Used two ways across the arc (§H, Tier 1): as the authoring discipline that keeps every
    sheet balanced against one budget, and later as the play-time growth economy the ``train``
    verb spends level-earned points through — one mechanism, two callers."""
    budget: int
    floor: int
    ceil: int
    cost: Mapping[int, int]

    def cost_of_score(self, score: int) -> int:
        """The cumulative point cost of a single score. Raises if the score is outside the
        cost table (i.e. outside the buyable range)."""
        try:
            return self.cost[int(score)]
        except KeyError:
            raise ExprError(f"score {score} is outside the point-buy range") from None

    def cost_of(self, values: Mapping[str, int]) -> int:
        """Total point cost of every attribute value in ``values``."""
        return sum(self.cost_of_score(v) for v in values.values())

    def remaining(self, values: Mapping[str, int]) -> int:
        return self.budget - self.cost_of(values)

    def validate(self, values: Mapping[str, int]) -> list[str]:
        """Human-readable reasons ``values`` is not a legal point-buy allocation (each score in
        ``[floor, ceil]`` and the total within budget); empty list ⇒ legal."""
        errors: list[str] = []
        total = 0
        for name, v in values.items():
            if not (self.floor <= v <= self.ceil):
                errors.append(f"{name}={v} is outside the buy range {self.floor}..{self.ceil}")
                continue
            total += self.cost.get(int(v), 0)
        if not errors and total > self.budget:
            errors.append(f"spends {total} points, over the budget of {self.budget}")
        return errors

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None) -> "PointBuy | None":
        if not cfg:
            return None
        cost = {int(k): int(v) for k, v in (cfg.get("cost") or {}).items()}
        return cls(budget=int(cfg.get("budget", 0)), floor=int(cfg.get("floor", 1)),
                   ceil=int(cfg.get("ceil", 20)), cost=cost)


# --- the declared vocabulary ------------------------------------------------

@dataclass
class StatSet:
    """The game's declared stat vocabulary: the ``attributes``, the ordered ``derived`` values
    (name → compiled :class:`Expr`), and an optional :class:`PointBuy`. Built once from world
    data; validates blocks and assembles the derivation namespace the rest of the RPG layer
    reads from."""
    attributes: list = field(default_factory=list)          # list[StatDef]
    derived: list = field(default_factory=list)             # list[tuple[str, Expr]]
    point_buy: Any = None                                   # PointBuy | None
    _by_name: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {s.name: s for s in self.attributes}

    # --- queries -----------------------------------------------------------

    def names(self) -> tuple:
        """The attribute names, in declared order."""
        return tuple(s.name for s in self.attributes)

    def get(self, name: str) -> StatDef | None:
        return self._by_name.get(name)

    def is_empty(self) -> bool:
        return not self.attributes

    def defaults(self) -> dict:
        """A block of every attribute at its declared default."""
        return {s.name: s.default for s in self.attributes}

    # --- blocks (a {name: value} allocation) -------------------------------

    def coerce(self, values: Mapping[str, int] | None) -> dict:
        """A runtime-tolerant block: start from defaults, take known attributes from ``values``
        clamped into bounds, and **drop** any unknown key. Never raises — the shape used on
        save-overlay load, mirroring the tolerant-load discipline elsewhere in ``loom``."""
        block = self.defaults()
        for name, v in (values or {}).items():
            stat = self._by_name.get(name)
            if stat is not None:
                try:
                    block[name] = stat.clamp(v)
                except (TypeError, ValueError):
                    pass  # keep the default for a non-numeric authored value
        return block

    def validate(self, values: Mapping[str, int]) -> list[str]:
        """Authoring-strict check of a block: reasons it is not a clean allocation (unknown
        attribute, out-of-bounds value). Empty ⇒ clean. Does not apply the point-buy budget —
        call :meth:`PointBuy.validate` for that."""
        errors: list[str] = []
        for name, v in values.items():
            stat = self._by_name.get(name)
            if stat is None:
                errors.append(f"unknown attribute {name!r}")
            elif not isinstance(v, (int, bool)):
                errors.append(f"{name} must be an integer, got {v!r}")
            elif not stat.in_bounds(v):
                errors.append(f"{name}={v} is outside bounds {stat.min}..{stat.max}")
        return errors

    # --- the derivation namespace ------------------------------------------

    def derive(self, values: Mapping[str, int], extra: Mapping[str, Any] | None = None) -> dict:
        """Assemble the full namespace formulas read from: the caller's ``extra`` (``level``,
        ``hit_die``, skill levels — whatever the sheet supplies), then the coerced base
        attribute values, then each derived value evaluated **in declared order** over the
        accumulating namespace (so a derived value may reference an earlier one). This is the
        map handed to every pool / XP / combat :class:`Expr`."""
        ns: dict = dict(extra or {})
        ns.update(self.coerce(values))
        for name, expr in self.derived:
            ns[name] = expr.evaluate(ns)
        return ns

    # --- construction from world data --------------------------------------

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None) -> "StatSet":
        """Parse a ``"stats"`` block: ``attributes`` (a list of ``{name,min,max,default}``),
        ``derived`` (a name → formula-string map, order preserved), and optional ``point_buy``.
        A malformed derivation formula raises :class:`ExprError` naming the offending value —
        bad world data fails loudly at load, like the worlddraft gate."""
        cfg = cfg or {}
        attributes = [StatDef(name=str(a["name"]),
                              min=int(a.get("min", 1)),
                              max=int(a.get("max", 30)),
                              default=int(a.get("default", 10)))
                      for a in cfg.get("attributes", []) if a.get("name")]
        derived: list = []
        for name, formula in (cfg.get("derived") or {}).items():
            try:
                derived.append((str(name), Expr(str(formula))))
            except ExprError as e:
                raise ExprError(f"derived stat {name!r}: {e}") from e
        return cls(attributes=attributes, derived=derived,
                   point_buy=PointBuy.from_config(cfg.get("point_buy")))
