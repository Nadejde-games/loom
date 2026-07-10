"""An NPC mind: persona core + memory, able to hold a conversation *and* to
propose actions.

The golden rule holds: this layer never mutates the world and never trusts model
text. When a registry of actions is supplied, the mind asks the model for a
single JSON turn — ``{"speech": ..., "actions": [...]}`` — parses it tolerantly,
validates each proposed action against the registry, and retries once (feeding
the exact validation error back) before dropping anything invalid. It returns a
``Turn`` of *validated* intents; the engine is what actually executes them.

Without a registry the mind stays a pure conversationalist (plain-text reply),
so lightweight callers and the `FakeProvider` path keep working unchanged.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from ..world.entity import Npc
from ..action import ActionRegistry, ActionIntent
from .provider import LLMProvider
from .memory import MemoryStream

MAX_ACTIONS = 3   # cap per turn: bounds execution and keeps replies focused


@dataclass
class Turn:
    """A parsed, validated mind turn: what to say, and what (safely) to do."""
    speech: str = ""
    actions: list = field(default_factory=list)   # list[ActionIntent]


_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\r?\n?|\r?\n?```$",
                       re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _extract_json(text: str) -> dict | None:
    """Best-effort: pull a single JSON object out of a model reply.

    Handles a bare object, a ```json fenced block, an object embedded in stray
    prose, and — importantly — trailing junk after the object (models
    occasionally append a stray brace or a stray word). Returns None when
    nothing object-shaped can be recovered; the caller then degrades to
    treating the whole reply as speech.
    """
    if not text:
        return None
    s = _strip_fences(text)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    start = s.find("{")
    if start == -1:
        return None
    # Parse one JSON object from the first '{' and ignore anything after it
    # (e.g. the stray '}' Qwen3.5 sometimes appends).
    try:
        obj, _ = json.JSONDecoder().raw_decode(s, start)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # Last resort: span to the final '}'.
    end = s.rfind("}")
    if end > start:
        try:
            obj = json.loads(s[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            return None
    return None


class NpcMind:
    def __init__(self, npc: Npc, provider: LLMProvider,
                 memory: MemoryStream | None = None,
                 registry: ActionRegistry | None = None):
        self.npc = npc
        self.provider = provider
        self.memory = memory or MemoryStream()
        self.registry = registry

    # ---- prompt ----
    def _system_prompt(self) -> str:
        p = self.npc.persona or {}
        parts = [f"You are {self.npc.name}, a character in a living text world."]
        if p.get("backstory"):
            parts.append(str(p["backstory"]))
        if p.get("traits"):
            parts.append("Traits: " + ", ".join(p["traits"]))
        if p.get("goals"):
            parts.append("Goals: " + ", ".join(p["goals"]))
        if p.get("voice"):
            parts.append("Speak like this: " + str(p["voice"]))
        mems = self.memory.recent()
        if mems:
            parts.append("Recent memories:\n" + "\n".join(f"- {m.text}" for m in mems))
        parts.append("Stay in character. Never break character or mention being an AI.")
        if self.registry is not None:
            parts.append(self._action_instructions())
        else:
            parts.append("Reply with a single short spoken line, optionally a "
                         "brief physical action.")
        return "\n\n".join(parts)

    def _action_instructions(self) -> str:
        return (
            'Respond with a single JSON object and nothing else, in exactly this '
            'shape:\n'
            '{"speech": "<your in-character spoken line, one short sentence>", '
            '"actions": [{"name": "<action>", "args": {<named arguments>}}]}\n'
            '"args" is an object mapping each argument name to its value (never a '
            'bare list). Keep "speech" to one short line. Add an action only when '
            'it truly fits the moment; otherwise use an empty list. Never invent '
            'actions or arguments beyond those listed.\n'
            'Example: {"speech": "Careful, traveler.", "actions": [{"name": '
            '"emote", "args": {"text": "narrows his eyes"}}]}\n'
            'Available actions:\n'
            + self.registry.describe()
        )

    # ---- turn ----
    async def converse(self, speaker_name: str, utterance: str) -> Turn:
        """Hear an utterance; return a validated Turn (speech + safe actions)."""
        self.memory.add(f'{speaker_name} said to me: "{utterance}"', kind="speech")
        system = self._system_prompt()
        messages = [{"role": "user", "content": f'{speaker_name} says: "{utterance}"'}]

        raw = await self.provider.complete(system, messages)
        speech, actions, errors = self._parse_turn(raw)

        # One bounded retry, only when the model proposed something invalid and
        # actions are even possible. The correction names the exact problem.
        if errors and self.registry is not None:
            correction = ("Your previous reply proposed invalid actions:\n"
                          + "\n".join(f"- {e}" for e in errors)
                          + "\nReply again as a single JSON object; fix or omit "
                            "those actions.")
            retry = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ]
            raw2 = await self.provider.complete(system, retry)
            speech2, actions2, _ = self._parse_turn(raw2)
            speech = speech2 or speech      # prefer the retry's line if it has one
            actions = actions2              # invalid actions already dropped here

        if speech:
            self.memory.add(f'I replied: "{speech}"', kind="speech")
        return Turn(speech=speech, actions=actions)

    def _parse_turn(self, raw: str) -> tuple[str, list, list]:
        """Return (speech, valid_intents, errors_for_invalid_proposals)."""
        obj = _extract_json(raw)
        if obj is None:
            # No JSON at all: treat the whole reply as spoken text. Never crash.
            return _strip_fences(raw).strip(), [], []
        speech = obj.get("speech")
        speech = speech.strip() if isinstance(speech, str) else ""
        proposed = obj.get("actions")
        if not isinstance(proposed, list):
            proposed = []
        valid: list = []
        errors: list[str] = []
        for item in proposed[:MAX_ACTIONS]:
            if not isinstance(item, dict):
                errors.append(f"action entry must be an object, "
                              f"got {type(item).__name__}")
                continue
            if self.registry is None:
                continue
            name = item.get("name")
            args = item.get("args", {})
            if args is None:
                args = {}
            errs = self.registry.validate(name, args)
            if errs:
                errors.extend(errs)
                continue
            valid.append(ActionIntent(name=name, args=args))
        return speech, valid, errors

    # ---- back-compat ----
    async def hear_and_respond(self, speaker_name: str, utterance: str) -> str:
        """Dialogue-only convenience: the spoken line of a full turn."""
        turn = await self.converse(speaker_name, utterance)
        return turn.speech
