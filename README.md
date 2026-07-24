# Loom + the forever game

[![docs](https://github.com/Nadejde-games/loom/actions/workflows/docs.yml/badge.svg)](https://nadejde-games.github.io/loom/)

Four things live here:

- **`loom/`** — a reusable, game-agnostic framework for AI-driven, text-first,
  server–client worlds. (Working name; rename freely.)
- **`game/`** — the first world built on Loom. Content and configuration only.
- **`client/`** — a minimal reference terminal client.
- **`authoring/`** — a text-mode workbench for exploring and editing a world
  (the Phase 8 tool; optional).

The framework core keeps a **small, deliberate dependency budget** (see
`docs/DEPENDENCIES.md`) and runs offline: with no API key it drives NPCs through a
deterministic `FakeProvider`. Point it at a real model — hosted or local — and every
NPC upgrades with no code change.

> **📖 Full documentation: [nadejde-games.github.io/loom](https://nadejde-games.github.io/loom/)** —
> setup, choosing models for your hardware, the engine, the workbench, and the
> development guide. (Source in `docs/`, deployed by CI; `make docs` to preview locally.)

## Quick start

```bash
git clone https://github.com/Nadejde-games/loom.git && cd loom
./setup.sh
```

The wizard (macOS or Ubuntu) checks your Python, creates a virtualenv, installs the
package, and sets up your backend — **OpenRouter** (hosted, needs an API key) or
**Ollama** (local inference, no key) — writing a `.env` you can run against. Then:

```bash
make server      # start the game server
make play        # connect a terminal client and play (in a second shell)
make workbench   # open the authoring workbench
make test        # run the offline test suite
```

`make` on its own lists every target.

### Manual setup

If you would rather not run the wizard:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[authoring,game]"     # or ".[game]" for just the game, "." for the bare core
cp .env.example .env                    # then put your OPENROUTER_API_KEY in it
LOOM_PROVIDER=openrouter python game/main.py
```

With the venv active, `PYTHONPATH` is not needed.

## Architecture in one screen

```
clients ──(newline-delimited JSON envelopes: {"c":channel,"d":data})── server
   terminal reads "text"/"system"; rich clients later add "map"/"entities"
                              │
                     GameServer (async TCP)  ── continuous GameLoop
                              │
                           Engine  ── commands, sessions, player state
                              │
        World (locations/entities, editable JSON)   AI layer
                                              (LLMProvider → NpcMind → MemoryStream)
```

Design commitments:
- **Protocol separated from transport** — TCP now, WebSocket later, same schema.
- **World is editable data** (`game/world/world.json`), not code.
- **AI behind one interface** — `LLMProvider`; the engine can't tell fake from real.
- **Never execute raw model text** — NPCs speak *and* act, but every state
  change goes through a schema-validated action (`loom/action.py`); invalid
  proposals are retried, then dropped. Dialogue is shown; actions are validated.

## Playing

The server binds `127.0.0.1:4000` (override with `LOOM_HOST` / `LOOM_PORT`). Connect
with `make play` (or `python client/terminal.py`).

On connect the world asks your name; type it. Your identity — your location,
inventory, quests, and **the NPCs' memory of you** — persists across sessions and
restarts: reconnect under the same name and the world remembers you. Set
`LOOM_REQUIRE_LOGIN=0` for anonymous, session-ephemeral wanderers instead.

Try: `look`, `say hello`, `go north`, `go south`, `help`, `quit`.

## The authoring workbench

A text-mode world editor (Textual). Explore the world graph, **play in the editor**,
and edit either by hand or by **asking an AI author in natural language** ("add a
locked door to the cellar", "give Khalen a backstory"). Every change — typed or
agent-proposed — is built on a shadow copy, validated by the world survey, and applied
only if the world still surveys clean.

```bash
make workbench                          # or: python -m authoring [world.json | world-dir]
```

Needs the `[authoring]` extra. The in-editor play and the AI author follow your
`LOOM_PROVIDER` — hosted OpenRouter (`OPENROUTER_API_KEY`) or a local Ollama model (the
wizard sets a director/author tier for you). Without a live backend the workbench still
opens read-only and play degrades to an offline dry run.

## Configuration

Runtime config lives in a git-ignored `.env` at the repo root (the wizard writes it;
`.env.example` is the template). Every entry point loads it automatically. The keys
that matter most:

| Variable | Meaning |
|----------|---------|
| `LOOM_PROVIDER` | `openrouter` \| `ollama` \| `vllm` \| `anthropic` \| `fake` (offline default) |
| `OPENROUTER_API_KEY` | your OpenRouter key (hosted inference) |
| `LOOM_OPENROUTER_MODEL` | NPC model slug (default `qwen/qwen3.6-35b-a3b`) |
| `LOOM_GM_MODEL` | director / world-author tier (default `qwen/qwen3.6-27b` on OpenRouter) |
| `LOOM_OLLAMA_MODEL` | local NPC model tag (default `qwen3.5:35b-a3b`) |
| `LOOM_HOST` / `LOOM_PORT` | server bind (default `127.0.0.1:4000`) |

## Alternative backends

The provider is swappable by `base_url` + model name — all the same OpenAI-compatible
path — so any of these drive the same world with no code change. Select with env:

```bash
LOOM_PROVIDER=fake                                       # deterministic, offline (default)
LOOM_PROVIDER=openrouter                                 # hosted inference (the quick-start default)
   OPENROUTER_API_KEY=sk-or-...                          # required (auto-loaded from .env)
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b   # local inference via Ollama
   LOOM_OLLAMA_HOST=http://localhost:11434               # (default)
LOOM_PROVIDER=vllm LOOM_VLLM_MODEL=qwen-local            # local inference via vLLM
   LOOM_VLLM_HOST=http://localhost:8000                  # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...            # Claude (pip install -e ".[anthropic]")
```

The wizard sets up OpenRouter and Ollama; vLLM and Anthropic are configured by hand.
A local backend shared with another workload can be slow to first token; replies run
off the game loop, so the world never stalls. For the larger-context director on
Ollama, `ops/modelfiles/loom-gm.Modelfile` builds a `loom-gm` variant (the wizard
offers this). `ops/` holds legacy local-inference service config — not needed for the
hosted path.

## Checks

```bash
make test                               # offline unit tests (no network, no GPU)
python scripts/smoke.py                 # end-to-end check (server must be running)
python scripts/try_provider.py          # ping the active provider: speech + validated actions
```

## Status

A persistent, memory-driven world runs end to end. NPCs speak *and* act through
schema-validated turns; they remember who they've met (relevance-ranked memory that
survives restarts), reflect into durable beliefs, and stir on their own when a room
falls quiet. An unseen game-master director shapes ambient beats — weather, a turning
world-clock, foreshadowing into rooms just ahead of the players — and a loot forge
rewards completed quests with model-authored items. Players have durable identity: the
world remembers you by name across disconnects.

The **authoring workbench** (Phase 8) closes the loop: explore, play, and edit a world
from one text-mode app — by hand or by asking an AI author — behind a shadow-validate
safety gate. See `docs/PLAN.md` for the roadmap and `docs/spikes/` for the design notes.
