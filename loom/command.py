"""The player-command parser (B1): flexible player text -> a structured command.

This is the player-side mirror of the NPC action seam. Where an NPC *proposes*
an action as JSON and the engine validates it, a player *types* an action as
prose and this parser turns it into the same kind of proposal — a canonical verb
with its object phrases — which the engine then resolves against the world and
runs through the very same ``ActionRegistry``. One execution path, whoever acts.

Layering (deliberate, like ``naming.py``): this module is pure *syntax* and holds
no world state. It knows the surface forms of verbs, their arity, and their
prepositions; it does **not** resolve nouns to entities or touch the ``World`` —
that is the engine's job (it owns the world and the scopes). The parser names the
scope each object slot should resolve against *symbolically* (``FLOOR``,
``INVENTORY``, …); the engine maps those symbols to real candidate sets. So the
whole parser is unit-testable offline with nothing but a verb table.

Design informed by the IF/MUD prior art (see docs/BACKLOG.md B1): a verb table
with synonyms and multi-word verbs (DikuMUD/LPMud), a ``verb + DO + prep + IO``
grammar, and scope-based noun resolution with disambiguation (Inform 7 / TADS).
The tolerant free-text LLM fallback is the planned second tier (B1b); this is the
deterministic first tier, which handles the overwhelming majority of input with
no model at all.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# --- symbolic scopes -------------------------------------------------------
# The engine maps each to a real candidate list; the parser only names them, so
# it never imports the world. Kept as plain strings for painless testing.
FLOOR = "floor"              # items lying in the actor's room
INVENTORY = "inventory"      # items the actor is holding
OCCUPANTS = "occupants"      # other characters present in the room
PRESENT = "present"          # occupants + floor items (a source to take from)
SCOPE = "scope"             # everything in reach (occupants + floor + inventory)
IOBJ_CONTENTS = "iobj_contents"  # items held by the resolved indirect object
RAW = "raw"                  # not resolved — passed through verbatim (a direction)

# Directions are a small closed set, resolved by the world's exits (RAW), not by
# name-resolution. Bare directions are commands in their own right ("n", "north").
DIRECTIONS = {
    "north": "north", "south": "south", "east": "east", "west": "west",
    "up": "up", "down": "down",
    "n": "north", "s": "south", "e": "east", "w": "west", "u": "up", "d": "down",
}

_ARTICLES = ("the ", "a ", "an ", "my ", "your ")


@dataclass(frozen=True)
class Slot:
    """One object of a verb: which action-arg it fills, resolved in which scope."""
    arg: str
    scope: str


@dataclass(frozen=True)
class Verb:
    """A canonical verb: how it is written, and the shape of its command.

    ``kind`` drives engine dispatch:
      * ``"action"`` — a world-changing action run through the registry seam;
        ``target`` is the action name (e.g. ``take_item``).
      * ``"query"``  — a read-only command handled directly (look, inventory);
        ``target`` is a query id.
      * ``"text"``   — the remainder is free text, not object phrases (say).
    """
    canonical: str
    kind: str
    target: str = ""
    dobj: Slot | None = None
    preps: tuple = ()               # prepositions that separate DO from IO
    iobj: Slot | None = None
    # take X from Y: the item is found in Y's contents, so when an indirect
    # object was supplied the direct object resolves against IOBJ_CONTENTS.
    dobj_from_iobj: bool = False
    ack: str = ""                   # second-person confirmation, e.g. "You take {item}."
    ack_source: str = ""            # variant used when an (optional) source resolved


@dataclass
class Parse:
    """The result of parsing a line: a matched verb plus its raw phrases.

    Nouns are *not* resolved here — ``dobj``/``iobj`` are the phrases the engine
    will resolve against scope. ``verb is None`` means the first word matched no
    known verb (``unknown`` carries it); an empty line yields ``verb is None``
    with an empty ``unknown``.
    """
    verb: Verb | None = None
    surface: str = ""               # the exact verb text matched
    dobj: str = ""                  # direct-object phrase (raw)
    iobj: str = ""                  # indirect-object phrase (raw)
    words: str = ""                 # free-text remainder (for kind == "text")
    unknown: str = ""               # the unrecognised first token, if any


def _strip_article(phrase: str) -> str:
    p = phrase.strip()
    low = p.lower()
    for art in _ARTICLES:
        if low.startswith(art):
            return p[len(art):].strip()
    return p


def default_verbs() -> dict:
    """The built-in player vocabulary: surface form -> ``Verb``.

    Game-agnostic. A game may add or override entries (e.g. a new verb bound to a
    custom action) without touching this module. Multi-word surfaces (``pick up``)
    are matched greedily before single words.
    """
    look = Verb("look", "query", target="look")
    examine = Verb("examine", "query", target="examine",
                   dobj=Slot("target", SCOPE))
    take = Verb("take", "action", target="take_item",
                dobj=Slot("item", FLOOR), preps=("from",),
                iobj=Slot("source", PRESENT), dobj_from_iobj=True,
                ack="You take {item}.", ack_source="You take {item} from {source}.")
    drop = Verb("drop", "action", target="drop_item",
                dobj=Slot("item", INVENTORY), ack="You drop {item}.")
    give = Verb("give", "action", target="give_item",
                dobj=Slot("item", INVENTORY), preps=("to",),
                iobj=Slot("recipient", OCCUPANTS), ack="You give {item} to {recipient}.")
    say = Verb("say", "text", target="say")
    go = Verb("go", "query", target="go", dobj=Slot("direction", RAW))
    inv = Verb("inventory", "query", target="inventory")
    quests = Verb("quests", "query", target="quests")
    who = Verb("who", "query", target="who")
    hlp = Verb("help", "query", target="help")
    quit_ = Verb("quit", "query", target="quit")

    table = {
        "look": look, "l": look,
        "examine": examine, "x": examine, "inspect": examine, "look at": examine,
        "take": take, "get": take, "grab": take, "pick up": take,
        "drop": drop, "put down": drop,
        "give": give, "hand": give,
        "say": say,
        "go": go, "move": go, "walk": go,
        "inventory": inv, "inv": inv, "i": inv,
        "quests": quests, "quest": quests, "journal": quests, "log": quests,
        "who": who,
        "help": hlp, "?": hlp, "commands": hlp,
        "quit": quit_, "exit": quit_,
    }
    return table


def _distinct_verbs(verbs: dict, allowed=None) -> list:
    """The distinct Verbs behind a surface->Verb table, first-seen order, optionally
    narrowed to an allowed set of canonical names."""
    seen, out = set(), []
    for v in verbs.values():
        if v.canonical in seen:
            continue
        if allowed is not None and v.canonical not in allowed:
            continue
        seen.add(v.canonical)
        out.append(v)
    return out


def _wants_dobj(v: Verb) -> bool:
    """Whether a verb takes a direct object (or, for say/go, a text argument)."""
    return v.kind == "text" or v.target == "go" or v.dobj is not None


def _usage_line(v: Verb) -> str:
    """A single human-readable usage hint, derived from the verb's own shape."""
    parts = [v.canonical]
    if v.kind == "text":
        parts.append("<words>")
    elif v.target == "go":
        parts.append("<direction>")
    else:
        if v.dobj is not None:
            parts.append(f"<{v.dobj.arg}>")
        if v.iobj is not None:
            prep = v.preps[0] if v.preps else "with"
            core = f"{prep} <{v.iobj.arg}>"
            parts.append(f"[{core}]" if v.dobj_from_iobj else core)
    return " ".join(parts)


