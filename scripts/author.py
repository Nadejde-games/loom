#!/usr/bin/env python3
"""Author a region from a brief and validate it — the write-side CLI (Phase 7, B7).

The command-line face of ``loom/ai/author.py``: a brief in, a validated region out.
Code frames a structurally-valid skeleton (``loom/authoring.py``), the model authors
only flavour, and the atlas judges — a generate -> validate -> repair loop that ships a
region only when it surveys with zero errors. Extend-mode by default: the region joins
the existing game world through one code-written attach edge.

    # preview (no write) — plan, flavour, and survey a region off the hilltop:
    set -a && . ./.env && set +a
    LOOM_PROVIDER=openrouter python scripts/author.py \\
        "Past the hilltop, a windswept moor runs to a ruined watchtower where a lonely
         beacon-keeper tends a cold signal-fire." --attach hilltop

    # write the combined world (existing + region) to a new file:
    ... --attach hilltop --out game/world/with_moor.json

Exits non-zero if the region could not be brought to zero errors — so it gates a
generated world in CI, exactly as ``scripts/atlas.py`` does for a hand-authored one.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom.content import load_world
from loom.atlas import survey, render_text, mermaid
from loom.ai.author import author_region
from loom.ai.provider import get_default_provider, OpenRouterProvider

_DEFAULT_WORLD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "game", "world", "world.json")
# The GM tier authors the world — wide-context creative work, like directing (the
# signed-off Decision 3). Authoring is not latency-critical, so the token budget is
# generous (a persona is far larger than an NPC's one-line turn — the provider's
# default of 400 would truncate it).
_AUTHOR_MODEL = "qwen/qwen3.6-27b"
_AUTHOR_MAX_TOKENS = 1200


def _provider(model: str, max_tokens: int):
    """The authoring provider. Honours ``LOOM_PROVIDER`` like the rest of the engine,
    but on OpenRouter (chat inference is OpenRouter-only) pins the GM model and a large
    token budget. Any other choice (the offline fake, a local backend) falls through to
    the standard resolver so the CLI still runs without a key."""
    choice = os.environ.get("LOOM_PROVIDER", "").strip().lower()
    if choice == "openrouter" or (not choice and os.environ.get("OPENROUTER_API_KEY")):
        return OpenRouterProvider(
            model=os.environ.get("LOOM_OPENROUTER_MODEL", model),
            api_key=(os.environ.get("LOOM_OPENROUTER_API_KEY")
                     or os.environ.get("OPENROUTER_API_KEY") or None),
            max_tokens=max_tokens)
    return get_default_provider()


def _combined_world(base_world, start: str, region: dict, attach: dict) -> dict:
    """The existing world with the region folded in — a valid standalone ``world.json``
    (the attach edge written onto the anchor room, base ``meta`` and ``start`` kept)."""
    from loom.authoring import world_to_dicts
    data = world_to_dicts(base_world)
    if attach:
        for loc in data["locations"]:
            if loc["id"] == attach["anchor"]:
                loc["exits"][attach["direction"]] = attach["entry"]
    out = {"start_location": start}
    for key in ("locations", "npcs", "items"):
        out[key] = data[key] + region.get(key, [])
    out.update(base_world.meta)          # director/clock/weather/loot/start_quests
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Author and validate a region from a brief.")
    ap.add_argument("brief", help="a prose brief describing the region to author")
    ap.add_argument("--extend", default=_DEFAULT_WORLD,
                    help="world to extend (file or directory; default: the game world)")
    ap.add_argument("--attach", default=None,
                    help="existing room id the region attaches to (default: the start)")
    ap.add_argument("--out", default=None,
                    help="write the combined world (existing + region) to this path")
    ap.add_argument("--map", action="store_true",
                    help="also print a Mermaid graph of the resulting world")
    ap.add_argument("--cap", type=int, default=3, help="max repair rounds (default 3)")
    ap.add_argument("--prefix", default="",
                    help="id prefix for new entities (e.g. 'moor_')")
    ap.add_argument("--model", default=_AUTHOR_MODEL, help="authoring model slug")
    args = ap.parse_args(argv)

    base_world, start = load_world(args.extend)
    attach = args.attach or start
    if attach not in base_world.locations:
        print(f"[author] no such room to attach to: {attach!r}", file=sys.stderr)
        return 2

    provider = _provider(args.model, _AUTHOR_MAX_TOKENS)
    print(f"[author] extending {os.path.relpath(args.extend)} at '{attach}' "
          f"via {getattr(provider, 'name', 'provider')}", file=sys.stderr)

    result = asyncio.run(author_region(
        provider, args.brief, base_world=base_world, start=start,
        attach_to=attach, cap=args.cap, prefix=args.prefix,
        log=lambda m: print(f"[author] {m}", file=sys.stderr)))

    if result.view is None:
        print(f"[author] {result.reason}", file=sys.stderr)
        return 1

    print(render_text(result.view))
    if args.map:
        print()
        print(mermaid(result.view))
    s = result.view.summary()
    print(f"\n[author] {s['locations']} rooms · {s['npcs']} npcs · {s['items']} items · "
          f"reachable {s['reachable']}/{s['locations']} · "
          f"{s['errors']} errors · {s['warnings']} warnings · "
          f"repair rounds: {result.rounds}", file=sys.stderr)

    if not result.ok:
        print(f"[author] NOT clean: {result.reason}", file=sys.stderr)
        return 1

    if args.out:
        out = _combined_world(base_world, start, result.region, result.attach)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"[author] wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
