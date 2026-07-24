# Setup in detail

This page explains what the wizard actually does, how to install by hand, and how
configuration is resolved at runtime. For the fast path, see the
[quick start](quickstart.md); for picking models, see
[Choosing models](models.md).

## What the wizard writes

`setup.sh` only ever touches a handful of keys in `.env` (seeded from
`.env.example` the first time, then updated key-by-key — it never rewrites the
file wholesale, and a value you set by hand survives re-runs):

| Key | When |
|-----|------|
| `LOOM_PROVIDER` | always — `ollama` or `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter path only (entered silently, skipped if blank) |
| `LOOM_AUTHOR_MODEL` | always — the authoring-agent tier |
| `LOOM_GM_MODEL` | always — the director tier |
| `LOOM_OLLAMA_MODEL` / `LOOM_OPENROUTER_MODEL` | the NPC tier, per backend |
| `LOOM_PORT` | always — a port verified free at setup time |

Everything else in `.env` is yours to manage; `.env.example` is the annotated
template. The file is git-ignored, and **a real environment variable always beats
a `.env` entry** — every entry point loads `.env` with
"don't-override" semantics.

Two wizard-only variables are useful when scripting or testing it:
`LOOM_SETUP_DRY_RUN=1` prints the install and model-pull steps instead of running
them, and `LOOM_SETUP_ENV_FILE` redirects where the `.env` is written.

## The two backend paths

**Ollama (local, the default).** The wizard installs the runtime if missing
(`brew install ollama` on macOS, the official install script on Linux), starts
the daemon if it isn't reachable, then prompts for each of the three model roles
and pulls what you pick — a bad tag re-prompts rather than aborting, and a tag
shared across roles is only pulled once. At the end it offers to **warm-load**
the chosen models (a one-token generate with `keep_alive: -1`) so the first
in-game response is fast.

**OpenRouter (hosted).** The wizard asks for your key (from
[openrouter.ai/keys](https://openrouter.ai/keys)) and the three model slugs.
Nothing is pulled — an invalid slug surfaces at first inference, not at setup.

Two more backends exist but are configured by hand, not by the wizard:

```bash
LOOM_PROVIDER=vllm LOOM_VLLM_MODEL=qwen-local     # local vLLM
   LOOM_VLLM_HOST=http://localhost:8000            # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...      # Claude — pip install -e ".[anthropic]"
```

And with `LOOM_PROVIDER=fake` (or nothing configured at all) the engine runs
offline on the deterministic `FakeProvider`.

## Manual install

If you would rather skip the wizard:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[authoring,game]"
cp .env.example .env       # then fill in your backend of choice
python game/main.py
```

The extras are layered deliberately:

| Install | What you get |
|---------|--------------|
| `pip install -e .` | the bare framework core — only `httpx` and `json-repair` |
| `pip install -e ".[game]"` | + `python-dotenv`, so `game/main.py` auto-loads `.env` |
| `pip install -e ".[authoring]"` | + Textual and Pydantic-AI, for the [workbench](../workbench/index.md) |
| `pip install -e ".[anthropic]"` | + the Anthropic SDK, for Claude-driven NPCs |
| `pip install -e ".[docs]"` | + MkDocs Material, to build this site locally |

Requires Python **3.11+**. Entry points are run from the repo root:
`python game/main.py` (server), `python client/terminal.py` (client),
`python -m authoring [world.json | world-dir]` (workbench). With the venv active
no `PYTHONPATH` is needed. `make` targets are thin wrappers over exactly these
commands (they call `.venv/bin/python` directly, so you don't even need the venv
activated).

## The three model roles

Loom deliberately separates *who is thinking* from *which model thinks it*. Three
roles are independently selectable, because they want different things:

| Role | Env var | Wants |
|------|---------|-------|
| **Authoring agent** — the workbench's AI world-author | `LOOM_AUTHOR_MODEL` | reliable judgment |
| **Game master / director** — the unseen hand shaping ambient beats | `LOOM_GM_MODEL` | judgment over speed (it runs on a slow cadence) |
| **NPC reactions** — character dialogue in the world | `LOOM_OLLAMA_MODEL` or `LOOM_OPENROUTER_MODEL` | speed (it's the player-facing latency) |

Unset roles fall back sensibly: the authoring agent tries
`LOOM_AUTHOR_MODEL` → `LOOM_GM_MODEL` → the NPC tier; on OpenRouter the director
defaults to `qwen/qwen3.6-27b` even when `LOOM_GM_MODEL` is unset, while on
Ollama/vLLM it simply shares the NPC model until you set one.

## Networking, logging, and the rest

- `LOOM_HOST` / `LOOM_PORT` — server bind, default `127.0.0.1:4000`. The client
  reads the same variables, so one `.env` serves both.
- `LOOM_INFER_LOG` — per-call inference telemetry in the server log
  (elapsed · tokens · tok/s, streamed live for local backends). On by default;
  `0` silences it.
- `LOOM_VERBOSE` — the debug firehose (salience skips, act-gate reasons,
  reflection accumulation). Off by default.

The many gameplay toggles (`LOOM_DIRECTOR_*`, `LOOM_REFLECT*`, `LOOM_IDLE_NPC`,
`LOOM_REQUIRE_LOGIN`, …) are documented in
[Engine → Configuration](../engine/configuration.md).
