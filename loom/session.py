"""A single connected client. Transport-facing; holds no game logic."""
from __future__ import annotations
import asyncio
from .protocol import Message, Channel


class Session:
    def __init__(self, sid: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.id = sid
        self.reader = reader
        self.writer = writer
        # set once the session is bound to a character
        self.player_id: str | None = None

    @property
    def addr(self) -> str:
        peer = self.writer.get_extra_info("peername")
        return f"{peer[0]}:{peer[1]}" if peer else "?"

    async def send(self, channel: str, data) -> None:
        self.writer.write(Message(channel, data).to_bytes())
        await self.writer.drain()

    async def send_text(self, text) -> None:
        # ``text`` is a plain string or a *styled line* — a list of semantic spans
        # (B3). The wire payload is polymorphic (protocol.py); a plain terminal
        # flattens a styled line to prose, a rich client themes it.
        await self.send(Channel.TEXT, text)

    async def send_system(self, text: str) -> None:
        await self.send(Channel.SYSTEM, text)

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
