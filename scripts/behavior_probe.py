"""Behavioral regression harness — E2E mind tests against the LIVE model.

Why this exists (and why the offline unittest suite is not enough): the offline
tests drive a scripted ``FakeProvider``. They can prove the *engine* is correct
— given a valid action, the world changes right — but they structurally CANNOT
prove the real model *chooses* the right action or *uses* what it perceives. Both
of the faults found in play (a willing guide that spoke of leading but never
moved; an NPC blind to an item lying at its feet) lived entirely in that blind
spot. This harness is the gate for it.

The contract: one scenario per behavior we have verified working. Run it after
ANY change that touches a prompt, the action catalogue, or perception — and not
only for the thing you changed: the action catalogue is a shared, competitive
surface, so adding one action can regress the selection of another. Once a
behavior passes here it STAYS here; if a later change breaks it, this run fails
and names it, and the rule is: fix it before moving on.

Behavior is stochastic, so each scenario runs N trials and passes iff the number
of successes meets a threshold. Thresholds are set from measured rates, generous
enough to tolerate sampling noise but tight enough to catch a real collapse (the
original move bug was 0/5, the original silence bug 0/10 — either would fail a
threshold of 2).

RE-BASELINED 2026-07-19 to the game's current models (was characterised on
``qwen3.5:35b-a3b`` via Ollama). The game now runs two models: **NPCs on
``qwen3.6-35b-a3b``**, the **director on ``qwen3.6-27b``** (local vLLM, or hosted
OpenRouter — same OpenAI waist). So run the NPC-side scenarios against the NPC model
and the director-side ones against the director model:

    LOOM_PROVIDER=openrouter LOOM_OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b \\
        python scripts/behavior_probe.py converse react command   # NPC model
    LOOM_PROVIDER=openrouter LOOM_OPENROUTER_MODEL=qwen/qwen3.6-27b \\
        python scripts/behavior_probe.py director gate            # director model

A scenario may be a hard **gate** (``gated=True``, the default — a failure fails the
run) or a **watch item** (``gated=False`` — measured and printed, but it does NOT
fail the run). The watch list holds behaviors that are known-limited rather than
broken-by-regression (see the dated notes per scenario). Most of the 2026-07-19
re-baseline's watch items have since been fixed with single, model-agnostic prompt
rules and RE-GATED — the free-text command fallback (a flat-enum schema; see
``loom/command.py``), the NPC's strong-stimulus reaction (a magnitude rule;
``NpcMind.react``), and the director's "reach" actions (an observe de-hedge, since the
act-gate already owns restraint; ``DirectorMind._action_instructions``) — all
documented in ``docs/PROMPTING.md``. What remains is the blended idle-move variant,
whose act-gated twin (the path the game actually runs) holds green. Exit code 0 iff
every *gated* scenario met its threshold.
"""
from __future__ import annotations
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from loom.content import load_world
from loom.engine import Engine
from loom.ai import get_default_provider, NpcMind
from loom.ai.director import DirectorMind
from loom.ai.embedding import get_default_embedder
from loom.ai.memory import _cosine
from loom.ai.intent import interpret
from loom.action import default_registry
from loom.command import command_schema, describe_verbs, default_verbs
from loom import loot
from loom.ai import loot as loot_ai

WORLD = os.path.join(os.path.dirname(__file__), "..", "game", "world", "world.json")


# --- predicates over a returned Turn ---------------------------------------
def emits(name: str, **args) -> Callable:
    """The turn includes an action of ``name`` (optionally with given args)."""
    def check(turn):
        for a in turn.actions:
            if a.name != name:
                continue
            if all(str(a.args.get(k)).lower() == str(v).lower()
                   for k, v in args.items()):
                return True
        return False
    return check


def is_silent(turn) -> bool:
    return turn.is_silent


def speaks(turn) -> bool:
    return bool(turn.speech)


def mentions(*words) -> Callable:
    """The spoken line references any of these words (case-insensitive)."""
    def check(turn):
        s = turn.speech.lower()
        return any(w.lower() in s for w in words)
    return check


def NOT(pred: Callable) -> Callable:
    return lambda turn: not pred(turn)


def well_formed_envelope(turn) -> bool:
    """The turn never leaked a raw JSON envelope as spoken text (B6).

    The degrade-to-speech fallback treats an unparseable reply as the whole
    spoken line — so a malformed envelope surfaces as ``speech`` that is itself
    raw JSON (starts with ``{`` and carries a ``"speech"`` key). Under
    constrained decoding that malformation is impossible at the token level;
    this predicate is the standing guard that it stays impossible. Clean prose
    and honest silence both pass.
    """
    s = turn.speech.strip()
    return not (s.startswith("{") and '"speech"' in s)


# --- world setup hooks (optional, per scenario) ----------------------------
def hold(item_id: str, holder_id: str) -> Callable:
    """Put an item into a holder's inventory before the scenario runs."""
    return lambda world: world.place_item(item_id, holder_id)


# --- scenario definition ----------------------------------------------------
@dataclass
class Scenario:
    name: str                       # dotted id, e.g. "move.guide-leads"
    npc_id: str                     # who is answering
    utterance: str                  # what the player says
    check: Callable                 # Turn -> bool: the behavior we require
    desc: str                       # human-readable expectation
    addressed: bool = True          # was the NPC named (directed vs overheard)
    n: int = 5                      # trials
    threshold: int = 4              # pass iff successes >= threshold
    setup: Callable | None = None   # optional (world) -> None world prep
    act_gate: bool = False          # run the mind two-pass (B5) instead of blended
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ()


