# Spike — TaleWeave AI (build-vs-adopt for Loom)

**Date:** 2026-07-11 · **Question:** should Loom adopt/fork
[TaleWeave AI](https://github.com/ssube/taleweave-ai) instead of building its own
engine — in particular the Phase 3 game-master director?
**Method:** full read of the actual source (66 Python files, ~8.2k LOC), claims
verified against code, not the README. Repo studied at the pinned public HEAD.

---

## Verdict

**Build. Keep Loom's from-scratch asyncio core. Steal patterns, not code.**

TaleWeave genuinely implements the hardest thing on our roadmap that we have not
built — a working **plan→act loop with a whitelisted action registry and a
retry seam** — and it is MIT-licensed, so a fork would be legally trivial. But it
does **not** implement the two capabilities Loom is most differentiated on, and
adopting it would force us to inherit exactly the three things Loom was designed to
avoid. The net of a fork is: take the one loop we could rebuild in a week, and pay
for it with a dependency core, a concurrency model, and a data schema that all
fight our design. Not worth it. The value here is a **pattern catalogue**, and it
is a rich one.

A second, unlooked-for result: TaleWeave independently **validates the constrained-
decoding decision** we just made. Its safety seam scrapes JSON out of free-text
model replies and repairs unbalanced braces by hand (`action.py:82-93`) — the exact
B6 failure mode. It has no grammar constraint anywhere at runtime. We are removing,
by construction, the class of bug their engine still carries.

---

## What TaleWeave is

An LLM-driven text-adventure / MUD engine, MIT (Copyright 2024 apextoaster LLC).
A world is generated from a prompt or hand-authored JSON; characters take turns in
a flat round-robin; each character plans, then acts, via LLM tool-calls dispatched
against a whitelisted registry. Transports: a WebSocket server and a full Discord
bot, decoupled from the engine by a `pyee` event bus. It is a coherent, runnable
**research prototype** — not a hardened platform (see *Maturity* below).

## 1. The "director" — there isn't one (flat round-robin)

The top loop runs **every registered system, every turn**, over a static character
order — no slow cadence, no world-observing director (`engine.py:143`):

```python
def simulate_world(world, systems, turns):
    for i in count():
        current_turn = get_current_turn()
        for system in systems:
            if system.simulate:
                system.simulate(world, current_turn)
        set_current_turn(current_turn + 1)
        if i >= turns: break
```

**Plan→act is real and is the best idea here.** Two separate core systems iterate
`world.order` identically: `simulate_planning` first (up to `planning_steps`, default
3, LLM steps against a *planning* toolbox — notes + calendar — ending on an "END"
keyword; skipped on turn 0 while memory is empty), then `simulate_action` (one action
per character against the *action* toolbox). A third core system, `summary`, has no
`simulate` — it is an event listener maintaining per-character digests.

**The "dungeon master" is a narrator, not a director.** It is invoked *synchronously
inside actions* (effect adjudication, combat outcomes, world/room/item generation).
It never runs on its own cadence, never observes the world between turns, and never
injects events. Quest auto-generation is an explicit stub — `# TODO: generate one new
quest` (`systems/rpg/quest/system.py:189`) does nothing. **So the exact thing we set
Phase 3 to build — a slow-cadence, world-observing director that injects quests/events
— does not exist in TaleWeave.** We would still be building it.

## 2. The safety seam — text→JSON + brace-repair + retry (no constrained decoding)

The model is *told by prompt* to "Reply with a JSON function call". The reply string
is then parsed by convention, with manual string surgery that proves it is raw text,
not a structured tool-call channel (`systems/core/action.py:76-93`):

```python
value = value.removesuffix("END").strip()
if '"action_ ' in value: value = value.replace('"action_ ', '"action_')   # whitespace hack
if value.startswith("{") and not value.endswith("}"):                     # brace-repair hack
    fixed = value + ("}" * (open_count - close_count)); loads(fixed) ...
```

Dispatch goes through `packit`'s `loop_retry(..., toolbox=action_toolbox)`: the parsed
`function` name is matched against a whitelisted `Toolbox` and the corresponding Python
function is called. **Validation is "call it and catch," not schema pre-validation** —
unknown names raise, handler exceptions become a natural-language error fed back for a
retry (default 5). A grep for `grammar|outlines|gbnf|response_format|tool_calls|
json_schema` across `taleweave/` returns **only** `schema.py:57`
(`TypeAdapter(model).json_schema()`) — an *offline CLI validator*, not the runtime.

**Assessment vs Loom.** TaleWeave's seam is directionally the same as ours — the model
can only dispatch named, registered functions, so it cannot execute arbitrary code —
but it is *weaker* than Loom's on two axes: (a) arguments are bound by Python call
semantics rather than a declared per-arg schema, and (b) the envelope is scraped from
free text with fragile repair heuristics, which is exactly the B6 leak. Our
grammar-constrained decoding (now *Phase 2 hardening*) makes that malformation
impossible. **This is the spike's strongest reinforcement of a decision already taken.**

## 3. Action & system model — clean extension seam, RPG-baked data model

An action is a plain Python function; its signature = parameters, its docstring =
the model-facing description (`packit` introspects both). Registration is by "action
group" via `add_extra_actions(group, [fns])`; a `Toolbox` is built per group. Behaviour
attaches through a **`GameSystem` hook contract** — `initialize / simulate / format /
generate / data(load,save)` (`game_system.py:64-88`) — and *this contract is genuinely
game-agnostic and worth studying*. Planning, action, summary, snapshot, hunger, hygiene,
weather, quests are all `GameSystem`s loaded by dotted path.

**But the core data model is not game-agnostic.** The entity types bake in RPG/adventure
concepts — `Room`, `Portal`, `Character(backstory, items, active_effects, attributes)`,
`Item` (`models/entity.py`). The loop is generic; the schema is not. A fork inherits
rooms/portals/inventory/effects whether the game wants them or not.

## 4. World & memory model — no memory stream

**World:** editable JSON (pydantic dataclasses), generated from a prompt *or* hand-
authored, persisted as JSON every turn (`<world>.state.json`). Open `attributes` bag
per entity for systems to read/write. Portal linking at generation is `choice(...)`
random with a `# TODO` to have the DM choose destinations.

**Memory:** there is **no Generative-Agents memory stream, no embeddings, no retrieval**
(grep for `embed|vector|faiss|chroma|retriev|rag|similarity` → nothing). Memory is three
concrete, code-simple things: a `deque(maxlen=25)` rolling window of chat messages;
self-authored **notes** (`take_note/read_notes/edit_note/summarize_notes`); and a
**calendar** (`schedule_event/check_calendar`). Persona = the character's `backstory`
string used verbatim as the system prompt. Cross-character awareness comes from the
**digest** system — a per-character buffer of others' actions, rendered as a second-
person "what everyone else did since your last turn" summary.

So Loom's memory-stream substrate (recency × importance × relevance, reflection,
shared by players and NPCs) is **not** something we could inherit — it isn't here.

