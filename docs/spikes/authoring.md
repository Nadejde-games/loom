# Spike — AI-assisted world authoring (B7 write side): brief → a valid world

*Opened 2026-07-21. Phase 7, slice 2 (the write side; the atlas — `94e9635` — was slice 1,
its read/validate half). Status: **BUILT & both-gate green 2026-07-22**. Decisions: (1)
**skeleton-first** — code generates the graph, the model authors only flavour; (2) first
slice **incremental extend-mode** — a new region merged into the existing world; (3) authoring
model the **GM tier `qwen/qwen3.6-27b`**; (4) brief is prose with optional light hints.*

## Why

The atlas answers *"is this world sane?"*. This slice answers *"make me one."* An author —
a person with a brief, or the game itself wanting to grow — describes a place, and the write
side turns that into a **valid `world.json`**: rooms with reciprocal exits, NPCs with
personas, items, wired into the schema `loom/content.py` loads. The strategic goal, agreed
2026-07-21, is to **grow the tiny 3-room / 2-NPC world so the deep sim (director · reflection
· idle-NPC · loot) is fed** — more places to lull, more minds to reflect, more room to roam.

The atlas is not just the reader here; it is the **judge**. Its `Finding{severity, code,
where, message}` list is the same signal a human reads *and* the write-side loop consumes to
know whether what it just wrote holds together.

## Prior art (two sweeps, 2026-07-21)

Two research passes were run to the loot-spike bar (reference-level sources, per-claim
verification tables). The full tables live with the sweeps; the load-bearing findings:

### A. The generation side — *code owns the graph; the model dresses it*

The evidence converges hard on **skeleton-first, staged** authoring, and it pushes the golden
rule one step further than the atlas did: not merely *validate* structure, but **generate**
it in code.

