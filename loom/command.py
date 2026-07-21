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
import re
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

# --- multi-command splitting (B11) -----------------------------------------
# A line may carry more than one intent. Two mature IF parsers (TADS 3, Inform 7)
# converge on the model we follow: `.` `;` and the word `then` are *unconditional*
# command separators, while `and` / `,` are object-conjunctions by default and are
# promoted to a command separator only when the next token is a known verb
# (verb-led promotion). A free-text verb (say) swallows its remainder verbatim and
# is never re-split. See docs/spikes/commands.md.
MAX_COMMANDS = 16                # a runaway fuse (the cascade-fuse lesson)
_SEPARATORS = {".", ";", ","}    # punctuation tokens (own tokens; `,` is soft)
_CHAIN_WORDS = {"then"}          # word separators that always start a new command
_ALL_WORDS = {"all", "everything"}   # bare quantifier — expands against scope
# Split a direct-object phrase into conjoined objects: "X and Y", "X, Y".
_CONJ_RE = re.compile(r"\s*,\s*|\s+and\s+", re.IGNORECASE)
# One token = a run of non-space, non-separator chars, OR a single separator char.
# Spans are kept so a free-text verb can reclaim its exact remainder from the
# original line (commas/periods inside an utterance are preserved untouched).
_TOKEN_RE = re.compile(r"[.;,]|[^\s.;,]+")


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
    all_objects: bool = False       # the direct object was bare "all"/"everything"
    truncated: bool = False         # set on the last Parse when the runaway cap cut the line
    source: str = ""                # the original segment text (for the B1b fallback)


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
    """A JSON Schema constraining a free-text command to one canonical verb and its
    object phrases — the verb-table counterpart of ``ActionRegistry.json_schema``.

    A **flat** object — ``{"command": {"verb": <enum>, "dobj": <string>, "iobj":
    <string>}}`` — deliberately NOT a ``oneOf`` over per-verb branches. A
    discriminated union whose branches differ only in which fields they require
    invites a weaker model, under strict decoding, to collapse to the *simplest*
    branch: measured 2026-07-19, ``qwen3.6-35b-a3b`` picked ``look`` (the only
    object-free verb, the shortest valid branch) for *every* input, 0/4, where a flat
    enum maps them 4/4. A flat schema makes the model *reason* the verb rather than
    take the cheapest grammar path, and still guarantees the shape (verb ∈ the allowed
    set). ``dobj`` is required so the object is actually filled — a harmless spurious
    ``dobj`` on an object-free verb like ``look`` is ignored downstream; ``iobj`` is
    optional (give's recipient, take's source). See docs/PROMPTING.md for the wider
    lesson. Still flows through the identical dispatch the deterministic parser feeds.
    """
    canon = [v.canonical for v in _distinct_verbs(verbs, allowed)]
    return {
        "type": "object",
        "properties": {"command": {
            "type": "object",
            "properties": {
                "verb": {"enum": canon},
                "dobj": {"type": "string"},
                "iobj": {"type": "string"},
            },
            "required": ["verb", "dobj"],
            "additionalProperties": False,
        }},
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
    dobj = _strip_article(dobj)
    # A bare "all"/"everything" as a world-action's object is a quantifier, not a
    # thing to name-resolve — the engine expands it against the verb's scope (B11).
    all_objects = verb.kind == "action" and dobj.lower() in _ALL_WORDS
    return Parse(verb=verb, surface=surface,
                 dobj=dobj, iobj=_strip_article(iobj), all_objects=all_objects)


# --- multi-command entry (B11) ---------------------------------------------

def _tokens_with_spans(text: str) -> list:
    """Tokenise ``text`` into ``(token, start, end)`` spans over the *original*
    string: runs of non-space/non-separator chars, and each ``. ; ,`` as its own
    token. The spans let a free-text verb reclaim its exact remainder verbatim."""
    return [(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _match_verb_tokens(toks: list, idx: int, verbs: dict):
    """The greedy verb match of ``parse``, over the tokenised line: try a two-word
    surface, then one word. Returns ``(Verb, n_tokens)`` or ``(None, 0)``. A
    separator token is never a verb. Used both to start a segment and, crucially,
    to look ahead past ``and``/``,`` — is the next thing a command, or an object?"""
    if idx >= len(toks):
        return (None, 0)
    w0 = toks[idx][0]
    if w0 in _SEPARATORS:
        return (None, 0)
    first = w0.lower()
    if idx + 1 < len(toks):
        w1 = toks[idx + 1][0]
        if w1 not in _SEPARATORS:
            two = f"{first} {w1.lower()}"
            if two in verbs:
                return (verbs[two], 2)
    if first in verbs:
        return (verbs[first], 1)
    return (None, 0)


def split_commands(text: str, verbs: dict) -> list:
    """Split a line into command segments (raw strings) — the deterministic,
    verb-led splitter (B11). ``. ; then`` always separate; ``and`` / ``,`` separate
    only when the next token is a known verb, else they stay inside the current
    segment as an object-conjunction; a free-text verb swallows the rest of the line.

    Caps at ``MAX_COMMANDS`` + 1 segments so ``parse_line`` can flag a truncated
    line rather than silently dropping the tail."""
    toks = _tokens_with_spans(text)
    segments: list = []
    i, n = 0, len(toks)
    while i < n and len(segments) <= MAX_COMMANDS:
        # Skip whatever separator (or dangling and/then) ended the previous segment.
        while i < n and (toks[i][0] in _SEPARATORS
                         or toks[i][0].lower() in _CHAIN_WORDS
                         or toks[i][0].lower() == "and"):
            i += 1
        if i >= n:
            break
        start = toks[i][1]
        verb, vlen = _match_verb_tokens(toks, i, verbs)
        # A free-text verb (say/tell) keeps everything after it, verbatim — one
        # utterance, never re-split — so it is the last command on its line.
        if verb is not None and verb.kind == "text":
            seg = text[start:].strip()
            if seg:
                segments.append(seg)
            break
        consumed = vlen if verb is not None else 1
        j = i + consumed
        end = toks[i + consumed - 1][2]
        while j < n:
            w = toks[j][0]
            wl = w.lower()
            if w in (".", ";") or wl in _CHAIN_WORDS:
                break
            if w == "," or wl == "and":
                nxt, _ = _match_verb_tokens(toks, j + 1, verbs)
                if nxt is not None:                 # verb-led: a new command begins
                    break
                # else: a conjunction inside this command's object phrase — keep it.
            end = toks[j][2]
            j += 1
        seg = text[start:end].strip()
        if seg:
            segments.append(seg)
        i = j
    return segments


def _expand_conjunction(p: Parse) -> list:
    """A single world-action whose direct object is ``X and Y`` / ``X, Y`` becomes
    one ``Parse`` per object, sharing the verb and indirect object (``give sword and
    shield to Odd`` → two gives → Odd). Returns ``[p]`` when there is nothing to
    expand (not an action, no object, a bare ``all``, or a single object)."""
    if (p.verb is None or p.verb.kind != "action" or p.verb.dobj is None
            or p.all_objects or not p.dobj):
        return [p]
    parts = [_strip_article(x) for x in _CONJ_RE.split(p.dobj)]
    parts = [x for x in parts if x]
    if len(parts) <= 1:
        return [p]
    return [Parse(verb=p.verb, surface=p.surface, dobj=obj, iobj=p.iobj,
                  source=p.source) for obj in parts]


def parse_line(text: str, verbs: dict) -> list:
    """Parse a whole line of player input into a *sequence* of ``Parse`` (B11).

    Splits the line into command segments (verb-led), parses each with the
    single-command ``parse``, and expands object-conjunctions into repeated
    actions. A line with no separators yields exactly what ``parse`` alone would,
    wrapped in a one-element list — so the single-command path is unchanged. The
    last ``Parse`` carries ``truncated=True`` if the runaway cap cut the line."""
    segments = split_commands(text, verbs)
    truncated = len(segments) > MAX_COMMANDS
    if truncated:
        segments = segments[:MAX_COMMANDS]
    parses: list = []
    for seg in segments:
        p = parse(seg, verbs)
        p.source = seg
        parses.extend(_expand_conjunction(p))
    if truncated and parses:
        parses[-1].truncated = True
    return parses
