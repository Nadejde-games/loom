"""Benchmark the active LLM backend on real game turns — latency and concurrency.

Backend-agnostic: it resolves the provider from the environment (see
``loom.ai.get_default_provider``) and, for any OpenAI-compatible backend, reuses that
provider's own url / key / model / body (so reasoning-suppression and the grammar are
exactly what the game sends). Prompts are the real ones — persona + perceived scene +
action catalogue + the turn-envelope grammar, built through the engine.

Two views:
  * single-request scenarios, streamed, so time-to-first-token (TTFT ≈ prefill +
    network) is separated from decode throughput (tok/s);
  * a concurrency sweep through the real pooled ``provider.complete`` path — the
    engine fires NPC replies concurrently, so behaviour under load is what matters.

  python scripts/bench_inference.py                                  # active backend
  LOOM_PROVIDER=vllm  LOOM_VLLM_MODEL=qwen-local  python scripts/bench_inference.py
  LOOM_PROVIDER=openrouter                        python scripts/bench_inference.py

Results and the local-vs-hosted comparison live in
``docs/benchmarks/inference-latency.md``. Read-only: it never mutates the server.
"""
from __future__ import annotations
import asyncio
import json
import os
import statistics
import time
import urllib.request

# Load a repo-root .env (e.g. OPENROUTER_API_KEY), like game/main.py.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".env"), override=False)
except ImportError:
    pass

from loom.world import World, Location, Npc, Item
from loom.engine import Engine
from loom.ai import FakeProvider, get_default_provider

TRIALS = 3
CONCURRENCY = (1, 2, 4, 8)


def _prompts():
    """Build the real system prompts (blended + act-gate decision) and the grammar,
    exactly as the engine composes them for an NPC turn."""
    w = World()
    w.add_location(Location(id="clearing", name="The Clearing",
        description="A ring of dark pines around trodden grass; a trail bends north, "
                    "a hollow opens east.",
        exits={"north": "ridge", "east": "hollow"}))
    w.add_location(Location(id="ridge", name="The Ridge"))
    w.add_location(Location(id="hollow", name="The Hollow"))
    w.add_entity(Npc(id="wren", name="Wren the Wayfinder", location_id="clearing",
        persona={"backstory": "A cheerful guide who has walked every trail in these "
                              "hills since childhood.",
                 "traits": ["warm", "eager", "observant", "helpful"],
                 "goals": ["lead travelers where they wish to go",
                           "keep newcomers safe on the paths"],
                 "voice": "bright and encouraging, plain-spoken",
                 "disposition": "outgoing; quick to help and to speak"}))
    w.add_entity(Item(id="lantern", name="a battered brass lantern", holder="clearing",
                      aliases=["lantern"]))
    eng = Engine(w, FakeProvider(), start_location="clearing", npc_act_gate=True)
    mind = eng.minds["wren"]
    scene = eng._scene_for(w.entities["wren"], "clearing")
    return (mind._system_prompt(scene), mind._decision_system(scene),
            mind.registry.json_schema(mind.offered))


UTTER = ['A traveler says to you: "Wren, will you lead me north up the trail?"',
         'A traveler says to you: "Wren, can you take me east to the hollow?"',
         'A traveler says to you: "Wren, would you guide me north, please?"']


def _stream(provider, system, user, max_tokens, schema, temperature):
    """One streamed call reusing the provider's own endpoint/key/body. Returns
    ttft, total, prompt/completion tokens, and decode tok/s."""
    payload = {"model": provider.model, "max_tokens": max_tokens,
               "temperature": temperature, "stream": True,
               "stream_options": {"include_usage": True},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               **provider.extra_body}
    if schema is not None:
        payload.update(provider._schema_payload(schema))
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    req = urllib.request.Request(provider.url, data=body, headers=headers, method="POST")
    t0 = time.perf_counter()
    ttft = None
    usage = None
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            try:
                o = json.loads(d)
            except ValueError:
                continue
            if o.get("usage"):
                usage = o["usage"]
            for ch in o.get("choices") or []:
                if (ch.get("delta") or {}).get("content") and ttft is None:
                    ttft = time.perf_counter() - t0
    total = time.perf_counter() - t0
    ct = (usage or {}).get("completion_tokens")
    pt = (usage or {}).get("prompt_tokens")
    dtps = ct / (total - ttft) if (ct and ttft and total - ttft > 0.05) else None
    return dict(ttft=ttft, total=total, pt=pt, ct=ct, dtps=dtps)


def _median(rows, k):
    vals = [r[k] for r in rows if r.get(k) is not None]
    return statistics.median(vals) if vals else 0.0


def bench_single(provider):
    blended, decision, schema = _prompts()
    scenarios = [
        ("decode_rate (256-tok out, no grammar)", "You are a concise, vivid writer.",
         "Describe a misty forest clearing at dawn in vivid, flowing detail.", 256, None, 0.7),
        ("act_gate_decision (grammar, t=0.2)", decision, None, 400, schema, 0.2),
        ("blended_turn (grammar, t=0.8)", blended, None, 400, schema, 0.8),
        ("blended_unconstrained (no grammar)", blended, None, 400, None, 0.8),
    ]
    print(f"\n== single request (median of {TRIALS}) — {provider.name} ==")
    print(f"{'scenario':<38}{'prompt':>7}{'out':>6}{'ttft':>7}{'total':>8}{'tok/s':>7}")
    for name, sysp, fixed, mx, schema_, temp in scenarios:
        rows = []
        for i in range(TRIALS):
            user = fixed or UTTER[i % len(UTTER)]
            try:
                rows.append(_stream(provider, sysp, user, mx, schema_, temp))
            except Exception as exc:
                print(f"  {name[:36]:36} trial {i + 1} ERROR: {exc}")
            time.sleep(0.3)
        if rows:
            print(f"{name:<38}{_median(rows,'pt'):>7.0f}{_median(rows,'ct'):>6.0f}"
                  f"{_median(rows,'ttft'):>7.2f}{_median(rows,'total'):>8.2f}"
                  f"{_median(rows,'dtps'):>7.1f}")


async def bench_concurrency(provider):
    system = ('Respond with a single JSON object: '
              '{"speech":"<one short in-character line>","actions":[]}')
    messages = [{"role": "user",
                 "content": 'A traveler says to you: "Wren, will you lead me north?"'}]
    print(f"\n== concurrency (pooled provider.complete) — {provider.name} ==")
    print(f"  {'N':>3}{'wall_s':>9}{'med_lat_s':>11}{'max_lat_s':>11}")
    for n in CONCURRENCY:
        async def one():
            t0 = time.perf_counter()
            await provider.complete(system, messages)
            return time.perf_counter() - t0
        t0 = time.perf_counter()
        lat = await asyncio.gather(*[one() for _ in range(n)], return_exceptions=True)
        lat = [x for x in lat if isinstance(x, float)]
        wall = time.perf_counter() - t0
        if lat:
            print(f"  {n:>3}{wall:>9.2f}{statistics.median(lat):>11.2f}{max(lat):>11.2f}")
        await asyncio.sleep(0.5)


def main():
    provider = get_default_provider()
    print(f"[bench] backend: {provider.name}")
    if isinstance(provider, FakeProvider) or not hasattr(provider, "url"):
        print("[bench] the active backend is not an HTTP model provider "
              "(set LOOM_PROVIDER=vllm|openrouter|ollama). Nothing to measure.")
        return
    bench_single(provider)
    asyncio.run(bench_concurrency(provider))


if __name__ == "__main__":
    main()
