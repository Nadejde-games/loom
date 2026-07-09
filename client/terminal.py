"""A minimal terminal client. Reads the 'text'/'system' channels and prints
them; sends each typed line as an 'input' envelope. It knows nothing about the
game — proof that the protocol, not the client, carries the world. A rich
client would additionally subscribe to 'map'/'entities'; this one ignores them.
"""
from __future__ import annotations
import asyncio
import os
import sys
from loom.protocol import Message, Channel


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
            print(f"\033[2m* {msg.data}\033[0m")
        elif msg.channel == Channel.TEXT:
            print(msg.data)
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
