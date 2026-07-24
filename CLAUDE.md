# CLAUDE.md

Guidance for AI agents (and humans) working in this repository.

## What this is

Two things live here:

- **`loom/`** — a reusable, **game-agnostic** framework for AI-driven, text-first,
  server–client worlds (a MUD/interactive-fiction engine). Python 3.11+, asyncio, built
  from scratch. NPCs are LLM-driven minds; a game-master "director" shapes ambient beats.
- **`game/`** — the first world built on Loom. **Content and configuration only**, no engine
  logic. The world is editable data (`game/world/world.json`), not code.

Supporting pieces: **`client/`** (a minimal reference terminal client), **`authoring/`** (a
Textual text-mode workbench to explore and edit a world, including an AI world-author).

## Layout

| Path | What |
|------|------|
| `loom/` | the framework. Command/session/server/engine, world model, persistence, quests, loot, clock/weather, the salience gate, the action validator. |
| `loom/ai/` | the AI layer: `provider.py` (LLM providers), `mind.py` (`NpcMind`), `director.py`, `reflection.py`, `idle.py`, `memory*.py`, `telemetry.py`. |
| `loom/world/` | the pure world model (locations, entities, conditions). |
| `loom/worlddraft.py` | the authoring **safety gate** — shadow-copy → validate → apply. |
| `game/` | the example world + `game/main.py` (the server entry point). |
| `client/terminal.py` | the reference play client. |
| `authoring/` | the workbench (`python -m authoring`) + the authoring agent (Pydantic-AI). |
| `scripts/` | dev tools — `behavior_probe.py`, `bench_inference.py`, `smoke.py`, `try_provider.py`, `atlas.py`, `author.py`, `wire_demo.py`. |
| `docs/` | `PLAN.md` (roadmap), `DEPENDENCIES.md` (dep policy), `PROMPTING.md`, `WALKTHROUGH.md`, `spikes/` (design notes), `benchmarks/` — plus the **documentation website** (`index.md`, `setup/`, `engine/`, `workbench/`, `development.md`, `demo-game.md`), built by MkDocs Material (`mkdocs.yml`, deployed to GitHub Pages by `.github/workflows/docs.yml`; `make docs` to preview). Keep the site pages in sync with user-facing changes; the internal notes are excluded from the published site. |
| `ops/` | legacy local-inference config (Ollama/vLLM) — not the primary path. |

## Setup & running

The interactive wizard is the fastest path (macOS or Ubuntu): it creates a virtualenv,
installs the package, sets up a backend (Ollama or OpenRouter), picks models, chooses a
free port, and writes `.env`:

```bash
./setup.sh
```

`make` (or `make help`) lists the day-to-day tasks:

```bash
make server      # start the game server        (LOOM_PROVIDER from .env)
make play        # connect a client and play    (second terminal)
make workbench   # open the authoring workbench  (needs the [authoring] extra)
make test        # run the offline test suite    (no network, no GPU)
make smoke       # end-to-end check (server must be running)
```

Manual install: `pip install -e ".[authoring,game]"` (or `.[game]` for just the game, `.`
for the bare offline core). Entry points are run from the repo root: `python game/main.py`,
`python -m authoring [world]`, `python client/terminal.py`.

### Configuration (`.env`)

Runtime config lives in a git-ignored `.env` at the repo root (`.env.example` is the
template; the wizard writes it; every entry point auto-loads it). Key variables:

- `LOOM_PROVIDER` — `ollama` | `openrouter` | `vllm` | `anthropic` | `fake` (offline default).
- **Three model roles, each independently selectable:** `LOOM_AUTHOR_MODEL` (authoring
  agent), `LOOM_GM_MODEL` (director), and the NPC tier (`LOOM_OLLAMA_MODEL` /
  `LOOM_OPENROUTER_MODEL`).
