# Loom + the forever game

Two things live here:

- **`loom/`** — a reusable, game-agnostic framework for AI-driven, text-first,
  server–client worlds. (Working name; rename freely.)
- **`game/`** — the first world built on Loom. Content and configuration only.
- **`client/`** — a minimal reference terminal client.

The framework core is **dependency-free** and runs offline: with no API key it
drives NPCs through a deterministic `FakeProvider`. Set `ANTHROPIC_API_KEY`
(and `pip install -e ".[anthropic]"`) and every NPC upgrades to Claude with no
code change.

## Architecture in one screen

```
clients ──(newline-delimited JSON envelopes: {"c":channel,"d":data})── server
   terminal reads "text"/"system"; rich clients later add "map"/"entities"
                              │
                     GameServer (async TCP)  ── continuous GameLoop
                              │
                           Engine  ── commands, sessions, player state
                              │
        World (locations/entities, editable JSON)   AI layer
                                              (LLMProvider → NpcMind → MemoryStream)
```

Design commitments:
- **Protocol separated from transport** — TCP now, WebSocket later, same schema.
- **World is editable data** (`game/world/world.json`), not code.
- **AI behind one interface** — `LLMProvider`; the engine can't tell fake from real.
- **Never execute raw model text** — dialogue only for now; state changes will
  go through schema-validated tool-calls (the seam is in place).

## Run it

Terminal 1 — start the server:

```bash
cd loom-... && PYTHONPATH=. python3 game/main.py
```

Terminal 2 — connect and play:

```bash
PYTHONPATH=. python3 client/terminal.py
```

Try: `look`, `say hello`, `go north`, `go south`, `help`, `quit`.

Or run the automated end-to-end check (server must be running):

```bash
PYTHONPATH=. python3 scripts/smoke.py
```

## Status

First milestone: the talking-NPC vertical slice — server, protocol, loop,
world, terminal client, and one memory-bearing NPC, all wired end to end.