# The suite. Every entry is a behavior confirmed working at least once; the
# whole set is the regression contract. Add to it whenever a new behavior lands.
SCENARIOS = [
    Scenario(
        # ~70% single-pass on qwen3.5:35b-a3b (the model's honest ceiling, see
        # BACKLOG B5). A wide sample keeps the estimate stable; the threshold is
        # set to catch a COLLAPSE (the original bug was 0/5) or a regression, not
        # to force the model past its ceiling. Raising the ceiling needs a
        # stronger lever (a two-pass action decision, or a better model).
        name="move.guide-leads", npc_id="guide", tags=("move",),
        utterance="Wren, will you lead me north to show me the way?",
        check=emits("move"), n=8, threshold=5,
        desc="a willing guide emits `move` when asked to lead (~70% ceiling)"),
    Scenario(
        # WATCH (re-baseline 2026-07-19): on qwen3.6-35b-a3b the *blended* guide moved
        # on idle chatter 2/4 (over-eager) — but the game runs NPCs act-gated, and the
        # act-gated twin below (move.guide-no-idle-move-gated) holds 4/4 on the same
        # model. So the representative path is fine; this blended variant is a watch,
        # not a gate.
        name="move.guide-no-idle-move", npc_id="guide", tags=("move",),
        utterance="Wren, lovely weather today isn't it?",
        check=NOT(emits("move")), n=4, threshold=3, gated=False,
        desc="the guide does NOT move on idle chatter (no over-eager move)"),
    Scenario(
        # B5: the two-pass act-gate should raise the guide's move-when-asked rate
        # above the ~70% blended ceiling — the deed is chosen on its own, where the
        # cosmetic emote is not a flavour attractor. Measured live, threshold set
        # from the run; the point is to clear the ceiling the blended turn plateaued at.
        name="move.guide-leads-gated", npc_id="guide", tags=("move", "gate"),
        utterance="Wren, will you lead me north to show me the way?",
        check=emits("move"), act_gate=True, n=8, threshold=7,
        desc="the two-pass act-gate raises move-when-asked above the ~70% ceiling (B5)"),
    Scenario(
        # The guard on the other side: the gate must not over-fire move on idle talk.
        name="move.guide-no-idle-move-gated", npc_id="guide", tags=("move", "gate"),
        utterance="Wren, lovely weather today isn't it?",
        check=NOT(emits("move")), act_gate=True, n=4, threshold=3,
        desc="the act-gate still does NOT move on idle chatter (no over-eager move)"),
    Scenario(
        name="move.hermit-stays-put", npc_id="hermit", tags=("move",),
        utterance="please leave, old man.", addressed=True,
        check=NOT(emits("move")), n=4, threshold=3,
        desc="a rooted hermit does NOT walk off when told to leave"),
    Scenario(
        name="silence.hermit-idle", npc_id="hermit", tags=("silence",),
        utterance="what a grey sky today.", addressed=False,
        check=is_silent, n=5, threshold=2,
        desc="a reticent hermit often stays silent on an overheard idle remark"),
    Scenario(
        # Regression guard for defaulting the two-pass on: the split speech path must
        # still honour silence (B4) — a reticent hermit can decide no action AND say
        # nothing, an empty turn.
        name="silence.hermit-idle-gated", npc_id="hermit", tags=("silence", "gate"),
        utterance="what a grey sky today.", addressed=False,
        check=is_silent, act_gate=True, n=5, threshold=2,
        desc="the act-gate preserves reticent silence on an overheard idle remark"),
    Scenario(
        name="speech.guide-answers", npc_id="guide", tags=("speech",),
        utterance="Wren, are you there?",
        check=speaks, n=3, threshold=3,
        desc="a directly-addressed, gregarious guide answers"),
    Scenario(
        name="perception.ground-items", npc_id="guide", tags=("perception",),
        utterance="Wren, what do you see on the ground here?",
        check=mentions("key", "lantern", "map"), n=3, threshold=2,
        desc="an NPC grounds its reply on items it can perceive on the floor"),
    Scenario(
        # Regression guard: the two-pass *speech* pass still grounds on perceived
        # items (the compose pass reads the same scene).
        name="perception.ground-items-gated", npc_id="guide",
        tags=("perception", "gate"),
        utterance="Wren, what do you see on the ground here?",
        check=mentions("key", "lantern", "map"), act_gate=True, n=3, threshold=2,
        desc="the act-gate's speech pass still grounds on perceived floor items"),
    Scenario(
        name="give.guide-hands-over", npc_id="guide", tags=("give",),
        utterance="Wren, please hand your map to Odd for me.",
        check=emits("give_item"), n=5, threshold=3,
        desc="a willing NPC emits `give_item` when asked to hand something over"),
    Scenario(
        # Regression guard: the decision pass picks give_item when asked to hand over
        # (another world-mutating action, like move — it must not under-select here).
        name="give.guide-hands-over-gated", npc_id="guide", tags=("give", "gate"),
        utterance="Wren, please hand your map to Odd for me.",
        check=emits("give_item"), act_gate=True, n=5, threshold=3,
        desc="the act-gate's decision pass emits give_item when asked to hand over"),
    Scenario(
        # Phase 2 hardening: constrained decoding guarantees a well-formed
        # envelope at the token level. The utterance provokes an actions array
        # (emote/move) — exactly where the original B6 malformation occurred (a
        # missing object-close inside the array). A structural guarantee, so the
        # threshold is perfect: any leak here is a real regression of the
        # constraint, not sampling noise.
        name="envelope.well-formed", npc_id="guide", tags=("envelope",),
        utterance="Wren, will you lead me north to show me the way?",
        check=well_formed_envelope, n=5, threshold=5,
        desc="constrained decoding never leaks a raw JSON envelope as speech (B6)"),
]


# --- B1b: the free-text intent fallback (a player-command gate) --------------
# Behavioral because it is the *model* mapping unfamiliar phrasing onto a verb;
# the deterministic parser (offline-tested) covers the phrasings it knows. Every
# `text` below uses a verb the verb table does NOT have, so it can only pass by
# the LLM fallback interpreting it correctly.
@dataclass
class CommandScenario:
    name: str
    text: str                       # free-text player input (unknown verb)
    expect_verb: str                # the canonical verb it must map to
    n: int = 4
    threshold: int = 3
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ("command",)


COMMAND_CONTEXT = ("Here with you: Wren\n"
                   "On the ground: a brass key\n"
                   "You are carrying: a rusty lantern\n"
                   "Exits: north, down")

