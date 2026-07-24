# Choosing models

Loom has [three model roles](details.md#the-three-model-roles) — the authoring
agent, the director, and the NPC tier — and the right pick for each depends on
your hardware and on which trade-off you care about: **judgment** (authoring, the
director) versus **speed** (NPCs, the player-facing latency).

## The short version

- **No GPU, or you just want the best experience:** use **OpenRouter**. Hosted
  inference is dramatically faster than a modest local card, stays fast when a
  whole room of NPCs replies at once, and costs fractions of a cent per turn.
- **A capable GPU (16 GB+ VRAM) or a big-memory Mac:** local **Ollama** works
  well — free, private, offline.
- **A small GPU or laptop:** local still works, but drop to the smaller tiers and
  expect the finer character behavior to soften. Below ~4B, models stop holding
  the structured turn format reliably — that's why the menu bottoms out there.

## Local models (Ollama)

Sizes are rough q4 quantization footprints — what the model occupies in VRAM
(or unified memory on a Mac):

| Model | ~Memory | Kind | Character |
|-------|---------|------|-----------|
| `qwen3.6:27b` | ~17 GB | dense | strong judgment; the best local pick for authoring and the director |
| `qwen3.5:35b-a3b` | ~24 GB | MoE (~3B active) | the fastest; the default NPC tier — but can be hit-and-miss for authoring |
| `qwen3.5:9b` | ~6 GB | dense | a lighter option for a smaller GPU |
| `qwen3.5:4b` | ~3 GB | dense | the smallest; for a modest GPU or laptop |

The MoE/dense split *is* the trade-off: `qwen3.5:35b-a3b` activates only ~3B
parameters per token, so it decodes fast (measured ~0.8 s per warm in-character
turn on an RTX 6000 Ada, versus ~2.1 s for the dense 27B) — ideal for NPC
chatter. The dense 27B reads situations more reliably, which is what the
world-author and the director need and where a slow reply doesn't hurt (the
director runs on a slow ambient cadence anyway).

### Picking by VRAM

| Your hardware | NPC tier | Director / author tier |
|---------------|----------|------------------------|
| ~4 GB (laptop, modest GPU) | `qwen3.5:4b` | `qwen3.5:4b` — workable for play; expect weak authoring |
| ~8 GB | `qwen3.5:9b` | `qwen3.5:9b` |
| 16–24 GB | `qwen3.6:27b`, or `qwen3.5:35b-a3b` if it fits | `qwen3.6:27b` |
| 24 GB+ / multi-GPU | `qwen3.5:35b-a3b` | `qwen3.6:27b` (both can stay resident — see below) |

A cross-model behavioral sweep found the **core NPC loop holds down to 9B**:
speech, validated actions, silence, and memory all keep working. Smaller than
that, models increasingly miss the finer direction — over-talking, reacting when
they shouldn't — and produce malformed turn envelopes more often (the engine
tolerantly recovers and never leaks raw JSON at you, but dropped turns show).
If your characters feel flat or ignore their personas, **a stronger model is
usually the fix** before any prompt tuning.

### On a Mac (Apple Silicon)

The wizard offers **MLX builds first** on macOS — they run on Metal, and their
footprint comes out of unified memory:

| Model | ~Memory | Notes |
|-------|---------|-------|
| `qwen3.5:35b-mlx` | ~20 GB | fastest on Metal if you have the RAM; only ~3B active params, so less reliable for authoring |
| `qwen3.5:27b-mlx` | ~16 GB | fine for authoring, but feels very slow during live gameplay |
| `qwen3.5:9b-mlx` | ~6 GB | the lighter middle option |
| `qwen3.5:4b-mlx` | ~3 GB | good for quick-and-dirty gameplay; not really up to authoring |

The wizard's macOS defaults are deliberately memory-conscious
(`27b-mlx` author / `9b-mlx` director / `4b-mlx` NPCs); raise them if your
machine has headroom.

## Hosted models (OpenRouter)

| Model | Kind | Character |
|-------|------|-----------|
| `qwen/qwen3.6-27b` | dense | strong judgment; the default author and director tier |
| `qwen/qwen3.6-35b-a3b` | MoE | fastest; the default NPC tier |
| `qwen/qwen3.5-9b` | dense | a lighter, cheaper option |

Benchmarks (in `docs/benchmarks/`, reproducible with
`scripts/bench_inference.py`) make the hosted-versus-local trade-off concrete.
Against a local vLLM box that was sharing its GPU with another workload:

- **Single turn:** a full NPC turn (two model calls with the act-gate on) took
  **~1.4 s** on hosted `35b-a3b` versus **~6–7 s** locally — roughly 3–10×
  faster end-to-end, almost entirely decode throughput (113–160 tok/s hosted vs
  14–18 tok/s local-shared).
- **Concurrency is the bigger story:** local per-request latency inflates
  linearly as a room of NPCs replies at once (1→2.4 s, 4→7.8 s), while hosted
  stays **flat** through 8 concurrent requests. A four-NPC room that costs ~8 s
  per reply locally costs under a second hosted.
- **Cost:** ≈$0.00005 per turn on the NPC tier, ≈$0.0005 on the 27B — negligible,
  not zero. Local is free on hardware you own, contends with anything else on
  the card, and is private/offline.

A dedicated local GPU narrows the single-stream gap (the same box lightly loaded
ran ~2 s/turn) but not the concurrency gap.

## Tuning a local setup

For a serious local deployment, `ops/` carries service-level tuning for Ollama
(Linux/systemd): keep the context window modest so the NPC KV-cache stays cheap
(`OLLAMA_CONTEXT_LENGTH=8192`), allow two models resident at once so the NPC and
director tiers don't evict each other (`OLLAMA_MAX_LOADED_MODELS=2`), keep
models hot (`OLLAMA_KEEP_ALIVE=-1`), and enable flash attention. For a
wider-context director, `ops/modelfiles/loom-gm.Modelfile` builds a `loom-gm`
variant (`qwen3.6:27b` at 32K context) — Ollama's OpenAI-compatible endpoint
ignores per-request context sizes, so a model variant is the clean way to give
one role a bigger window. Note 32K context on a 27B claims a large slice of a
48 GB card; lower `num_ctx` or set `OLLAMA_KV_CACHE_TYPE=q8_0` if it gets tight.

!!! warning "Targeting a new model? Check the thinking channel"
    Qwen "thinking" models route chain-of-thought into a separate channel that
    still counts against the token budget — left on, replies come back empty.
    Loom's providers disable it (`reasoning_effort: "none"` on Ollama/vLLM, the
    equivalent flag on OpenRouter), but when you point a role at a new model
    family, verify replies actually land in `content` before blaming anything
    else. `python scripts/try_provider.py` is the quick check.
