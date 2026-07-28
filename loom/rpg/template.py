"""Character templates — races, classes, and backgrounds as *one* mechanism (Phase 9,
Tier 0). See docs/spikes/rpg-systems.md §H.

Races, classes, and backgrounds are architecturally one thing: a **template**, a named data
bundle that contributes to a starting sheet — adjusts base stats, grants starting skills and
build points, sets the starting level / hit-die (and, at later tiers, abilities and gear). The
framework knows exactly one operation: **compose an ordered template stack into a sheet**
(base → race → class → background). "Race"/"class"/"background" are just labels the *game*
declares in ``kind``; the framework is agnostic to how many there are and what they are called —
the same enum-free split every Tier 0 system uses. This is also the layered-composition seam
gear and buffs plug into at Tier 1 (base → +modifiers → derived), built once here.

Pure and deterministic: :func:`compose` reads a ruleset's defaults and calls its
``make_sheet`` by duck type — it imports nothing else from the RPG layer, so it composes any
object that quacks like a :class:`~loom.rpg.sheet.Ruleset`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Template:
    """A named contribution to a starting sheet. ``stats`` are **additive** deltas to the base
    attributes (``{"con": +1}``); ``skills`` grant a skill at a starting level; ``points`` grant
    build points; ``level`` / ``hit_die`` *set* those scalars (last template in the stack that
    specifies one wins). ``kind`` is the game's label (``race`` / ``class`` / ``background``)."""
    name: str
    kind: str = ""
    stats: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    points: int = 0
    level: Any = None                                   # int | None (None ⇒ leave as is)
    hit_die: Any = None                                 # int | None

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "Template":
        return cls(
            name=str(cfg["name"]),
            kind=str(cfg.get("kind", "")),
            stats={str(k): int(v) for k, v in (cfg.get("stats") or {}).items()},
            skills={str(k): int(v) for k, v in (cfg.get("skills") or {}).items()},
            points=int(cfg.get("points", 0)),
            level=(int(cfg["level"]) if cfg.get("level") is not None else None),
            hit_die=(int(cfg["hit_die"]) if cfg.get("hit_die") is not None else None),
        )


def compose(ruleset, templates, *, base: Mapping[str, int] | None = None,
            level: int = 1, full: bool = True):
    """Compose an ordered ``templates`` stack into a fresh sheet: start from the ruleset's
    default stat block (or ``base``), apply each template's additive stat deltas, accumulate
    granted skills (highest level wins) and build points, and take the last-set level / hit-die.
    Returns whatever ``ruleset.make_sheet`` produces — a :class:`~loom.rpg.sheet.Sheet`."""
    stats = dict(ruleset.stats.defaults())
    if base:
        stats.update({k: int(v) for k, v in base.items()})
    skills: dict = {}
    points = 0
    hit_die = None
    for t in templates:
        for key, delta in t.stats.items():
            stats[key] = stats.get(key, 0) + int(delta)
        for name, lvl in t.skills.items():
            skills[name] = max(skills.get(name, 0), int(lvl))
        points += t.points
        if t.level is not None:
            level = int(t.level)
        if t.hit_die is not None:
            hit_die = int(t.hit_die)
    skill_map = {name: {"xp": 0, "level": lvl} for name, lvl in skills.items()}
    return ruleset.make_sheet(stats=stats, level=level, hit_die=hit_die,
                              skills=skill_map, points=points, full=full)


def default_sheet(ruleset, *, full: bool = True):
    """Compose the ruleset's declared **default** template stack — the single assigned starting
    sheet a connecting player is given (§H: build through play, no creation screen). Falls back
    to a bare default sheet when no templates are declared."""
    templates = [ruleset.template(n) for n in getattr(ruleset, "default_templates", [])]
    return compose(ruleset, [t for t in templates if t is not None], full=full)
