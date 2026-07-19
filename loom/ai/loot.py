"""The loot-forge flavour author: a code-rolled brief -> the item's name and lore.

The AI-layer counterpart of the deterministic ``loom/loot.py`` forge, and the sibling
of ``loom/ai/intent.py``. Code rolls the mechanical brief (tier, theme, tags — all
code-owned); this asks the model to author *only the flavour* (name, description,
aliases) that fits that brief and the moment, constrained by the flat JSON schema so it
cannot emit a number, a tier, or a structure the forge did not ask for. The model writes
words; code owns every mechanic — the golden rule, applied to loot.

World-free, like ``intent.py``: handed a brief and a rendered schema, never the ``World``.
"""
from __future__ import annotations

from .provider import LLMProvider
from .mind import _extract_json   # shared tolerant single-object JSON extraction


def _system_prompt(brief) -> str:
    tags = ", ".join(brief.tags) if brief.tags else "(none)"
    theme = brief.theme or "(none)"
    return (
        "You are the loot-forge of a text adventure: you give a newly found object its "
        "name and its lore. What KIND of thing it is — its rarity, its theme, and a few "
        "qualities — has already been decided and is fixed. Your only task is to write "
        "the words that bring it to life, true to what you are told and to the moment it "
        "appears in. Do NOT invent stats, numbers, powers, or extra qualities, and do "
        "not contradict the brief.\n\n"
        f"Rarity: {brief.tier}\n"
        f"Theme: {theme}\n"
        f"Qualities: {tags}\n"
        f"Where and why it appears: {brief.context or 'found by a wanderer'}\n\n"
        "Reply with a single JSON object and nothing else, in this shape:\n"
        '{"name": "<the name as it reads in the room, e.g. \'a rain-beaded silver '
        'charm\'>", "description": "<a sentence or two of lore, read when it is '
        'examined>", "aliases": ["<a short extra noun it answers to, e.g. \'charm\'>"]}'
        "\n\n"
        "Let the theme and qualities show in the words as description, not as a list. "
        "A rarer thing earns a richer name and lore; a common thing is plain and honest. "
        "The name carries its own article (\"a\", \"an\", \"the\"); keep it short."
    )


async def author_flavour(provider: LLMProvider, schema: dict, brief) -> dict | None:
    """Ask the model to author an item's flavour for a code-rolled ``brief``. Returns the
    parsed JSON dict (raw shape — the caller runs ``loot.parse_flavour`` to validate and
    normalise it), or ``None`` when nothing object-shaped comes back (an unconstrained
    backend that just chatted, or the offline ``FakeProvider``) so the forge falls back
    to a brief-only item. Never raises on a bad reply — a failed forge must never break
    the turn that triggered it."""
    system = _system_prompt(brief)
    messages = [{"role": "user", "content": "Forge it."}]
    try:
        raw = await provider.complete(system, messages, schema=schema)
    except Exception as exc:
        # Degrade to None (the caller forges a brief-only item) — but SURFACE it, so a
        # transient provider error (a throttle) is never silently read as "the model
        # declined", exactly as intent.interpret learned to.
        print(f"[loom] loot flavour failed ({type(exc).__name__}: {exc}); "
              "forging from the brief alone")
        return None
    obj = _extract_json(raw)
    return obj if isinstance(obj, dict) else None