# The free-text intent fallback (B1b). These regressed to 0/4 on qwen3.6-35b-a3b at
# the 2026-07-19 re-baseline — the command grammar was a `oneOf` and the a3b MoE
# collapsed to the simplest branch (`look`) for every input. FIXED the same day by
# flattening `command_schema` to a verb-enum (the model must reason the verb, not take
# the cheapest branch): 16/16 on qwen3.6-35b-a3b, 4/4 on qwen3.5-9b. So they are hard
# gates again. See docs/PROMPTING.md for the wider lesson.
COMMAND_SCENARIOS = [
    CommandScenario("command.offer-is-give", "offer the lantern to Wren", "give"),
    CommandScenario("command.scoop-is-take", "scoop up the brass key", "take"),
    CommandScenario("command.head-is-go", "head north", "go"),
    CommandScenario("command.peer-is-examine", "peer at the brass key", "examine"),
]


async def run_command_scenario(provider, sc: CommandScenario, schema, catalogue):
    successes, samples = 0, []
    for _ in range(sc.n):
        try:
            res = await interpret(provider, schema, catalogue, COMMAND_CONTEXT, sc.text)
            ok = res is not None and res[0] == sc.expect_verb
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        samples.append((ok, str(res)))
    return successes, samples


# --- Phase 3: the game-master director (a world-observing gate) --------------
# Behavioral because it is the *model* reading perception (a chronicle of what
# changed + a current snapshot) and choosing to shape the scene, grounded on a
# real location id. Note what this gate does and does NOT prove: on the current
# model the director stages a beat on nearly every pulse — restraint ("intervene
# sparingly") is under-weighted, a known ceiling like B5, tracked in BACKLOG B8.
# So this proves the GROUNDED, well-formed beat (it targets a room that exists,
# where players are, and never leaks a raw envelope), not restraint.
@dataclass
class DirectorScenario:
    name: str
    digest: str                     # the chronicle the director reads
    snapshot: str                   # the current-state view
    check: Callable                 # Turn -> bool
    desc: str
    offered: tuple = ("stage_event",)  # the director-action subset this scenario tests
    lull: bool = False              # drive the quiet-room (lull) nudge, not the activity one
    foreshadow: bool = False        # include (and invite shaping) the rooms just ahead
    n: int = 5
    threshold: int = 3
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ("director",)


def stages_in(location: str) -> Callable:
    """The director staged an ambient beat into the given (real) location id."""
    def check(turn):
        return any(a.name == "stage_event"
                   and str(a.args.get("location")) == location
                   for a in turn.actions)
    return check


def acts_in(location: str) -> Callable:
    """The director took *any* action targeting the given (real) location id — used
    for foreshadowing, where either a standing condition or a beat set *ahead* counts."""
    def check(turn):
        return any(str(a.args.get("location")) == location for a in turn.actions)
    return check


def sets_condition_in(location: str) -> Callable:
    """The director raised a well-formed *standing* condition over the given (real)
    location — a valid set_condition with a non-empty tag and line."""
    def check(turn):
        return any(a.name == "set_condition"
                   and str(a.args.get("location")) == location
                   and str(a.args.get("tag", "")).strip()
                   and str(a.args.get("text", "")).strip()
                   for a in turn.actions)
    return check


def spawns_item_in(location: str) -> Callable:
    """The director brought a real object into the given (real) location — a valid
    spawn_item with a non-empty name and appearance line (the thing is grounded and
    announced, the reach half of proving the new capability)."""
    def check(turn):
        return any(a.name == "spawn_item"
                   and str(a.args.get("location")) == location
                   and str(a.args.get("name", "")).strip()
                   and str(a.args.get("text", "")).strip()
                   for a in turn.actions)
    return check


def offers_quest_in(location: str, destinations: tuple) -> Callable:
    """The director set a well-formed quest before the players in the given (real)
    location, pointing at a *real* onward place — a valid offer_quest with non-empty
    title/summary/line and a destination among ``destinations`` (the real ids the
    snapshot exposed, so the goal is grounded, not hallucinated)."""
    def check(turn):
        return any(a.name == "offer_quest"
                   and str(a.args.get("location")) == location
                   and str(a.args.get("title", "")).strip()
                   and str(a.args.get("summary", "")).strip()
                   and str(a.args.get("text", "")).strip()
                   and str(a.args.get("destination", "")) in destinations
                   for a in turn.actions)
    return check


DIRECTOR_PERSONA = {
    "tone": "a hushed, folkloric wilderness where small omens carry weight and "
            "the land seems half-awake",
    "goals": ["make the wilds feel watchful and alive",
              "let small signs draw wanderers onward, never forcing them"],
}

# An evocative moment with a real location id ("cave_mouth", from the demo world)
# and players present — exactly the perception the engine hands the director.
DIRECTOR_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    '- Wanderer-1 said: "is anyone out here in this dark?"\n'
    "- Odd the Hermit narrows his eyes\n"
    "- Wren the Wayfinder: This way, traveler — the north path is safest.")
DIRECTOR_SNAPSHOT = (
    "- cave_mouth (The Cave Mouth): Odd the Hermit, Wren the Wayfinder, "
    "Wanderer-1; exits: north, down; on the ground: a rusty lantern")

# A moment that invites a *standing* change rather than a one-off beat — the sky
# is turning, so a weather condition that persists (not a single narrated line) is
# the natural touch. Same real location id for grounding.
DIRECTOR_WEATHER_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    '- Wanderer-1 said: "the wind is picking up — feels like a storm is coming."\n'
    "- Wren the Wayfinder: Aye, the sky's gone the colour of slate.\n"
    "- Odd the Hermit glances up at the darkening clouds")

# A *quiet* moment — a lone wanderer, nothing stirring since. This is what the lull
# nudge reads: the scene has gone still, and a gentle beat keeps it from going dead.
DIRECTOR_LULL_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    "- Wanderer-1 looks slowly around the quiet clearing.")

# A *bare* moment — a lone arrival, nothing of note. This is the B8 case where the
# ungated director staged anyway (6/6): the act-gate should judge it needs nothing.
DIRECTOR_BARE_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.")

