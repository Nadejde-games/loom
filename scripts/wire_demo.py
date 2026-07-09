"""Shows exactly what travels on the socket between client and server — and
proves a raw client (telnet/nc) can drive the game. Single process: it starts
the server and connects a RAW client that sends plain newline text (no JSON
envelope) and prints the exact bytes the server sends back.

Run:  PYTHONPATH=. python3 scripts/wire_demo.py
"""
from __future__ import annotations
import asyncio
from loom import GameServer, Engine
from loom.ai import FakeProvider
from loom.content import load_world

PORT = 4055


async def main():
    world, start = load_world("game/world/world.json")
    server = GameServer(Engine(world, FakeProvider(), start), "127.0.0.1", PORT)
    srv = await asyncio.start_server(server._on_connect, "127.0.0.1", PORT)

    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)

    async def dump(seconds=0.5):
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=seconds)
                if not line:
                    break
                print("   RECV", repr(line))
        except asyncio.TimeoutError:
            pass

    print("=== bytes server sends the moment a raw client connects ===")
    await dump()

    print("\n=== client sends a RAW line (what you'd type in telnet/nc): b'look\\n' ===")
    writer.write(b"look\n")
    await writer.drain()
    await dump()

    print("\n=== client sends RAW b'say hi\\n' ===")
    writer.write(b"say hi\n")
    await writer.drain()
    await dump()

    writer.close()
    srv.close()
    await srv.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