## 5. Backend, transport, maturity

- **Backend:** talks to models through `packit` → LangChain; provider swappable by env
  (`PACKIT_DRIVER=ollama`, `PACKIT_MODEL`, optional OpenAI-compatible endpoint —
  vLLM/OpenAI documented). **No fake/offline provider** — nothing can test without a
  live model.
- **Transport:** cleanly decoupled via a `pyee` event bus. WebSocket server + a real
  (~400 LOC) Discord bot. Notable polish: **human-takeover of any NPC** —
  `RemotePlayer` replaces a character's LLM agent and falls back to the LLM on
  timeout/disconnect (`player.py:185-236`).
- **Maturity — research prototype, not a platform:**
  - **Tests: effectively none.** The entire suite is `tests/utils/test_search.py`
    (2 cases on `find_room`). Core loop, seam, planning, memory, transport: untested.
  - **Dependencies: heavy.** Core imports `packit` (single-maintainer, git-pinned, not
    on PyPI) wrapping an **old** LangChain (`langchain-core 0.1.50`, pre-0.2), plus
    pydantic, pyee, Jinja2, rule-engine, discord.py, graphviz, pillow, numpy. The core
    is **not** dependency-light.
  - **Architecture: synchronous + global singletons.** World/room/character/agents/
    systems all live as module-level mutable globals (`context.py:30-44`); the loop
    blocks; concurrency is threads. This is the **deepest mismatch** with Loom's asyncio
    design.
  - **Debt:** ~39 TODO/FIXME markers, fragile JSON-repair, stubbed director/quests, and
    a missing `systems/core/__init__.py` that breaks the *documented default launch* as
    committed.

