# Quick start

One command takes you from a fresh clone to a running world:

```bash
git clone <this repo> && cd forever-unstarted-game
./setup.sh
```

The interactive wizard (macOS or Ubuntu/Debian) walks you through everything:

1. **Finds a Python** — it probes for Python 3.13 → 3.11 and tells you exactly what
   to install if none qualifies (`brew install python@3.12` on macOS,
   `apt-get install python3 python3-venv` on Linux).
2. **Creates a virtualenv** at `.venv/` (or reuses the one you have).
3. **Asks whether to include the authoring workbench** (default: yes) and installs
   the package with the right extras.
4. **Sets up a backend** — **Ollama** (local inference, no API key, needs disk and
   RAM/VRAM — the default) or **OpenRouter** (hosted, no GPU, needs an API key).
   For Ollama it will install the runtime, start the daemon, and pull the models
   you pick; for OpenRouter it asks for your key.
5. **Lets you pick a model for each of the three roles** — the authoring agent, the
   game-master director, and the NPC tier — with sensible per-platform defaults.
   See [Choosing models](models.md) for what to pick on your hardware.
6. **Picks a free port** (it checks whether the default `4000` is taken and scans
   for a free one if so) and **writes everything to `.env`**.
7. **Offers a self-check** — the offline test suite, about a minute, no network.

The wizard is **idempotent**: run it again any time — it reuses the virtualenv,
never overwrites `.env` values it isn't explicitly changing, and only pulls models
it hasn't pulled before.

## Then play

```bash
make server      # start the game server
make play        # connect a terminal client (in a second shell)
```

On connect the world asks your name; type it. Your identity — location, inventory,
quests, and the NPCs' memory of you — persists across sessions and restarts.

Other day-to-day targets (`make` on its own lists them all):

```bash
make workbench   # open the authoring workbench
make test        # run the offline test suite (no network, no GPU)
make smoke       # end-to-end check (server must be running)
make docs        # preview this documentation site locally
```

!!! tip "No GPU, no key, no problem"
    With no backend configured at all, the engine runs fully offline: NPCs are
    driven by a deterministic `FakeProvider`. Everything else — the world, the
    server, persistence, the client — works identically.

Prefer to see exactly what the wizard does, or to install by hand? Read
[Setup in detail](details.md).