# The players are about to head *north* to the hill path — an empty room one exit
# away. The foreshadow snapshot marks it [ahead, empty], so the director can shape
# what they will find there before they arrive (off-screen staging, B9).
DIRECTOR_AHEAD_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    '- Wren the Wayfinder: The north path it is, then — follow me up the hill.\n'
    '- Wanderer-1 said: "lead on, I\'m right behind you."')
DIRECTOR_AHEAD_SNAPSHOT = (
    "- cave_mouth (The Cave Mouth): Odd the Hermit, Wren the Wayfinder, "
    "Wanderer-1; exits: north, down; on the ground: a rusty lantern\n"
    "- hill_path (The Hill Path) [ahead, empty]; exits: south")

# A moment inviting a *thing* — the wanderer is searching, and Wren points at the
# ground. The reach action spawn_item: the director may bring a small object into
# the room for them to find. Same real location id for grounding.
DIRECTOR_SPAWN_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    '- Wanderer-1 said: "there must be something useful left lying around this old '
    'place — let me look."\n'
    "- Wren the Wayfinder: Keep your eyes on the ground here — these hills hide "
    "small things for those who look.\n"
    "- Odd the Hermit watches the stranger search, saying nothing")

# A moment inviting a *purpose* — the wanderer asks where to go, and the guide names
# the hill. The reach action offer_quest: the director may set a gentle goal pointing
# at hill_path (the empty room just ahead, so the snapshot must expose its id — the
# foreshadow snapshot does). The offer targets the occupied room; the goal points on.
DIRECTOR_QUEST_DIGEST = (
    "- Wanderer-1 arrived in The Cave Mouth.\n"
    '- Wanderer-1 said: "I don\'t know where to go from here. Is there anywhere '
    'worth heading?"\n'
    "- Wren the Wayfinder: The hill path to the north climbs to an old lookout — "
    "worth the walk, if you've the legs for it.\n"
    "- Odd the Hermit: Aye. There's something up that path, if you care to find it.")

DIRECTOR_SCENARIOS = [
    DirectorScenario(
        # Measured 7/8 and 6/6 grounded into cave_mouth, 0 envelope leaks. The
        # threshold catches a collapse (a director that stops staging, or stages
        # into a room that doesn't exist), not the occasional abstain.
        name="director.sets-a-scene",
        digest=DIRECTOR_DIGEST, snapshot=DIRECTOR_SNAPSHOT,
        check=stages_in("cave_mouth"), offered=("stage_event",),
        n=5, threshold=3,
        desc="the director stages a well-formed ambient beat into the room "
             "where players are"),
    DirectorScenario(
        # The world-shaping half: given a sky that is turning, the director raises
        # a standing condition (a storm) grounded in a real room, with a non-empty
        # tag (the clear handle) and line. Proves the new capability the way
        # sets-a-scene proved stage_event.
        # RE-GATED 2026-07-19: the reach actions fell off on qwen3.6-27b (0-1/5) at the
        # re-baseline. Root cause was the observe prompt DOUBLE-restraining — it
        # re-asserted "intervene sparingly, most beats need nothing" even though the
        # act-gate already owns restraint (it decides wait/act BEFORE observe runs).
        # The instruction-following 27b took that hedge literally and fell silent,
        # while the less-restrained 9b reached anyway (spawns-item 4/5). Removing the
        # redundant hedge and telling observe to COMMIT to the fitting touch
        # (DirectorMind._action_instructions) recovered the reach on 27b with the
        # act-gate's restraint intact (director.restraint still 8/8): spawns-item
        # 0->5/5, sets-a-condition 1->3/5, foreshadows 0->3/8. One model-agnostic
        # rule, no per-model branch. See docs/PROMPTING.md.
        name="director.sets-a-condition",
        digest=DIRECTOR_WEATHER_DIGEST, snapshot=DIRECTOR_SNAPSHOT,
        check=sets_condition_in("cave_mouth"), offered=("set_condition",),
        n=5, threshold=3,
        desc="the director raises a standing condition over the room where "
             "players are, grounded and well-formed"),
    DirectorScenario(
        # The lull path (B9): given a still scene and the gentler lull nudge, the
        # director offers one grounded, low-key beat rather than letting the room go
        # dead. Threshold catches a *collapse* of the lull path (never stirring a
        # quiet room, or staging into a room that doesn't exist), not the occasional
        # abstain — the lull nudge deliberately still permits "do nothing".
        name="director.lull-beat",
        digest=DIRECTOR_LULL_DIGEST, snapshot=DIRECTOR_SNAPSHOT,
        check=stages_in("cave_mouth"), offered=("stage_event",), lull=True,
        n=5, threshold=3,
        desc="the director stirs a quiet room with a gentle ambient beat "
             "(the lull trigger)"),
    DirectorScenario(
        # Off-screen staging (B9): with the players about to walk north and the empty
        # hill_path marked [ahead, empty], the director may foreshadow there — shape
        # what they will find before they arrive. Offered both actions (foreshadowing
        # prefers a *standing* condition, but a beat ahead counts too); the check is
        # any action targeting hill_path. Threshold catches a *collapse* (the director
        # never reaches the room ahead), not a rate — foreshadowing is a *sometimes*
        # touch, and shaping the occupied room first is also correct.
        # RE-GATED 2026-07-19: 0/8 -> 3/8 with the observe de-hedge/commit fix (see
        # director.sets-a-condition for the full root cause). Foreshadowing is a
        # *sometimes* touch, so the threshold stays a collapse-detector (>=2), not a rate.
        name="director.foreshadows",
        digest=DIRECTOR_AHEAD_DIGEST, snapshot=DIRECTOR_AHEAD_SNAPSHOT,
        check=acts_in("hill_path"), offered=("stage_event", "set_condition"),
        foreshadow=True, n=8, threshold=2,
        desc="the director foreshadows into the empty room the players are about "
             "to enter (off-screen staging)"),
    DirectorScenario(
        # Reach, slice 1: given a searching wanderer and a hint, the director brings a
        # real object into the room they are in — grounded (a location that exists) and
        # well-formed (a name and appearance line). Proves spawn_item the way
        # sets-a-scene proved stage_event. Threshold is a collapse-detector (the director
        # never spawns, or spawns into a room that doesn't exist), not a rate — the offer
        # subset is spawn_item only, so the question is whether it acts well when invited.
        # RE-GATED 2026-07-19: 0/5 -> 5/5 with the observe de-hedge/commit fix (see
        # director.sets-a-condition for the full root cause — this was the clearest
        # case: 27b spawned 0/5 where the less-restrained 9b spawned 4/5).
        name="director.spawns-item",
        digest=DIRECTOR_SPAWN_DIGEST, snapshot=DIRECTOR_SNAPSHOT,
        check=spawns_item_in("cave_mouth"), offered=("spawn_item",),
        n=5, threshold=3,
        desc="the director spawns a real, well-formed object into the room where "
             "players are (reach: spawn_item)"),
    DirectorScenario(
        # Reach, slice 2: given a wanderer asking where to go, the director sets a gentle
        # quest pointing at a *real* onward place. Needs foreshadow on so the snapshot
        # exposes the destination id (hill_path) — you cannot send someone to a place you
        # cannot see; in live play foreshadow is on by default. The offer targets the
        # occupied cave_mouth; the goal points to hill_path. Threshold is a
        # collapse-detector (the director never offers, or names a place not shown), not
        # a rate — offering a quest is a *sometimes*, deliberate touch.
        name="director.offers-quest",
        digest=DIRECTOR_QUEST_DIGEST, snapshot=DIRECTOR_AHEAD_SNAPSHOT,
        check=offers_quest_in("cave_mouth", ("hill_path",)),
        offered=("offer_quest",), foreshadow=True, n=8, threshold=3,
        desc="the director offers a grounded quest pointing at a real onward room "
             "(reach: offer_quest)"),
]


