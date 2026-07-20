"""The continuous game loop. Systems register tick callbacks; the loop calls
them at a fixed cadence. This is what makes the world *live* even when nobody
is typing — ambient events, NPC autonomy, and the game-master's slow pulse all
hang off here later."""
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable

from . import log

TickFn = Callable[[float], Awaitable[None]]


class GameLoop:
    def __init__(self, tick_seconds: float = 1.0):
        self.tick_seconds = tick_seconds
        self._systems: list[TickFn] = []
        self._running = False
        self.tick = 0

    def add_system(self, fn: TickFn) -> None:
        self._systems.append(fn)

    async def run(self) -> None:
        self._running = True
        while self._running:
            self.tick += 1
            for system in self._systems:
                try:
                    await system(self.tick_seconds)
                except Exception as exc:  # a broken system must not kill the loop
                    log.event(f"system error: {exc!r}")
            await asyncio.sleep(self.tick_seconds)

    def stop(self) -> None:
        self._running = False
