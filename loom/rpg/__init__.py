"""``loom.rpg`` — the optional RPG mechanics layer (Phase 9). See
docs/spikes/rpg-systems.md.

A game-agnostic *machinery* package: a stat engine, resource pools, a derivation evaluator,
the character sheet, and (as later tiers land) skills, gear, combat. Two invariants govern it
and are not re-decided per module: **mechanism lives here, every race/class/number/formula is
world data**; and **code resolves, the model narrates** — the numbers are computed by
deterministic, seeded, offline-testable code, the LLM only chooses intent and narrates.

This subpackage is **opt-in and dependency-free** — not a pip extra (it adds no deps), and the
base engine never imports it. A game attaches it explicitly (as it attaches the clock and
weather); entities without a :class:`Sheet` are wholly unaffected, so a sheetless world behaves
exactly as before.
"""
from __future__ import annotations

from .expr import Expr, ExprError, compile_expr, evaluate, SAFE_FUNCS
from .stats import StatDef, StatSet, PointBuy
from .pool import Pool, PoolSpec, RegenSystem
from .skills import SkillDef, SkillSet
from .progression import Progression
from .effects import Modifier, Effect
from .template import Template, compose, default_sheet
from .sheet import Sheet, Ruleset

__all__ = [
    "Expr", "ExprError", "compile_expr", "evaluate", "SAFE_FUNCS",
    "StatDef", "StatSet", "PointBuy",
    "Pool", "PoolSpec", "RegenSystem",
    "SkillDef", "SkillSet",
    "Progression",
    "Modifier", "Effect",
    "Template", "compose", "default_sheet",
    "Sheet", "Ruleset",
]