async def run_director_scenario(provider, sc: DirectorScenario):
    successes, samples = 0, []
    for _ in range(sc.n):
        # Fresh mind (fresh memory) per trial, offered only its director actions —
        # the same subset the engine gives the real director.
        mind = DirectorMind(persona=DIRECTOR_PERSONA, provider=provider,
                            registry=default_registry(), offered=list(sc.offered))
        try:
            turn = await mind.observe(sc.digest, sc.snapshot, lull=sc.lull,
                                      foreshadow=sc.foreshadow)
            ok = bool(sc.check(turn))
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        acts = ",".join(f"{a.name}{a.args}" for a in turn.actions) or "-"
        samples.append((ok, "SILENT" if turn.is_silent else f"[{acts}]"))
    return successes, samples


# --- B8: the model-side act-gate (the whole open question) --------------------
# The act-gate asks the model ONE narrow, low-temperature question — is a beat
# warranted right now? — before the full compose is paid for. It probes
# DirectorMind.decide directly (the judgment), NOT observe (the compose). The
# lever is unproven: the same model that over-stages may just answer "act" every
# time. So we measure BOTH poles as a tension pair — it must *discriminate*:
#   * director.restraint     — a bare scene the ungated director staged 6/6 (B8):
#                              the gate should judge it needs nothing ("wait").
#   * director.gate-acts-on-cue — an evocative scene: the gate should still act,
#                              proving it did not simply learn to always wait.
# Adopt the gate iff BOTH discriminate live; otherwise report the null result and
# ship it off. Thresholds are provisional until this first live run sets the rates.
@dataclass
class GateScenario:
    name: str
    digest: str
    snapshot: str
    want_act: bool                  # the decision we expect the gate to reach
    desc: str
    n: int = 8
    threshold: int = 5              # collapse-detector (either pole failing = fail)
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ("director", "gate")


GATE_SCENARIOS = [
    GateScenario(
        name="director.restraint",
        digest=DIRECTOR_BARE_DIGEST, snapshot=DIRECTOR_SNAPSHOT, want_act=False,
        desc="the act-gate judges a bare, nothing-of-note scene needs no beat "
             "(model-side restraint, B8)"),
    GateScenario(
        name="director.gate-acts-on-cue",
        digest=DIRECTOR_DIGEST, snapshot=DIRECTOR_SNAPSHOT, want_act=True,
        desc="the act-gate still acts on an evocative scene (it did not just learn "
             "to always wait)"),
    # NOTE: the lull path is deliberately NOT gated (the gate is scoped to the
    # activity path in Director._run_beat). Measured 2026-07-13: decide() on a quiet
    # lull scene judged 'wait' 8/8 — the model would kill the B9 liveliness floor if
    # the gate sat on it. The floor stays deterministic; director.lull-beat (which
    # probes observe(lull=True)) remains its guard.
]


async def run_gate_scenario(provider, sc: GateScenario):
    successes, samples = 0, []
    for _ in range(sc.n):
        mind = DirectorMind(persona=DIRECTOR_PERSONA, provider=provider,
                            registry=default_registry(), offered=["stage_event"])
        try:
            act, why = await mind.decide(sc.digest, sc.snapshot)
            ok = (act == sc.want_act)
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        samples.append((ok, f'{"act " if act else "wait"} — {why!r}'))
    return successes, samples


# --- autonomous reactions (B9) — the mind reacts of its own volition -----------
# These probe NpcMind.react directly (the mind's *choice*): does an NPC respond to
# a change in the world or another character's deed when it genuinely concerns it,
# and — the restraint half — stay quiet on something trivial? The engine's cascade
# plumbing and its rails are the offline suite's job (tests/test_reactions.py).
@dataclass
class ReactScenario:
    name: str
    npc_id: str
    event: str                      # what happens around the NPC (an observation)
    check: Callable                 # Turn -> bool
    desc: str
    n: int = 5
    threshold: int = 3
    setup: Callable | None = None   # optional (world) -> None (e.g. set a condition)
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ("react",)


