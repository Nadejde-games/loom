"""The salience gate: whether an NPC engages with what it just heard.

Design intent B4 — not every NPC should react to everything. Two things decide
it, at two layers. First, a cheap, deterministic *gate* here, run before any LLM
call so an unengaged NPC costs nothing (no thinking beat, no generation, no
line). Second, for those the gate lets through, the NPC's *own* persona-driven
choice inside the mind: it may still answer with silence (an empty turn), which
the engine renders as nothing. This module is the first layer.

It is game-agnostic, dependency-free, and swappable: a game — or the Phase 3
director mediating attention across a room — can install a smarter gate
(including an LLM-backed one) behind the same seam.

The default gate does one thing mechanically well: **directed address**. If the
speaker names someone present, only the named NPC(s) engage and the rest stay
out. When no one present is named, the gate defers to the NPCs themselves —
everyone is let through to decide in character. Topic and mood relevance belong
there, where the persona is known, not in this lexical layer.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import re


@dataclass
class SalienceContext:
    """What the gate needs to decide, as plain data — no ``World`` access."""
    npc: Any                                    # deciding NPC; has .name, .persona
    speaker_name: str
    utterance: str
    present_npcs: list = field(default_factory=list)  # names of NPCs in the room


def _name_forms(name: str) -> list[str]:
    """Address forms for a name: the full name and its first word."""
    name = (name or "").strip()
    if not name:
        return []
    forms = {name.lower(), name.split()[0].lower()}
    return [f for f in forms if f]


def _mentions(name: str, utterance: str) -> bool:
    """Is ``name`` (full or first name) addressed in ``utterance``, whole-word?"""
    u = utterance.lower()
    return any(re.search(rf"\b{re.escape(f)}\b", u) for f in _name_forms(name))


def is_addressed(name: str, utterance: str) -> bool:
    """Public: is ``name`` directly named in ``utterance``? The engine uses this
    to frame a heard line as addressed-to-you vs. merely overheard (B4)."""
    return _mentions(name, utterance)


class SalienceGate:
    """Decide whether an NPC engages with an utterance.

    Override ``should_engage`` to install a different policy — e.g. an LLM-backed
    relevance gate, or the Phase 3 director arbitrating who in the room answers.
    """

    def should_engage(self, ctx: SalienceContext) -> bool:
        addressed = any(_mentions(n, ctx.utterance) for n in ctx.present_npcs)
        if addressed:
            # Someone present was named: only the named engage; others stay out.
            return _mentions(ctx.npc.name, ctx.utterance)
        # No one present was named: defer to the NPC's own in-character judgement.
        return True


def default_gate() -> SalienceGate:
    """The gate every world gets unless it installs its own."""
    return SalienceGate()