- `OPENROUTER_API_KEY` (hosted), `LOOM_OLLAMA_HOST`, `LOOM_HOST`/`LOOM_PORT` (server bind),
  `LOOM_INFER_LOG` (per-call inference telemetry in the server log), `LOOM_VERBOSE` (debug
  firehose). Many feature toggles exist (`LOOM_DIRECTOR_*`, `LOOM_REFLECT*`, `LOOM_IDLE_NPC`,
  `LOOM_REQUIRE_LOGIN`, …) — see the comments in `game/main.py`.

The engine runs **fully offline with no key** via a deterministic `FakeProvider`.

## Testing — always run BOTH gates after a change

1. **Offline unit suite** (fast, no network, no GPU) — must stay green:
   ```bash
   make test          # or: python -m unittest discover -s tests
   ```
2. **Live behavioral harness** — real model, exercises actual behavior. Load `.env`, then:
   ```bash
   set -a && . ./.env && set +a
   python scripts/behavior_probe.py [selector]   # a scenario name or tag; omit to run all
   ```

Offline tests drive providers with scripted/fake doubles and Pydantic-AI's `FunctionModel`,
so the whole system is testable with **zero network**. When you add behavior, add both an
offline test and (where it's a model behavior) a `behavior_probe` scenario.

## Architecture & invariants (do not break these)

- **Never execute raw model text — the golden rule.** NPCs speak *and* act, but every state
  change goes through a schema-validated action (`loom/action.py`). Dialogue is shown;
  actions are validated, invalid ones retried once then dropped. Model JSON is parsed
  tolerantly (`loom/ai/mind.py`) and **never** spoken raw.
- **`loom/` is game-agnostic and dependency-light.** Core deps are only `httpx` +
  `json-repair` (see `docs/DEPENDENCIES.md`). No numpy/torch/sqlite-vec, and **`loom/` never
  imports Textual or pydantic-ai** — those are `authoring`-extra tool deps; the UI imports
  `loom`, never the reverse.
- **AI is behind one interface** (`LLMProvider`); the engine can't tell fake from real. All
  providers speak the OpenAI-compatible `/v1` schema (Ollama/vLLM/OpenRouter differ only by
  base_url + model).
- **Protocol is separated from transport** (newline-delimited JSON envelopes over TCP now,
  WebSocket later, same schema).
- **The authoring agent is a SEPARATE stack** — native tool-calling via Pydantic-AI, not
  loom's slim `LLMProvider`. The shadow-validate-apply **safety gate lives in tested
  `loom/worlddraft.py`**, never in the agent framework; the agent can only *propose*, the
  human confirms.
- **One comprehensive prompt rule set across all models** — do not add per-model prompt
  profiles.

## Conventions & gotchas

- **Commits:** commit only when asked. Work on `master` (no remote is configured). End commit
  messages with a `Co-Authored-By: Claude …` trailer (see `git log` for the format in use).
- **Persistence is local, never committed:** `game/world.save.json` (mutable overlay) and
  `game/world.memory.db` (SQLite memory) are git-ignored runtime state — they must **not**
  live inside `game/world/` (the loader would ingest them as content).
- **Qwen thinking models** route chain-of-thought into a separate channel counted against the
  token budget; `OllamaProvider` sends `reasoning_effort: "none"` so replies land in
  `content`. When targeting a new model, verify this.
- **Ollama's enforcement of `response_format` json_schema over `/v1` is unverified** — on
  Ollama the real guardrail against malformed turn envelopes is the tolerant parser + the
  one-shot retry, not grammar-constrained decoding (only vLLM is verified for that). Smaller
  models produce malformed JSON more often; the parser must degrade gracefully, never leak.
- **Inference telemetry:** `LogInferenceReporter` (installed by `game/main.py`) shows each
  model call live in the server log (elapsed · tokens · tok/s); local backends stream for a
  live token count. `LOOM_INFER_LOG=0` silences it.
- **Before large or foundational changes, read the relevant `docs/spikes/*.md`** and
  `docs/PLAN.md` — the design rationale and phase history are recorded there.
