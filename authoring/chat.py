"""The authoring-agent chat panel (Phase 8, slice 3) — talk to the world-builder.

This is the left-pane *alternate* to the navigator (the Explorer toggles between them with
``a``), so the inspector on the right — the room/npc/item you are looking at — stays visible
while you author. What you type goes to the agent (``authoring.agent``); the agent grounds it,
calls its tools, and — for a write — stages a validated proposal. The panel shows the proposed
diff and reveals the Apply / Discard controls; **nothing reaches the staged world until you
Apply**, and nothing reaches disk until Save. When you Apply, the inspector repaints at once
(the ``on_applied`` callback). Escape returns to the navigator.

UI only — the agent, the tools, and the safety gate live in ``authoring.agent`` /
``loom.worlddraft``; this panel just supplies the transcript, the input, and the human's
apply/discard/undo/save moves. The agent runs off the UI thread in a Textual worker, so a
model call never freezes the screen.
"""
from __future__ import annotations
import io

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, TextArea

from .agent import build_agent, build_model, run_turn


class ChatPanel(Vertical):
    """The embedded authoring-agent panel over one world.

    Built with a shared :class:`~authoring.agent.AuthoringSession` (so it edits the same staged
    ``Draft`` the Explorer shows) and an ``on_applied`` callback the Explorer passes to repaint
    on a commit. For tests, an ``agent`` (a ``FunctionModel``-backed agent) can be injected so
    the panel runs with no network."""

    BINDINGS = [
        ("escape", "leave", "Navigator"),
        ("ctrl+l", "save_log", "Save log"),
    ]

    DEFAULT_CSS = """
    ChatPanel { layout: vertical; height: 1fr; }
    ChatPanel #transcript { height: 1fr; border: none; }
    ChatPanel #controls { height: auto; }
    ChatPanel #controls Button {
        height: 1;
        min-width: 0;
        border: none;
        padding: 0 1;
        margin: 0 1 0 0;
        color: $text-muted;
        background: transparent;
    }
    ChatPanel #controls Button:hover { color: $text; background: $boost; }
    ChatPanel #msg { border: none; }
    """

    def __init__(self, *, session, agent=None, on_applied=None):
        super().__init__()
        self.session = session
        self._injected_agent = agent
        self._on_applied = on_applied         # () -> None: repaint the Explorer after a commit
        self.agent = None
        self._history = None                  # threaded message history across turns
        self._lines: list[str] = []           # the transcript, line by line (also the save-log)

    def compose(self) -> ComposeResult:
        # A read-only TextArea (not RichLog) so the transcript is terminal-like: the author can
        # select and copy it (ctrl+c → system clipboard via OSC-52), and ctrl+l saves the whole
        # log to a file. RichLog cannot be selected on Textual 8.2.8.
        yield TextArea("", id="transcript", read_only=True, soft_wrap=True,
                       show_line_numbers=False)
        with Horizontal(id="controls"):
            # Discrete + contextual: Apply/Discard appear only when a change is pending, Undo
            # only when there is something to undo — so the row is quiet until it matters.
            yield Button("apply", id="confirm")
            yield Button("discard", id="reject")
            yield Button("undo", id="undo")
            yield Button("save", id="save")
        yield Input(placeholder="ask the agent to change the world …  (Esc → navigator)",
                    id="msg")

    def on_mount(self) -> None:
        if self._injected_agent is not None:
            self.agent = self._injected_agent
        else:
            model = build_model()
            if model is None:
                self._log_system("No OPENROUTER_API_KEY in the environment — the authoring "
                                 "agent needs a live model. Set it and reopen.")
                self.query_one("#msg", Input).disabled = True
            else:
                self.agent = build_agent(model)
        if self.agent is not None:
            self._log_system(f"Staged world: {self._summary()}. "
                             "Tell me what to build or change.")
        self._refresh_controls()

    def focus_input(self) -> None:
        """Put the cursor in the message box — called by the Explorer when it reveals us."""
        try:
            self.query_one("#msg", Input).focus()
        except Exception:
            pass

    # -- transcript --------------------------------------------------------------
    def _log(self, line: str) -> None:
        self._lines.append(line)
        ta = self.query_one("#transcript", TextArea)
        ta.text = "\n".join(self._lines)       # read-only assignment is not a user edit
        ta.scroll_end(animate=False)

    def _log_author(self, text: str) -> None:
        self._log(f"you › {text}")

    def _log_agent(self, text: str) -> None:
        self._log(f"agent › {text}")

    def _log_system(self, text: str) -> None:
        self._log(f"· {text}")

    def _summary(self) -> str:
        s = self.session.draft.view().summary()
        return (f"{s['locations']} rooms · {s['npcs']} npcs · {s['items']} items · "
                f"{s['errors']}e/{s['warnings']}w")

    # -- controls ----------------------------------------------------------------
    def _refresh_controls(self) -> None:
        pending = self.session is not None and self.session.pending is not None
        has_undo = self.session is not None and bool(self.session.draft.checkpoints)
        self.query_one("#confirm", Button).display = pending      # shown only when it matters
        self.query_one("#reject", Button).display = pending
        self.query_one("#undo", Button).display = has_undo

    def _applied(self) -> None:
        """After a commit/undo: repaint the Explorer so the inspector reflects the change."""
        if self._on_applied is not None:
            self._on_applied()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "confirm":
            if self.session.confirm():
                self._log_system("Applied to the staged world (Save to write it to disk).")
                self._applied()
        elif bid == "reject":
            if self.session.reject():
                self._log_system("Discarded the proposal — nothing changed.")
        elif bid == "undo":
            if self.session.undo():
                self._log_system("Undid the last applied change.")
                self._applied()
        elif bid == "save":
            self._save()
        self._refresh_controls()

    def _save(self) -> None:
        try:
            path = self.session.save()
            self._log_system(f"Saved the world to {path}.")
        except Exception as exc:                       # no path, or a write error
            self._log_system(f"Save failed: {exc}")

    # -- the turn ----------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        self.query_one("#msg", Input).value = ""
        if msg and self.agent is not None:
            self.run_worker(self._turn(msg), exclusive=True)

    async def _on_agent_event(self, ctx, events) -> None:
        """Pydantic-AI's event stream — surface live progress so the builder can see the agent
        is working (region authoring can take many seconds with no other output). A tool CALL
        and its RESULT fire from the agent graph regardless of the provider, so this activity
        line is reliable even when a backend streams text in coarse chunks."""
        from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent
        async for ev in events:
            if isinstance(ev, FunctionToolCallEvent):
                self._log_system(f"⏳ {ev.part.tool_name}…")
            elif isinstance(ev, FunctionToolResultEvent):
                self._log_system(f"✓ {getattr(ev.part, 'tool_name', 'tool')}")

    async def _turn(self, msg: str) -> None:
        """Run one agent turn off the UI thread: echo the author's line, send the message,
        render the reply, and — if a change was staged — show the exact diff and reveal the
        Apply control. Live tool activity streams in via ``_on_agent_event``. The staged world
        is not touched here; only the human's Apply commits it."""
        self._log_author(msg)
        box = self.query_one("#msg", Input)
        box.disabled = True
        try:
            result = await run_turn(self.agent, self.session, msg, history=self._history,
                                    event_handler=self._on_agent_event)
            self._history = result.all_messages()
            self._log_agent(str(result.output))
            if self.session.pending is not None:
                diff = "\n".join(self.session.pending.diff) or "(no structural change)"
                self._log_system("Proposed (awaiting your confirmation):\n" + diff)
        except Exception as exc:
            self._log_system(f"Error: {exc}")
        finally:
            box.disabled = False
            box.focus()
            self._refresh_controls()

    def action_save_log(self) -> None:
        """Save the whole transcript to a file (`ctrl+l`) — robust over SSH, and the reliable
        "share it" path where clipboard access is flaky. For an arbitrary slice, select it in
        the transcript and press `ctrl+c` (copies to the system clipboard via OSC-52)."""
        text = "\n".join(self._lines).strip()
        if not text:
            self._log_system("Nothing to save yet.")
            return
        try:
            self.app.deliver_text(io.StringIO(text + "\n"),
                                  save_filename="authoring-chat.log")
            self._log_system("Saved the transcript to authoring-chat.log.")
        except Exception as exc:                       # a failed delivery must not crash the UI
            self._log_system(f"Save-log failed: {exc}")

    def action_leave(self) -> None:
        """Return to the navigator (the inspector stays visible either way)."""
        self.app.action_chat()                         # the Explorer toggles the panes back
