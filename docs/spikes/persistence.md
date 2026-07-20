# Spike — Persistence (prior art for making the world survive a restart)

**Date:** 2026-07-19 · **Question:** how should Loom persist a world that is meant to run
*forever*, when today everything — entity positions & inventories, spawned/forged loot,
standing conditions, per-player quests, clock/weather state, every NPC and director memory
stream, and the chronicle — lives only in memory and dies on restart? Specifically: what
to persist vs what to reload from `world.json`; snapshot vs event-log; JSON vs SQLite vs
pickle; when to write; how to version — and the tightest first slice that proves a forged
reward survives a reboot. · **Method:** survey of five prior-art families
(DikuMUD/CircleMUD, LPMud, Evennia, TinyMUD/PennMUSH; the snapshot-vs-event-sourcing
literature; the Python save-format/serialization literature; the Stanford
Generative-Agents memory stream), verified against reference-level sources rather than
blog hand-waving, then mapped onto Loom's actual runtime state (`loom/world/world.py`,
`loom/world/entity.py`, `loom/world/conditions.py`, `loom/quest.py`, `loom/chronicle.py`,
`loom/ai/memory.py`, `loom/clock.py`, `loom/weather.py`, `loom/content.py`,
`loom/engine.py`). No code written.

---

## Verdict

**Persist a versioned JSON *overlay* of the mutable runtime state, dumped atomically on
shutdown and on a periodic autosave, and always reload the authored `world.json` as the
definition beneath it.** Every long-running text world in the survey — DikuMUD, LPMud,
TinyMUD, PennMUSH — draws the same hard line Loom already drew in its own architecture: the
**authored world is static and reloaded from source**, and **only the mutable delta
persists** (DikuMUD keeps *player files*, not the world; the world is reset from zone
files). Loom's `world.json`→runtime split *is* that line, so the persistence layer should
be a **delta on top of the authored source**, not a full snapshot that swallows the
authored definitions and freezes builder edits. For the first slice, take the **snapshot**
model (dump the whole runtime overlay; restore by loading it) that the MUD lineage
overwhelmingly uses — **not** event sourcing, whose complexity Fowler documents and whose
benefits Loom does not yet need — and note that Loom's existing chronicle is a **bounded,
lossy perception digest, not a complete change-log**: persist it *as data*, do not promote
it to a rebuild source. Pick **JSON** (stdlib, human-readable, git-diffable, the same shape
as the authored file the "world is editable data" commitment already blesses) over SQLite
(correct, but its payoff — transactional incremental writes, blob columns for embeddings —
only arrives with Phase 5's growing memory streams and vectors, exactly where the plan
already commits to SQLite) and over pickle (a version-fragility and security footgun the
save-game literature explicitly warns against). Version with a top-level integer + tolerant
load + pure migration functions. The minimal proof: forge an item, drop it, shut down,
restart — the item is still on the floor with its `tier`/`tags` intact.

---

## 1. MUD/MUSH persistence — the authored-vs-mutable split is the master lesson

