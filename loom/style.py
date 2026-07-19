"""Semantic text styling for the wire (BACKLOG B3).

Route (b): the engine tags spans with *semantic roles* — a name, a spoken line, a
gesture, an item, an exit — and the client owns the theme that turns a role into
colour. The engine never emits a raw colour code, so a rich client, a plain
terminal, and a ``NO_COLOR`` terminal all read the same message and each render it
as far as it can. Game-agnostic and dependency-free: a styled line is just data.

A *styled line* is a list of spans, each ``{"t": <text>, "s": <role>}`` (the role
is omitted for default, unstyled text). It rides the existing ``text`` channel
exactly where a plain string would — the payload is polymorphic, a string OR a span
list — so styling is opt-in per line and every plain line keeps flowing unchanged.
``plain()`` flattens either form back to prose for the chronicle, logs, and any
client with no colour.
"""
from __future__ import annotations


class Style:
    """The semantic roles a span may carry. A client maps each to a colour/weight;
    an unknown role (or none) renders as default text — so the vocabulary can grow
    without breaking any client."""
    NAME = "name"        # a character's name
    SPEECH = "speech"    # a spoken line
    EMOTE = "emote"      # a gesture / physical-action narration
    ITEM = "item"        # an object in the world
    EXIT = "exit"        # a way out of a place
    PLACE = "place"      # a location's name / heading
    QUEST = "quest"      # a quest / goal title
    AMBIENT = "ambient"  # the world's own unattributed voice (director, weather, clock)
    DANGER = "danger"    # a warning / hostile beat


def span(text, role: str | None = None) -> dict:
    """One span: a run of text and the semantic role it plays (none = default)."""
    text = str(text)
    return {"t": text, "s": role} if role else {"t": text}


def styled(*parts) -> list:
    """Normalise ``parts`` — spans, bare strings, or nested lists of either (e.g. a
    ``join_styled`` run of spans with plain separators) — into one styled line (a
    list of spans). Bare strings become default spans; empty parts are dropped;
    adjacent default spans are merged, so the wire stays compact."""
    line: list = []
    for p in _flatten(parts):
        if p is None:
            continue
        if isinstance(p, str):
            if not p:
                continue
            sp = {"t": p}
        elif isinstance(p, dict):
            if not p.get("t"):
                continue
            sp = p
        else:
            continue
        _append(line, sp)
    return line


def _flatten(parts):
    for p in parts:
        if isinstance(p, list):
            yield from _flatten(p)
        else:
            yield p


def _append(line: list, sp: dict) -> None:
    # Merge with the previous span when both are default (no role), else append.
    if line and "s" not in line[-1] and "s" not in sp:
        line[-1] = {"t": line[-1]["t"] + sp["t"]}
    else:
        line.append(sp)


def join_styled(items, role: str, sep: str = ", ") -> list:
    """A styled run of like-typed tokens — names, items, exits — each in ``role``
    and separated by plain ``sep`` (e.g. occupant names, comma-joined). Returns a
    list of parts to splice into ``styled(...)``."""
    out: list = []
    for i, it in enumerate(items):
        if i:
            out.append(sep)
        out.append(span(it, role))
    return out


def plain(line) -> str:
    """Flatten a styled line (or an already-plain string) to prose — for the
    chronicle, logs, and any client without colour."""
    if isinstance(line, str):
        return line
    if isinstance(line, list):
        return "".join(sp.get("t", "") for sp in line)
    return "" if line is None else str(line)
