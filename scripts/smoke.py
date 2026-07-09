"""End-to-end smoke test. Assumes the game server is running (see README).

Connects, runs a scripted exchange, and prints everything the server sends
back. Deterministic under FakeProvider — no API key needed.

Run:  PYTHONPATH=. python3 scripts/smoke.py
"""
from __future__ import annotations
import asyncio
from loom.protocol import Message, Channel


async def _connect(host, port, attempts=25):
    last = None
    for _ in range(attempts):
        try:
            return await asyncio.open_connection(host, port)
        except OSError as exc:  # server not up yet
            last = exc
            await asyncio.sleep(0.2)
    raise last


async def run(host="127.0.0.1", port=4000):
    reader, writer = await _connect(host, port)

    async def drain(seconds=0.8):
        out = []
        try:
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=seconds)
                if not raw:
                    break
                m = Message.from_line(raw.decode())
                out.append((m.channel, m.data))
        except asyncio.TimeoutError:
            pass
        return out

    async def send(text):
        writer.write(Message(Channel.INPUT, text).to_bytes())
        await writer.drain()

    def show(events):
        for ch, d in events:
            for line in str(d).splitlines() or [""]:
                print(f"  [{ch}] {line}")

    print("=== on connect ===")
    show(await drain())

    for cmd in ["look", "say hello there", "go north", "look", "go south", "who", "quit"]:
        print(f"\n>>> {cmd}")
        await send(cmd)
        show(await drain())

    writer.close()
    print("\n=== smoke test complete ===")


if __name__ == "__main__":
    asyncio.run(run())
