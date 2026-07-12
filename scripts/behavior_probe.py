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

DIRECTOR_SCENARIOS = [
    DirectorScenario(
        # Measured 7/8 and 6/6 grounded into cave_mouth, 0 envelope leaks. The
        # threshold catches a collapse (a director that stops staging, or stages
        # into a room that doesn't exist), not the occasional abstain.
        name="director.sets-a-scene",
        digest=DIRECTOR_DIGEST, snapshot=DIRECTOR_SNAPSHOT,
        check=stages_in("cave_mouth"), n=5, threshold=3,
        desc="the director stages a well-formed ambient beat into the room "
             "where players are"),
]


async def run_director_scenario(provider, sc: DirectorScenario):
    successes, samples = 0, []
    for _ in range(sc.n):
        # Fresh mind (fresh memory) per trial, offered only its director actions —
        # the same subset the engine gives the real director.
        mind = DirectorMind(persona=DIRECTOR_PERSONA, provider=provider,
                            registry=default_registry(), offered=["stage_event"])
        try:
            turn = await mind.observe(sc.digest, sc.snapshot)
            ok = bool(sc.check(turn))
        except Exception as exc:
            samples.append((False, f"ERROR: {exc!r}"))
            continue
        successes += ok
        acts = ",".join(f"{a.name}{a.args}" for a in turn.actions) or "-"
        samples.append((ok, "SILENT" if turn.is_silent else f"[{acts}]"))
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
    if not chosen and not chosen_cmds and not chosen_dir:
        tags = sorted({t for s in SCENARIOS for t in s.tags}
                      | {t for s in COMMAND_SCENARIOS for t in s.tags}
                      | {t for s in DIRECTOR_SCENARIOS for t in s.tags})
        print(f"No scenarios match {selector!r}. Known tags: {tags}")
        return 2

    total = len(chosen) + len(chosen_cmds) + len(chosen_dir)
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

    print()
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        print("A verified behavior regressed. Fix it before moving on.")
        return 1
    print(f"OK — all {total} behavioral scenarios met their thresholds.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
