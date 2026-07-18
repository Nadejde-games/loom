"""Wire protocol: newline-delimited JSON message envelopes.

Every message is a small JSON object with a channel tag ``c`` and a data
payload ``d``, followed by a newline::

    {"c": "text", "d": "You enter a dark cave.\\n"}

The channel tag is the whole extensibility story. A terminal client reads the
``text`` channel and ignores everything else; a future graphical client can
subscribe to ``map`` / ``entities`` / ``tiles`` channels that ride the *same*
connection, with no change to this format and no server rewrite. The format is
transport-agnostic: the same bytes work over TCP today and WebSocket later.

This deliberately borrows the proven MUD pattern (GMCP's text-plus-structured
side-channels) while dropping the 1990s telnet wire framing.

The ``text`` channel's payload is polymorphic (B3, rich formatting): ``d`` is
either a plain string (as above) OR a *styled line* — a list of semantic spans::

    {"c": "text", "d": [{"t": "Odd", "s": "name"},
                        {"t": " shakes his head and says, ", "s": "emote"},
                        {"t": "\\"The hills know my name.\\"", "s": "speech"}]}

Each span is ``{"t": <text>, "s": <role>}`` (the role omitted for default text).
The engine tags *semantic* roles (``name``/``speech``/``emote``/``item``/``exit``…,
see ``loom.style``); the client owns the theme that maps a role to colour. A plain
or ``NO_COLOR`` client joins the ``t`` fields back to prose (``loom.style.plain``),
so styling never breaks a simpler reader — and unstyled lines still ride as plain
strings, so the change is opt-in per line.
"""
from __future__ import annotations
import json
from dataclasses import dataclass


class Channel:
    """Well-known channel names. Games and rich clients may define more."""
    # server -> client
    TEXT = "text"        # human-readable prose; every client understands this
    SYSTEM = "system"    # out-of-band notices (connect, errors, prompts)
    # client -> server
    INPUT = "input"      # a single line of player input
    # reserved for rich clients (not emitted yet, but the channel is free)
    MAP = "map"
    ENTITIES = "entities"


@dataclass(frozen=True)
class Message:
    channel: str
    data: object

    def to_bytes(self) -> bytes:
        return (json.dumps({"c": self.channel, "d": self.data}) + "\n").encode("utf-8")

    @classmethod
    def from_line(cls, line: str) -> "Message":
        obj = json.loads(line)
        return cls(channel=obj.get("c", ""), data=obj.get("d"))