def describe_verbs(verbs: dict, allowed=None) -> str:
    """A compact catalogue of commands for the intent-parser prompt — the verb
    counterpart of ``ActionRegistry.describe()``."""
    return "\n".join(f"- {_usage_line(v)}" for v in _distinct_verbs(verbs, allowed))


def command_schema(verbs: dict, allowed=None) -> dict:
    """A JSON Schema constraining a free-text command to one canonical verb and
    its object phrases — the verb-table counterpart of ``ActionRegistry.json_schema``.

    The LLM fallback (B1b) is constrained to this so it can only emit a real
    command: an object ``{"command": {...}}`` whose value is a ``oneOf`` over the
    (allowed) verbs, each branch pinning ``verb`` to a const and declaring the
    string object phrases that verb takes (``dobj`` always for a verb that wants
    one; ``iobj`` required for a two-object verb like give, optional for take's
    source). Same shape the deterministic parser produces, so the model's output
    flows through the identical dispatch.
    """
    branches = []
    for v in _distinct_verbs(verbs, allowed):
        props = {"verb": {"const": v.canonical}}
        required = ["verb"]
        if _wants_dobj(v):
            props["dobj"] = {"type": "string"}
            required.append("dobj")
        if v.iobj is not None:
            props["iobj"] = {"type": "string"}
            if not v.dobj_from_iobj:      # give needs a recipient; take's source is optional
                required.append("iobj")
        branches.append({
            "type": "object", "properties": props,
            "required": required, "additionalProperties": False,
        })
    return {
        "type": "object",
        "properties": {"command": {"oneOf": branches}},
        "required": ["command"],
        "additionalProperties": False,
    }


def parse(text: str, verbs: dict) -> Parse:
    """Parse one line of player input against a verb table.

    Order of attempts: an empty line; a bare direction ("n", "north"); a
    two-word verb ("pick up"); a one-word verb. On a match, the remainder is
    split into a direct- and (optionally) indirect-object phrase on the verb's
    preposition. Free-text verbs (say) keep the remainder verbatim, with original
    case.
    """
    raw = text.strip()
    if not raw:
        return Parse()
    tokens = raw.split()
    first = tokens[0].lower()

    # A bare direction is a movement command with the direction as its object.
    if first in DIRECTIONS and len(tokens) == 1:
        return Parse(verb=verbs.get("go"), surface=first,
                     dobj=DIRECTIONS[first])

    # Greedy verb match: try a two-word surface first ("pick up", "look at").
    verb = surface = None
    if len(tokens) >= 2:
        two = f"{first} {tokens[1].lower()}"
        if two in verbs:
            verb, surface = verbs[two], two
            rest = " ".join(tokens[2:])
    if verb is None:
        if first in verbs:
            verb, surface = verbs[first], first
            rest = " ".join(tokens[1:])
        else:
            return Parse(unknown=tokens[0])

    rest = rest.strip()

    # Free text: keep the remainder as the player wrote it.
    if verb.kind == "text":
        return Parse(verb=verb, surface=surface, words=rest)

    # go/move: the remainder is a raw direction, normalised via the alias map.
    if verb.target == "go":
        d = rest.lower()
        return Parse(verb=verb, surface=surface, dobj=DIRECTIONS.get(d, d))

    # Object grammar: split on the verb's preposition into DO / IO, if present.
    dobj, iobj = rest, ""
    for prep in verb.preps:
        marker = f" {prep} "
        if marker in rest:
            head, _, tail = rest.partition(marker)
            dobj, iobj = head, tail
            break
    return Parse(verb=verb, surface=surface,
                 dobj=_strip_article(dobj), iobj=_strip_article(iobj))
