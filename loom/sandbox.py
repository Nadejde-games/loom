"""Ephemeral play-in-editor sandbox (Phase 8, slice 2): boot the real engine at any
room, play it live, and guarantee it writes nothing to the authored world.

The atlas *reads* a world and the author *writes* one; this lets you *stand in* one — the
felt half of the workbench. It is a thin wrapper around the real :class:`~loom.engine.Engine`:
the recon (docs/spikes/play-in-editor.md) found booting at an arbitrary room is a constructor
param, the play path is five calls, and isolation is nearly free — the engine never writes
``world.json``, and persistence / the memory store / embeddings are all opt-in and left off
here. Play *does* mutate the world in memory (the player joins, moves, takes things), so the
sandbox runs on a **deep copy** and drops it on exit; the authored world is never touched.

Two engine facts this bridges: output is polymorphic (a plain string or a list of styled
spans — flattened here through :func:`loom.style.plain`), and NPC replies are asynchronous
(they arrive later on detached tasks in ``engine._tasks``, cancelled on teardown). The engine
speaks to a socket-shaped session; this supplies a socket-free one that forwards every line to
an ``emit`` callback the UI provides.

Pure of any UI — imports only the framework, never Textual. The provider is injected, so the
caller chooses live minds or the offline :class:`~loom.ai.FakeProvider`; that policy lives in
the UI, not here. This is also the seam the Phase 8 authoring agent (slice 3) will reuse to
preview a change before committing it.
"""
from __future__ import annotations
import asyncio
import copy
import inspect

from . import style
from .content import load_world
from .engine import Engine
from .world import World


class _SinkSession:
    """A socket-free engine session: it satisfies the duck type the engine drives
    (``id``, a settable ``player_id``, async ``send_text`` / ``send_system`` / ``close``,
    plus a log-only ``addr``) and forwards each flattened line to ``emit(text, system)``.
    ``emit`` may be sync or async."""

    def __init__(self, sid: str, emit):
        self.id = sid
        self.player_id = None
        self.addr = "sandbox"
        self._emit = emit

    async def _out(self, text: str, system: bool) -> None:
        res = self._emit(text, system)
        if inspect.isawaitable(res):
            await res

    async def send_text(self, payload) -> None:
        await self._out(style.plain(payload), False)

    async def send_system(self, text) -> None:
        await self._out(style.plain(text), True)

    async def close(self) -> None:
        pass


class Sandbox:
    """A throwaway live session over a deep-copied world, booted at ``room_id``.

    ``emit(text: str, system: bool)`` receives every line the engine produces (system
    beats flagged ``system=True``). Lifecycle: :meth:`start` (mint the player, banner +
    first look), :meth:`send` (one line of player input), :meth:`close` (cancel in-flight
    NPC tasks, disconnect — nothing persisted). :meth:`drain` awaits pending NPC replies so
    a caller (or a test) can observe the asynchronous output.
    """

    def __init__(self, world: World, room_id: str, provider, emit, *, sid: str = "sandbox"):
        # Deep-copy so play (which mutates the world in memory) can never reach the
        # caller's world; attach no persistence / memory / embedder — the bare engine
        # writes nothing to disk.
        self.world = copy.deepcopy(world)
        self.provider = provider
        self.room_id = room_id
        self.engine = Engine(self.world, provider, start_location=room_id)
        self.session = _SinkSession(sid, emit)
        self._started = False

    @classmethod
    def from_path(cls, path: str, room_id: str, provider, emit, **kw) -> "Sandbox":
        """Load a *fresh* world from ``path`` (a file or region-dir) and sandbox it — the
        authored files are only ever read."""
        world, _ = load_world(path)
        return cls(world, room_id, provider, emit, **kw)

    async def start(self) -> None:
        """Mint the player at the room and emit the banner + first look."""
        if self._started:
            return
        self._started = True
        await self.engine.on_connect(self.session)

    async def send(self, text: str) -> None:
        """Feed one line of player input (``look`` / ``go north`` / ``say …``). NPC replies
        arrive asynchronously via ``emit`` from background tasks; use :meth:`drain` to wait."""
        await self.engine.on_input(self.session, text)

    async def drain(self, timeout: float = 5.0) -> None:
        """Await the engine's in-flight NPC/reaction tasks (the asynchronous replies), up to
        ``timeout`` seconds. A no-op when nothing is pending."""
        pending = set(self.engine._tasks)
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def close(self) -> None:
        """Tear down: cancel any in-flight tasks and disconnect. The deep-copied world and
        the engine are dropped; nothing was written to the authored files."""
        pending = list(self.engine._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.engine.on_disconnect(self.session)
