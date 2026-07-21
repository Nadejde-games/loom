"""World atlas (B7) — see and validate a world at a glance.

The *read* side of Phase 7 authoring: load a world (a ``world.json`` file or a
directory of them), survey it, and render a readable overview — an adjacency map (and
a Mermaid graph in Markdown mode), a character sheet per NPC, an item table, the
world-level config, and a structural-lint report. Pure and offline: no model, no GPU,
no network. Doubles as a validator — exits with an error make it exit non-zero, so it
can gate a generated world in CI.

Run:
    PYTHONPATH=. python scripts/atlas.py [path] [--format text|md]

``path`` defaults to game/world/world.json; ``--format`` defaults to text.
"""
from __future__ import annotations
import argparse
import os
import sys

from loom.content import load_world
from loom.atlas import survey, render_text, render_markdown

_DEFAULT_WORLD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "game", "world", "world.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Render and validate a world (the B7 world atlas).")
    ap.add_argument("path", nargs="?", default=_DEFAULT_WORLD,
                    help="a world.json file or a directory of them "
                         "(default: game/world/world.json)")
    ap.add_argument("--format", choices=["text", "md"], default="text",
                    help="text (terminal) or md (Markdown + a Mermaid map)")
    args = ap.parse_args(argv)

    world, start = load_world(args.path)
    view = survey(world, start, source=os.path.relpath(args.path))
    render = render_markdown if args.format == "md" else render_text
    print(render(view))
    # Non-zero on any structural error, so the atlas can gate a generated world.
    return 1 if view.errors else 0


if __name__ == "__main__":
    sys.exit(main())