**DikuMUD / CircleMUD — static world reloaded from source; only player state persists.** In
CircleMUD the entire world (rooms, mobs, objects, zones) is authored as text files in
`lib/world`, "parsed directly into memory by `db.c`" at boot and **never written back**.
World *state* is not persisted at all — it is **reset on a timer**: "the server sets each
zone to its initial state as defined by the author, and periodically resets the zone to its
initial state (a *zone reset*)… All zones are reset when the server first boots, and
periodically reset again while the game is running" ([CircleMUD Builder's Manual — Zone
Files](https://www.circlemud.org/cdp/building/building-6.html); [The Mechanics of World
Building](https://www.circlemud.org/cdp/building/building-2.html); [CircleMUD — The
World](https://www.circlemud.org/world.html)). The **only** durable runtime state is the
**player file** — the character and, via *rent* or *crash-save*, the objects they carry —
written on quit and periodically so a crash loses little ([rec.games.mud servers
FAQ](http://www.mudconnect.com/mudfaq/mudfaq-p4.html); crash-save/rent is canonical Diku
knowledge, not fetched live this spike). **The lesson is the whole spike in miniature:**
authored content is a *definition* reloaded from source; a builder's edit to a room takes
effect on the next boot; only what the *players and the simulation mutated* is saved, and
it is saved *apart* from the authored world.

**LPMud — `save_object` / `restore_object`, a per-object flatfile of a variable set, with
explicit format versioning.** LPMud persists an object by encoding "the saveable variables
of the current object" — every global **not declared `static` or `nosave`** — into a
flatfile, restored by `restore_object` ([`save_object(E)` — LDMud efun
docs](https://wunderland.mud.de/mud/doc/efun/save_object.html)). Two details matter for
Loom. First, the unit of persistence is **a chosen subset of an object's variables**, not
the whole process — the programmer *marks* what is transient (`nosave`) vs durable, the same
authored-vs-mutable discrimination made per-field. Second — and directly relevant to §4 —
`save_object` takes an explicit **savefile-format version** argument (`-1` native, `0`
legacy ≤3.2.8, `1` for closures/symbols, `2` for improved floats), a real-world instance of
*versioning the save format itself* so an old file is still readable.

**Evennia — the opposite pole: the whole game state in a database, at the cost of a full
ORM.** The modern Python MUD framework persists *everything* through Django's ORM. Each
in-game object is a "typeclass" — a Python class decorating a DB row — and arbitrary
per-object `Attributes` are stored as rows whose value is a **`PickledObjectField`** (Python
objects pickled into the DB); iterables become `PackedList`/`PackedDict` so in-place
mutation writes through, and references to other game objects are stored as **dbrefs**,
aggressively cached in memory via an idmapper so typeclass instances outlive a vanilla
Django query ([Evennia — Attributes](https://www.evennia.com/docs/latest/Components/Attributes.html);
[Evennia — Typeclasses](https://www.evennia.com/docs/latest/Components/Typeclasses.html)).
This *works* and is battle-tested, but it is exactly the cost Loom's commitments reject: a
full ORM + database + a pickle-in-DB storage model, with the game's data model welded to
Django. It is the "SQLite is a better choice for hundreds of NPCs with individual state"
argument taken to its terminus — the right answer for a platform, over-heavy for Loom's
first slice.

**TinyMUD / PennMUSH / TinyMUSH — periodic full-database flatfile dump + dump-on-shutdown.**
The MUSH lineage is the purest **snapshot** model. PennMUSH "loads the database entirely
into memory while the game is running… and a copy of the database is dumped onto disk
periodically"; the dump is a **flatfile** terminated by a literal `***END OF DUMP***`
sentinel, and `@dump` can run in a `/paranoid` mode that consistency-checks the disk copy
and writes progress to a checkpoint log every *N* objects ([Flatfile (PennMUSH) — TinyMUX
wiki](https://wiki.tinymux.org/index.php/Flatfile_(PennMUSH));
[penncmd.hlp](https://github.com/pennmush/pennmush/blob/master/game/txt/hlp/penncmd.hlp)).
Original TinyMUD's netmud process "will write a checkpoint out to `dump-file` every 3600
seconds," the interval set by `DUMP_INTERVAL` in `config.h` ([TinyMUD 1.5.4
README](https://mudbytes.net/files/view/721/?path=tinymud-1.5.4/README)). The cadence lesson
is explicit: **dump on a fixed interval and on shutdown**, hold the live world in memory,
and treat the disk file as a recoverable checkpoint. (Notably, TinyMUX's *modern* rewrite
moved the dump to a SQLite WAL checkpoint — the same JSON→SQLite migration path this spike
recommends Loom take across phases.)

**Synthesis of §1:** across four independent lineages the pattern is invariant — **authored
world = static, reloaded from source; mutable state = a separately-persisted delta or a
periodic full dump.** DikuMUD keeps the delta *small* (player files) and resets the rest;
MUSH dumps the *whole* mutable database on a timer; Evennia persists everything continuously
through a DB. Loom's `world.json`-as-definition already sits at the DikuMUD end of that
spectrum, which is the cheapest and the most aligned with "the world is editable data."

## 2. Snapshot vs event sourcing — the two canonical durable-state approaches

The reference framing is Fowler's. **Event sourcing** = "capture all changes to an
application state as a sequence of events," so you can "discard the application state
completely and rebuild it by re-running the events from the event log on an empty
application," and **snapshots are an optimization on top**: "it is often useful to build
snapshots of the working copy so that you don't have to process all the events from scratch
every time," e.g. start the day "from an overnight snapshot" and replay only the day's
events after a crash ([Fowler — Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html)).
Its advantages are auditability, temporal queries ("state at any point in time"), and fine
crash-recovery granularity. Its documented costs are real: replaying events that touched
**external systems** risks re-sending notifications and "requires sophisticated gateway
logic"; **bug-fix and code-change semantics** get subtle (an old event replayed through new
code may not reproduce the historical outcome); and **time-dependent logic** ("bi-temporal")
Fowler flatly calls "very messy."

**A plain periodic snapshot** — dump the full current state, restore by loading the latest
dump — is the MUSH/Diku model of §1. Trade-offs vs event sourcing, framed for a game: a
snapshot loses everything *since* the last dump on a crash (coarse granularity) but is
trivial to implement, trivial to reason about, cheap to restore (no replay), and immune to
the external-replay and code-change hazards; an event log gives per-event recovery and a
free audit trail but pays replay cost, log-growth, and the versioning-of-events burden.

**Can Loom's chronicle double as the event log?** No — and this is the load-bearing
assessment. `loom/chronicle.py` is a `deque(maxlen=200)` of salient *perceptions* —
"arrivals, speech, actions, moves, ambient beats" — with a monotonic `_seq` cursor whose
sole job is the director's "what is new since I last looked" laziness gate. It is
**bounded** (old events fall out while `_seq` keeps growing) and **lossy by construction**:
it records *narratable beats*, not every state mutation, and `record("")` is silently
dropped. You cannot rebuild the world by replaying it — a forged item's full record, a
quest's per-player status, a condition's `(tag,text)` are not reconstructible from
"Wanderer-1 took the brass key." **So the chronicle is a *derived perception digest*, not a
change-log.** The correct move is to **persist the chronicle as data** — its surviving tail
plus the `_seq` cursor — inside the snapshot, so the director's continuity survives a reboot
(it does not suddenly think nothing ever happened), and to **not** attempt to make it the
source of truth. This matches the file's own note that it is "reusable later as the
substrate for persistence." Event sourcing stays on the shelf until Loom has a concrete need
for audit/time-travel that a snapshot cannot serve.

## 3. Storage format in a dependency-light Python app — JSON vs SQLite vs pickle

**JSON** is the save-game default for exactly Loom's constraints. The guidance is blunt:
"Python's built-in `json` module is the best default for save files, producing
human-readable output… use JSON with the `json` module, `os.replace()` for atomic writes,
and include a version number in every save file for forward migration" ([Game Save Best
Practices — Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame)). For Loom it is
uniquely aligned: it is **stdlib** (honours dependency-lightness), **human-readable and
git-diffable** (honours "the world is editable data" — the runtime overlay is the *same
shape* as the authored `world.json` the loader already parses in `loom/content.py`), and
**versions cleanly** (a top-level int, §4). Its weakness is that a growing file is rewritten
whole each dump and it has no place for binary vectors — precisely the pressures that don't
exist until Phase 5.

**SQLite** is the right *second* store, and the plan already knows it. It is stdlib
(`sqlite3`), transactional (a dump can't half-commit), queryable, comfortable with large
memory tables (thousands of per-agent memory rows), supports **incremental** writes (append
one memory row without rewriting the world), and is the natural home for **embedding blobs**
— Phase 5's plan literally reads "start SQLite + brute-force cosine" for embedding retrieval
(`docs/PLAN.md` §Phase 5). The general guidance agrees: SQLite beats a flat JSON file "for
games with large amounts of data — hundreds of NPCs with individual state… complex quest
systems," and "supports transactions for atomicity and handles concurrent access better than
flat files if your game uses threading" ([Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame)).
None of those pressures bind the *first* slice, so SQLite is deferred, not rejected — the
recommendation stages the JSON→SQLite migration deliberately (as TinyMUX did).

**Pickle** is rejected. It is fast and captures arbitrary object graphs, but the save-game
literature warns on both axes that matter to a versioned, forever-running game: it is
**version-fragile** — "tightly tied to the Python version and environment… if the class
definition changes, unpickling might fail or result in a corrupted object" — and it is a
**security footgun** — "a malicious pickle can compromise their machine — stick with JSON
for save data," echoing the stdlib's own red-box warning never to unpickle untrusted data
([Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame); [Python —
`pickle`](https://docs.python.org/3/library/pickle.html)). For save data that "must version
cleanly" across years of code changes and that a builder should be able to read and
hand-edit, pickle is disqualified on both counts. (Note the irony that Evennia stores
Attributes as pickles-in-a-DB — acceptable only because the DB is trusted and single-owner;
Loom gains nothing and inherits the fragility.)

## 4. Schema/versioning & migration of save data

The standard pattern is small and non-negotiable, and Loom's commitments already name it a
cross-cutting concern. Every save begins with **something immutable that identifies its
version** — "never parse a file without knowing its schema version; every format should
start with… a version integer" — and load **dispatches on that version through a chain of
pure migration functions** to the current shape: "structure loads by reading the header
(version…), dispatching to `LoadV1`, `LoadV2`… materializing a single in-memory model your
gameplay code understands, and writing using only the latest serializer," with migrators
kept as **side-effect-free pure functions** (`MigrateV2ToV3()`) that are trivially
unit-testable ([Reliable Save Migration —
GamineAI](https://gamineai.com/blog/reliable-save-migration-unity-godot-old-files-2026);
[Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame)). Two robustness rules
round it out: **tolerant loading** — ignore unknown fields, **default new fields** that an
older save lacks — and **always write the latest version**, so a load-migrate-save cycle
upgrades a file in place. LPMud's `save_object` format argument (§1) is the same idea shipped
in production for two decades: the format is explicitly numbered so a newer driver still
reads an older savefile ([LDMud efun docs](https://wunderland.mud.de/mud/doc/efun/save_object.html)).
For Loom this is a single top-level `"version": 1`, a `_migrate(save)` dispatcher, and
`dict.get(key, default)` reads at the composition seam — cheap, and demanded by commitment #2.

## 5. LLM-agent / memory-stream persistence

The reference architecture is the Stanford Generative Agents **memory stream**: "a long-term
memory module that records, in natural language, a comprehensive list of the agent's
experiences" — a **timestamped log** of every perception. Retrieval scores each record by a
weighted sum of three signals: **recency** (exponential decay since last access),
**importance** (an LLM-rated salience score), and **relevance** (cosine similarity between
the query embedding and the memory's embedding) ([Generative Agents,
arXiv:2304.03442](https://arxiv.org/pdf/2304.03442)). The persistence shape this implies is a
**growing, append-only list of records, each carrying `{text, timestamp, kind}` today and
gaining `importance:int` and `embedding:vector` later.** That is *exactly* Loom's
`loom/ai/memory.py`: `MemoryEntry(text, kind, t)` in an append-only `MemoryStream.entries`,
whose docstring already says "Importance scoring, embedding + relevance retrieval…
deliberately deferred; each slots in behind this same interface." The storage question is
therefore: **what store accommodates a per-agent record list that grows without bound and
later grows a vector field, without a rewrite?** A JSON list serializes the current three
fields for free and can carry an `importance` int later with no format change — but appends
rewrite the whole file, and a float vector is a poor JSON citizen (verbose, lossy, slow to
scan). The agent-framework norm is therefore **rows + a vector column/blob** in SQLite (the
shape MemGPT/Letta-style stores and the plan's own "SQLite + brute-force cosine" both take):
one row per memory, `text/kind/t/importance` as columns, `embedding` as a `BLOB`, scanned
brute-force for cosine until scale demands an index. **Conclusion:** persisting memory
streams as JSON now costs nothing and buys NPC continuity immediately; the *move to SQLite is
the moment embeddings and importance land (Phase 5)* — and because the `MemoryStream`
interface hides the store, that migration touches no callers.

One structural note the codebase forces: **memory does not live on `World`.** Each
`MemoryStream` hangs off an AI-layer mind (`Engine.minds[id].memory`, and
`DirectorMind.memory`), not off a world entity. A "world snapshot" alone is therefore
insufficient — the persistence layer must span **`World` + the minds' memory + the
chronicle**, keyed by agent id (with `"director"` a reserved key).

---

## Comparison

| Axis | DikuMUD / CircleMUD | LPMud (`save_object`) | TinyMUD / PennMUSH | Evennia | **Loom (proposed)** |
|---|---|---|---|---|---|
| **Authored vs mutable** | World reloaded from source; **only player files persist**; world *reset* on timer | Per-object: `static`/`nosave` = transient, rest saved | Whole mutable DB dumped; authored & mutable co-mingled in one db | Everything in the DB (no authored/mutable split) | **Reload `world.json` (definition); persist a runtime *overlay* (delta)** |
| **Snapshot vs log** | Snapshot (player file) + world reset | Snapshot (per-object flatfile) | **Periodic full snapshot (`@dump`)** | Continuous ORM writes | **Full-state snapshot overlay; chronicle persisted as data, not as a log** |
| **Format** | Custom textfiles | Flatfile, versioned | Flatfile (`***END OF DUMP***`) | RDBMS + pickled Attributes | **JSON now → SQLite at Phase 5 (embeddings)** |
| **When it writes** | On quit + crash-save/rent + boot reset | On explicit call | **Every 3600 s (`DUMP_INTERVAL`) + shutdown** | On change (ORM) | **On shutdown + periodic autosave; atomic temp→rename** |
| **Versioning** | Format tied to server build | **Explicit format arg (-1/0/1/2)** | Flatfile format versions | Django migrations | **Top-level `version` int + pure migrations + tolerant load** |
| **Fit to an LLM game** | Authored/mutable split is the model | Field-level save/nosave discrimination | Snapshot cadence & shutdown-dump cadence | Over-heavy (full ORM welded to Django) | **JSON overlay matches "editable data"; SQLite waits for memory/vectors** |

---

## Recommendation for Loom

### The authored-vs-runtime split — persist a delta/overlay, always reload `world.json`

Take a clear position: **a delta/overlay, not a full snapshot that includes the authored
definitions.** `world.json` remains the *definition* — locations (id/name/description/exits),
NPC personas, base items, `world.meta` (director persona, tone, loot tables, clock/weather
config), `start_quests` — and is always reloaded via `load_world` at boot
(`loom/content.py`). On top of it, a single versioned save file carries **only what runtime
mutated**. This is the DikuMUD line (§1) and it earns three things a full snapshot forfeits:
builder edits to authored content take effect on the next boot instead of being frozen into
a stale dump; the save file stays small and legible; and the "world is editable data"
commitment survives intact (the authored file is never overwritten by the engine).

**Persist (the mutable runtime overlay):**
- **Entity positions** — per `Character`, `location_id`; do **not** persist the
  `Location.occupants` mirror or the `World._contents` index: both are *derived*, and
  `World.add_entity`/`move`/`place_item` rebuild them from the source-of-truth fields on
  load. Store truth, rebuild indices.
- **Inventories & item placement** — per `Item`, `holder` (the single source of truth behind
  take/drop/give).
- **Spawned/forged items** — the *full record* of every runtime-minted `Item` not present in
  `world.json`: `id, name, description, holder, aliases, portable, tier, tags, theme` — plus
  **`World._id_counter`**, so post-restart `fresh_id` never collides with a saved `item_N`.
- **Standing conditions** — `World.conditions` in full (`_by_location` and the `_world` scope).
- **Per-player quests** — `World.quests` (`QuestBook._by_player` records + `_seq`). *(Slice-1
  caveat: quests are keyed by an ephemeral player id — they only re-associate once a stable
  player identity exists. Persist the structure forward-looking, but the first-slice proof
  rides on world/floor state, not on quest restoration.)*
- **Clock / weather state** — `WorldClock._minute` (+ current phase name) and `WeatherSystem`
  current index `_i` / `_pulses`. **Flag:** the weather `random.Random` internal state
  serializes poorly as JSON; store the sky index and *reseed* the walk on load rather than
  persisting RNG bit-state — exact-RNG fidelity is not worth the ugliness in slice 1.
- **Per-agent memory streams** — every `MemoryStream.entries` keyed by agent id (NPC ids plus
  the reserved `"director"`), each entry as `{text, kind, t}`. Cheap now; the eventual
  `importance`/`embedding` fields slot into the same records.
- **The chronicle** — its surviving tail (`recent()`) plus the `_seq` cursor, so the
  director's "what's new since N" continuity is not reset to zero.
- **Player accretion** — persist per-player history/memory *on the same substrate as NPCs*,
  **keyed by a durable player identity** — see the deferral note; this is real but blocked on
  an identity concept that doesn't exist yet.

**Always reload from `world.json` (never persisted):** the authored definitions above. New
authored entities appear on next boot; a forged item that shares no id with them is added by
the overlay. Composition on boot is: `load_world(world.json)` → build `World` +
`start_location` → bind minds (Engine + attach_director) → **apply overlay** (override
positions/holders on authored entities; `add_entity` the forged ones; replace
`conditions`/`quests`; set clock/weather scalars) → **restore memory streams + chronicle**.
Reads at that seam use `dict.get(key, default)` so a save from an older build loads
tolerantly.

### Snapshot vs event-log — snapshot, and the chronicle stays data

**Primary mechanism for the first slice: a full-state snapshot of the runtime overlay** (dump
it all; restore by loading it and composing onto the reloaded world). This is the MUSH/Diku
model (§1), it is trivial to implement and reason about, it restores with no replay, and it
dodges the external-replay and code-change hazards Fowler documents for event sourcing (§2).
**The existing chronicle does *not* feed it as an event log** — it is bounded and lossy, a
perception digest, not a complete change-record (§2). It is **persisted as data inside the
snapshot** (tail + cursor) so continuity survives, and left as a candidate to *seed* a real
event log only if a future need for audit/time-travel appears.

### Format — JSON now, SQLite when embeddings arrive

**JSON for the first slice.** Stdlib, human-readable, git-diffable, the same shape the loader
already speaks, versions cleanly — it satisfies every commitment (editable data,
dependency-light, clean versioning) at once (§3). **SQLite is the staged successor, adopted
in Phase 5** as the home for the *growing* memory streams and their *embedding blobs* +
brute-force cosine — the one place JSON's whole-file-rewrite cost and vector-hostility
actually bite, and where `docs/PLAN.md` already commits to SQLite. Because `MemoryStream`
hides its store, that migration is local. **Pickle is rejected** on version-fragility and
security grounds (§3).

### When to persist — shutdown + periodic autosave, written atomically

- **On graceful shutdown** — the primary write for slice 1 (MUSH/Diku dump-on-shutdown).
- **Periodic autosave** — a fixed-interval dump on the game loop, stealing TinyMUD's
  `DUMP_INTERVAL` (3600 s is the canonical reference; a few minutes suits a livelier world).
  A forever-game *will* crash; shutdown-only loses everything since boot.
- **Crash safety — write-to-temp-then-atomic-rename.** Serialize to `world.save.json.tmp` in
  the *same directory* as the target (so the rename stays intra-filesystem and therefore
  atomic — `os.replace` "will be an atomic operation" on POSIX and replaces on Windows too,
  but "may fail if src and dst are on different filesystems"; [Python —
  `os.replace`](https://docs.python.org/3/library/os.html)), `fsync`, then `os.replace` over
  the live file; retain the prior file as `world.save.bak`. A crash mid-write can never
  corrupt the live save — the PennMUSH `/paranoid` checkpoint instinct, in three stdlib calls.

### Schema versioning — the minimal version-field pattern

A top-level `"version": 1`; on load, read the version first and dispatch through a chain of
**pure** `_migrate_vN_to_vN1(save)` functions to the current shape; **tolerant load** (ignore
unknown keys, default missing new fields via `.get`); **always write the latest version** so
a load→save cycle upgrades a file in place (§4). Migrators are pure and unit-tested against
fixture saves — cheap insurance that the day a field moves, a two-year-old save still boots.

### A minimal first slice

The tightest end-to-end proof of persistence:

1. **Save on shutdown** — serialize the runtime overlay (entity positions & holders; the full
   records of spawned/forged items + `_id_counter`; standing conditions; clock/weather
   scalars) to a **versioned JSON** file, written **atomically** (temp → `fsync` →
   `os.replace`, keep `.bak`).
2. **Restore on startup** — `load_world(world.json)`, build the Engine + attach the director,
   then apply the overlay onto the composed world before play begins.
3. **The proof:** the forge mints an item (`World.spawn_item`, `tier`/`tags` attached), it is
   dropped to a location floor (or already lies in the world), the server shuts down,
   restarts — **the item is still on that floor, with its `tier`/`tags` intact, perceivable
   through the existing `look`/`take` path.** Because carried items drop to the floor on
   disconnect (`World.remove_entity`), this proof needs no durable player identity: it rides
   entirely on world/floor state.

Including the **memory streams and the chronicle tail** in the same overlay is nearly free
(plain JSON lists of the fields that already exist) and buys the single most valuable
"forever" property — NPCs and the director *remember across reboots* — so fold them in as
slice 1b rather than a separate phase.

**Defer:** memory **importance scores and embedding vectors** (Phase 5, and the trigger to
move memory to **SQLite**); **fine-grained / event-driven autosave** (start with shutdown + a
coarse interval); **multi-file save sharding by region**; **exact weather-RNG fidelity**
(reseed, don't serialize bit-state); and **durable player accretion** — persisting player
personality/history requires a **stable player identity** (account/name), which the current
session-keyed, disconnect-ephemeral `Player` model (`Engine.players`, `remove_entity` drops
inventory to the floor) does not yet provide; land it when identity lands, on the very same
memory-stream substrate as NPCs.

---

## What to steal / what to skip

**Steal**
- **The authored-vs-mutable line** (DikuMUD/CircleMUD, §1) — reload `world.json` as the
  definition; persist only the runtime *delta* on top. Loom's architecture already draws this
  line; the persistence layer must honour it rather than snapshot over it.
- **The periodic-dump + dump-on-shutdown cadence** (TinyMUD `DUMP_INTERVAL`, PennMUSH periodic
  dump, §1) — a fixed-interval autosave plus a shutdown save; hold the live world in memory.
- **The full-state snapshot model** (MUSH lineage, §2) over event sourcing — trivial to
  restore, no replay, none of Fowler's external-replay/code-change hazards.
- **The version-header + pure-migrations + tolerant-load pattern** (GamineAI/Bugnet, and
  LPMud's numbered save format, §4) — a top-level `version` int, dispatch to pure migrators,
  default missing fields, always write latest.
- **Atomic temp→rename with a `.bak`** (Bugnet; `os.replace`, §3/When) — the standard
  save-game crash-safety technique, three stdlib calls.
- **Rows-plus-vector-blob as the eventual memory store** (Generative Agents + agent-framework
  norm, §5) — the shape the plan already names (SQLite + brute-force cosine); adopt it when
  importance/embeddings land, behind the unchanged `MemoryStream` interface.

**Skip (for now)**
- **A full snapshot that swallows the authored world** — it freezes builder edits and bloats
  the save; persist a delta and reload `world.json` (§1, Recommendation).
- **Event sourcing / promoting the chronicle to a rebuild log** — the chronicle is bounded and
  lossy; persist it as data, don't make it the source of truth (§2). Event sourcing waits for
  a concrete audit/time-travel need.
- **Pickle** — version-fragile and a security footgun for save data that must version cleanly
  across years (§3).
- **SQLite in slice 1** — correct store, wrong phase; its payoff (incremental writes, vector
  blobs) only appears with Phase 5's growing memory + embeddings (§3, §5).
- **A full ORM (the Evennia model)** — right for a platform, but it welds the data model to a
  database and breaks dependency-lightness (§1).
- **Exact weather-RNG serialization and durable player accretion in slice 1** — reseed the
  weather walk; defer player history until a stable player identity exists (Recommendation/Defer).

---

## Appendix — sources & verification status

| Claim area | Source | Status |
|---|---|---|
| CircleMUD: world reloaded from text into memory; zone *reset* to author's initial state on a timer | [Builder's Manual — Zone Files](https://www.circlemud.org/cdp/building/building-6.html), [World Building](https://www.circlemud.org/cdp/building/building-2.html), [The World](https://www.circlemud.org/world.html) | Verified (primary manual + search) |
| Diku: only player files persist; rent/crash-save for objects | [rec.games.mud servers FAQ](http://www.mudconnect.com/mudfaq/mudfaq-p4.html) | Server FAQ verified; **crash-save/rent specifics are canonical Diku knowledge, not fetched live** |
| LPMud `save_object`: saves non-`static`/non-`nosave` globals; explicit savefile format versions (-1/0/1/2) | [LDMud efun — `save_object(E)`](https://wunderland.mud.de/mud/doc/efun/save_object.html) | Verified (fetched); **line-by-line flatfile syntax not detailed in the doc** |
| Evennia: Django ORM typeclasses; Attributes stored via `PickledObjectField`; `PackedList/PackedDict`; dbrefs; idmapper caching | [Evennia — Attributes](https://www.evennia.com/docs/latest/Components/Attributes.html), [Typeclasses](https://www.evennia.com/docs/latest/Components/Typeclasses.html) | Verified via docs search (not full-page fetch) |
| PennMUSH: whole db in memory, periodic flatfile dump, `***END OF DUMP***`, `/paranoid` checkpoint; TinyMUD checkpoint every 3600 s (`DUMP_INTERVAL`) | [Flatfile (PennMUSH)](https://wiki.tinymux.org/index.php/Flatfile_(PennMUSH)), [penncmd.hlp](https://github.com/pennmush/pennmush/blob/master/game/txt/hlp/penncmd.hlp), [TinyMUD README](https://mudbytes.net/files/view/721/?path=tinymud-1.5.4/README) | Verified (search + wiki) |
| Event sourcing: definition, snapshot-as-optimization, complete rebuild, external-replay/code-change/temporal costs | [Fowler — Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html) | Verified (fetched, quoted) |
| JSON as save default; `os.replace` atomic write; version-number-per-save; SQLite for large/many-NPC state | [Game Save Best Practices — Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame) | Verified (fetched) |
| pickle version-fragility + security warning | [Bugnet](https://bugnet.io/blog/game-save-best-practices-pygame), [Python — `pickle`](https://docs.python.org/3/library/pickle.html) | Verified |
| Save migration: version header, dispatch `LoadV1/V2`, pure migrators, tolerant load | [Reliable Save Migration — GamineAI](https://gamineai.com/blog/reliable-save-migration-unity-godot-old-files-2026) | Verified (search) |
| `os.replace` atomic on POSIX, cross-filesystem caveat | [Python — `os.replace`](https://docs.python.org/3/library/os.html) | **Doc page fetch truncated before the entry**; guarantee is well-established and stated per the standard phrasing — flag if exact wording is ever load-bearing |
| Generative Agents memory stream: NL timestamped records; retrieval = recency(exp-decay) + importance(LLM) + relevance(embedding cosine) | [Generative Agents, arXiv:2304.03442](https://arxiv.org/pdf/2304.03442) | Verified (search + prior spike) |
| Loom runtime-state facts (source-of-truth vs derived indices; `_id_counter`; conditions `_by_location`/`_world`; `QuestBook`; `MemoryStream(text,kind,t)` on minds not World; chronicle bounded deque + `_seq`; session-ephemeral players; `load_world` reload; Phase 5 "SQLite + brute-force cosine") | `loom/world/world.py`, `loom/world/entity.py`, `loom/world/conditions.py`, `loom/quest.py`, `loom/ai/memory.py`, `loom/chronicle.py`, `loom/clock.py`, `loom/weather.py`, `loom/content.py`, `loom/engine.py`, `docs/PLAN.md` | Verified against working tree |
