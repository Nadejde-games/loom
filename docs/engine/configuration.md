# Configuration

Runtime configuration lives in a git-ignored `.env` at the repo root
(`.env.example` is the annotated template, the
[setup wizard](../setup/quickstart.md) writes it, and every entry point
auto-loads it — a real environment variable always wins). There are two
configuration surfaces: **environment variables**, which select backends and
tune the engine's systems, and **`world.json` meta blocks**, which are authored
world content.

Boolean variables follow one idiom throughout: anything except `0`, empty, or
`false` counts as on.

## Providers and models

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_PROVIDER` | `fake` \| `ollama` \| `vllm` \| `openrouter` \| `anthropic` | inferred from whichever model var is set; else `fake` |
| `LOOM_OLLAMA_MODEL` | NPC tier, local | `qwen3.5:35b-a3b` |
| `LOOM_OLLAMA_HOST` | Ollama base URL | `http://localhost:11434` |
| `LOOM_VLLM_MODEL` / `LOOM_VLLM_HOST` / `LOOM_VLLM_API_KEY` | vLLM served model, base URL, optional key | `qwen-local` / `http://localhost:8000` / unset |
| `LOOM_OPENROUTER_MODEL` | NPC tier, hosted | `qwen/qwen3.6-35b-a3b` |
| `OPENROUTER_API_KEY` | hosted key (`LOOM_OPENROUTER_API_KEY` also checked, first) | unset |
| `LOOM_OPENROUTER_HOST` | base-URL override | `https://openrouter.ai/api/v1` |
| `ANTHROPIC_API_KEY` | Claude key (needs the `[anthropic]` extra) | unset |
| `LOOM_GM_MODEL` | director tier | `qwen/qwen3.6-27b` on OpenRouter; on local backends the director shares the NPC model until set |
| `LOOM_AUTHOR_MODEL` | authoring-agent tier | falls back to `LOOM_GM_MODEL`, then the NPC tier |

## Memory embeddings

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_EMBEDDER` | `fake` \| `openrouter` \| `none` (`none` = recency + importance only) | OpenRouter if a key is present, else none |
| `LOOM_EMBED_MODEL` | embedding model | `baai/bge-m3` |

## Server and logging

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_HOST` / `LOOM_PORT` | server bind (the client reads the same vars) | `127.0.0.1` / `4000` |
| `LOOM_INFER_LOG` | live per-call inference telemetry in the server log | `1` |
| `LOOM_VERBOSE` | the debug firehose — salience skips, act-gate reasons, reflection questions | `0` |

## Engine systems

These toggles are read by the game entry point (`game/main.py`), which is where
each system is attached — the base engine has none of them until wired. The
comments in that file are the authoritative fine print.

**NPC behavior**

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_NPC_ACT_GATE` | two-pass turns: a cheap low-temperature pass decides the deed authoritatively | `1` |
| `LOOM_REQUIRE_LOGIN` | durable identity — the name prompt on connect; `0` = anonymous, session-ephemeral wanderers | `1` |
| `LOOM_IDLE_NPC` | quiet pulses before an NPC may stir unbidden; `0` = off | `4` |
| `LOOM_IDLE_PERIOD` / `LOOM_IDLE_COOLDOWN` | idler cadence in loop ticks / rest pulses per NPC | `12` / `3` |

**The director**

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_DIRECTOR_PERIOD` | loop ticks between director pulses | `12` |
| `LOOM_DIRECTOR_MIN_EVENTS` | new chronicle events required for an activity beat | `3` |
| `LOOM_DIRECTOR_COOLDOWN` | pulses of breathing room after a beat | `2` |
| `LOOM_DIRECTOR_LULL` | quiet pulses before a gentle lull beat; `0` = off | `6` |
| `LOOM_DIRECTOR_FORESHADOW` | let the director see and shape empty rooms one exit ahead | `1` |
| `LOOM_DIRECTOR_ACT_GATE` | the model-side wait/act decision before an activity beat | `1` |

The idle floor and the director lull are deliberately kept level so neither
always breaks a silence first.

**Reflection**

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_REFLECT` | enable reflection | `1` |
| `LOOM_REFLECT_PERIOD` | ticks between reflection pulses | `20` |
| `LOOM_REFLECT_THRESHOLD` | accumulated memory-importance required to trip | `24` |
| `LOOM_REFLECT_TELL` | the visible "gaze turns inward" ambient beat | `1` |

The defaults are tuned for observability (reflection trips within a minute of
lively play); raise the period and threshold for a calmer, cheaper long run.

**World, loot, persistence**

| Variable | Meaning | Default |
|----------|---------|---------|
| `LOOM_CLOCK_FACTOR` | game-minutes per real second (overrides the world's authored `clock.factor`) | authored value |
| `LOOM_LOOT_SEED` | pin the loot roll for a reproducible run | unset (fresh variety) |
| `LOOM_MEMORY_DB` | SQLite memory store path | `game/world.memory.db` |
| `LOOM_SAVE_PATH` | the mutable save overlay | `game/world.save.json` |
| `LOOM_AUTOSAVE_PULSES` | autosave cadence; `0` = save on shutdown only | `60` |

!!! warning "Keep runtime state out of the world directory"
    The save overlay and the memory database are runtime state, never content.
    They must not live inside `game/world/` — the loader would ingest them as
    world files.

## The other surface: `world.json` meta blocks

Structural keys of a world file are `start_location`, `locations`, `npcs`, and
`items`. **Any other top-level key** is carried verbatim as world-level `meta` —
authored configuration the game wires into the engine:

| Block | Shape | Drives |
|-------|-------|--------|
| `director` | `{tone, goals[]}` | the game-master's persona |
| `clock` | `{factor, start_minute, phases: [{name, start, condition, ambient}]}` | the world-clock's day phases |
| `weather` | `{period_pulses, change_chance, start_index, states: [{name, condition, ambient}]}` | the weather random walk |
| `loot` | `{tiers: [{name, rank, weight, max_tags}], themes[], tags: [{tag, group, min_rank, when[]}]}` | the loot forge's tables |
| `start_quests` | `[{title, summary, destination}]` | quests offered on arrival |

See the [demo game](../demo-game.md) for a complete worked example of all five.
