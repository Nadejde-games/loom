"""Use-based skills — the organic growth axis beside deliberate attribute-building (Phase 9;
the second growth currency the user asked for). See docs/spikes/rpg-systems.md Tier 1 "Skills".

The Elder Scrolls / RuneScape model: *you get better at what you do*. A **skill** is a
data-declared proficiency (unarmed, blades, archery, firearms, a magic school, lockpicking) —
the vocabulary is game data, so guns and spells coexist. Each skill is a per-character
``{xp, level}`` on the :class:`~loom.rpg.sheet.Sheet`, and it rises **through use**: every
skill-tagged action awards its skill XP on use, with a bonus on success, through the same
XP-curve evaluator character level uses. A skill's ``governing`` attribute is named so a formula
can read **both** an attribute and its skill (``bow to-hit = dex_mod + archery``); the sheet's
namespace already exposes each skill's level by name.

This module ships the *mechanism* — the vocabulary, the curve, and the award/level-up — pure and
offline-testable. The *trigger* (which action tags which skill) is a Tier 2 concern (combat and
abilities), which will call :meth:`SkillSet.award`; nothing here reaches into the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .expr import Expr


@dataclass(frozen=True)
class SkillDef:
    """One declared proficiency. ``governing`` names the attribute/modifier that also feeds a
    formula using this skill (empty ⇒ the skill stands alone). ``max_level`` caps growth
    (0 ⇒ uncapped)."""
    name: str
    governing: str = ""
    max_level: int = 0

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "SkillDef":
        return cls(name=str(cfg["name"]), governing=str(cfg.get("governing", "")),
                   max_level=int(cfg.get("max_level", 0)))


@dataclass
class SkillSet:
    """The game's declared skill vocabulary and the shared progression rule: the XP ``curve``
    (``xp`` needed to reach a level, a formula in ``level``) and the per-use award (``xp_per_use``
    plus ``success_bonus`` on success). One rule spans every skill, guns to spells."""
    skills: list = field(default_factory=list)          # list[SkillDef]
    curve: Any = None                                   # Expr: xp threshold for a level
    xp_per_use: int = 5
    success_bonus: int = 5
    _by_name: dict = field(default_factory=dict, init=False, repr=False)

    _LEVEL_CAP = 999                                    # runaway-loop backstop

    def __post_init__(self) -> None:
        self._by_name = {s.name: s for s in self.skills}

    def names(self) -> tuple:
        return tuple(s.name for s in self.skills)

    def get(self, name: str) -> SkillDef | None:
        return self._by_name.get(name)

    def is_empty(self) -> bool:
        return not self.skills

    def xp_to_level(self, level: int) -> int:
        """XP required to reach ``level`` (the running total, not the increment). ``level <= 0``
        is free (an untrained skill is level 0)."""
        if level <= 0 or self.curve is None:
            return 0
        return int(self.curve.evaluate({"level": level}))

    def award(self, skills: dict, name: str, *, success: bool = False) -> dict:
        """Award use-XP to ``skills[name]`` (creating it at level 0), then level it up while the
        running XP meets the next threshold. Mutates ``skills`` in place and returns
        ``{gain, xp, level, leveled}``. The one hook Tier 2 calls when a skill-tagged action
        resolves — the `success` bonus is why a landed blow trains faster than a whiff."""
        entry = skills.setdefault(name, {"xp": 0, "level": 0})
        cap = self._cap_for(name)
        gain = self.xp_per_use + (self.success_bonus if success else 0)
        entry["xp"] = int(entry.get("xp", 0)) + gain
        leveled = False
        while entry["level"] < cap:
            need = self.xp_to_level(entry["level"] + 1)
            if need <= self.xp_to_level(entry["level"]) or entry["xp"] < need:
                break                                   # non-increasing curve, or not there yet
            entry["level"] += 1
            leveled = True
        return {"gain": gain, "xp": entry["xp"], "level": entry["level"], "leveled": leveled}

    def _cap_for(self, name: str) -> int:
        spec = self._by_name.get(name)
        if spec is not None and spec.max_level:
            return min(spec.max_level, self._LEVEL_CAP)
        return self._LEVEL_CAP

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None) -> "SkillSet | None":
        """Parse a ``"skills"`` block: ``list`` of skill defs, an XP ``curve`` formula, and the
        ``xp_per_use`` / ``success_bonus`` award. ``None`` when no block is declared."""
        if not cfg:
            return None
        skills = [SkillDef.from_config(s) for s in cfg.get("list", []) if s.get("name")]
        curve = Expr(str(cfg.get("curve", "50 * level * level")))
        return cls(skills=skills, curve=curve,
                   xp_per_use=int(cfg.get("xp_per_use", 5)),
                   success_bonus=int(cfg.get("success_bonus", 5)))
