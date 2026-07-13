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

Usage (needs the live model up):
    LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b \\
        python scripts/behavior_probe.py                 # every scenario
    ... python scripts/behavior_probe.py move            # only name/tag 'move'

Exit code 0 iff every selected scenario met its threshold.
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
from loom.ai.intent import interpret
from loom.action import default_registry
from loom.command import command_schema, describe_verbs, default_verbs

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
        name="move.guide-no-idle-move", npc_id="guide", tags=("move",),
        utterance="Wren, lovely weather today isn't it?",
        check=NOT(emits("move")), n=4, threshold=3,
        desc="the guide does NOT move on idle chatter (no over-eager move)"),
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
        name="give.guide-hands-over", npc_id="guide", tags=("give",),
        utterance="Wren, please hand your map to Odd for me.",
        check=emits("give_item"), n=5, threshold=3,
        desc="a willing NPC emits `give_item` when asked to hand something over"),
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
    tags: tuple = ("command",)


COMMAND_CONTEXT = ("Here with you: Wren\n"
                   "On the ground: a brass key\n"
                   "You are carrying: a rusty lantern\n"
                   "Exits: north, down")

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
        name="director.foreshadows",
        digest=DIRECTOR_AHEAD_DIGEST, snapshot=DIRECTOR_AHEAD_SNAPSHOT,
        check=acts_in("hill_path"), offered=("stage_event", "set_condition"),
        foreshadow=True, n=8, threshold=2,
        desc="the director foreshadows into the empty room the players are about "
             "to enter (off-screen staging)"),
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
        # Observed ~0.75-0.8 react rate (5/5, 3/5); n=6/threshold=3 tolerates that
        # variance and catches a collapse (the react path going silent), not a dip.
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
                       offered=engine.npc_actions)
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


async def main():
    provider = get_default_provider()
    pname = getattr(provider, "name", type(provider).__name__)
    selector = sys.argv[1] if len(sys.argv) > 1 else None

    def picks(s):
        return selector is None or selector == s.name or selector in s.tags
    chosen = [s for s in SCENARIOS if picks(s)]
    chosen_cmds = [s for s in COMMAND_SCENARIOS if picks(s)]
    chosen_dir = [s for s in DIRECTOR_SCENARIOS if picks(s)]
    chosen_react = [s for s in REACT_SCENARIOS if picks(s)]
    if not chosen and not chosen_cmds and not chosen_dir and not chosen_react:
        tags = sorted({t for s in SCENARIOS for t in s.tags}
                      | {t for s in COMMAND_SCENARIOS for t in s.tags}
                      | {t for s in DIRECTOR_SCENARIOS for t in s.tags}
                      | {t for s in REACT_SCENARIOS for t in s.tags})
        print(f"No scenarios match {selector!r}. Known tags: {tags}")
        return 2

    total = (len(chosen) + len(chosen_cmds) + len(chosen_dir) + len(chosen_react))
    print(f"=== Loom behavioral regression — provider {pname} ===")
    if pname.startswith("fake"):
        print("WARNING: FakeProvider is scripted — this harness only means "
              "anything against the LIVE model. Set LOOM_PROVIDER=ollama.\n")
    print(f"{total} scenario(s); each is N live trials "
          f"against a stochastic model.\n")

    failed = []
    for sc in chosen:
        successes, samples = await run_scenario(provider, sc)
        ok = successes >= sc.threshold
        verdict = "PASS" if ok else "FAIL"
        print(f"[{verdict}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            failed.append(sc.name)
            for hit, detail in samples:              # show the evidence on failure
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
            ok = successes >= sc.threshold
            verdict = "PASS" if ok else "FAIL"
            print(f"[{verdict}] {sc.name:<28} {successes}/{sc.n} "
                  f"(need >={sc.threshold})  — free text → `{sc.expect_verb}`")
            if not ok:
                failed.append(sc.name)
                for hit, detail in samples:
                    print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_dir:
        # Phase 3: the game-master director stages a grounded ambient beat.
        successes, samples = await run_director_scenario(provider, sc)
        ok = successes >= sc.threshold
        verdict = "PASS" if ok else "FAIL"
        print(f"[{verdict}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            failed.append(sc.name)
            for hit, detail in samples:
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    for sc in chosen_react:
        # B9: an NPC reacts to the world / another character of its own volition.
        successes, samples = await run_react_scenario(provider, sc)
        ok = successes >= sc.threshold
        verdict = "PASS" if ok else "FAIL"
        print(f"[{verdict}] {sc.name:<28} {successes}/{sc.n} (need >={sc.threshold})"
              f"  — {sc.desc}")
        if not ok:
            failed.append(sc.name)
            for hit, detail in samples:
                print(f"          {'ok ' if hit else 'MISS'} {detail}")

    print()
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        print("A verified behavior regressed. Fix it before moving on.")
        return 1
    print(f"OK — all {total} behavioral scenarios met their thresholds.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
