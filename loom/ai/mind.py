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

    @property
    def is_silent(self) -> bool:
        """The NPC chose not to react — no words, no actions. The engine renders
        this as nothing (B4): silence is a first-class outcome, not a failure."""
        return not self.speech and not self.actions


@dataclass
class Scene:
    """A read-only snapshot of an NPC's surroundings, handed in by the engine.

    The mind must never read the ``World`` itself — that is the engine's layer.
    Instead the engine composes this from the world and passes it in, so the NPC
    can perceive where it is, where it can go, and who else is present, and can
    therefore choose actions like ``move`` against real exits rather than
    guessing. Plain data: names and strings, no world objects.
    """
    location: str = ""
    description: str = ""
    exits: list = field(default_factory=list)     # exit directions it can take
    others: list = field(default_factory=list)    # names of other entities here
    items: list = field(default_factory=list)     # names of items on the ground here
    inventory: list = field(default_factory=list) # names of items the NPC holds
    conditions: list = field(default_factory=list) # standing atmosphere here (storm, night)


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


def parse_turn(registry: ActionRegistry | None, raw: str) -> tuple[str, list, list]:
    """Turn a raw model reply into ``(speech, valid_intents, errors)``.

    The shared reading of the turn envelope, used by every mind on the seam (the
    NPC mind and the game-master director alike) so a turn is parsed and validated
    identically no matter who proposed it. Tolerant of the malformations the
    tolerant extractor handles; each proposed action is checked against
    ``registry`` and only validated intents survive, with the exact errors for any
    invalid proposal returned for a one-shot retry. Never raises.
    """
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
        if registry is None:
            continue
        name = item.get("name")
        args = item.get("args", {})
        if args is None:
            args = {}
        errs = registry.validate(name, args)
        if errs:
            errors.extend(errs)
            continue
        valid.append(ActionIntent(name=name, args=args))
    return speech, valid, errors


