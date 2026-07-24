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


# A transient one-line status pinned at the bottom of the terminal (live inference
# progress), rewritten in place with a carriage return rather than scrolled. Permanent
# event lines clear it, print above it, then redraw it — so a slow model's ticking
# elapsed/token counter never floods the log. TTY-only: piped/redirected output has no
# cursor to move, so there the live line is suppressed and only the permanent ▶/✓ lines
# (which still carry the tally) appear.
_status: str = ""


def _tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


def set_status(text: str) -> None:
    """Set (or clear, with ``""``) the pinned live status line. A no-op off a TTY."""
    global _status
    text = text or ""
    if not _tty():
        _status = ""
        return
    if text:
        text = text[:_term_width() - 1]          # keep it to one visual row (no wrap)
    _status = text
    sys.stdout.write("\r\033[K" + text)          # CR, clear the row, write the status
    sys.stdout.flush()


def _emit(msg: str, mark: str = "") -> None:
    line = f"[loom {time.strftime('%H:%M:%S')}]{mark} {msg}"
    if _status and _tty():
        # Clear the pinned status line, print the permanent line above it, then redraw the
        # status on the new bottom row so it stays anchored while the log scrolls past.
        sys.stdout.write("\r\033[K")
        print(line, flush=True)
        sys.stdout.write(_status)
        sys.stdout.flush()
    else:
        print(line, flush=True)


def event(msg: str) -> None:
    """A notable thing that happened — always shown, in real time."""
    _emit(msg)


def debug(msg: str) -> None:
    """A fine-grained trace line — shown only under ``LOOM_VERBOSE``."""
    if VERBOSE:
        _emit(msg, mark=" ·")