def _set_storm(world):
    """Put a standing storm over the reacting NPC's room, so it is in the Scene
    (slice 1's conditions) as well as narrated — the NPC perceives the weather."""
    loc = world.entities["guide"].location_id
    world.conditions.set(loc, "storm",
                         "A cold rain lashes down and the wind rises to a howl.")


def _set_nightfall(world):
    """Put the world-clock's nightfall over everything (the world scope), so the
    reacting NPC carries the new time-of-day in its Scene via the world-scope
    fold-in — the autonomous-clock counterpart of _set_storm (B9)."""
    world.conditions.set_world(
        "time", "Night has fallen; the dark presses close, and the cold with it.")


REACT_SCENARIOS = [
    ReactScenario(
        # n=6/threshold=3 tolerates the react rate's variance and catches a collapse
        # (the react path going silent), not a dip. RE-GATED 2026-07-19: this briefly
        # fell to a WATCH when the re-baseline found qwen3.6-35b-a3b under-reacting to
        # the storm (1/6) and qwen3.5-9b not reacting at all (0/6). Root cause was the
        # react nudge itself — it was weighted almost entirely toward restraint ("most
        # of the time a character does nothing"), which suppressed reaction even to a
        # storm that physically reaches the character. Rewriting the nudge to a
        # *magnitude test* (answer what is strong or close; let the slight and idle
        # pass — NpcMind.react) recovered it with ONE model-agnostic rule: measured
        # 6/6 on both qwen3.6-35b-a3b and qwen3.5-9b, with restraint (ignores-trivial)
        # preserved 5/5 on both. So it is a hard gate again. See docs/PROMPTING.md.
        name="npc.reacts-to-world", npc_id="guide",
        event="A cold rain sweeps in and the wind rises to a howl.",
        check=speaks, setup=_set_storm, n=6, threshold=3,
        desc="an NPC reacts of its own volition to a change in the world (a storm)"),
    ReactScenario(
        # NPC->NPC reaction is a genuine coin-flip on this model (~50%): an NPC
        # legitimately may or may not answer another's deed — that IS the 'own
        # volition' point, and the same high bar that keeps ignores-trivial silent.
        # So this gates a *collapse* of the react path (0-1/8 = the model stopped
        # reacting to other characters at all), not the rate. The mechanism itself
        # is proven deterministically in tests/test_reactions.py.
        name="npc.reacts-to-npc", npc_id="guide",
        event="Odd the Hermit suddenly draws his knife, steps in front of you, "
              "and stares into the dark to the north.",
        check=speaks, n=8, threshold=2,
        desc="an NPC reacts of its own volition to another character's word and deed"),
    ReactScenario(
        # The world-clock's own turn (B9): a gentle, non-threatening world change,
        # far milder than the storm above — measured ~28% for the wayfinder (3/8,
        # then 2/10), to whom nightfall is routine, vs ~85% for the storm. At a rate
        # this low a threshold *at* the rate would flake, so n=10/threshold=1 is a
        # pure collapse-detector: it fails only if the world-scope beat stops
        # reaching a mind at all (a perception/wiring regression driving the rate to
        # zero). The ~28% rate is *characterised*, not enforced — a mild stimulus has
        # a real ceiling. The autonomous trigger itself is proven offline (test_clock.py);
        # this guards that its beat still reaches the mind live through the
        # world-scope fold-in (_set_nightfall sets a world-scope condition).
        name="npc.reacts-to-nightfall", npc_id="guide",
        event="Night comes down over the hills, and the cold deepens with the dark.",
        check=speaks, setup=_set_nightfall, n=10, threshold=1,
        desc="an NPC reacts of its own volition to the world's own turn (nightfall)"),
    ReactScenario(
        # The restraint half (the high bar). The reticent hermit should usually let
        # a trivial ambient flicker pass. The local model under-weights 'do nothing'
        # (cf. B4/B8), so the threshold catches a collapse of restraint, not perfection.
        name="npc.ignores-trivial", npc_id="hermit",
        event="A single leaf drifts down and settles on the moss.",
        check=is_silent, n=5, threshold=2,
        desc="a reticent NPC usually lets a trivial ambient event pass in silence"),
]


async def run_react_scenario(provider, sc: ReactScenario):
    world, start = load_world(WORLD)
    if sc.setup:
        sc.setup(world)
    engine = Engine(world, provider, start_location=start)
    npc = world.entities[sc.npc_id]
    successes, samples = 0, []
    for _ in range(sc.n):
        # Fresh mind per trial, offered the same action subset the engine gives NPCs
        # — so a reaction can be a word OR a real action (flee the storm, draw a blade).
        scene = engine._scene_for(npc, npc.location_id)
        mind = NpcMind(npc, provider, registry=engine.actions,
                       offered=engine.npc_actions)
        try:
            turn = await mind.react(sc.event, scene=scene)
            ok = bool(sc.check(turn))
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        acts = ",".join(f"{a.name}{a.args}" for a in turn.actions) or "-"
        tag = "SILENT" if turn.is_silent else f'"{turn.speech}" [{acts}]'
        samples.append((ok, tag))
    return successes, samples


async def run_scenario(provider, sc: Scenario):
    world, start = load_world(WORLD)
    if sc.setup:
        sc.setup(world)
    engine = Engine(world, provider, start_location=start)
    npc = world.entities[sc.npc_id]
    successes, samples = 0, []
    for _ in range(sc.n):
        scene = engine._scene_for(npc, npc.location_id)
        # Fresh mind (fresh memory) per trial so trials are independent and the
        # result is a clean characterisation, not a drifting conversation. Offer
        # the same action subset the engine gives its NPCs, so the harness tests
        # the real catalogue (not the player-only take/drop).
        mind = NpcMind(npc, provider, registry=engine.actions,
                       offered=engine.npc_actions, act_gate=sc.act_gate)
        try:
            turn = await mind.converse("Wanderer", sc.utterance,
                                       scene=scene, addressed=sc.addressed)
            ok = bool(sc.check(turn))
        except Exception as exc:              # a crash is a failed trial, not a stop
            turn, ok = None, False
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        acts = ",".join(f"{a.name}{a.args}" for a in turn.actions) or "-"
        tag = "SILENT" if turn.is_silent else f'"{turn.speech}" [{acts}]'
        samples.append((ok, tag))
    return successes, samples


