"""The loot forge (Phase 4): dynamic, context-aware item generation on the golden rule.

The split the prior art settled on (see docs/spikes/loot-generation.md), inverted for
an LLM engine: **code rolls the mechanics, the model authors the flavour.** Every mature
loot system — Path of Exile, Diablo, Borderlands, DikuMUD — derives an item's name from
mechanics that a weighted table rolls, gated by a single level scalar (ilvl / affix-level
/ item-power). Loom keeps that separation exactly and moves the model into the one slot
where it beats a canned affix→name grammar: writing a *situated* name and lore. The model
never emits a number, a tier, or a category — the LLM literature measures it unreliable
at precisely that (best models ~65% constraint feasibility), and the golden rule forbids
it regardless.

This module is the **code-owned, deterministic half** — game-agnostic and dependency-free
like the rest of ``loom``, and fully offline-testable (the roll is driven by an injected
``random.Random``; seed it and it is reproducible):

- ``LootTables`` — the authored content: rarity ``tiers`` (each a weight + an ordinal
  ``rank``), a ``themes`` pool (the coherence handle, Borderlands' manufacturer analog),
  and a ``tags`` pool (the mechanical classification, PoE's affix pool). Authored as world
  data (the ``"loot"`` block in world.json), never hardcoded here.
- ``roll_brief`` — the roll: pick a ``tier`` (weighted), then draw ``tags`` gated by the
  tier's rank and *filtered toward the moment* (a tag that resonates with the place's
  current conditions is preferred), one per family group (PoE's mod-group guard) so a
  brief never contradicts itself. Higher tier ⇒ more tags ⇒ a richer item.
- ``flavour_schema`` / ``parse_flavour`` — the flat, minimal JSON schema the model authors
  into and the tolerant validation of what comes back. Deliberately **flat** (no ``oneOf``
  over item kinds — a weak model collapses a ``oneOf`` to its simplest branch, and heavier
  schemas incur a generation "constraint tax"): one object, ``name`` + ``description`` +
  bounded ``aliases``.
- ``fallback_flavour`` — a real, if plain, item composed from the brief alone, so the
  forge **never fails to a non-item** when the model is unreachable or unusable.

The *model call* itself lives in ``loom/ai/loot.py`` (world-free, like ``loom/ai/intent.py``);
the *orchestration* (gather context → roll → author → assemble via ``World.spawn_item``)
lives in the engine, which owns the world and the trigger.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# --- the authored tables ----------------------------------------------------

@dataclass(frozen=True)
class Tier:
    """One rarity band. ``rank`` is the ordinal scalar (1 = commonest) that gates which
    tags are eligible and how many an item of this tier may carry — Loom's ilvl analog.
    ``weight`` is its relative roll frequency. ``max_tags`` caps the tag count (0 ⇒ use
    ``rank``, so richer tiers naturally carry more)."""
    name: str
    rank: int = 1
    weight: int = 1
    max_tags: int = 0


@dataclass(frozen=True)
class TagSpec:
    """One drawable quality. ``group`` is its family — at most one tag per group is
    drawn, so contradictory qualities never co-occur (PoE mod-groups). An ungrouped tag
    is its own family. ``min_rank`` gates it to tiers of at least that rank (rarer
    qualities on rarer items). ``when`` names the condition tags this quality resonates
    with (e.g. ``weather``, ``time``); a tag whose ``when`` matches the place's current
    conditions is preferred, so a storm-lit place tends to yield a storm-touched thing."""
    tag: str
    group: str = ""
    min_rank: int = 1
    when: tuple = ()


@dataclass
class LootTables:
    """The authored loot content: the tier ladder, the theme pool, and the tag pool.
    Parsed from the game's ``"loot"`` block; ``is_empty`` (no tiers) means there is
    nothing to roll, and the forge stays dark."""
    tiers: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.tiers

    @classmethod
    def from_config(cls, cfg: dict | None) -> "LootTables":
        cfg = cfg or {}
        tiers = [Tier(name=str(t["name"]), rank=int(t.get("rank", i + 1)),
                      weight=int(t.get("weight", 1)),
                      max_tags=int(t.get("max_tags", 0)))
                 for i, t in enumerate(cfg.get("tiers", [])) if t.get("name")]
        themes = [str(x) for x in cfg.get("themes", []) if str(x).strip()]
        tags = [TagSpec(tag=str(t["tag"]), group=str(t.get("group", "")),
                        min_rank=int(t.get("min_rank", 1)),
                        when=tuple(str(w) for w in t.get("when", ())))
                for t in cfg.get("tags", []) if t.get("tag")]
        return cls(tiers=tiers, themes=themes, tags=tags)


# --- the rolled brief -------------------------------------------------------

@dataclass
class Brief:
    """A code-rolled item spec — the mechanical half, decided before the model is asked.
    The model authors flavour *to* this and may not alter it; the engine attaches
    ``tier``/``tags``/``theme`` to the finished ``Item`` as code-owned truth. ``context``
    is a one-line situational blurb (where/why it appears) for the flavour prompt only."""
    tier: str
    rank: int
    theme: str
    tags: list
    context: str = ""


def _weighted_choice(items: list, rng: Any, weight) -> Any:
    """Pick one item with probability proportional to ``weight(item)`` (min 1), from an
    injected RNG so the roll is deterministic under a seed."""
    total = sum(max(1, weight(i)) for i in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for i in items:
        upto += max(1, weight(i))
        if r <= upto:
            return i
    return items[-1]


def _draw_tags(specs: list, rank: int, rng: Any, context_tags: set,
               max_n: int) -> list:
    """Draw up to ``max_n`` tags eligible at ``rank``, one per family group, preferring
    those that resonate with ``context_tags``. Deterministic under a seeded ``rng``."""
    eligible = [t for t in specs if t.min_rank <= rank]

    def resonant(t: TagSpec) -> bool:
        return (t.tag in context_tags or (t.group and t.group in context_tags)
                or any(w in context_tags for w in t.when))

    preferred = [t for t in eligible if resonant(t)]
    rest = [t for t in eligible if not resonant(t)]
    rng.shuffle(preferred)
    rng.shuffle(rest)
    chosen: list = []
    used_groups: set = set()
    for t in preferred + rest:
        if len(chosen) >= max_n:
            break
        family = t.group or t.tag        # an ungrouped tag is its own family
        if family in used_groups:
            continue
        used_groups.add(family)
        chosen.append(t.tag)
    return chosen


def roll_brief(tables: LootTables, rng: Any, *, context_tags=(),
               context_blurb: str = "") -> Brief | None:
    """Roll a full item brief: a weighted ``tier``, then ``tags`` gated by its rank and
    filtered toward the moment, then a ``theme``. ``None`` if there are no tiers to roll.
    Pure but for ``rng`` — seed the ``random.Random`` for a reproducible roll."""
    if not tables.tiers:
        return None
    tier = _weighted_choice(tables.tiers, rng, weight=lambda t: t.weight)
    max_n = tier.max_tags or tier.rank
    tags = _draw_tags(tables.tags, tier.rank, rng, set(context_tags), max_n)
    theme = rng.choice(tables.themes) if tables.themes else ""
    return Brief(tier=tier.name, rank=tier.rank, theme=theme, tags=tags,
                 context=context_blurb)


# --- the flavour schema + validation (the model's flat, minimal surface) ----

def flavour_schema(max_aliases: int = 4) -> dict:
    """The flat JSON schema the model authors an item's flavour into: ``name`` +
    ``description`` (required) and a bounded ``aliases`` array. No ``oneOf``, no nested
    branches, no mechanical fields — the model touches only words, and the shape is
    minimal so grammar-constrained decoding pays no "constraint tax"."""
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"},
                        "maxItems": max_aliases},
        },
        "required": ["name", "description"],
        "additionalProperties": False,
    }


def parse_flavour(obj: Any, *, max_aliases: int = 4, max_name: int = 80,
                  max_desc: int = 500) -> dict | None:
    """Validate and normalise a model-authored flavour object. Returns a clean
    ``{name, description, aliases}`` dict, or ``None`` if it is not a usable object (not
    a dict, or missing a non-empty name or description) — the caller then falls back to
    a brief-only item. Bounds every field so a runaway reply can never mint a monster
    string; the model authors only flavour, so nothing mechanical is read here."""
    if not isinstance(obj, dict):
        return None
    name = str(obj.get("name", "")).strip()
    desc = str(obj.get("description", "")).strip()
    if not name or not desc:
        return None
    raw_aliases = obj.get("aliases")
    if not isinstance(raw_aliases, list):
        raw_aliases = []
    aliases: list = []
    for a in raw_aliases:
        a = str(a).strip()
        if a and a.lower() not in (x.lower() for x in aliases):
            aliases.append(a[:max_name])
        if len(aliases) >= max_aliases:
            break
    return {"name": name[:max_name], "description": desc[:max_desc],
            "aliases": aliases}


def fallback_flavour(brief: Brief) -> dict:
    """A real — if plain — item composed from the brief alone, so the forge never fails
    to a non-item when the model is unreachable or its reply unusable. Names it from its
    strongest quality and its theme, so even the fallback reads in-world."""
    base = brief.theme or "trinket"
    qual = brief.tags[0].replace("-", " ") if brief.tags else ""
    name = f"a {qual} {base}".replace("  ", " ").strip() if qual else f"a {base}"
    desc = (f"A {base} of {brief.tier} make"
            + (f", marked by something {qual}." if qual else "."))
    aliases = [brief.theme] if brief.theme else []
    return {"name": name, "description": desc, "aliases": aliases}


@dataclass
class LootForge:
    """The wired forge the engine holds: the authored ``tables``, the ``provider`` that
    authors flavour, a seeded ``rng`` for the roll, and the constant flat ``schema``.
    A holder, not the orchestrator — the engine drives context → roll → author →
    assemble, because only it owns the world and the trigger."""
    tables: LootTables
    provider: Any
    rng: Any
    schema: dict
    max_aliases: int = 4
