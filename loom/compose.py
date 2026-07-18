"""Composing a mind's turn into the line(s) a room reads (BACKLOG B2).

Presentation only. The action seam is untouched: a ``Turn`` — the validated speech
and the validated deeds — stays exactly as it is; this module decides only how
those pieces are *rendered* to a room. Historically an NPC's speech and each of its
deeds landed as separate lines (the MUD "one act, one line" convention — DikuMUD,
LPMud); here they are woven into a single beat in the interactive-fiction
speech-attribution style — ``Odd shakes his head slowly and says, "..."``. What the
old MUD ``act()`` / Evennia ``msg_contents`` machinery did — per-observer name and
pronoun substitution — this game does not need: the narration handed in is already
third-person, name-substituted, and broadcast room-wide (multiplayer-correct). What
we adopt is only the speech-attribution *join*.

Game-agnostic and pure: no world, no I/O. The beat is emitted as a *styled line*
(BACKLOG B3) — a list of semantic spans (name, emote, speech) — so a rich client
can colour it; ``loom.style.plain`` flattens it back to prose for the chronicle,
logs, and a plain terminal.
"""
from __future__ import annotations

from .style import Style, span, styled, plain

# Terminal punctuation a spoken line may already carry — we never double it.
_TERMINAL = (".", "!", "?", "…")


def _quote_speech(speech: str) -> str:
    """The spoken line, quoted, with a sentence-ending period supplied only when
    the line does not already close on its own terminal punctuation."""
    s = speech.strip()
    if not s.endswith(_TERMINAL):
        s += "."
    return f'"{s}"'


def _strip_trailing_period(clause: str) -> str:
    """Drop a single trailing period from a deed clause so it can take a joining
    conjunction (``... slowly`` + ``and says, ...``). Leaves ``!`` and ``?``."""
    return clause[:-1] if clause.endswith(".") else clause


def _elide_name(clause: str, name: str) -> str:
    """Remove a leading actor name from a deed clause — applied to the second and
    later deeds of a multi-deed beat, so the actor is not renamed each clause
    (``Wren nods once, leaves heading north`` not ``Wren nods once, Wren leaves``).
    A clause that does not begin with the name (a custom action) is left whole."""
    prefix = name + " "
    return clause[len(prefix):] if clause.startswith(prefix) else clause


def _first_deed_spans(clause: str, speaker: str) -> list:
    """The first deed clause as spans: its leading actor name in the NAME role and
    the gesture that follows in EMOTE, so a character's name is highlighted even
    inside a fused beat. A clause that does not begin with the name (a custom
    action) becomes a single EMOTE span — never mangled."""
    if speaker and clause.startswith(speaker + " "):
        return [span(speaker, Style.NAME), span(clause[len(speaker):], Style.EMOTE)]
    return [span(clause, Style.EMOTE)]


def compose_beat(speaker: str, speech: str, deeds: list) -> list:
    """Render one room beat from an actor's ``speech`` and the ``deeds`` it
    performed *in that same room* (each already a room-ready line, e.g.
    ``Odd nods slowly``), as a styled line — a list of semantic spans (B3).

    Action-first, speech-attribution style (the chosen B2 form); the prose a plain
    terminal reads (via ``loom.style.plain``) is:

      * speech, no deed     -> ``{speaker}: {speech}``  (unchanged format)
      * one deed + speech   -> ``{deed} and says, "{speech}."``
      * many deeds + speech -> ``{deed}, {deed}, and says, "{speech}."``
      * deed(s), no speech  -> the deed clause(s) alone (a period restored)

    ``speaker`` may be ``""`` for a bodiless source (the director's ambient
    voice): a speech-only beat is then just the line itself, unattributed.
    Returns ``[]`` when there is nothing to say and nothing to render.
    """
    speech = (speech or "").strip()
    deeds = [d.strip() for d in deeds if d and d.strip()]
    if not deeds:
        if not speech:
            return []
        if not speaker:
            return styled(span(speech, Style.SPEECH))
        return styled(span(speaker, Style.NAME), ": ", span(speech, Style.SPEECH))
    # Tidy each deed (drop a colliding period; elide the repeated name from the
    # second deed on), then render the clauses as spans: the first splits into
    # name + gesture; the rest are gesture spans, comma-joined.
    parts: list = []
    for i, deed in enumerate(deeds):
        deed = _strip_trailing_period(deed)
        if i == 0:
            parts += _first_deed_spans(deed, speaker)
        else:
            parts += [", ", span(_elide_name(deed, speaker) if speaker else deed,
                                  Style.EMOTE)]
    if not speech:
        # Bodiless or silent deed(s): the clause(s) alone, a period restored.
        if not plain(styled(*parts)).endswith(_TERMINAL):
            parts.append(".")
        return styled(*parts)
    joiner = " and says, " if len(deeds) == 1 else ", and says, "
    parts += [joiner, span(_quote_speech(speech), Style.SPEECH)]
    return styled(*parts)
