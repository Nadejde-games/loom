# Development

How to work on the engine and the workbench: the testing philosophy, the
dependency policy, and the invariants that everything else leans on.

## Getting oriented

`docs/` in the repository carries the engineering record alongside this site:
`PLAN.md` (the roadmap and phase history), `WALKTHROUGH.md` (a guided
code-reading tour, outside-in, ending with a 12-step trace of one `say`
command), `PROMPTING.md` (how to write personas and the game master),
`DEPENDENCIES.md` (the dependency policy in full), and `spikes/` (the design
notes behind every foundational choice). **Before large or foundational
changes, read the relevant spike and the plan** — the rationale is recorded so
it doesn't get re-litigated.

## The two gates

Every change runs against two test gates, and the rule is: **always run both.**

They exist because the two gates prove different things, and the claims are
never conflated. The offline suite proves the *engine* is correct —
**mechanical** claims: given a valid action, the world changes right. But it
drives a scripted provider, so it structurally cannot prove that a real model
*chooses* the right action or uses what it perceives — **behavioral** claims.
Both of the first faults found in live play (a willing guide who spoke of
leading but never moved; an NPC blind to the item at its feet) lived entirely
in that blind spot.

### Gate 1 — the offline suite

```bash
make test        # python -m unittest discover -s tests
```

No network, no GPU, no sockets, no wall-clock — roughly 750 tests across 37
modules, one per subsystem, on stdlib `unittest`. Run one module or one test
directly:

```bash
python -m unittest tests.test_engine
python -m unittest tests.test_engine.EngineStylingTests
```

Zero-network is achieved with a small vocabulary of doubles, worth knowing
before you write a test:

- the shipped deterministic **`FakeProvider`** (the engine can't tell it from a
  real one);
- **scripted providers** — tiny classes implementing the `complete(...)`
  protocol that return queued replies and *record* what they were asked, so a
  test can assert on the schema or temperature the engine sent;
- **capturing providers** that subclass the HTTP layer and intercept the
  request body, proving schema forwarding without a server;
- **Pydantic-AI's `FunctionModel`** for the authoring agent — the whole
  tool-calling loop driven deterministically, including a streaming variant
  that exercises the chat panel;
- fake sessions and injected loop pulses instead of sockets and wall-clock,
  seeded RNG for weather and loot, and Textual's `run_test()` pilot for real
  UI smoke tests.

Tests for the `[authoring]` extra skip cleanly when it isn't installed; the
core suite stays green on a bare install.

### Gate 2 — the live behavioral harness

```bash
set -a && . ./.env && set +a
python scripts/behavior_probe.py [selector]    # a scenario name or tag; omit for all
```

The harness runs real scenarios against the live model in the real game world:
"a willing guide emits `move` when asked to lead", "the reticent hermit stays
silent on idle chatter", "the director waits on a bare scene *and* acts on an
evocative one". Because behavior is stochastic, each scenario runs **N trials
and passes if successes meet a threshold** — set from measured rates, generous
enough to tolerate sampling noise, tight enough to catch a collapse. A miss
prints every sample's speech and actions as evidence. Scenarios can be `gated`
(a miss fails the run) or `watch` (measured, not blocking) — known-limited
behaviors land as watch and are re-gated once fixed.

Adding a scenario is appending a `Scenario(...)` to the right family list —
there's no registration; the runner and the tag selector pick it up. Compose
the check from the predicate vocabulary (`emits("move")`, `is_silent`,
`mentions(...)`, `NOT(...)`), and set `n`/`threshold` from a **measured** rate,
not a wish, with the measurement noted in a comment — every existing entry
shows the shape.

The standing rule: **when you add behavior, add both an offline test and —
where it's a model behavior — a probe scenario.** And run the whole harness,
not just your scenario: the action catalogue is a shared, competitive surface,
so adding one action can regress the *selection* of another.

Two lighter checks round it out: `make smoke` (an end-to-end scripted session
against a running server, deterministic under the fake provider) and
`python scripts/try_provider.py` (one live NPC turn through the full
parse-validate path).

## The dependency policy

Loom is not "dependency-free" — that's a proxy for what's actually wanted:
**minimal, stable, conflict-free coupling**. The rule is a budget. A dependency
earns core entry only if it clears all five tests: cheap and common; a shallow
(ideally empty) sub-dependency tree; no native extension; no plausible version
conflict with a host app; and it replaces meaningfully more hand-rolled code
than it costs. The budget is held **strictly for `loom/`** (a library — its
dependencies become its consumers' pins) and loosely for `game/` and
`scripts/`. Today the core is exactly `httpx` and `json-repair`.

The flip side is deliberate: the action-schema validator stays hand-written —
about forty lines of stdlib type-checking on the security seam, where reading
every line is a feature and where its errors are model-facing strings that
drive the retry loop. A generic validation library would add a native
extension to the core and degrade those messages.

## Invariants — do not break these

- **Never execute raw model text.** Every state change goes through a
  schema-validated action; dialogue is shown, actions are validated, invalid
  ones retried once then dropped; model JSON is parsed tolerantly and never
  spoken raw.
- **`loom/` is game-agnostic.** Anything that names a specific room, NPC, or
  story lives in `game/`, never in `loom/`. Ambient systems attach explicitly;
  the base engine has none.
- **`loom/` never imports Textual or Pydantic-AI.** The workbench and agent
  import `loom`, never the reverse — and the authoring safety gate lives in
  tested `loom/worlddraft.py`, not in the agent framework. The agent proposes;
  the human confirms.
- **AI stays behind one interface.** Providers speak the OpenAI-compatible
  `/v1` schema and differ only by base URL and model.
- **Protocol stays separate from transport.**
- **One comprehensive prompt rule set across all models.** Per-model prompt
  profiles were weighed and rejected: every prompt fix must be a single rule
  proven on more than one model.
- **Runtime state never enters `game/world/`.** The save overlay and memory
  database sit beside the world directory (git-ignored); inside it, the loader
  would ingest them as content.

Two model-facing gotchas to keep in mind: Qwen thinking models route
chain-of-thought into a channel that eats the token budget (providers suppress
it — verify when targeting a new model), and Ollama's enforcement of
`response_format` json_schema is unverified, so there the tolerant parser and
one-shot retry are the real guardrail.

## Dev tools (`scripts/`)

| Script | What it does |
|--------|--------------|
| `behavior_probe.py` | gate 2 — the live behavioral harness |
| `bench_inference.py` | benchmark the active backend on real game turns: TTFT, decode tok/s, a concurrency sweep |
| `smoke.py` | end-to-end scripted session against a running server |
| `try_provider.py` | one live NPC turn: speech + validated actions + latency |
| `atlas.py` | survey any world — map, character sheets, item table, structural lint; exits non-zero on errors, so it gates generated worlds |
| `author.py` | CLI region author: a prose brief in, a validated region out |
| `wire_demo.py` | a single-process demo of exactly what travels on the socket |

## This documentation site

The site is MkDocs Material: pages live in `docs/`, navigation in `mkdocs.yml`,
and GitHub Actions builds and deploys it to GitHub Pages on every push to
`main` that touches them. Preview locally with `make docs` (needs the `[docs]`
extra). The internal engineering notes in `docs/` (`PLAN.md`, `spikes/`, …) are
excluded from the published site on purpose — pages here summarize them
instead.
