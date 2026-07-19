# Loom + the forever game

Two things live here:

- **`loom/`** — a reusable, game-agnostic framework for AI-driven, text-first,
  server–client worlds. (Working name; rename freely.)
- **`game/`** — the first world built on Loom. Content and configuration only.
- **`client/`** — a minimal reference terminal client.

The framework core keeps a **small, deliberate dependency budget** (see
`docs/DEPENDENCIES.md`) and runs offline: with no API key it drives NPCs through a
deterministic `FakeProvider`. Set `ANTHROPIC_API_KEY` (and `pip install -e
".[anthropic]"`) and every NPC upgrades to Claude with no code change.

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

## Run it

One-time setup (editable install into a virtualenv):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

With the venv active, `PYTHONPATH` is not needed.

**Terminal 1 — start the server.** Local inference on vLLM (point it at your
running server; `qwen-local` here is the `--served-model-name`):

```bash
source .venv/bin/activate
LOOM_PROVIDER=vllm LOOM_VLLM_MODEL=qwen-local python game/main.py
#   LOOM_VLLM_HOST=http://localhost:8000   # (default)
#   LOOM_VLLM_API_KEY=...                   # only if vLLM was started with --api-key
```

Or hosted, via OpenRouter (no local GPU needed). Put your key in a `.env` at the
repo root — `OPENROUTER_API_KEY=sk-or-...` — and it is loaded automatically:

```bash
source .venv/bin/activate
LOOM_PROVIDER=openrouter python game/main.py
#   NPCs default to qwen/qwen3.6-35b-a3b; the game-master director to qwen/qwen3.6-27b.
#   LOOM_OPENROUTER_MODEL=qwen/qwen3.6-27b   # override the NPC model
#   LOOM_GM_MODEL=qwen/qwen3.6-35b-a3b       # override the director model
```

Or on Ollama:

```bash
source .venv/bin/activate
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b python game/main.py
```

All are the same OpenAI-compatible path — they differ only by `base_url` + model
name. A local backend shared with another workload can be slow to first token;
replies run off the game loop, so the world never stalls.

Or run it fully offline with the deterministic `FakeProvider` (no model, no key):

```bash
source .venv/bin/activate
python game/main.py
```

The server binds `127.0.0.1:4000`; override with `LOOM_HOST` / `LOOM_PORT`.

**Terminal 2 — connect and play:**

```bash
source .venv/bin/activate
python client/terminal.py
```

Try: `look`, `say hello`, `go north`, `go south`, `help`, `quit`.

### Provider selection (env)

```bash
LOOM_PROVIDER=fake                                       # deterministic, offline (default)
LOOM_PROVIDER=vllm LOOM_VLLM_MODEL=qwen-local            # local inference via vLLM
   LOOM_VLLM_HOST=http://localhost:8000                  # (default)
   LOOM_VLLM_API_KEY=...                                 # (optional; only if started with --api-key)
LOOM_PROVIDER=openrouter                                 # hosted inference via OpenRouter
   OPENROUTER_API_KEY=sk-or-...                          # required (auto-loaded from .env)
   LOOM_OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b            # NPC model (default)
   LOOM_GM_MODEL=qwen/qwen3.6-27b                        # director model (default on OpenRouter)
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b   # local inference via Ollama
   LOOM_OLLAMA_HOST=http://localhost:11434               # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...            # Claude
```

### Checks

```bash
python -m unittest discover -s tests    # offline unit tests (no GPU)
python scripts/smoke.py                 # end-to-end check (server must be running)
python scripts/try_provider.py          # ping the active provider: speech + validated actions
```

## Status

The talking-NPC vertical slice runs end to end on local GPU inference, and NPCs
can now *act*: a mind returns a validated turn (speech + schema-checked actions),
the engine executes only what validates and narrates it to the room. First
built-in action is `emote`. An NPC's speech and deed render as **one composed beat**
(`Odd shakes his head slowly and says, "…"`), carried as semantically **styled
spans** on the wire — a rich client themes them (names, speech, items, exits), a
plain or `NO_COLOR` terminal reads the same prose. See `docs/PLAN.md` for the
roadmap.