# --- Phase 4: the loot forge — the model authors flavour to a code-rolled brief ---
# This probes loom.ai.loot.author_flavour directly (the model's *flavour* half): given
# a brief whose mechanics (tier/tags/theme) are already fixed by code, does the model
# return a schema-valid, in-theme name and lore? The forge's mechanical half — the roll,
# the tier gating, the family guard, the assembly — is deterministic and is the offline
# suite's job (tests/test_loot.py). This is the mind gate the offline suite structurally
# cannot see: whether the model authors a usable, grounded item, not a number or a
# malformed reply. Constrained decoding guarantees the shape; this checks the substance.
@dataclass
class LootScenario:
    name: str
    brief: object                   # a loot.Brief the code would have rolled
    check: Callable                 # (raw dict|None) -> bool
    desc: str
    n: int = 6
    threshold: int = 4
    gated: bool = True              # False = measured but NOT a hard gate (a watch item)
    tags: tuple = ("loot",)


def _grounded_in(brief) -> Callable:
    """The authored flavour is schema-valid AND reflects the brief — its theme, one of
    its qualities, or the moment it was forged for shows in the name or lore. Lenient by
    design (the model rephrases: 'storm-worn' -> 'battered by storms'), so it matches on
    word *stems*; it fails only on a malformed reply or flavour with no grounding at all
    — a collapse of the forge, not a strict-wording check."""
    stems = [brief.theme.lower()] if brief.theme else []
    stems += [t.split("-")[0].lower() for t in brief.tags]
    stems += [w for w in ("storm", "dusk", "night", "rain", "signal", "fire", "hill",
                          "cave", "mine", "wood", "cold") if w in brief.context.lower()]
    stems = [s for s in stems if len(s) >= 3]

    def check(raw):
        flav = loot.parse_flavour(raw)
        if flav is None:
            return False
        text = (flav["name"] + " " + flav["description"]).lower()
        return any(s in text for s in stems)
    return check


# A rich brief (rare tier, three qualities, a storm-at-dusk quest reward) — the code has
# already decided everything mechanical; the model authors only the words. The real
# forge rolls this and hands it over; here we hand it a fixed one and watch it write.
_LOOT_BRIEF = loot.Brief(
    tier="rare", rank=3, theme="talisman",
    tags=["storm-worn", "dusk-touched", "ill-omened"],
    context="a reward for reaching the old signal-fire on the hill, "
            "under a driving storm at dusk")

LOOT_SCENARIOS = [
    LootScenario(
        # The forge's mind gate: schema-valid, grounded flavour for a code-rolled brief.
        # Constrained decoding makes the shape near-certain, so a miss is either an empty
        # field (parse rejects) or flavour with no grounding at all — a collapse, which
        # n=6/threshold=4 catches while tolerating the model's rephrasing latitude.
        name="loot.forges-in-theme", brief=_LOOT_BRIEF,
        check=_grounded_in(_LOOT_BRIEF), n=6, threshold=4,
        desc="the loot forge authors a schema-valid, in-theme item for a "
             "code-rolled brief (the model writes flavour, never mechanics)"),
]


async def run_loot_scenario(provider, sc: LootScenario):
    schema = loot.flavour_schema()
    successes, samples = 0, []
    for _ in range(sc.n):
        try:
            raw = await loot_ai.author_flavour(provider, schema, sc.brief)
            ok = bool(sc.check(raw))
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        flav = loot.parse_flavour(raw)
        detail = (f'"{flav["name"]}" — {flav["description"][:64]}'
                  if flav else f"INVALID: {raw!r}")
        samples.append((ok, detail))
    return successes, samples


@dataclass
class MemoryScenario:
    """Phase 5 memory depth: a REAL embedder must rank the on-topic memory top for
    *paraphrase* queries that share no exact words with it — the semantic relevance the
    lexical fake (offline) cannot do, and the reason this is a live gate. Each query is
    one trial; success = the target memory ranks first by cosine."""
    name: str
    memories: list                  # the pool of memory texts
    queries: list                   # paraphrase queries; each should rank `target` top
    target: int                     # index in `memories` that is on-topic
    desc: str
    threshold: int = 3
    gated: bool = True
    tags: tuple = ("memory",)

    @property
    def n(self) -> int:
        return len(self.queries)


_MEM_SET = [
    "The player swore to bring me the black key.",          # 0 — the on-topic promise
    "A merchant sold me dried figs at the market.",
    "The weather turned cold as clouds gathered.",
    "A child laughed somewhere down the lane.",
]

MEMORY_SCENARIOS = [
    MemoryScenario(
        name="memory.paraphrase-relevance", memories=_MEM_SET,
        queries=[
            "when will you hand over that dark iron thing you promised me?",
            "tell me about the oath you made to fetch something for me",
            "did you forget the vow to deliver the dark lock-opener?",
        ],
        target=0, threshold=3,
        desc="a real embedder ranks the on-topic memory top for paraphrase queries "
             "sharing no exact words (relevance retrieval, not keyword match)"),
]


async def run_memory_scenario(embedder, sc: MemoryScenario):
    mem_vecs = await embedder.embed(sc.memories)
    q_vecs = await embedder.embed(sc.queries)
    successes, samples = 0, []
    for q, qv in zip(sc.queries, q_vecs):
        ranked = sorted(((_cosine(qv, mv), i) for i, mv in enumerate(mem_vecs)),
                        reverse=True)
        top_cos, top_i = ranked[0]
        ok = (top_i == sc.target)
        successes += ok
        samples.append((ok, f'{q[:44]!r} -> M{top_i} (cos {top_cos:.3f})'))
    return successes, samples


