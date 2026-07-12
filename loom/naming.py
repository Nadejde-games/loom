"""Name resolution: turn a noun phrase a player or an NPC used into the one
entity it means, chosen from what is currently in reach.

Game-agnostic and dependency-free. This is the other half of ``salience`` —
``salience.is_addressed`` only asks "was this name mentioned in an utterance?";
here we go the other way: given a phrase and the set of entities in scope,
decide which one it denotes, and report honestly when the phrase is *ambiguous*
so the caller can ask "which one do you mean?" (the Inform / TADS model) rather
than guessing.

The matching is layered by strictness, after the DikuMUD/LPMud verb tables and
IF parsers: an exact id/name/alias hit beats a whole-word hit beats a prefix
("bra" -> "brass key") hit. One winner resolves; several at the top strictness
are surfaced as ambiguous; none is a no-match. Entities are duck-typed — this
module deliberately does not import ``loom.world`` — so any object exposing
``name`` (and optionally ``aliases`` / ``id``) can be resolved.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Resolved:
    """Exactly one entity in scope matched the phrase."""
    entity: Any


@dataclass
class Ambiguous:
    """Several entities matched equally well — the caller must disambiguate."""
    candidates: list = field(default_factory=list)


@dataclass
class NoMatch:
    """Nothing in scope matched the phrase."""
    phrase: str = ""


def _norm(text: Any) -> str:
    """Lowercase and collapse whitespace — the canonical form for comparison."""
    return " ".join(str(text).lower().split())


def _name_forms(entity: Any) -> list[str]:
    """Every lowercased string this entity answers to: its name, its declared
    aliases, and — as a convenience for typed ids — its id."""
    forms: list[str] = []
    name = getattr(entity, "name", None)
    if name:
        forms.append(_norm(name))
    for alias in getattr(entity, "aliases", None) or ():
        forms.append(_norm(alias))
    eid = getattr(entity, "id", None)
    if eid:
        forms.append(_norm(eid))
    return [f for f in forms if f]


def _score(phrase: str, entity: Any) -> int:
    """How strongly ``phrase`` denotes ``entity``: 3 exact form, 2 whole-word
    run, 1 prefix, 0 no match. Higher is stricter."""
    p = _norm(phrase)
    if not p:
        return 0
    best = 0
    for form in _name_forms(entity):
        if p == form:
            return 3
        # A run of whole words: "key" or "brass key" inside "ornate brass key".
        if f" {p} " in f" {form} ":
            best = max(best, 2)
        # Prefix abbreviation: "bra" -> "brass key", "brass k" -> "brass key".
        elif form.startswith(p) or any(w.startswith(p) for w in form.split()):
            best = max(best, 1)
    return best


def resolve(phrase: str, scope) -> Resolved | Ambiguous | NoMatch:
    """Resolve ``phrase`` against an iterable of entities in scope.

    Returns ``Resolved(entity)`` on a single best match, ``Ambiguous(...)`` when
    several tie at the top strictness, or ``NoMatch`` otherwise. Because an exact
    match always outranks a mere word or prefix hit, "Odd" beside "Oddments" is
    unambiguous, while two brass keys are correctly flagged ambiguous."""
    scored = [(s, e) for e in scope if (s := _score(phrase, e)) > 0]
    if not scored:
        return NoMatch(phrase=_norm(phrase))
    best = max(s for s, _ in scored)
    winners = [e for s, e in scored if s == best]
    return Resolved(winners[0]) if len(winners) == 1 else Ambiguous(winners)
