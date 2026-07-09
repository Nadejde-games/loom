"""Async TCP server. A pure transport concern — all game logic lives behind
the ``Handler`` protocol, so the same server drives any game (and later a
WebSocket transport can implement the same handler contract)."""
from __future__ import annotations
import asyncio
import itertools
from typing import Protocol
from .protocol import Message
from .session import Session


class Handler(Protocol):
    async def on_connect(self, session: Session) -> None: ...
    async def on_input(self, session: Session, text: str) -> None: ...
    async def on_disconnect(self, session: Session) -> None: ...


class GameServer:
    def __init__(self, handler: Handler, host: str = "127.0.0.1", port: int = 4000):
        self.handler = handler
        self.host = host
        self.port = port
        self.sessions: dict[str, Session] = {}
        self._ids = itertools.count(1)

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(self._on_connect, self.host, self.port)
        addr = ", ".join(str(s.getsockname()) for s in server.sockets)
        print(f"[loom] listening on {addr}")
        async with server:
            await server.serve_forever()

    async def _on_connect(self, reader, writer):
        sid = f"s{next(self._ids)}"
        session = Session(sid, reader, writer)
        self.sessions[sid] = session
        try:
            await self.handler.on_connect(session)
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                # Accept either a raw line (nc/telnet-friendly) or a JSON envelope.
                payload = text
                if text.startswith("{"):
                    try:
                        payload = Message.from_line(text).data
                    except Exception:
                        payload = text
                if isinstance(payload, str):
                    await self.handler.on_input(session, payload)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self.sessions.pop(sid, None)
            await self.handler.on_disconnect(session)
            await session.close()

    async def broadcast(self, channel: str, data) -> None:
        for s in list(self.sessions.values()):
            try:
                await s.send(channel, data)
            except Exception:
                pass