async def main():
    provider = get_default_provider()
    pname = getattr(provider, "name", type(provider).__name__)
    selector = sys.argv[1] if len(sys.argv) > 1 else None

    def picks(s):
        return selector is None or selector == s.name or selector in s.tags
    chosen = [s for s in SCENARIOS if picks(s)]
    chosen_cmds = [s for s in COMMAND_SCENARIOS if picks(s)]
    chosen_dir = [s for s in DIRECTOR_SCENARIOS if picks(s)]
    chosen_gate = [s for s in GATE_SCENARIOS if picks(s)]
    chosen_react = [s for s in REACT_SCENARIOS if picks(s)]
    chosen_loot = [s for s in LOOT_SCENARIOS if picks(s)]
    chosen_mem = [s for s in MEMORY_SCENARIOS if picks(s)]
    if not any((chosen, chosen_cmds, chosen_dir, chosen_gate, chosen_react,
                chosen_loot, chosen_mem)):
        tags = sorted({t for s in SCENARIOS for t in s.tags}
                      | {t for s in COMMAND_SCENARIOS for t in s.tags}
                      | {t for s in DIRECTOR_SCENARIOS for t in s.tags}
                      | {t for s in GATE_SCENARIOS for t in s.tags}
                      | {t for s in REACT_SCENARIOS for t in s.tags}
                      | {t for s in LOOT_SCENARIOS for t in s.tags}
                      | {t for s in MEMORY_SCENARIOS for t in s.tags})
        print(f"No scenarios match {selector!r}. Known tags: {tags}")
        return 2

    total = (len(chosen) + len(chosen_cmds) + len(chosen_dir)
             + len(chosen_gate) + len(chosen_react) + len(chosen_loot)
             + len(chosen_mem))
    print(f"=== Loom behavioral regression — provider {pname} ===")
    if pname.startswith("fake"):
        print("WARNING: FakeProvider is scripted — this harness only means "
              "anything against the LIVE model. Set LOOM_PROVIDER=ollama.\n")
    print(f"{total} scenario(s); each is N live trials "
          f"against a stochastic model.\n")

    failed, watch = [], []

    def verdict(sc, successes):
        """PASS / FAIL (a gated regression, fails the run) / WATCH (an ungated,
        known-degraded behaviour — measured and shown, but not a hard gate)."""
        ok = successes >= sc.threshold
        if ok:
            return ok, "PASS"
        if getattr(sc, "gated", True):
            failed.append(sc.name)
            return ok, "FAIL"
        watch.append(sc.name)
        return ok, "WATCH"

    for sc in chosen:
        successes, samples = await run_scenario(provider, sc)
        ok, v = verdict(sc, successes)
        print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            for hit, detail in samples:              # show the evidence on a miss
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    if chosen_cmds:
        # B1b: the free-text intent fallback — the model maps unfamiliar phrasing
        # onto the right command verb (see run_command_scenario).
        allowed = [c for c in {v.canonical for v in default_verbs().values()}
                   if c not in ("quit", "help")]
        schema = command_schema(default_verbs(), allowed)
        catalogue = describe_verbs(default_verbs(), allowed)
        for sc in chosen_cmds:
            successes, samples = await run_command_scenario(
                provider, sc, schema, catalogue)
            ok, v = verdict(sc, successes)
            print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} "
                  f"(need >={sc.threshold})  — free text → `{sc.expect_verb}`")
            if not ok:
                for hit, detail in samples:
                    print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_dir:
        # Phase 3: the game-master director stages a grounded ambient beat.
        successes, samples = await run_director_scenario(provider, sc)
        ok, v = verdict(sc, successes)
        print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            for hit, detail in samples:
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_gate:
        # B8: the model-side act-gate judges whether a beat is warranted (decide,
        # not observe). A tension pair — it must restrain on a bare scene AND still
        # act on an evocative one.
        successes, samples = await run_gate_scenario(provider, sc)
        ok, v = verdict(sc, successes)
        print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            for hit, detail in samples:
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_react:
        # B9: an NPC reacts to the world / another character of its own volition.
        successes, samples = await run_react_scenario(provider, sc)
        ok, v = verdict(sc, successes)
        print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            for hit, detail in samples:
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_loot:
        # Phase 4: the loot forge authors schema-valid, in-theme flavour for a
        # code-rolled brief (the mechanical roll is the offline suite's job).
        successes, samples = await run_loot_scenario(provider, sc)
        ok, v = verdict(sc, successes)
        print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        # Show the forged items even on a pass — the point is to read the loot.
        for hit, detail in samples:
            print(f"          {'ok ' if hit else 'MISS'} {detail}")

    if chosen_mem:
        # Phase 5 memory depth: the LIVE embedding gate — a real embedder ranks a
        # paraphrase of an on-topic memory above unrelated trivia (the offline suite
        # proves the retrieval math on the deterministic fake). Uses the EMBEDDER, not
        # the chat provider; if none is configured it cannot run, so it is noted as a
        # watch item rather than failing the run.
        embedder = get_default_embedder()
        ename = getattr(embedder, "name", None)
        if embedder is None:
            for sc in chosen_mem:
                watch.append(sc.name)
                print(f"[WATCH] {sc.name:<28} —  no embedder configured "
                      "(set OPENROUTER_API_KEY / LOOM_EMBEDDER); live memory gate skipped")
        else:
            print(f"(memory gate via embedder {ename})")
            for sc in chosen_mem:
                successes, samples = await run_memory_scenario(embedder, sc)
                ok, v = verdict(sc, successes)
                print(f"[{v:>5}] {sc.name:<28} {successes}/{sc.n} "
                      f"(need >={sc.threshold})  — {sc.desc}")
                for hit, detail in samples:
                    print(f"          {'ok ' if hit else 'MISS'} {detail}")

    print()
    if watch:
        # Known-degraded on the current models (see the per-scenario notes) — measured
        # and shown, but not gating. Revisit; not a regression to block on.
        print(f"WATCH ({len(watch)}, not gated): {', '.join(watch)}")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        print("A verified behavior regressed. Fix it before moving on.")
        return 1
    gated_total = total - len(watch)
    print(f"OK — all {gated_total} gated scenarios met their thresholds"
          + (f" ({len(watch)} watch item(s) noted above)." if watch else "."))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
