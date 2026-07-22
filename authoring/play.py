"""The play-in-editor screen (Phase 8, slice 2) — a modal Textual screen that runs the
real engine in a throwaway :class:`~loom.sandbox.Sandbox`.

Entering play from the Explorer pushes this screen: a scrolling transcript over a command
line. It boots the sandbox at the chosen room, forwards each typed line to the engine, and
renders the engine's output — including the asynchronous NPC replies that arrive on later
ticks. Escape tears the sandbox down (cancelling in-flight model calls) and returns to the
Explorer; the authored world is never touched (the harness guarantees it).

UI only — the isolation and engine-driving live in `loom/sandbox.py`; this screen just
supplies the transcript sink and the input line.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, RichLog

from loom.sandbox import Sandbox


class PlayScreen(Screen):
    """A modal play session. Constructed with a world path, the room to start in, a built
    provider (live or fake), and a human-readable mode label for the header."""

    BINDINGS = [("escape", "exit_play", "Leave play")]

    CSS = """
    PlayScreen { layout: vertical; }
    #transcript { height: 1fr; border: round $secondary; padding: 0 1; }
    #cmd { dock: bottom; }
    """

    def __init__(self, world_path: str, room_id: str, provider, mode_label: str):
        super().__init__()
        self.world_path = world_path
        self.room_id = room_id
        self.provider = provider
        self.mode_label = mode_label
        self.sandbox: Sandbox | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield Input(placeholder="look · go north · say hello …  (Esc to leave)", id="cmd")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Play · {self.room_id}"
        self.sub_title = self.mode_label
        self.sandbox = Sandbox.from_path(self.world_path, self.room_id,
                                         self.provider, self._emit)
        self.query_one("#cmd", Input).focus()
        self.run_worker(self.sandbox.start(), exclusive=False)

    def _emit(self, text: str, system: bool) -> None:
        """The transcript sink the engine writes through (system beats dimmed)."""
        log = self.query_one("#transcript", RichLog)
        log.write(f"· {text}" if system else text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        self.query_one("#cmd", Input).value = ""
        if line and self.sandbox is not None:
            self.run_worker(self.sandbox.send(line), exclusive=False)

    def action_exit_play(self) -> None:
        self.run_worker(self._teardown(), exclusive=False)

    async def _teardown(self) -> None:
        if self.sandbox is not None:
            await self.sandbox.close()
            self.sandbox = None
        self.app.pop_screen()
