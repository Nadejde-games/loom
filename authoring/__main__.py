"""``python -m authoring [world.json | world-dir]`` — launch the Explorer on a world.

Defaults to the shipped game world so the workbench opens on something real with no
arguments. The path may be a single ``world.json`` or a directory of region files (the
loader merges a directory, exactly as the engine does).

Live play needs ``OPENROUTER_API_KEY``; a tiny dependency-free loader pulls it from the
repo-root ``.env`` (python-dotenv is not in the authoring extra), so live minds work with
no shell dance. Without a key, play degrades to an offline dry run."""
from __future__ import annotations
import os
import sys

from .app import WorkbenchApp

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_DEFAULT_WORLD = os.path.join(_REPO_ROOT, "game", "world", "world.json")


def _load_env(repo_root: str) -> None:
    """Load repo-root ``.env`` into the environment (only keys not already set). A minimal
    KEY=VALUE parser — enough for OPENROUTER_API_KEY; no dependency."""
    path = os.path.join(repo_root, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else _DEFAULT_WORLD
    if not os.path.exists(path):
        print(f"no such world: {path}", file=sys.stderr)
        return 2
    _load_env(_REPO_ROOT)
    WorkbenchApp.from_path(path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
