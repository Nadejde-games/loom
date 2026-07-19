# Prompting NPCs and the director across models

A living guide for game builders (and the authoring tooling to come): how to get an
LLM to behave the way a Loom game needs — reliably, across a range of models — and
where the framework does it for you versus where you must prompt it yourself.

Loom's golden rule keeps you *safe* regardless of the model: every world change goes
through a schema-validated action, so a badly-behaved model can never corrupt the
world. This guide is about the next thing up — making the model *choose well*: speak
when addressed, act when asked, stay quiet when nothing is called for, map a player's
free text to the right command. That is behaviour, and behaviour varies by model.

## The measurement instrument

`scripts/behavior_probe.py` is both the **specification** of wanted behaviour (one
scenario per behaviour, with a pass bar) and the **way to measure** any model against
it. Change a prompt, run the affected scenarios against the target model, watch the
rate move. Everything below was found with it. Run the NPC-side scenarios against
your NPC model and the director-side ones against your director model (see the file
header). It is characterised across the Qwen family in
`docs/benchmarks/` and the sweep notes.

## What the framework handles for you

- **Shape.** Constrained decoding (`ActionRegistry.json_schema`, `command_schema`)
  guarantees the *shape* of every turn/command on a backend that supports it, with a
  tolerant parse + validate/retry underneath as defence in depth.
- **Thinking suppression.** The Qwen3.x models reason by default and, on a small
  token budget, the chain-of-thought crowds out the answer. Every provider suppresses
  it (`reasoning_effort:"none"` locally, `reasoning:{enabled:false}` on OpenRouter);
  pass `think=True` where you *want* deliberation.
- **Rate-limit resilience.** OpenRouter rate-limits *bursts* per model (worse on the
  pricier ones). `OpenRouterProvider` paces adaptively — zero delay in normal play
  (calls are already seconds apart), spacing out only once a throttle is seen and
  decaying back. Heavy tooling (like the harness) may still run slowly under a
  cooldown; that is pacing working, not a bug.

## Learnings (proven, with the measurement)

### 1. Prefer a *flat enum* schema over a `oneOf` discriminated union

**The trap.** A grammar that is a `oneOf` over branches which differ only in which
fields they require invites a weaker model, under strict decoding, to collapse to the
**simplest branch** — the one needing the least output.

**Measured (2026-07-19).** The free-text command parser (B1b) used a `oneOf` over
per-verb branches. `qwen3.6-35b-a3b` (a 3B-active MoE) emitted `{"verb":"look"}` — the
only object-free verb, the shortest valid branch — for *every* input: **0/4**. The
same model, given the *same prompt* with the grammar **removed**, mapped all four
correctly. The fix was not to drop the grammar but to **flatten** it: one object with
`verb` as an `enum` and `dobj` required. The model must then *reason* the verb rather
than take the cheapest grammar path — **4/4 on both `qwen3.6-35b-a3b` and
`qwen3.5-9b`**, shape still guaranteed. (See `loom/command.py::command_schema`.)

**Rule of thumb.** When a constrained field is a choice among alternatives, model it
as an **enum on a flat object**, not a `oneOf` of structurally-different branches —
especially if any branch is markedly cheaper to emit. Discriminated unions are fine
when every branch is comparably rich (the turn envelope's `speech` + `actions` did not
collapse on the same model), but a union that *is* the whole output is collapse-prone.

### 2. Constrained decoding is not a substitute for a good prompt

The grammar guarantees the model emits a *valid* answer, never the *right* one.
Silence, an empty action list, and the wrong-but-valid choice all satisfy the schema.
The prompt still has to carry intent, examples, and disposition. (More per-behaviour
learnings — reaction, restraint, the director's reach — will land here as they are
tuned across the models.)

## When nudging isn't enough: model profiles (planned)

Some quirks are model-specific and cannot be fixed in a shared prompt without hurting
other models. The planned **behaviour-profile** layer lets a profile — selected by the
game for its model, injected into the mind/director — append per-surface
reinforcement (`reinforce("react")`, …) without the mind ever hard-coding a model
name. The framework will ship profiles for the characterised Qwen models; game
builders author profiles for theirs. This section will document how, and — honestly —
the behaviours that profiles still can't force, so you know what you are buying.

*Status: the schema and rate-limit learnings above are shipped; the reaction /
director-reach learnings and the profile layer are in progress.*
