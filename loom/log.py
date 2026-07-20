"""Real-time, level-gated server logging.

Plain ``print`` block-buffers when stdout is not a terminal (piped, redirected, run under
a wrapper), so events pile up and flush in batches instead of appearing as they happen.
These helpers **always flush**, so the console is a live trace of the world — and each line
is timestamped, to make debugging correlate.

Two levels:
  * ``event`` — a notable thing that happened (a connection, an NPC's turn, a director
    beat, a reflection). Always shown.
  * ``debug`` — the fine-grained trace (salience skips, retries, reflection accumulation,
    act-gate reasons). Shown only when ``LOOM_VERBOSE`` is set — the firehose for working
    out *why* something did or did not happen.

Framework-internal and dependency-free; games and the engine call ``event``/``debug``
instead of bare ``print`` so output is real-time and uniform. ``set_verbose`` lets a host
flip the firehose at runtime; the env var seeds the default.
"""
from __future__ import annotations
import os
import sys
import time

# Seeded from the environment; a host may override at runtime via ``set_verbose``.
VERBOSE = os.environ.get("LOOM_VERBOSE", "0") not in ("0", "", "false")


def set_verbose(on: bool) -> None:
    """Turn the ``debug`` firehose on or off at runtime."""
    global VERBOSE
    VERBOSE = bool(on)


def real_time_stdout() -> None:
    """Force this process's stdout/stderr to line-buffered, so every ``print`` (not only
    these helpers) flushes as it happens rather than block-buffering when piped. Called
    once at a server's entry point. A no-op if the streams cannot be reconfigured."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass


def _emit(msg: str, mark: str = "") -> None:
    print(f"[loom {time.strftime('%H:%M:%S')}]{mark} {msg}", flush=True)


def event(msg: str) -> None:
    """A notable thing that happened — always shown, in real time."""
    _emit(msg)


def debug(msg: str) -> None:
    """A fine-grained trace line — shown only under ``LOOM_VERBOSE``."""
    if VERBOSE:
        _emit(msg, mark=" ·")
