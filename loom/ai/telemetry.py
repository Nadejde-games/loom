"""Inference telemetry — a swappable sink for live progress on model calls.

Dependency-free and **inert by default**: with no reporter installed (the offline tests,
any embedding host that doesn't want it), providers add nothing to their path. A host —
the game server, the authoring workbench — installs a reporter to surface each model call
in real time: elapsed seconds, tokens generated as they stream, and a final tokens/sec
tally. This makes a slow local backend's activity visible during play and lets you check
how fast things are actually running.

The provider layer is the single choke point every call passes through (minds, director,
reflection, loot, the authoring tools), so instrumenting it there covers them all. A caller
may tag the in-flight call through :data:`inference_label` (e.g. an NPC's name) so the line
reads legibly; unset, the provider falls back to the model name.
"""
from __future__ import annotations

import contextvars
import itertools
import time
from dataclasses import dataclass

# The label for the in-flight call, set by a caller and read by the provider. A
# ``contextvars`` var so it propagates across ``await`` within the same task and is
# isolated between concurrent NPC turns. Empty -> the provider uses the model name.
inference_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "inference_label", default="")

_counter = itertools.count(1)


@dataclass
class InferenceCall:
    """One model call in flight. ``tokens`` climbs as a streamed reply arrives; a
    non-streamed call fills it once at the end from the response's ``usage``."""

    id: int
    label: str
    model: str
    started: float                  # time.perf_counter() at start
    tokens: int = 0
    finished: bool = False
    ok: bool = True
    _end: float | None = None

    @property
    def elapsed(self) -> float:
        """Seconds since the call started — live while running, frozen once finished."""
        end = self._end if self._end is not None else time.perf_counter()
        return end - self.started

    @property
    def tok_s(self) -> float:
        """Decode rate over the whole call (0 until at least one token is counted)."""
        e = self.elapsed
        return self.tokens / e if e > 0 and self.tokens else 0.0

    def stop(self, ok: bool = True) -> None:
        self.finished = True
        self.ok = ok
        self._end = time.perf_counter()


def new_call(model: str) -> InferenceCall:
    """A fresh call record, id auto-assigned and label taken from the context."""
    return InferenceCall(id=next(_counter), label=inference_label.get() or model,
                         model=model, started=time.perf_counter())


class InferenceReporter:
    """Sink for a call's lifecycle. No-op base; a host subclasses to render.

    ``start`` fires once when the call begins, ``progress`` on a periodic heartbeat while
    it runs (only after it has been going long enough to be worth showing), and ``finish``
    once when it ends (``call.ok`` is False if it raised)."""

    def start(self, call: InferenceCall) -> None: ...
    def progress(self, call: InferenceCall) -> None: ...
    def finish(self, call: InferenceCall) -> None: ...


_reporter: InferenceReporter | None = None


def set_reporter(reporter: InferenceReporter | None) -> None:
    """Install (or clear, with ``None``) the process-wide reporter."""
    global _reporter
    _reporter = reporter


def get_reporter() -> InferenceReporter | None:
    """The installed reporter, or ``None`` — the provider's cue to stay on its plain path."""
    return _reporter


class LogInferenceReporter(InferenceReporter):
    """Render each call to the server log as it happens: a start line, a per-second live
    line (elapsed · tokens · tok/s) once a call runs long enough to matter, and a final
    tally. Append-only and concurrency-safe — each line carries the call's label and id, so
    overlapping NPC turns stay legible. A slow local (streamed) call shows its token count
    climbing; a fast hosted call shows just start and the end tally."""

    def __init__(self, emit=None):
        if emit is None:
            from .. import log
            emit = log.event
        self._emit = emit

    @staticmethod
    def _tally(call: InferenceCall) -> str:
        if not call.tokens:
            return ""
        return f" · {call.tokens} tok · {call.tok_s:.1f} tok/s"

    def start(self, call: InferenceCall) -> None:
        who = call.label if call.label == call.model else f"{call.label} · {call.model}"
        self._emit(f"llm ▶ {who} · #{call.id} generating…")

    def progress(self, call: InferenceCall) -> None:
        self._emit(f"llm … {call.label} · #{call.id} · {call.elapsed:.0f}s{self._tally(call)}")

    def finish(self, call: InferenceCall) -> None:
        mark = "✓" if call.ok else "✗"
        self._emit(f"llm {mark} {call.label} · #{call.id} · {call.elapsed:.1f}s"
                   f"{self._tally(call)}")
