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
from json_repair import repair_json
from ..world.entity import Npc
from ..action import ActionRegistry, ActionIntent
from .provider import LLMProvider
from .memory import MemoryStream

MAX_ACTIONS = 3   # cap per turn: bounds execution and keeps replies focused

# The two-pass act-gate (BACKLOG B5), opt-in. The local model under-selects the
# world-mutating action (a guide asked to lead speaks of it but emits `move` only
# ~70% of the time, substituting a cosmetic `emote`). A single blended turn makes
# the action compete with dialogue for the model's attention; splitting it — a
# cheap, low-temperature pass that decides the DEED alone, then a second that
# speaks to it — lets the action be chosen on its own, where `emote` is not a
# flavour attractor. Authoritative: the decided action IS the turn's action (the
# plan→act split), so the move rate becomes the decision's accuracy, not the blended
# compose's. Research-y and unproven until measured live; off by default.
DECIDE_TEMPERATURE = 0.2


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
    prose, and the malformations a model emits under load — trailing junk, a stray
    appended brace, an unclosed object (the B6 shape). Grammar-constrained decoding
    makes a malformed envelope impossible on backends that support it; this is the
    defense-in-depth for those that don't (and the ``FakeProvider`` path).

    The fast path is a strict parse (the well-formed, constrained case, no repair);
    only a reply that actually *looks like* a JSON object (contains ``{``) falls
    through to ``json_repair``, so genuine prose still yields ``None`` and the caller
    degrades to treating the whole reply as speech rather than a repaired object.
    """
    if not text:
        return None
    s = _strip_fences(text)
    try:
        obj = json.loads(s)                      # fast path: already well-formed
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    start = s.find("{")
    if start == -1:                              # no object at all -> speech
        return None
    # Tolerant recovery of the object from the first '{' (json_repair fixes the
    # stray-brace / unclosed / trailing-junk shapes). Returns None on anything not
    # object-shaped, preserving the degrade-to-speech contract.
    try:
        obj = repair_json(s[start:], return_objects=True)
    except Exception:
        return None
    return obj if isinstance(obj, dict) and obj else None


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
                 offered: list | None = None,
                 act_gate: bool = False,
                 embedder=None):
        self.npc = npc
        self.provider = provider
        # The embedder (if any) rides on the memory stream, so retrieval ranks by
        # relevance as well as recency/importance. None = recency+importance only.
        self.memory = memory or MemoryStream(embedder=embedder)
        self.registry = registry
        # The subset of the registry this mind is offered — its action catalogue
        # need not be the whole registry (the player may take/drop where a given
        # NPC does not). None = every registered action. The prompt catalogue and
        # the constrained-decoding grammar are both narrowed to this same set, so
        # what the mind is told about and what it is constrained to stay one.
        self.offered = offered
        # The two-pass act-gate (B5), opt-in (default off). When set, ``converse``
        # decides its action in a cheap low-temp pass, then composes speech to it —
        # so a willing guide reliably *moves*, not just talks of moving. Off keeps
        # the single blended turn (every existing behaviour) exactly. Unproven until
        # measured live; the game opts in only if it clears the ~70% move ceiling.
        self.act_gate = act_gate

    # ---- prompt ----
    async def _recall(self, query: str, scene: Scene | None = None,
                      k: int = 8) -> list:
        """The memories to inject for this turn: relevance+importance+recency
        retrieval when an embedder is present, else exactly the recent ones. ``query``
        is the incoming utterance or event — what the NPC is answering. Computed once
        per turn (an awaited embedding) and threaded into each prompt this turn builds,
        so the retrieval cost is paid at most once however many passes the turn runs."""
        return await self.memory.retrieve(query, k=k)

    def _base_lines(self, scene: Scene | None = None,
                    memories: list | None = None) -> list:
        """Persona, memories, and the perceived scene — the shared head of every
        prompt this mind builds (the blended turn, and the two-pass decision and
        speech prompts). Ends with the stay-in-character line; each caller appends
        its own instruction block after it. ``memories`` is the retrieved set for this
        turn; None falls back to the recent ones (the query-free path, unchanged)."""
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
        mems = memories if memories is not None else self.memory.recent()
        if mems:
            parts.append("Memories that bear on this moment:\n"
                         + "\n".join(f"- {m.text}" for m in mems))
        if scene is not None:
            parts.append(self._scene_description(scene))
        parts.append("Stay in character. Never break character or mention being an AI.")
        return parts

    def _system_prompt(self, scene: Scene | None = None,
                       memories: list | None = None) -> str:
        parts = self._base_lines(scene, memories)
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

        # Retrieve the memories that bear on this utterance once, and thread them into
        # every prompt this turn builds (both act-gate passes share them).
        mems = await self._recall(utterance, scene)
        if self.act_gate and self.registry is not None:
            # Two-pass act-gate (B5): a cheap, low-temperature pass decides the DEED
            # authoritatively — raising the move-when-asked rate off the blended
            # ceiling, because the action is chosen on its own where the cosmetic
            # emote is not a flavour attractor. The SPEECH, and the choice to stay
            # silent (B4), stay the proven blended turn; only its action is discarded
            # in favour of the decided one. Same two-call cost as the blended path
            # with its retry, and every speech behaviour is preserved by construction.
            actions = await self._decide_actions(heard, scene, mems)
            speech, _blended_actions = await self._blended_turn(heard, scene, mems)
        else:
            speech, actions = await self._blended_turn(heard, scene, mems)

        if speech:
            self.memory.add(f'I replied: "{speech}"', kind="speech")
        return Turn(speech=speech, actions=actions)

    async def _blended_turn(self, heard: str, scene: Scene | None,
                            memories: list | None = None) -> tuple[str, list]:
        """The single blended turn: one constrained-envelope call (speech + actions),
        tolerant parse, and one bounded retry. The default turn shape — and, under the
        act-gate, the source of the spoken line (its actions discarded for the
        decision pass's). Returns ``(speech, actions)``; records no memory."""
        system = self._system_prompt(scene, memories)
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
        return speech, actions

    # ---- the two-pass act-gate (B5): decide the deed; the blended turn speaks ----
    def _action_decision_instructions(self) -> str:
        return (
            'Decide only what you DO now — not what you say. Someone nearby has just '
            'spoken or acted. Choose the single action your character would take in '
            'response, or none at all. This is about deeds, not words: take an action '
            'only when the moment truly calls for changing the world — you are asked '
            'to lead the way or to go somewhere (use "move" with a direction from '
            'your surroundings), to hand something over ("give_item"), and so on. If '
            'the moment calls only for words — a greeting, a question, idle talk, '
            'something that asks nothing of you — take NO action. Stay true to your '
            'character: a rooted or wary one acts rarely; a willing, helpful one acts '
            'when genuinely asked. If you would lead or go, the "move" is the step '
            'that actually takes you there — do not settle for a gesture in its place.\n'
            'Reply with a single JSON object and nothing else: {"speech": "", '
            '"actions": [{"name": "<action>", "args": {<named arguments>}}]}. Leave '
            '"speech" empty — you are choosing the deed here, not the words. Put the '
            'one action you choose in "actions", or leave it [] for no action. Never '
            'invent actions or arguments beyond those listed.\n'
            'Example (asked to lead or to go): {"speech": "", "actions": [{"name": '
            '"move", "args": {"direction": "north"}}]}\n'
            'Example (only words are called for): {"speech": "", "actions": []}\n'
            'Available actions:\n' + self.registry.describe(self.offered)
        )

    def _decision_system(self, scene: Scene | None,
                         memories: list | None = None) -> str:
        parts = self._base_lines(scene, memories)
        parts.append(self._action_decision_instructions())
        return "\n\n".join(parts)

    async def _decide_actions(self, heard: str, scene: Scene | None,
                              memories: list | None = None) -> list:
        """Pass 1 of the act-gate: a cheap, low-temperature, constrained pass whose
        only job is to pick the action this moment calls for (or none). Returns the
        validated intents — the turn's *authoritative* actions. Same tolerant parse,
        constrained grammar, and one bounded retry as the blended turn; any speech in
        the reply is ignored (the second pass composes it)."""
        system = self._decision_system(scene, memories)
        messages = [{"role": "user", "content": heard}]
        schema = self.registry.json_schema(self.offered)
        raw = await self.provider.complete(system, messages, schema=schema,
                                           temperature=DECIDE_TEMPERATURE)
        _speech, actions, errors = self._parse_turn(raw)
        if errors:
            correction = ("Your previous reply proposed invalid actions:\n"
                          + "\n".join(f"- {e}" for e in errors)
                          + "\nReply again as a single JSON object; fix or omit "
                            "those actions.")
            retry = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ]
            raw2 = await self.provider.complete(system, retry, schema=schema,
                                                temperature=DECIDE_TEMPERATURE)
            _s2, actions, _e2 = self._parse_turn(raw2)
        return actions

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
        mems = await self._recall(event, scene)
        system = self._system_prompt(scene, mems)
        nudge = (
            f"Something happens around you: {event}\n"
            "This was not said to you and asks nothing of you, so respond only as "
            "your character truly would — never out of politeness. Weigh how much "
            "it touches you. Something strong or close — weather turned harsh enough "
            "to feel, a threat or a blade drawn near, anything that reaches you "
            "bodily or plainly bears on what your character cares about — is what a "
            "real person answers: react to it, with a word, a gesture, or an action "
            "from your surroundings. Something slight, distant, or idle — a faint "
            "sound, a shift of light, a leaf coming down — a person simply takes in "
            "and lets pass; then reply with exactly {\"speech\": \"\", "
            '"actions": []}. React when it genuinely moves your character; stay '
            "silent when it does not. Never react merely to react."
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
