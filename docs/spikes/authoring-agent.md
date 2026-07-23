# Spike — the authoring agent (Phase 8, slice 3): build a world by asking

*Opened 2026-07-22. Status: **BUILT & both-gate green 2026-07-23.** Offline 719 (the pure gate
+ the agent loop on Pydantic-AI's `FunctionModel` + the chat UI smoke, all zero-network); live
`author-agent` 2/2 + 2/2 on `qwen/qwen3.6-27b` (the real model grounds, picks the tool, and
stages a change that surveys clean). Forks all signed off 2026-07-22.*
The third and largest slice of the authoring workbench (see `docs/spikes/workbench.md`; slice 1
= the Explorer, slice 2 = `docs/spikes/play-in-editor.md`). The Explorer let an author *see* a
world and play-in-editor let them *stand in it*; this lets them **change it by asking** — a
natural-language chat that grounds a request against the world, calls tools to read and to
author, validates every mutation on a **shadow copy**, shows a diff, and merges into the live
world **only on confirmation**. The write half of the workbench; the live gate deepens here.*

## Why

The Phase 7 write side works but its surface is a string of CLI incantations
(`scripts/author.py "…" --attach hilltop --out …`, `scripts/atlas.py`, hand-edited JSON). The
Phase 8 ask named the cure: an **agent chat** that inspects a zone or NPC and edits / generates
/ tests from natural language, hiding the tools beneath it. Everything the agent *does* already
exists as pure, tested capability (survey, author, assemble, search, sandbox); this slice is the
**agent that calls them**, the **safety gate** that stands between it and the live world, and the
**chat** it speaks through.

## The reframe (user decision 2026-07-22) — a separate stack, not the engine's waist

The engine's minds run through Loom's slim `LLMProvider` (`loom/ai/provider.py:30`): a single
schema-constrained-JSON call, deliberately dependency-light, offline on `FakeProvider`. That waist
is right for an NPC's one-shot turn and **wrong for a multi-step authoring agent**. Signed-off
decision: **the authoring agent is a separate stack.** It does *not* reuse `loom.ai.provider`; it
uses **native tool-calling** through its own model client, is free to pull heavier dependencies,
and adopts an off-the-shelf agentic framework. The split:

- **`loom/` keeps only the pure, game-agnostic CAPABILITIES and the SAFETY GATE.** Dependency-light,
  offline-testable on zero LLM. **The validate-on-a-shadow-copy invariant lives here, in tested
  code — never in the agent framework's good behaviour.**
- **`authoring/` hosts the agent** — its own native-tool-calling client, the framework, the tool
  *adapters* that wrap the `loom/` capabilities, the ReAct loop, the confirm/diff/apply/undo
  orchestration, and the Textual chat. Heavy deps are fine here (it is already home to Textual).

So the tools' *bodies* stay pure and tested; the *agent that calls them* is unconstrained. The
offline gate still exercises the gate + capabilities with no model at all; the live gate exercises
the agent end-to-end.

## The reuse map (in-repo recon, cited) — the tools are wrappers

Almost every tool body already exists. The agent tools are thin adapters over these:

- **`region_author` is already a validated shadow-apply.** `author_region`
  (`loom/ai/author.py:206`) runs plan → frame → flavour → survey → a bounded repair loop and
  returns an `AuthorResult(ok, region, attach, view, rounds, reason)` where `ok` is true iff the
  assembled candidate world surveys with **zero errors** — it builds throwaway candidate worlds to
  validate and *never touches the live one*. The safety gate for authoring a whole region comes for
  free.
- **The shadow-copy build.** `assemble(region, base=, attach=)` (`loom/authoring.py:417`)
  deep-copies its inputs and returns a fresh candidate `World`; `world_to_dicts`
  (`loom/authoring.py:377`) serialises a live world back to plain `world.json` dicts; `frame_skeleton`
  (`loom/authoring.py:238`) / `apply_flavour` (`loom/authoring.py:334`) are the structure-by-construction
  substrate the field-patch tools reuse.
- **The judge.** `survey(world, start, source=)` (`loom/atlas.py:125`) → an `AtlasView` with
  `.errors` / `.warnings` / `.summary()` and a list of `Finding(severity, code, where, message)`
  (`loom/atlas.py:38`) — the same structured findings the write-side repair loop already reads,
  surfaced to the agent as remediation prose.
- **`world_search` + grounding.** `search(view, query, *, scope, kinds, tag, limit)`
  (`loom/explore.py:183`) ranks every entity; `location_index` (`loom/explore.py:152`) and
  `map_model` (`loom/explore.py:93`) resolve "this NPC / the north wing" to concrete ids.
- **`preview_play`.** `Sandbox(world|from_path, room_id, provider, emit)` (`loom/sandbox.py`, slice 2)
  — boot the real engine at a room on a throwaway deep copy and test a change before committing it.
- **The save path.** `_combined_world(base, start, region, attach)` (`scripts/author.py:61`) shows
  how a region folds into a standalone `world.json`; the explicit "save" reuses that fold.

## New machinery to build in `loom/` (pure, offline-tested)

The one genuinely new pure surface — a staging + gate module (working name `loom/worlddraft.py`):

- **`WorldDraft`** — the *staged* world as dicts + a **multi-level checkpoint stack**. Loaded from
  `world.json`; the live authored file is untouched until an explicit save.
- **`shadow_validate(draft, mutation) -> (ok, view, candidate)`** — build a candidate from the
  staged world + the proposed mutation (a region from `author_region`, a field patch from
  `entity_edit`, or a new entity from `entity_spawn`), `survey` it, return the verdict. Pure; the
  live/staged world is never touched by a validation.
- **`apply(draft, candidate)`** — push the current staged world onto the checkpoint stack, then
  fold the candidate in. Only ever called after `ok` **and** a human confirm. **`undo(draft)`** pops.
- **`entity_edit` / `entity_spawn` bodies** — a field patch on an existing entity (name /
  description / persona / aliases / an exit) and a new NPC/item placed on a real room/holder (id
  minted via the code-owned namespace), each routed through `shadow_validate` so a dangling exit or
  a bad holder is caught on the copy, never merged.

## Prior art (two research sweeps, 2026-07-22 — cited)

**Sweep A — the loop, the safety pattern, the tool catalogue** (agent-side, to the loot-spike bar):

- **ReAct** (Yao et al. 2022, [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)) is prompt-level,
  not API-level: interleave thought → tool → observation, feed the observation back. Grounding tool
  output into context is what suppresses error propagation.
- **Validator-gated apply on a shadow copy + diff + checkpoint** is the field consensus:
  **Cursor's shadow workspace** (lint on a hidden copy, surface a diff only when clean —
  [cursor.com/blog/shadow-workspace](https://cursor.com/blog/shadow-workspace)); **Aider's**
  architect/editor split + git-backed undo *stack* ([aider.chat/docs/git](https://aider.chat/docs/git.html));
  **LangGraph** read-free / write-gated human-in-the-loop
  ([docs.langchain.com](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)). Reads
  auto-run; writes/merges always confirm via diff; **never trust a model's self-declared "safe"
  flag** — gate on the deterministic validator (our atlas survey).
- **Few, coarse, workflow-named tools returning structured, human-readable findings** beat
  fine-grained CRUD (Anthropic,
  [writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)):
  consolidate, prefer semantic ids over UUIDs, make errors actionable prose.
- **Grounding:** feed the current UI selection + a world-search hit as explicit context so referents
  bind to ids deterministically; **echo the resolved referent** in the diff; clarify only on genuine
  ambiguity.

**Sweep B — the framework** (a ranked survey; full brief in memory):

- **`qwen/qwen3.6-27b` is a real OpenRouter slug** with **native OpenAI-format tool-calling** — the
  denser "agentic" Qwen tier, positioned for "fewer broken tool calls" than 3.5. Native tool-calling
  is genuinely available on our exact model.
- **Pydantic-AI ranked best fit**, ahead of the OpenAI Agents SDK and LangGraph, on: native
  tool-calling over an arbitrary OpenAI-compatible `base_url` (first-class OpenRouter provider);
  tools as typed Python functions whose args are **Pydantic-validated before our code runs**;
  async; a **library, not a framework**; and — the axis we weighted heaviest — the **best offline
  fake-model story** (`TestModel` / `FunctionModel` + `agent.override(model=…)` drive the *whole*
  agent deterministically with **no network**). Its **deferred-tools / approval** flow lands exactly
  on "pause before a WRITE merges, show the diff, resume on confirm."
- **None of the top candidates forces any compromise of the shadow-copy / zero-errors /
  human-confirms invariant** — the framework only orchestrates; our pure gate stays the sole
  authority over a merge. LangGraph's headline HITL edge is **redundant** here for exactly that
  reason.
- **Gotcha:** bound *thinking* on tool-emitting turns (a reasoning model can otherwise route the
  call into its reasoning channel and silently no-op — the same failure the engine's providers
  already guard with `reasoning:{enabled:false}`); be ready to **pin OpenRouter providers** that
  support tools. Verify `tool_choice` / parallel calls / streaming empirically.

## Decisions — signed off 2026-07-22 (via AskUserQuestion)

1. **Transport → native tool-calling, through a separate authoring-only client** — not the engine's
   schema-constrained-JSON envelope. *(Rejected: reusing `loom.ai.provider` and hand-rolling a
   constrained-JSON ReAct loop — proven elsewhere in Loom, but the wrong fit for a multi-step agent,
   and it would conflate two different concerns.)*
2. **Framework → Pydantic-AI.** *(Runner-up: the OpenAI Agents SDK — thinner, but we'd hand-roll
   both the fake model and the confirm pause. Third: LangGraph — heaviest, and its HITL edge is
   redundant against our own gate.)*
3. **Agent model → the GM/director tier `qwen/qwen3.6-27b`** over OpenRouter (standing OpenRouter-only
   rule holds), thinking bounded on tool turns. *(Rejected: the NPC tier `qwen3.6-35b-a3b` — tuned for
   short in-character turns, not multi-step planning.)*
4. **Apply UX → always confirm, showing a diff.** Read is free (survey / search / preview auto-run);
   **every write is gated** — even a clean shadow survey shows the diff and waits. *(Rejected:
   auto-apply-when-clean — removes the human gate on writes.)*
5. **Persistence → stage in memory + explicit save.** Apply folds into the in-memory `WorldDraft`;
   a separate explicit save writes `game/world/world.json`. Mirrors the play-in-editor isolation
   (the engine never writes; `author.py` writes only on `--out`). *(Rejected: write-on-apply —
   every apply mutates the authored file.)*
6. **Catalogue → the fuller cut:** reads `zone_survey` / `world_search` / `preview_play`, plus
   `region_author`, `world_revalidate`, **`entity_edit`**, **`entity_spawn`**. *(The two write-side
   newcomers need the new field-patch → shadow-survey machinery above.)*
7. **Adopted without a vote:** undo = a **multi-level in-memory checkpoint stack** (not a single slot,
   not git-backed — cheap over staged dicts); atlas findings surfaced to the agent as **human-readable
   remediation prose**, not tracebacks.

**Architecture (the standing invariant, not a fork):** the pure capabilities + the shadow-validate-
apply-with-checkpoint gate live in `loom/` (offline-testable, dependency-light, the safety authority);
the agent, its native-tool-calling client, the framework, and the Textual chat live in `authoring/`.
`loom` never imports Pydantic-AI or Textual. Pydantic-AI joins the **`authoring` optional extra**
(with Textual), never core.

## The design

- **The tool catalogue** — few, coarse, workflow-named typed Python functions, each wrapping a
  `loom/` capability and returning structured prose. **Reads run free:** `zone_survey` (atlas survey
  of a zone), `world_search` (`explore.search`), `preview_play` (`Sandbox`). **Writes are deferred
  (Pydantic-AI approval boundary):** `region_author` (`author_region`), `entity_edit`, `entity_spawn`
  each run `shadow_validate` and, if clean, return a **proposed diff** and *wait*; `world_revalidate`
  surveys the whole staged world. A write applies to the `WorldDraft` only after the human confirms.
- **The loop** — Pydantic-AI's `Agent` runs the ReAct loop natively over `OpenRouterModel(
  'qwen/qwen3.6-27b')`. We supply one comprehensive system prompt (a **single rule set**, not
  per-model), the tool set, the grounding context (current workbench selection + a world summary),
  and the deferred-tool approval handling. The loop is bounded (max turns) against repair spirals.
- **Grounding** — the workbench's open zone / inspected entity id is fed in as explicit context so
  "this NPC" binds deterministically; the resolved referent is **echoed in the diff** ("smithy will
  attach east of *market_square*"); a clarifying question is asked only when a required arg or
  referent is genuinely ambiguous, else the agent proposes a diff.
- **The chat** (`authoring/`) — a Textual chat pane beside the Explorer: the transcript, the agent's
  proposed diffs, and the confirm/reject control; `save` and `undo` bindings. Play-in-editor (slice 2)
  is reachable from a proposal to feel a change before confirming it.

## The build plan — ALL STEPS BUILT (2026-07-23)

As built: (0) `pydantic-ai-slim[openai]` added to the `authoring` extra + a live native-tool-call
probe (PASS — `qwen/qwen3.6-27b` emits a clean tool call, thinking bounded); (1) `loom/worlddraft.py`
+ a public `loom.authoring.mint_id` + `tests/test_worlddraft.py` (16); (2) `authoring/agent.py` +
`tests/test_agent.py` (9); (3) `authoring/chat.py` + the Explorer's `a` binding in `authoring/app.py`
+ `tests/test_chat.py` (3); (4) the `author-agent` family in `scripts/behavior_probe.py`, live 2/2 +
2/2. Offline suite 719 green.

**Post-build UX refinements (2026-07-23, from live testing — offline 731 green, live re-verified).**
Several rounds of real authoring surfaced rough edges, all fixed:
- **Copyable transcript + live progress + refresh.** The chat transcript is a read-only `TextArea`
  (not `RichLog`, which cannot be selected on Textual 8.2.8) — selectable + `ctrl+c` copy (OSC-52),
  with a `ctrl+l` **save-log** (`App.deliver_text`). Live tool activity streams in as `⏳/✓` via
  Pydantic-AI's `event_stream_handler` (`run_turn` degrades to a plain turn if a backend can't stream).
  The Explorer shares the staged `Draft` and re-surveys when it repaints.
- **The chat is an EMBEDDED left-pane panel, not a full-screen modal** (`ChatPanel`, toggled with `a`),
  so the inspector on the right stays visible while authoring; an Apply repaints the inspector at once.
  Controls are discrete + contextual (apply/discard appear only when a change is pending). The Explorer's
  agent imports are lazy, so the read-only Explorer needs only Textual.
- **The agent-loop fix.** `entity_edit` was name/description only, so a "give this npc a backstory"
  request had no valid move and the agent read in circles until the request-limit. It now edits an npc's
  persona (backstory / voice / traits / goals / disposition + wanders), merged; the step budget rose
  8→20; the prompt tells the agent to ground once and act.
- **Direct hand-editing in the inspector** (`e`) — the deferral is lifted, the apply-gate being proven.
  A uniform edit form (border-titled, wrapping, auto-height fields; booleans as checkboxes) edits
  name/description, an npc's full persona + wanders, and an item's aliases + portable — every apply
  routed through the SAME `loom.worlddraft` gate as the agent (validate on a shadow copy, commit with a
  checkpoint, refuse a change that would break the world); `ctrl+s` saves the world to disk. The id and
  structural graph edits (exits, an entity's location/holder) stay agent-territory by design.

The original ordered plan follows.

0. **De-risk the foundation first — a ~10-line live probe.** Add Pydantic-AI to the `authoring`
   extra; confirm `qwen/qwen3.6-27b` emits a *native* tool call through Pydantic-AI over OpenRouter
   with thinking bounded, before any loop is wired. If the model or a provider won't tool-call
   cleanly, we learn it now, not after the agent is built.
1. **The pure gate in `loom/`** — `WorldDraft` + `shadow_validate` + `apply`/`undo` + the
   `entity_edit` / `entity_spawn` bodies. Offline-tested with **zero LLM**: a dirty candidate is
   rejected *without* mutating the staged world; a clean one applies; the checkpoint stack push/pops;
   `region_author` folds in through the same gate.
2. **The agent in `authoring/`** — the tool adapters + the Pydantic-AI `Agent` + the loop.
   Offline-tested with `FunctionModel` (scripted tool calls, no network): the loop dispatches,
   read-free / write-gated holds, ambiguity clarifies, the bounded turn cap holds, a deferred write
   surfaces a diff and applies only on confirm.
3. **The chat pane** (`authoring/`) — the Textual surface + confirm/diff/undo/save, with a
   `run_test()` + `Pilot` smoke pass.
4. **The live gate** — `scripts/behavior_probe.py` gains an `author-agent` family: an NL request
   ("add a smithy east of the market with a gruff blacksmith") drives the agent to author + validate
   a change that surveys clean.

## Both gates

- **Offline** — the pure gate and capabilities on **zero LLM** (`loom/` tests), plus the agent loop
  on Pydantic-AI's `FunctionModel` / `TestModel` (`authoring/` tests, no network), plus a Textual UI
  smoke. `python -m unittest discover -s tests`.
- **Live** — `scripts/behavior_probe.py author-agent` on `qwen/qwen3.6-27b` over OpenRouter
  (`set -a && . ./.env && set +a && LOOM_OPENROUTER_MODEL=qwen/qwen3.6-27b …`). OpenRouter is
  burst-noisy — re-run for a clean pass.

## Deferrals

- **Direct field-editing in the inspector** (vs agent-mediated) — the agent's apply-gate first.
- **Git-backed undo / branching history** — the in-memory checkpoint stack first.
- **`textual serve` browser, multi-author, reference-spawns-node** — after the spine.
- **A second model for the "editor" half of an architect/editor split** — one agent tier first;
  split only if planning and mechanical editing measurably want different models.