class NpcMind:
    def __init__(self, npc: Npc, provider: LLMProvider,
                 memory: MemoryStream | None = None,
                 registry: ActionRegistry | None = None,
                 offered: list | None = None):
        self.npc = npc
        self.provider = provider
        self.memory = memory or MemoryStream()
        self.registry = registry
        # The subset of the registry this mind is offered — its action catalogue
        # need not be the whole registry (the player may take/drop where a given
        # NPC does not). None = every registered action. The prompt catalogue and
        # the constrained-decoding grammar are both narrowed to this same set, so
        # what the mind is told about and what it is constrained to stay one.
        self.offered = offered

    # ---- prompt ----
    def _system_prompt(self, scene: Scene | None = None) -> str:
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
        if p.get("disposition"):
            # How readily this character speaks at all — the silence prior. A
            # reticent disposition should stay quiet far more than a gregarious one.
            parts.append("Disposition: " + str(p["disposition"]))
        mems = self.memory.recent()
        if mems:
            parts.append("Recent memories:\n" + "\n".join(f"- {m.text}" for m in mems))
        if scene is not None:
            parts.append(self._scene_description(scene))
        parts.append("Stay in character. Never break character or mention being an AI.")
        if self.registry is not None:
            parts.append(self._action_instructions())
        else:
            parts.append("Reply with a single short spoken line, optionally a "
                         "brief physical action.")
        return "\n\n".join(parts)

    def _scene_description(self, scene: Scene) -> str:
        """Render the engine's perception snapshot as prompt lines the NPC reads
        to ground its choices — crucially, the exits it may actually take."""
        lines = ["Your surroundings right now:"]
        if scene.location:
            lines.append(f"- Place: {scene.location}")
        if scene.description:
            lines.append(f"- {scene.description}")
        # Standing conditions the game-master has set over this place (a storm,
        # nightfall). Part of what the NPC perceives — so it can answer, or act,
        # in light of the weather around it rather than a static room.
        for cond in scene.conditions:
            lines.append(f"- {cond}")
        if scene.exits:
            lines.append("- Exits you can take: " + ", ".join(scene.exits))
        else:
            lines.append("- There are no exits you can take from here.")
        if scene.items:
            lines.append("- On the ground here, in plain sight: "
                         + ", ".join(scene.items))
        if scene.inventory:
            lines.append("- You are carrying: " + ", ".join(scene.inventory))
        if scene.others:
            lines.append("- Also here with you: " + ", ".join(scene.others))
        else:
            lines.append("- You are alone here.")
        return "\n".join(lines)

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
            'You are not obliged to respond. If the words do not concern you, or '
            'your character would simply stay quiet, reply with exactly '
            '{"speech": "", "actions": []}. A wary or indifferent character often '
            'says nothing; an outgoing one is quicker to speak up. Silence is a '
            'valid, in-character choice — do not force a reply.\n'
            'When the moment truly calls for changing the world — someone asks '
            'you to lead or to go, or you decide to hand something over — include '
            'the matching action, because speaking of it does not make it happen. '
            'If you are asked to lead the way or to go somewhere, include a "move" '
            'action with a direction from your surroundings: the words are the '
            'invitation, the "move" is the step that actually takes you there. '
            'Ordinary talk, greetings, and passing remarks call for words alone, '
            'not an action.\n'
            'Example (choosing to speak and act): {"speech": "Careful, traveler.", '
            '"actions": [{"name": "emote", "args": {"text": "narrows his eyes"}}]}\n'
            'Example (going somewhere or leading the way): {"speech": "This way — '
            'follow me.", "actions": [{"name": "move", "args": {"direction": '
            '"north"}}]}\n'
            'Example (choosing silence, because the remark was not addressed to you '
            'and does not concern your character): {"speech": "", "actions": []}\n'
            'Available actions:\n'
            + self.registry.describe(self.offered)
        )

    # ---- turn ----
    async def converse(self, speaker_name: str, utterance: str,
                       scene: Scene | None = None, addressed: bool = True) -> Turn:
        """Hear an utterance; return a validated Turn (speech + safe actions).

        ``scene`` is the engine's read-only snapshot of the NPC's surroundings;
        when given, the NPC perceives its exits and company and can act on them.

        ``addressed`` says whether the speaker named *this* NPC. When False the
        utterance is framed as something merely overheard in the room, not a
        question put to the NPC — which loosens the assistant-reflex pull to
        answer everything and makes chosen silence more likely (B4).
        """
        if addressed:
            heard = f'{speaker_name} says to you: "{utterance}"'
            self.memory.add(f'{speaker_name} said to me: "{utterance}"', kind="speech")
        else:
            heard = f'You overhear {speaker_name} say to the room: "{utterance}"'
            self.memory.add(f'{speaker_name} said to the room: "{utterance}"',
                            kind="speech")
        system = self._system_prompt(scene)
        messages = [{"role": "user", "content": heard}]

        # Constrained decoding (Phase 2 hardening): when actions are in play, hand
        # the provider the turn-envelope grammar so a malformed envelope is
        # impossible at the token level rather than merely caught after. Providers
        # without constraint support (and the FakeProvider) ignore it, and the
        # tolerant parse + validate/retry below stays as defense-in-depth.
        schema = (self.registry.json_schema(self.offered)
                  if self.registry is not None else None)

        raw = await self.provider.complete(system, messages, schema=schema)
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
            raw2 = await self.provider.complete(system, retry, schema=schema)
            speech2, actions2, _ = self._parse_turn(raw2)
            speech = speech2 or speech      # prefer the retry's line if it has one
            actions = actions2              # invalid actions already dropped here

        if speech:
            self.memory.add(f'I replied: "{speech}"', kind="speech")
        return Turn(speech=speech, actions=actions)

    async def react(self, event: str, scene: Scene | None = None) -> Turn:
        """React — of the NPC's *own volition* — to something happening around it:
        a change in the world (a storm, nightfall) or another character's word or
        deed. Unlike ``converse`` this is not a line addressed to the NPC and asks
        nothing of it; it is an observation the NPC may or may not respond to.

        The bar is deliberately high — most events, most characters simply take in
        and do nothing (an empty turn, honoured by the engine as silence). This is
        the engine-level restraint on autonomous reaction: the character reacts only
        when the moment genuinely concerns it, never merely to react. Same
        constrained decoding, tolerant parse, and bounded retry as ``converse``.
        """
        system = self._system_prompt(scene)
        nudge = (
            f"Something happens around you: {event}\n"
            "This was not said to you and asks nothing of you. React only if it "
            "genuinely concerns your character and you would truly respond — with a "
            "word, a gesture, or an action from your surroundings. Most of the time "
            "a character simply takes such a thing in and does nothing; if so, reply "
            'with exactly {"speech": "", "actions": []}. Do not react merely to react.'
        )
        messages = [{"role": "user", "content": nudge}]
        schema = (self.registry.json_schema(self.offered)
                  if self.registry is not None else None)

        raw = await self.provider.complete(system, messages, schema=schema)
        speech, actions, errors = self._parse_turn(raw)
        if errors and self.registry is not None:
            correction = ("Your previous reply proposed invalid actions:\n"
                          + "\n".join(f"- {e}" for e in errors)
                          + "\nReply again as a single JSON object; fix or omit "
                            "those actions.")
            retry = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ]
            raw2 = await self.provider.complete(system, retry, schema=schema)
            speech2, actions2, _ = self._parse_turn(raw2)
            speech = speech2 or speech
            actions = actions2

        turn = Turn(speech=speech, actions=actions)
        # Record only an *engaged* reaction — a silent NPC writes no memory (the
        # standing world it saw is already in its Scene, so nothing is lost, and
        # bystander memory is not flooded by ambient noise; memory importance is
        # Phase 5's concern).
        if not turn.is_silent:
            self.memory.add(f"I noticed: {event}", kind="event")
            if speech:
                self.memory.add(f'I said: "{speech}"', kind="speech")
        return turn

    def _parse_turn(self, raw: str) -> tuple[str, list, list]:
        """Return (speech, valid_intents, errors_for_invalid_proposals)."""
        return parse_turn(self.registry, raw)

    # ---- back-compat ----
    async def hear_and_respond(self, speaker_name: str, utterance: str) -> str:
        """Dialogue-only convenience: the spoken line of a full turn."""
        turn = await self.converse(speaker_name, utterance)
        return turn.speech