- **Staged beats one-shot, measured.** *Word2World* (Nasir et al. 2024,
  [arXiv:2405.06686](https://arxiv.org/abs/2405.06686)) turns a story into a playable world
  with no fine-tuning by generating in passes (environment first, then characters/objects);
  its own ablation shows **one-step whole-world generation is significantly worse**, and an
  **A\*** agent *code-verifies reachability* — the LLM authors flavour and layout, the
  algorithm is the gate (~90% playable).
- **The academic IF precedent already splits structure from flavour** — and shows the failure
  we must design against. *Bringing Stories Alive* (Ammanabrolu et al. 2020,
  [arXiv:2001.10161](https://arxiv.org/abs/2001.10161)) runs **(1) knowledge-graph
  construction — locations/characters/objects as vertices, connections as edges — then (2)
  "flavortext" generation.** Crucially it **does not enforce reciprocal exits**; its graph can
  be asymmetric. The "north leads somewhere whose south doesn't come back" bug appears even in
  a careful academic system → **reciprocity must be enforced by code, never hoped for.**
- **Every robust procedural generator guarantees connectivity by construction or a graph
  pass, never by trusting emission.** Connectivity-by-construction — grow each new room onto
  the existing map so *every square is reachable by build order* (RogueBasin); BSP + Delaunay
  + **minimum spanning tree** to guarantee all rooms connect; **Dormans graph-grammars** —
  *mission graph (with lock/key access levels) first, space layout second*, solvability
  enforced at the graph layer before any geometry. And the cautionary pair: cellular-automata
  and **Wave-Function-Collapse** are strong locally but *"struggle with global
  solvability/connectivity"* and need a post-gen repair pass — the exact profile of
  *let-the-model-emit-a-map-then-repair*.
- **Reciprocity is a decades-old MUD bug.** DikuMUD's own builder manual warns you must define
  the exit in **both** rooms or "you will end up with a one-way direction." Hand-authored
  worlds shipped one-way exits *because reciprocity was a convention, not an enforced
  invariant.* Code should emit reciprocal edge **pairs** so the model can never desync them.

### B. Constrained decoding — buys *syntax*, not *semantics*; and a real tax at scale

- **Schema-constrained decoding guarantees shape, not meaning.** A grammar guarantees an
  `exits` field is a string→string map; it *cannot* express "this target id names a room that
  exists" or "the graph is connected" — exactly the atlas's `dangling-exit` / `unreachable-
  room`. So the division of labour is settled: **constrain the syntax, validate-and-repair the
  semantics.**
- **The genuine "constraint tax" is coverage and speed on large nested schemas — the
  monolithic whole-world call is the worst case.** *JSONSchemaBench*
  ([arXiv:2501.10868](https://arxiv.org/abs/2501.10868)) measured coverage **collapsing 96% →
  ~30% as schemas grow**, with compile-time and throughput penalties to match. Small
  per-entity schemas sit near 100%. This independently condemns a single giant world schema
  and favours **many small, well-scoped calls.**
- **Keep reasoning *out* of the constrained span.** "Let Me Speak Freely?" (Tam et al., EMNLP
  2024, [arXiv:2408.02442](https://arxiv.org/abs/2408.02442)) measured reasoning dropping hard
  when the model must think *inside* rigid JSON (GSM8K 76.6→49.3); dott×t's matched-prompt
  rebuttal and JSONSchemaBench show structured decoding is *fine-to-helpful* for pure
  format/selection. Reconciled: **the model may reason in prose, then serialize under
  constraint** — never reason about topology while constrained. Loom's golden rule already
  sits on the safe side (the model authors flavour, not structure).

### C. The repair loop — external-validator repair is *sound* (where self-correction is not)

- **The crux.** LLMs *degrade* when grading their own reasoning with no external signal (Huang
  et al., ICLR 2024, [arXiv:2310.01798](https://arxiv.org/abs/2310.01798)) — but self-
  correction *works* when the feedback is a **reliable external signal** (Kamoi et al., TACL
  2024, [arXiv:2406.01297](https://arxiv.org/abs/2406.01297); tool-feedback methods CRITIC,
  Self-Debugging [arXiv:2304.05128](https://arxiv.org/abs/2304.05128)). The atlas is that
  signal — the deterministic oracle those methods usually lacked. **Invariant: the model never
  judges correctness; only the atlas says "broken."**
- **Cap at 3 rounds.** The first round dominates; **two rounds capture 76–95% of achievable
  gains** (Arimbur 2026, [arXiv:2604.10508](https://arxiv.org/abs/2604.10508)). Stop on:
  zero-errors → success; **non-progress** (finding-set unchanged or count not strictly
  falling) → stop; cap reached → stop. On any stop-without-clean, **fall back to the last
  error-free snapshot** (greenfield with no clean prior: surface the residual findings, do not
  ship a broken world).
- **Patch the flagged entities; do not regenerate the world.** The atlas `where` is a fault
  localizer; whole-doc regeneration is the mechanism behind *thrashing* (fixing A re-breaks
  B). **Freeze what validated, repair only what's named, re-run the *full* atlas each round.**
  Give the model the **full report** every round (not one finding), and rich localized
  findings (`code`+`where`+`message`) — the highest-leverage lever (Self-Debugging).
- **Extend-mode = mixed-initiative authoring.** For growing an existing clean world, freeze it
  as constraints; the model may add/link only *new* entities, never mutate blessed ones;
  validate **globally** (a new region can break global reachability) but repair **narrowly**;
  report infeasibility explicitly rather than degrading the old world. This is the settled
  pattern in Tanagra (constraint-solve around frozen designer geometry), Inform 7's Problems
  panel, and Twine's broken-link flagging.

## The design

**The seam, sharpened.** The loot forge put the model on the flavour side of a *code-rolled
brief*. The write side is the same seam at world scale:

> **Code owns the graph — ids, connectivity, reciprocal exits, holder/location references.
> The model authors only flavour — names, descriptions, personas, item lore — per entity,
> under a small schema. The atlas + a bounded repair loop is the semantic safety net.**

**A new module `loom/ai/author.py`**, the sibling of `loom/ai/loot.py` and `intent.py`:
world-free, handed a brief and a small schema, tolerant parse, degrade-and-surface on failure
(never raises). A thin CLI `scripts/author.py` drives it (brief in → validated `world.json` or
region file out; exits non-zero if it cannot reach a clean world). The pure structural half —
the skeleton framer, schemas, and repair helpers — lives in `loom/authoring.py`; offline tests
in `tests/test_authoring.py`.

**The pipeline (the recommended architecture — skeleton-first, staged):**

1. **Plan (model, reason-in-prose → small schema).** The author proposes a *region plan* — a
   handful of rooms (slug + role), their intended adjacencies, NPC roles, item roles. This is
   the model reasoning about *what the place contains*, not emitting final structure. Directions
   are a *proposal*.
2. **Frame the skeleton (code).** Code turns the plan into a **guaranteed-valid graph**: assign
   canonical ids (a code-owned namespace — the model never invents an id), write **both
   half-edges** for every connection (reciprocity by construction), attach the region to the
   existing world through one real exit (connectivity by construction), place NPCs/items on
   real ids. Cross-references are impossible to dangle because code writes them.
3. **Flavour (model, per-entity, small schema — `author_flavour` generalized).** For each
   room / NPC / item in the code-built skeleton, one small constrained call authors the words:
   a room's description, an NPC's `{backstory, traits, goals, voice}`, an item's lore. Ids and
   structure are fixed; the model writes only strings. Near-100% schema coverage; no topology
   under constraint.
4. **Assemble + validate + repair (code).** Merge into the world (or a new region file), run
   `atlas.survey`. Structure was code-built, so structural errors should be *zero*; the repair
   loop (cap 3, targeted patch, freeze-clean, full-report-each-round) mops up any residual —
   chiefly *flavour* findings (a thin persona, a description that names something absent).

**Where the model reaches the model.** `author.py` takes a `provider`. Two provider concerns:

- **Token budget.** `provider.complete` defaults to `max_tokens=400` — fine for a one-line NPC
  turn, far too small for authored prose. The CLI constructs its own provider instance with a
  large budget; per-entity flavour calls are individually small, which keeps every call well
  inside a sane budget (another point for staging over one monolithic call).
- **The authoring model.** Authoring is wide-context creative work, like the director — the
  **denser GM tier (`qwen/qwen3.6-27b`, the `loom-gm` variant)** is the natural candidate over
  the NPC tier. Runs on OpenRouter like everything else. (Sign-off fork below.)

**Greenfield vs incremental.** The loader already merges a **directory** of region files
(`content.py`), so a new region is a new `*.json` merged in with no engine change — incremental
extension is nearly free structurally, and it is *exactly* the mode the repair research says is
soundest (existing graph anchors the model, new edges are few and boundary-checkable). It also
directly serves "feed the sim." Greenfield (a whole new world from a blank brief) is the
cleaner self-contained proof but the topologically harder, more fragile case.

## Decisions — signed off 2026-07-22

**Decision 1 — the architecture seam → (A) Skeleton-first.** Code generates the guaranteed-
valid graph (ids, reciprocal exits, connectivity, holder/location refs); the model authors
only flavour per entity. Most robust; most code; the model shapes topology by *proposing* it
in prose, code realizes it. The research's strongest endorsement. *(The rejected alternatives:
(B) generate-then-repair — simplest but fragile on global connectivity, sound only in extend-
mode; (C) hybrid-lite — model emits structure+flavour, code owns the load-bearing invariants.
(A) is where extend-mode grows toward greenfield without moving the seam.)*

**Decision 2 — first slice → incremental extend-mode.** A new region merged into the existing
world through one code-written attach edge. Feeds the sim; the research-soundest home for the
loop; the existing graph anchors the model. Greenfield follows once the seam is proven — the
framer is written to serve both (greenfield = no anchor, the entry room becomes `start`).

**Decision 3 — the authoring model → the GM tier (`qwen/qwen3.6-27b`).** Authoring is wide-
context creative work, like directing. One comprehensive rule set, not a per-model profile.

**Decision 4 — the brief → prose with optional light hints.** The model plans from prose;
hints (a rough room count, a seed NPC/item) only bias.

## Slice as built

- **`loom/authoring.py`** (framework, pure, zero deps) — `frame_skeleton` (the code-owned
  graph: ids, reciprocal exit pairs, connectivity, attach edge), `normalize_plan`,
  `apply_flavour`, `assemble`/`world_to_dicts`, the `plan_schema`/`flavour_schema` grammars,
  and the repair helpers (`repair_targets`, `targets_signature`, `stalled`, `distinct_where`,
  `format_report`).
- **`loom/ai/author.py`** (world-free model layer, sibling of `loot.py`/`intent.py`) —
  `propose_plan`, `author_flavour`, and `author_region` (plan → frame → flavour → survey →
  bounded repair; cap 3, targeted re-flavour, stop on clean/stall).
- **`scripts/author.py`** — the CLI (extend-mode default, GM tier + a large token budget,
  non-zero exit unless the region surveys clean; `--out` writes the combined world).
- **`tests/test_authoring.py`** — 28 offline tests.

**Both gates green (2026-07-22).**
- **Offline** — **639** (611 + 28). The framer's structural guarantees asserted directly
  (reciprocity, code-owned ids never colliding, orphan-connectivity, attach reciprocity,
  placement); `apply_flavour` touches only flavour; the full `author_region` loop against
  scripted providers — happy path (0 rounds, clean), graceful degrade (no plan → clean
  failure, not a crash), a forced repair round that clears thin-content, and the stall guard
  terminating on a never-improving provider. No model judgement, no network.
- **Live** (`scripts/behavior_probe.py author`, `qwen/qwen3.6-27b`): **2/2**. The GM model
  authored a 6-room and an 8-room region, each surveying **0 errors / 0 warnings, fully
  reachable, 0 repair rounds** — skeleton-first means clean-by-construction, so the repair
  loop is a net that (on the happy path) never has to fire. This is the write-side proof the
  offline fake structurally cannot give.

## Deferrals (out of this slice)

- **The meta blocks** (director/clock/weather/loot/start_quests) — author *rooms/NPCs/items*
  first; generating tuned config is a later slice (and code-owned defaults suffice meanwhile).
- **Quest authoring** — a region's quests lean on the Phase-3 quest subsystem; defer to its own
  slice.
- **A drawn map in the loop** — the atlas's mermaid view already renders a proposed region for
  human review; no new visualiser here.
- **Multi-region campaigns / world-scale coherence** — grow the tables and the plan pass later.

## Appendix — a brief, sketched (extend-mode)

```
BRIEF: "Past the hilltop, a windswept moor runs to a ruined watchtower. A lonely
        beacon-keeper tends a cold signal-fire; a cracked signal-horn lies in the tower."

PLAN (model → small schema)
  rooms:  moor (open, exposed) · watchtower_base · watchtower_top (the beacon)
  links:  hilltop → moor → watchtower_base → watchtower_top   (proposed adjacencies)
  npcs:   beacon_keeper @ watchtower_top (anchored)
  items:  signal_horn @ watchtower_base (floor)

SKELETON (code) — ids assigned, reciprocal edges written, attached to the existing world
  hilltop        --north-->  moor            moor          --south--> hilltop
  moor           --north-->  watchtower_base watchtower_base--south--> moor
  watchtower_base --up-->    watchtower_top  watchtower_top --down-->  watchtower_base
  beacon_keeper.location = watchtower_top    signal_horn.holder = watchtower_base

FLAVOUR (model, per entity, small schema)
  moor.description        = "Heather bows flat under a wind that never rests…"
  beacon_keeper.persona   = {backstory, traits, goals, voice}
  signal_horn.description = "A brass horn, split along one seam…"

VALIDATE (atlas)  →  0 errors, 0 warnings  →  merged as game/world/moor.json
```
