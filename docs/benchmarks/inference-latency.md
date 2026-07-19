# Benchmark — inference latency (local vLLM vs hosted OpenRouter)

**Date:** 2026-07-18 · **Question:** how fast is each inference backend for a real
game turn, and how does it behave under the concurrency the engine actually creates
(a room of NPCs answering at once, reaction cascades)?
**Method:** the real game prompts — persona + perceived scene + action catalogue +
the turn-envelope grammar, built through the engine — driven over streaming so we can
separate time-to-first-token (TTFT, ≈ prefill + network) from decode throughput.
Reproduce with `scripts/bench_inference.py`.

Backends measured:
- **vLLM (local):** `Qwen3.6-27B-FP8` served as `qwen-local`, **while the box was
  shared with another workload** — so these are loaded-server figures, not the
  dedicated floor.
- **OpenRouter (hosted):** `qwen/qwen3.6-27b` (the model-matched comparison) and
  `qwen/qwen3.6-35b-a3b` (the NPC default — a 3B-active MoE, faster by design).

---

## Single request (median of 3; one game turn)

| metric | vLLM-27b *(local, shared)* | OR-27b *(hosted)* | OR-35b-a3b *(hosted, NPC default)* |
|---|---:|---:|---:|
| TTFT | 0.32–0.38s | 0.36–0.58s | 0.25–0.36s |
| blended turn — total | **2.97s** | **0.99s** | **0.53s** |
| act-gate decision — total | 3.47s | 0.68s | 0.55s |
| decode rate (tok/s) | 14–18 | 70–86 | **113–160** |

Prompts were ~690–770 tokens; outputs ~25–35 tokens (a turn is short by design).

**Full act-gated NPC turn** (decision + blended = two calls — the player-facing wait):
- vLLM (shared): **~6–7s** (≈3.5s + ≈3s).
- OpenRouter 35b-a3b: **1.37s** wall-clock, e.g. *"Wren leaves, heading north and
  says, 'Lead the way!'"*.

## Concurrency — the structural difference

The engine fires NPC replies concurrently (a background task per NPC, plus reaction
cascades), so behaviour under load matters more than a single stream.

| concurrent requests | vLLM *(one replica)* per-req | OpenRouter 35b-a3b per-req |
|---:|---:|---:|
| 1 | 2.4s | 0.87s |
| 2 | 4.5s | 0.78s |
| 4 | 7.8s | 0.66s |
| 8 | — | 0.80s |

Local latency inflated **~linearly** with concurrency (one GPU, saturated under the
shared load); splitting across the two local replicas gave ~1.5×. OpenRouter stayed
**flat** — hosted infra fans out across many GPUs. A 4-NPC room that cost ~8s/reply
locally costs <1s/reply concurrently.

## Connection pooling (httpx)

The providers now use a shared, pooled `httpx.AsyncClient` (see
`docs/DEPENDENCIES.md`). First call pays the TLS handshake; pooled calls after do not:

| backend | 1st call | pooled median |
|---|---:|---:|
| OR-35b-a3b | 0.79s | 0.48s |
| OR-27b | 0.71s | 0.64s |

≈0.3s saved on every call after the first — meaningful on a remote backend, free on
local.

---

## Reading it

- **~3–10× faster end-to-end on OpenRouter, right now.** TTFT is comparable (network
  RTT ≈ local prefill; both are latency-light), so the win is almost entirely
  **decode throughput**. Turn time is decode-bound, so it collapses from ~3s to
  ~0.5–1s.
- **Concurrency is the decisive difference** — flat vs linear. For multiplayer rooms
  and reaction cascades, where the local box degraded worst, hosted scaling is a
  categorical improvement.
- **The 27b column is the fair model-matched read** (same model, local-shared vs
  hosted): still ~5× decode and ~3× lower turn latency.

## Honest caveats

- **Not fully apples-to-apples.** The local vLLM was measured *under another
  workload's load*; its own lightly-loaded floor was ~2s/turn, and a *dedicated*
  local box would narrow the single-stream gap — though **not** the horizontal-
  concurrency gap. OpenRouter figures are a snapshot; hosted latency varies with
  time-of-day and upstream routing.
- **Cost model differs.** OpenRouter is per-token (≈ $0.00005/turn on 35b-a3b,
  ≈ $0.0005 on 27b — negligible, not zero); local is "free" on owned hardware but
  contends with other work, and is private/offline.

## Upshot

For latency and especially concurrency, **OpenRouter is the stronger path today** —
the pooled provider runs ~0.5s/call and scales flat. Local vLLM's case is cost,
privacy, and offline operation, and it competes better single-stream when *not*
shared. The engine runs identically on either; `LOOM_PROVIDER` is the only switch.