## What Loom would inherit by forking (the case against adoption)

1. **`packit` + LangChain in the core** — directly kills Loom's "dependency-free core".
   To excise it we'd rewrite the seam and the provider — at which point little of
   TaleWeave remains.
2. **Synchronous, global-singleton architecture** — fights Loom's asyncio, non-global
   design at every turn; not reentrant.
3. **RPG concepts baked into the entity models** — rooms/portals/inventory/effects.
4. **~0 core tests + ~39 TODOs + a broken default launch** — we'd adopt the debt and
   the burden of verifying a loop we didn't write.

License (MIT) is the *only* dimension that poses no obstacle to a fork — but the other
four make the fork cost more than the from-scratch path it would replace.

## Patterns worth stealing (do adopt these ideas)

1. **Two toolboxes, two phases.** Planning tools (note-taking, `schedule_event`,
   `check_calendar`) separate from world-mutating action tools, run as two passes.
   Directly relevant to Loom's director cadence and to B5 (a cheap "should I act?"
   pass distinct from the in-character turn).
2. **Memory-as-tools.** `take_note / edit_note / summarize_notes` + a calendar give
   controllable, *inspectable* long-term memory **without embeddings** — a cheap
   complement or bridge to Loom's memory stream (Phase 5) that we can ship far earlier.
3. **The turn "digest".** Per-character buffers of others' actions, rendered as a
   second-person "what happened since your last turn" summary — an elegant answer to
   shared situational awareness on a round-robin. Compare to Loom's per-room broadcast.
4. **The `GameSystem` hook contract** (`initialize / simulate / format / generate /
   data`) with `format` injecting attribute-derived labels (hunger, mood) into prompts.
   A clean, game-agnostic extension seam — a reference for how Loom lets a *game* add
   systems without editing `loom/`.
5. **Declarative data-driven systems** (`systems/generic/logic.py`): YAML rules
   (`match/set/remove/trigger/chance` + expressions) so hunger/weather/mood are authored
   without code. A model for Loom's "world is editable data" commitment beyond content.
6. **Pub/sub transport + human-takeover-of-any-NPC** with an LLM `fallback_agent`.
   A concrete pattern for Phase 6 multiplayer and for "possess an NPC" play.
7. **Prompt library as external, model-family-specific YAML** merged by key at load —
   swap prompts per model without touching code. Relevant as our prompt surface grows.

## Decision & next actions

- **Decision:** do **not** adopt or fork TaleWeave. Continue Loom from scratch. Rationale
  above; the differentiators (memory stream, slow-cadence director) aren't in TaleWeave,
  and the fork cost is higher than the build cost it would replace.
- **Immediate:** proceed with *Phase 2 hardening — constrained decoding* (already in
  `docs/PLAN.md`). The spike reinforces it: TaleWeave still carries the B6 bug.
- **Phase 3 design input:** adopt the **two-phase plan→act split** and the **turn-digest**
  pattern when designing the director. Consider **memory-as-tools** (notes/calendar) as an
  earlier, cheaper step toward Phase 5 than embeddings.
- **Cross-cutting:** hold the **`GameSystem`-style hook contract** as the reference model
  when we formalise how a game registers systems against `loom/` (framework-first mandate).

## Appendix — claims verified directly against source (2026-07-11)

| Claim | Evidence (file:line) | Verified |
|---|---|---|
| Flat round-robin, every system every turn | `engine.py:143` `simulate_world` | ✓ |
| No constrained decoding at runtime | only hit is `schema.py:57` (offline CLI) | ✓ |
| JSON scraped from text + brace-repair | `systems/core/action.py:82-93` | ✓ |
| Global-singleton state | `context.py:30-44` (`current_world`, `dungeon_master`, …) | ✓ |
| No memory stream / retrieval | grep `embed|vector|chroma|retriev|rag` → none | ✓ |
| Quest generation is a stub | `systems/rpg/quest/system.py:189` `# TODO` | ✓ |
| MIT license | `LICENSE.md` (2024 apextoaster LLC) | ✓ |
| Tests cover only a util | `tests/utils/test_search.py` sole test file | ✓ |
