# Spike — Reflection (memory depth, slice 2)

**Date:** 2026-07-20 · **Question:** Slice 1 (importance + embedding-relevance retrieval
on SQLite, `docs/spikes/memory.md`) gave Loom's agents a memory that surfaces an old-but-
salient or old-but-relevant memory past the recent window. The deferred half of that spike
(§5 / Q5) is **reflection**: the Generative-Agents step where an agent, on a cadence,
distills its raw memories into *higher-level insights* and writes them back as memories, so a
long-lived NPC forms durable beliefs ("the player's promises mean nothing") instead of only
holding the raw lines. How does the reference implement it; what is the minimal useful form
for a forever-MUD; where does it sit in Loom's code; how is it triggered affordably; and how
is it proven offline? · **Method:** the reflection prior art was surveyed and **verified
against the primary source in slice 1** (`docs/spikes/memory.md` §1 reflection paragraph and
§5/Q5 quote the Generative Agents paper, arXiv:2304.03442, fetched via ar5iv); this spike
consolidates that finding and maps the depth-1 design onto the shipped memory substrate
(`loom/ai/memory.py`, `memory_store.py`, `mind.py`, `director.py`, `engine.py`). No new web
survey was needed — the mechanism is small and already quoted; the work here is the design.

---

## Verdict

**Ship the Generative-Agents reflection step, depth-1, as a second cognition beside the
director.** A `Reflector` orchestrator hangs off the game loop on a slow, opt-in cadence
(mirroring `Director`); when an agent's accumulated memory-importance since its last
reflection crosses a threshold, it runs a **two-call synthesis** — (1) generate up to three
salient high-level questions from the recent memories; (2) `retrieve()` per question to reach
*back past the recent window* into old-but-relevant memory; (3) synthesize up to three
insights, each grounded in cited evidence — and writes each insight back via
`memory.add(text, kind="reflection")`. Because a reflection is *just another memory*, this
needs **no memory-seam change**: it embeds lazily, persists to SQLite, and is retrieved like
any other memory (importance 8 by the existing kind weight, so it outranks trivia). It is
**depth-1**: reflections are written past the reflection watermark, so a reflection never
reflects on itself — no tree, no cascade, this slice. It runs for **NPCs and the director
alike** (one substrate, uniform treatment). It is **off by default** (`attach_reflector` /
`LOOM_REFLECT_*`), proven offline with a scripted provider, and cleared through the live gate
before it is trusted.

---

## 1. The reference mechanism (verified in slice 1, quoted here for the build)

From Generative Agents (arXiv:2304.03442, fetched and quoted in `docs/spikes/memory.md`):

- **Trigger:** reflection fires **"when the sum of the importance scores for the latest
  events perceived by the agents exceeds a threshold (150 in our implementation)"** — in
  practice "roughly two or three times a day."
- **Question generation:** query the LLM with **"the 100 most recent records"** and ask for
  the **"3 most salient high-level questions."**
- **Synthesis:** retrieve memories relevant to each question, then synthesize insights **with
  evidence citations in the form "insight (because of 1, 5, 3)"** where the numbers index the
  cited memories; each insight is **inserted back into the stream as a memory** (a non-leaf
  node).
- **Tree:** leaves are observations; non-leaf nodes are progressively more abstract; a
  reflection can itself be reflected upon. **Loom takes depth-1 only** — the tree is deferred.

The other surveyed systems (`docs/spikes/memory.md` §2–§5) have no better reflection recipe
to steal: MemGPT has no reflection (self-editing tools instead — rejected on the security
seam); mem0 replaces reflection-adjacent work with a per-add novelty *gate* (an LLM call per
memory — rejected on cost); A-MEM's link-evolution is the "if depth-1 is not enough"
escalation (shelved). So the GA step, minimized, is the design.

## 2. The Loom design (depth-1)

**The cognition** (`loom/ai/reflection.py::reflect`, provider-agnostic, operates on a
`MemoryStream` + an `LLMProvider` + a persona preamble):

1. **Questions.** Number the recent memories; ask the agent's *own* model for ≤3 salient
   high-level questions. Schema-constrained JSON (`{"questions": [...]}`), tolerant parse
   (`mind._extract_json`), the same seam every other mind call rides.
2. **Evidence.** For each question, `await memory.retrieve(question, k)` — the step that
   reaches back past `recent()` into old-but-relevant memory (with an embedder; without one it
   degrades to `recent(k)`, so reflection still summarizes the recent window). Dedup the union
   by object identity into one numbered evidence set.
3. **Insights.** Ask the model for ≤3 insights from the numbered evidence, each a
   `{"text": ..., "because_of": [<numbers>]}`. Schema-constrained.
4. **Write-back.** For each insight, map the cited numbers to the evidence texts and store
   **GA-style: `"<insight> (because of: <src>; <src>)"`** via `memory.add(text,
   kind="reflection")`. (Decision below.)

