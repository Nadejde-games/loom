"""Persistence: make a running world survive a restart (Phase 5, slice 1).

The whole of Loom's runtime state lives in memory and dies on restart — entity
positions and inventories, forged loot, standing conditions, per-player quests,
clock/weather state, every NPC and director memory stream, and the chronicle. This
module is the layer that makes it durable, and it draws the one line every
long-running text world in the prior art draws (DikuMUD, LPMud, TinyMUD, PennMUSH —
see ``docs/spikes/persistence.md``): **the authored world is a *definition*, reloaded
from ``world.json`` at every boot; only the mutable *delta* on top of it persists.**

The shape is a **versioned JSON overlay**:

* ``snapshot(engine)`` walks the live engine and returns a plain dict of only what
  runtime mutated — never the authored definitions.
* ``restore(engine, save)`` composes that overlay back onto a freshly-loaded world:
  it overrides authored NPC positions, re-homes and re-mints items, replaces the
  conditions/quests, sets the clock/weather scalars, and reloads every mind's memory
  and the chronicle. It reads every field through ``dict.get`` so a save from an older
  build loads tolerantly.
* ``save_atomic`` / ``load`` are the crash-safe file layer — write-to-temp, fsync,
  atomic ``os.replace``, and a retained ``.bak`` that ``load`` falls back to.
* ``_migrate`` dispatches an old save forward through pure migration functions and
  stamps the current version, so a load→save cycle upgrades a file in place.

It is a *snapshot*, not an event log: dump the whole overlay, restore by loading it.
The chronicle rides along **as data** (its tail + cursor), never promoted to a rebuild
source. JSON now; SQLite waits for Phase 5's memory embeddings, behind the unchanged
``MemoryStream`` interface. Game-agnostic — a framework capability the game opts into
(``Engine.attach_persistence``); the base engine persists nothing.
"""
from __future__ import annotations
import json
import os
from .world import Item, Npc, Player

# The save-format version. Bump it whenever the overlay's shape changes and add a
# pure ``_migrate_vN`` to ``_MIGRATIONS`` — an old save then upgrades on load.
SAVE_VERSION = 1

# from_version -> pure function(save: dict) -> save: dict. Empty at v1 (nothing to
# migrate yet); the dispatch loop below is the seam a future field-move slots into.
_MIGRATIONS: dict = {}


# ---- snapshot: the live engine -> a plain-data overlay --------------------------

def snapshot(engine) -> dict:
    """Walk the engine and return the mutable runtime overlay as a JSON-ready dict.

    Only source-of-truth fields are captured; the derived indices
    (``Location.occupants``, ``World._contents``) are rebuilt on ``restore`` from
    them. Authored definitions (locations, personas, base item text, ``world.meta``)
    are never snapshotted — they reload from ``world.json``."""
    world = engine.world
    # Positions: authored characters only. Players are session-ephemeral (no durable
    # identity yet) — their held items fall to the floor below, exactly as
    # ``World.remove_entity`` drops them on disconnect.
    positions = {eid: ent.location_id
                 for eid, ent in world.entities.items()
                 if isinstance(ent, Npc) and ent.location_id}
    items = [_item_record(world, it) for it in world.entities.values()
             if isinstance(it, Item)]
    memory = {nid: mind.memory.state() for nid, mind in engine.minds.items()}
    if engine.director is not None:
        # "director" is a reserved memory key — no authored NPC carries that id.
        memory["director"] = engine.director.mind.memory.state()
    return {
        "version": SAVE_VERSION,
        "positions": positions,
        "items": items,
        "id_counter": world._id_counter,
        "pcount": engine._pcount,
        "conditions": world.conditions.state(),
        "quests": world.quests.state(),
        "clock": _clock_state(engine.clock),
        "weather": _weather_state(engine.weather),
        "memory": memory,
        "chronicle": engine.chronicle.state(),
    }


def _item_record(world, item: Item) -> dict:
    """One item's full record. An item a *player* is holding is snapshotted as lying
    on that player's floor: a held item belongs to the world, and with no durable
    player identity yet it rests where the player last stood (drop-on-disconnect
    semantics), so the reward survives even if the player never returns."""
    holder = item.holder
    ent = world.entities.get(holder) if holder else None
    if isinstance(ent, Player):
        holder = ent.location_id
    return {"id": item.id, "name": item.name, "description": item.description,
            "holder": holder, "aliases": list(item.aliases),
            "portable": bool(item.portable), "tier": item.tier,
            "tags": list(item.tags), "theme": item.theme}


def _clock_state(clock) -> dict | None:
    if clock is None:
        return None
    return {"minute": clock.minute, "phase": clock.phase.name}


def _weather_state(weather) -> dict | None:
    """The sky's index and pulse counter — *not* the RNG bit-state, which serialises
    poorly as JSON and buys nothing: the walk is reseeded on load, not replayed."""
    if weather is None:
        return None
    return {"index": weather._i, "pulses": weather._pulses}


# ---- restore: an overlay -> composed onto a freshly-loaded world ----------------

