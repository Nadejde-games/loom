"""``python -m authoring [world.json | world-dir]`` — launch the Explorer on a world.

Defaults to the shipped game world so the workbench opens on something real with no
arguments. The path may be a single ``world.json`` or a directory of region files (the
loader merges a directory, exactly as the engine does)."""
from __future__ import annotations
import os
import sys

from .app import WorkbenchApp

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_WORLD = os.path.normpath(
    os.path.join(_HERE, "..", "game", "world", "world.json"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else _DEFAULT_WORLD
    if not os.path.exists(path):
        print(f"no such world: {path}", file=sys.stderr)
        return 2
    WorkbenchApp.from_path(path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