**The orchestrator** (`loom/ai/reflection.py::Reflector`, mirrors `Director`):

- A slow tick system (`install(loop)` → `loop.add_system(self.tick)`), opt-in.
- **Per-agent watermark** = the memory count at that agent's last reflection. Each pulse, sum
  the importance of memories since the watermark; the agent **most** over `importance_threshold`
  reflects. One agent per pulse, one background task at a time (`_running`), tracked on
  `engine._tasks` exactly like a director beat or an NPC reply; a broken reflection is logged,
  never fatal.
- **Depth-1 by construction:** after reflecting, the watermark advances to the current entry
  count — *past* the just-written reflections — so a reflection never re-triggers itself.
- **Lazy watermark init:** on first sight of an agent the watermark is set to its current
  length, so a store-backed restart does **not** re-reflect the whole loaded backlog every
  boot (the in-memory watermark is a slice-2 simplification; persisting reflection state is
  deferred). Reflection resumes on memories formed after the reflector starts watching.
- **Scope:** NPCs (`engine.minds`) **and** the director (`engine.director.mind`), keyed
  `"director"`. Each reflects with its *own* provider (NPCs on the NPC model, the director on
  the GM model) under **one shared reflection rule set** — no per-model prompts
  (`feedback-single-ruleset-not-per-model`).

**Engine/game:** `Engine.attach_reflector(loop, …)` mirrors `attach_director`; `game/main.py`
opts in via `LOOM_REFLECT_*`, **off by default** until the live gate proves it.

## Decisions

- **Citations — GA-style, in the stored text** (chosen over a clean-text "grounding gate"):
  the stored reflection reads `"<insight> (because of: <source>; <source>)"`, faithful to the
  paper and debuggable in the raw memory. The alternative (cite only as an accept/reject
  grounding gate, store clean prose) was considered and declined — provenance in the text is
  wanted here. An insight with no valid citations is still kept, minus the clause.
- **Scope — NPCs + director**: the director distills patterns across its own beats ("the
  wanderers linger longest at the ruined shrine"), deepening its shaping; same mechanism,
  same rule set.
- **Depth-1**, in-memory watermark, one-agent-per-pulse: the smallest form that proves value.

## The proof

**Offline (deterministic, a scripted provider + `FakeEmbeddingProvider`, SQLite `:memory:`):**
seed an NPC with several memories of a broken promise; run `reflect()`; assert it writes a
`kind="reflection"` memory carrying the GA-style `(because of: …)` clause, and that a later
`retrieve()` about the subject surfaces the **distilled belief**, not only the raw lines —
the crisp demonstration that reflection produces a durable higher-level memory. A second test
drives the `Reflector` trigger: below threshold it does nothing; once accumulated importance
crosses it, exactly one agent reflects and the watermark advances so it does not re-fire.
**Live gate:** a real model, given a real stream, produces a coherent grounded insight, and
the director's cadence is unaffected. Both gates before it is called done.

## Defer (with reasons)

- **Reflection tree (depth > 1), reflections-on-reflections, daily planning** — the GA tree;
  depth-1 proves the value first. The watermark already prevents self-reflection.
- **Persisting the reflection watermark** — in-memory this slice; a restart resets
  accumulation (reflection resumes on new memories, no backlog re-reflection). Persist later
  if cross-restart cadence matters.
- **LLM importance for the trigger** — the heuristic importance from slice 1 drives the
  threshold; no new model cost to decide *whether* to reflect.
- **mem0 novelty/dedup gate, A-MEM link-evolution** — the escalation past depth-1 if reflections
  or memories start to repeat; each is an LLM-per-add or a graph to maintain (`memory.md` §3–4).
- **Idle-NPC autonomy (B-thread)** — reflection is its prerequisite (an idle beat should flow
  from a reflected goal); it is the next Phase-5 thread, not this slice.

## Appendix — sources

| Claim | Source | Status |
|---|---|---|
| GA reflection: trigger sum-importance > 150 (~2–3×/day); 100 recent records → 3 questions; "insight (because of 1,5,3)"; tree of increasingly abstract nodes | [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) via [ar5iv](https://ar5iv.labs.arxiv.org/html/2304.03442) | **Verified in slice 1 (fetched, quoted — `docs/spikes/memory.md`)** |
| Loom memory substrate: `MemoryStream.add/recent/retrieve/state/load_state`, `MemoryEntry(text,kind,t,importance,embedding,rowid)`, SQLite `MemoryStore`, kind weight `reflection`=8; minds hold `.memory`/`.provider`; `Director` orchestrator pattern (`install`/`tick`/`_guarded_beat`/`engine._tasks`) | `loom/ai/{memory,memory_store,mind,director}.py`, `loom/engine.py` | **Verified against working tree** |