def restore(engine, save: dict) -> None:
    """Apply a snapshot overlay onto an already-built engine (``load_world`` +
    ``Engine`` + the ``attach_*`` wiring done). Order does not matter across the
    sections — each keys off source-of-truth ids — but this must run *after* the
    minds, director, clock and weather exist so their state has somewhere to land.
    Every read is a tolerant ``dict.get`` so an older save composes without error."""
    world = engine.world
    _restore_positions(world, save.get("positions") or {})
    _restore_items(world, save.get("items") or [])
    world._id_counter = int(save.get("id_counter", world._id_counter))
    engine._pcount = int(save.get("pcount", engine._pcount))
    if "conditions" in save:
        world.conditions.load_state(save["conditions"])
    if "quests" in save:
        world.quests.load_state(save["quests"])
    _restore_clock(engine.clock, save.get("clock"))
    _restore_weather(engine.weather, save.get("weather"))
    _restore_memory(engine, save.get("memory") or {})
    if "chronicle" in save:
        engine.chronicle.load_state(save["chronicle"])


def _restore_positions(world, positions: dict) -> None:
    """Override authored NPC positions. ``World.move`` rebuilds the occupants index
    from the truth; an unknown npc or a vanished destination is skipped (tolerant of
    a world edited since the save)."""
    for nid, loc in positions.items():
        if nid in world.entities and loc in world.locations:
            world.move(nid, loc)


def _restore_items(world, records: list) -> None:
    """Re-home authored items to where they now lie, and re-mint runtime-forged ones.

    An id already present is an authored item that moved: just re-home it. An id not
    present is a forged/spawned item gone with the last shutdown: reconstruct the full
    ``Item`` and add it. Either way ``World._contents`` rebuilds from ``holder``."""
    for rec in records:
        iid = rec.get("id")
        if not iid:
            continue
        holder = rec.get("holder")
        existing = world.entities.get(iid)
        if isinstance(existing, Item):
            _set_holder(world, existing, holder)
        else:
            item = Item(id=iid, name=rec.get("name", iid),
                        description=rec.get("description", ""), holder=holder,
                        aliases=list(rec.get("aliases", [])),
                        portable=bool(rec.get("portable", True)),
                        tier=rec.get("tier", ""), tags=list(rec.get("tags", [])),
                        theme=rec.get("theme", ""))
            world.add_entity(item)


def _set_holder(world, item: Item, holder) -> None:
    """Move an existing item to ``holder``, keeping the reverse index consistent —
    the same bookkeeping ``World.place_item`` does, but unconditional (persistence is
    authoritative: it re-homes even a fixed, non-portable item to where it was saved)."""
    old = item.holder
    if old and old in world._contents:
        world._contents[old].discard(item.id)
    item.holder = holder
    if holder:
        world._contents.setdefault(holder, set()).add(item.id)


def _restore_clock(clock, data) -> None:
    if clock is None or not data:
        return
    clock._minute = float(data.get("minute", clock._minute)) % (24 * 60)
    clock._phase = clock._phase_at(clock._minute)


def _restore_weather(weather, data) -> None:
    """Set the sky's index and pulse counter. The RNG is left as freshly seeded at
    attach time — the walk continues from the saved state, reseeded not replayed."""
    if weather is None or not data:
        return
    idx = int(data.get("index", weather._i))
    weather._i = min(max(0, idx), len(weather.states) - 1)
    weather._pulses = int(data.get("pulses", weather._pulses))


def _restore_memory(engine, memory: dict) -> None:
    """Reload each mind's memory stream, keyed by agent id (``"director"`` reserved).
    Memory for an agent no longer in the world is ignored (tolerant of a world edit)."""
    for nid, entries in memory.items():
        if nid == "director":
            if engine.director is not None:
                engine.director.mind.memory.load_state(entries)
        elif nid in engine.minds:
            engine.minds[nid].memory.load_state(entries)


# ---- the crash-safe file layer --------------------------------------------------

def save_atomic(path: str, save: dict) -> None:
    """Write ``save`` to ``path`` so a crash mid-write can never corrupt the live
    file. Serialise to a sibling ``.tmp`` (same directory, so the rename stays on one
    filesystem and is therefore atomic), ``fsync`` it, move the current file aside to
    ``.bak``, then atomically ``os.replace`` the temp into place. After a crash, a
    complete save is always readable from ``path`` or its ``.bak`` (see ``load``)."""
    tmp = path + ".tmp"
    data = json.dumps(save, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)


def load(path: str) -> dict | None:
    """Read, migrate, and return the overlay at ``path`` — or ``None`` if there is no
    readable save. Falls back to the ``.bak`` if the primary is missing or corrupt
    (the crash window in ``save_atomic``), so the prior good state is never lost."""
    save = _read(path)
    if save is None:
        save = _read(path + ".bak")
    if save is None:
        return None
    return _migrate(save)


def _read(path: str) -> dict | None:
    """Parse one save file, or ``None`` if absent or not valid JSON (a torn write)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _migrate(save: dict) -> dict:
    """Dispatch an old save forward through the pure migration chain and stamp the
    current version. A save at (or, tolerantly, above) the current version passes
    through unchanged; a version with no registered migration stops the chain but
    still loads — ``restore`` defaults every missing field."""
    v = int(save.get("version", SAVE_VERSION))
    while v < SAVE_VERSION and v in _MIGRATIONS:
        save = _MIGRATIONS[v](save)
        v += 1
    save["version"] = SAVE_VERSION
    return save
