"""A minimal terminal client. Reads the 'text'/'system' channels and prints
them; sends each typed line as an 'input' envelope. It knows nothing about the
game — proof that the protocol, not the client, carries the world. A rich
client would additionally subscribe to 'map'/'entities'; this one ignores them.

The 'text' payload may be a plain string or a *styled line* — a list of semantic
spans (B3). This client owns the theme: it maps each semantic role to an ANSI
colour below. Colour is off when output is not a TTY, when ``NO_COLOR`` is set
(https://no-color.org), or on a ``dumb`` terminal — then a styled line degrades to
plain prose. Swap ``_THEME`` to reskin; the server never sends a raw colour.
"""
from __future__ import annotations
import asyncio
import os
import sys
from loom.protocol import Message, Channel

# Colour only when it will render: a real terminal, colour not opted out.
_COLOR = (sys.stdout.isatty()
          and os.environ.get("NO_COLOR") is None
          and os.environ.get("TERM") != "dumb")

_RESET = "\033[0m"
# Semantic role -> ANSI SGR. Speech is left at default weight so dialogue reads
# plainly; the decorations (name, dim gesture, coloured items/exits) frame it.
_THEME = {
    "name":   "\033[1;36m",   # bold cyan — a character's name
    "speech": "",             # default — the spoken words read plainly
    "emote":  "\033[2m",      # dim — a gesture recedes behind the words
    "item":   "\033[33m",     # yellow — an object
    "exit":   "\033[32m",     # green — a way out
    "place":  "\033[1;35m",   # bold magenta — a location heading
    "danger": "\033[1;31m",   # bold red — a warning
}


def _render(data) -> str:
    """A wire payload as text for the terminal: a plain string as-is, a styled line
    (list of spans) themed to ANSI — or flattened to plain prose when colour is off
    or a span's role is unknown."""
    if isinstance(data, list):
        out = []
        for sp in data:
            if not isinstance(sp, dict):
                continue
            text = str(sp.get("t", ""))
            code = _THEME.get(sp.get("s")) if _COLOR else ""
            out.append(f"{code}{text}{_RESET}" if code else text)
        return "".join(out)
    return "" if data is None else str(data)


async def _reader(reader: asyncio.StreamReader) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            print("\n[disconnected]")
            return
        try:
            msg = Message.from_line(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
        if msg.channel == Channel.SYSTEM:
            print(f"\033[2m* {msg.data}\033[0m" if _COLOR else f"* {msg.data}")
        elif msg.channel == Channel.TEXT:
            print(_render(msg.data))
        # rich channels (map/entities/...) are ignored by a terminal — by design


async def _writer(writer: asyncio.StreamWriter) -> None:
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF (Ctrl-D)
            return
        writer.write(Message(Channel.INPUT, line.rstrip("\n")).to_bytes())
        await writer.drain()


async def main(host: str, port: int) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    print(f"[connected to {host}:{port}]  (Ctrl-C to quit)")
    r = asyncio.create_task(_reader(reader))
    w = asyncio.create_task(_writer(writer))
    _, pending = await asyncio.wait([r, w], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()


if __name__ == "__main__":
    host = os.environ.get("LOOM_HOST", "127.0.0.1")
    port = int(os.environ.get("LOOM_PORT", "4000"))
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        pass
