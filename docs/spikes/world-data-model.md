# Spike — World data model (how established MUDs store world *content*)

**Date:** 2026-07-25 · **Question:** Loom keeps its entire authored world — locations,
entities, conditions, quests, loot — in a single JSON file (`game/world/world.json`). How do
the established players in this space actually *model and serialize* world content, what are
the distinct data-model families, how does each work, and why was each chosen? What does that
say about the single-file design — and should Loom keep its own custom model or adopt an
established standard already used by many games (§9)? · **Method:** deep-research harness — 5 parallel search
angles, 21 sources fetched, 99 falsifiable claims extracted, top 25 put through 3-vote
adversarial verification (24/25 unanimous, all citing primary sources). Findings below are
tagged **[verified]** (survived the gate) or **[recon]** (searched/fetched but not carried
into the verified set — directionally reliable, not confirmed). No code written.

**Companion spike:** [`persistence.md`](persistence.md) answers the *orthogonal* question —
how the mutable **runtime** delta survives a restart (the overlay). This spike is about the
**authored content** model beneath that overlay. Read together they describe the full
content/state split.

---

## Verdict

**Loom is on the right side of the master axis, and one notch behind the established practice
on granularity and versioning.** Every system surveyed sits on one axis: *world-as-authored-
data-files* versus *world-as-live-persistent-object-database*. The file-based side (Diku
area files, Ranvier bundles) keeps a hard boundary between immutable canonical content and
mutable runtime state; it is source-control-friendly and needs no database engine. The
object-DB side (LambdaMOO, DGD, LPMud, MUSH) makes the running object graph *be* the world —
online building and continuous state come free, but canonical content and mutable state
co-mingle in one opaque dump that resists review and versioning. Evennia threads the needle
with an ORM but still shares one store. Loom's `world.json` → runtime split *is* the
file-based boundary, and it is the correct choice for a git-tracked, AI-authored world.

Where Loom diverges from mature file-based engines is **granularity and schema discipline**,
not the model itself. Diku shards content by zone *and* by kind; Ranvier shards by bundle;
every long-lived flat-file engine eventually split its monolith. A single `world.json`
strains on exactly the three vectors those splits relieve: (1) diff/merge and concurrent /
multi-agent authoring, (2) schema evolution (Loom has neither Django migrations nor DGD's
versioned snapshots — a schema bump is an ad-hoc whole-file migration), and (3) whole-file
parse/load at scale. None of this argues against the document model; it argues for the
**sharding + explicit `format_version` + migration path** that every durable file-based
engine adopted once its worlds grew. Defer until authoring friction or file size demands it,
but design the loader so a shard boundary and a version field can be added without a rewrite.

**On build-vs-adopt (§9): build the core, adopt at the edges.** There is no engine-neutral
world-state standard to adopt — every established format is bound to an engine and imports its
game model. Keep the custom JSON model as the canonical in-engine representation (it is
structurally coupled to Loom's differentiators — the golden-rule action layer, LLM minds, the
AI author, game-agnosticism — and no foreign format survives contact with them); harden it
with standard *practices* (JSON Schema, `format_version` + migrations, optional sharding); and
reach for established formats only as **import/export adapters at the boundary** — a Diku-area
importer to seed content, an optional Ink/Yarn adapter for the narrative layer — never as the
canonical store.

---

## 1. The organizing axis

Every engine in the survey resolves to one question: **is the world authored as external data
files, or is it a live persistent object database?**

- **Content-as-data-files** — canonical world is text/markup under source control; the server
  holds only ephemeral instances, regenerated on demand; no per-instance persistence.
  *(Diku family, Ranvier, Loom.)*
- **Live object database** — the world *is* the running object graph (data **and** code);
  authoring happens in-world and mutates the database directly; persistence is a periodic dump
  of the whole graph; content and mutable state co-mingle. *(LambdaMOO, DGD, LPMud, MUSH.)*
- **ORM hybrid** — game entities mapped onto a general-purpose relational DB via proxy
  classes; continuous persistence on mature RDBMS tooling, but still one shared store.
  *(Evennia.)*

Everything else is a variation on **where the content/state boundary is drawn** and **whether
world logic is data or code** (§6).

---

## 2. Diku flat-file "area files" — CircleMUD, SMAUG, ROM, tbaMUD **[verified]**

*Gate-verified against the CircleMUD Builder's Manual (Jeremy Elson, primary) and SMAUG
`doc/area.txt` (primary, corroborated across two independent repos).*

**How it works.** The world is plain-ASCII files, custom-parsed, partitioned by geographic
zone. CircleMUD splits by entity kind into five file types — `.wld` (rooms/exits), `.mob`
(mobiles/NPCs), `.obj` (objects), `.shp` (shops), `.zon` (zone reset scripts) — collectively
"the world" or "tinyworld" files. Every room, mobile, and object carries an integer **virtual
number (vnum)**; the vnum spaces are independent per kind, so room 3001 and object 3001
coexist. SMAUG folds the same data into a *single* area file with named sections
(`#AREA #HELPS #MOBILES #OBJECTS #ROOMS #RESETS #SHOPS #SPECIALS`, terminated `#$`); vnums run
1–32767 (signed 16-bit `short` max) and are unique within a kind. Strings are tilde-terminated
(`text~`) — the fixed delimiter grammar the hand-written parser keys on.

**Content/state separation is explicit and central.** Instances are *never* persisted
individually. Each zone/area carries a list of **reset commands** — `M` load mobile,
`O` load object, `G` give, `E` equip, `D` set door state — executed once at load and again
periodically as the zone "ages" (SMAUG: reset when ≥3 area-minutes old and empty of players,
or ≥15 area-minutes regardless). Per-command population caps and skip-if-already-present
checks prove a **recreate-on-reset** model, not instance persistence: the mob a player kills
respawns on the next reset; authored content is immutable canonical data.

**Why chosen.** Building is deliberately *not* programming — "building is done by writing data
files in a particular format," a role the manual distinguishes from the C coder. Flat text is
human-editable, diffable, and needs no database engine. The cost: brittle positional/delimiter
grammars, integer-vnum bookkeeping, and a full reboot to apply changes — which is exactly the
friction **OLC** (§7) was built to remove.

---

## 3. Persistent object databases — LambdaMOO, DGD, LPMOO **[verified]**

*Gate-verified against the LambdaMOO Programmer's Manual, dworkin.nl/dgd, the dworkin/dgd
repo, and the LPMOO project page (all primary).*

Here the world is not files parsed into memory — the world *is* the object graph, code and
data together, and persistence is a snapshot of that graph.

**LambdaMOO** keeps the *entire* database — objects, properties, and verbs (the code) —
resident in main memory, never on disk during operation. It persists by dumping the whole
database to a **flat-ASCII checkpoint** periodically (default `dump_interval` 3600 s) and
always at shutdown. The `.db` is human-readable text with a version header
(`** LambdaMOO Database, Format Version 4 **`), stable for over a decade. Objects use
**prototype inheritance** (parent/child, not class instantiation); world logic is **softcode**
— MOO verbs authored and stored *inside* the database (§6). There is no content/state boundary
at all: a builder's new room, a wizard's new verb, and a player's dropped item are all just
objects in the one persisted graph.

**DGD (Dworkin's Game Driver)** is described by its own author as "an object-oriented database
management system originally used to run MUDs" — persistence is first-class identity, not a
bolt-on. Unlike LambdaMOO it is **disk-based**: objects demand-page to a swap file
(`swap_file`/`swap_size`/`sector_size`), so it need not hold the whole graph in RAM. It
snapshots full state on an interval (`dump_interval` 3600 s), reboots from a snapshot and
"continues where it left off," and — distinctively — **recompiles all objects in place without
rebooting**. Its snapshot format is **versioned with a bounded migration path**: DGD 1.7 reads
snapshots from 1.5.9+; upgrading requires recompiling all objects and writing a *non-
incremental* snapshot (implying both incremental and non-incremental snapshot modes).

> Verification nuance: calling DGD "in-memory" split the vote 2-1. It is precisely a
> *disk-backed* object DB — the deliberate counterpoint to LambdaMOO's all-in-RAM model.
> Also: illustrative config numbers seen in one source (`swap_size=65535`, `sector_size=1024`)
> do **not** match real DGD configs (actual 1024/512 or 2048/512); only `dump_interval=3600`
> is confirmed across configs. Treat specific tuning numbers as templates, not canon.

**LPMOO** is the instructive hybrid: MOO reimplemented in LPC on the DGD driver. It inherits
DGD's object-swapping and persists via DGD's **binary** state dumps. Per the project author:
the binary files are "much larger than the equivalent LambdaMOO db file, but the time required
to checkpoint and read the database is significantly reduced." That is the clean **binary-vs-
text persistence tradeoff** — fast reload + large opaque files, versus compact, inspectable,
source-diffable, slower text. (Single primary source — the author's own project page.)

**Why chosen.** Continuous live worlds where everything a player or wizard does persists
automatically, and where building *is* coding done in-world. The cost is precisely the loss of
the content/state boundary: canonical content and mutable runtime state are the same object
graph, which complicates source control, review, and rebuilding a clean world.

---

## 4. Relational / ORM backend — Evennia **[verified]**

*Gate-verified against current Evennia docs (Typeclasses, models API) and the evennia/evennia
source (all primary; pinned to "latest" and stable across 0.9.5 / 1.0 / latest).*

Evennia (Python/Django) maps game entities onto a relational database through **typeclasses**.
Only **four** models are real database tables — `AccountDB`, `ObjectDB`, `ScriptDB`,
`ChannelDB` — all inheriting one abstract Django model, `TypedObject`. Every game-specific
class (a sword, a dragon, a room) is a Django **proxy model**: it adds Python behavior without
changing the schema, so many object *types* share one underlying table row "decorated" with a
normal Python class via get/set-attribute tricks.

- Fixed persistent fields use a `db_*` convention mapped to ORM columns (`db_key`).
- **Arbitrary** persistent per-object data lives in a *separate* `Attribute` table, reached as
  `obj.db.attrname` — Attributes are their own DB objects linked by ForeignKey, with iterables
  stored as special `PackedList`/`PackedDict` types.
- Non-persistent, in-memory-only data uses `ndb`/`nattributes`, wiped on reload.
- The hierarchy is three levels: DB model → `Default*` implementation → your game class
  (`TypedObject → ObjectDB → DefaultObject → Object`).

**Why chosen.** Real transactional persistence, querying, and Django's **migration framework**
for schema evolution, while designers still write ordinary Python classes. Persistence is
continuous (like the object-DB family) but rides mature RDBMS tooling. The cost: content and
state again share the store, and "four tables + an attribute bag" trades relational query
power for schema flexibility (Attributes are effectively a key-value EAV table).

---

## 5. Document bundles — Ranvier (JSON/YAML), indie engines **[recon]**

*Searched and fetched (ranviermud.com/bundles, ranviermud.com/building, RanvierMUD/docs) but
not carried into the gate-verified set. Directionally reliable, not confirmed.*

Ranvier (Node.js) is the closest established analogue to Loom. World content — areas, rooms,
NPCs, items, quests — is authored as **YAML/JSON bundles**: self-contained, toggleable content
packages on disk. Canonical content is bundle data; **player data is stored separately**,
preserving the content/state boundary the object-DB families dissolve. This is the modern
re-statement of the Diku insight (content is data, not code) using structured markup and a
real parser (a YAML library) instead of a hand-written positional grammar — trading tilde-
delimited fragility for schema-friendly, git-diffable documents.

This is the family Loom belongs to. The difference is granularity: Ranvier splits content into
many bundle files by area/concern; Loom holds one file (§8).

---

## 6. Soft-code vs hard-code — where world *logic* lives **[recon for MUSH specifics]**

Orthogonal to storage is **where world logic lives**:

- **Hard-code** — logic is in the compiled server (C for Diku); the world is inert data.
  Adding behavior means editing and recompiling the engine.
- **Soft-code** — logic is authored *as content, stored inside the database*, interpreted at
  runtime. MOO **verbs**, LPMud **LPC** objects, and **MUSHcode** (PennMUSH/TinyMUSH/
  RhostMUSH) are all softcode: builders program the world live, and that code persists in the
  same object store as the world data.

Softcode maximizes in-world authoring power but binds logic to the live database — versioning
and code review become hard because "source" is scattered across DB objects rather than files.
This is the deep reason the object-DB families struggle with source control and the file-based
families do not. **Loom's position:** world logic is engine code (hard-coded, in `loom/`);
content is inert data. It is on the file-based, review-friendly side — and NPC behaviour is
delegated to LLM minds rather than authored softcode, side-stepping the softcode/versioning
problem entirely.

---

## 7. Cross-cutting patterns (the decision axes)

| Concern | File-based (Diku / Ranvier / **Loom**) | Object DB (MOO / DGD / LPMud) | ORM (Evennia) |
|---|---|---|---|
| **Content vs mutable state** | Hard boundary — content is read-only files; instances regenerated (resets) or player data stored apart | None — one persisted graph | Shared store; `ndb` marks the non-persistent slice |
| **Live editing / OLC** | Bolt-on; OLC edits in-memory then *saves back to files* | Native — editing *is* mutating the live DB | Native (in-game cmds + batchcode) |
| **Persistence semantics** | Content immutable; save = write files | Periodic full-graph checkpoint (text or binary) + shutdown dump | Continuous RDBMS writes |
| **Versioning / source control** | Strong — text files diff & merge in git | Weak — opaque/monolithic DB dump | Weak for content; migrations for schema |
| **Schema evolution** | Ad-hoc parser changes (fragile) | Versioned snapshots + object recompile (DGD); none formal (MOO) | Django migrations (mature) |
| **Prototypes / spawning** | Reset scripts (Diku); prototype+spawner (Evennia); templates (Ranvier) | Parent/child clone (MOO); `clone_object` (LPC) | Prototype dicts → spawner → typeclassed instances |
| **Logic model** | Data + hard-coded engine (or scripts as data) | Softcode in-DB | Python typeclasses in files |

Two load-bearing patterns worth holding onto explicitly:

1. **Keep canonical content and mutable runtime state in separate stores.** Every file-based
   system does this; every live-object system pays for not doing it. Loom already does —
   `world.json` (canonical) vs the runtime overlay and memory DB (see `persistence.md`). This
   is the correct side of the axis for AI tooling and source control.
2. **Prototype → instance spawning is universal.** Diku resets, LPC `clone_object`, MOO
   parent/child, Evennia prototype+spawner, Ranvier templates all separate the *template* from
   the *spawned instance*. Authoring loot/mobs as prototypes instantiated at runtime inherits
   the clean regeneration story Diku gets from resets.

**Online building (OLC).** In the file-based world, live editing is a bolt-on: OLC packages
(NiMUD's OLC, OasisOLC for Merc/Diku) let builders edit rooms/mobs/objects in memory without a
reboot, then **write the changes back to the area files** — the files stay canonical. In the
object-DB world it is native, because editing simply mutates the live graph (`@dig`, MOO
verb editing). Loom's authoring workbench + `worlddraft.py` shadow-validate-apply gate is the
file-based pattern done right: propose against a shadow copy, validate, apply, and the human
saves back to the authored source.

---

## 8. Where the single-`world.json` design sits

Loom is firmly **Family 5 (document-as-content)** and already keeps the content/state boundary
the object-DB families lack. That is the design most friendly to git and to AI authoring, and
it is the modern consensus — Ranvier arrived at the same place independently.

The established practice Loom diverges from is **granularity**. Diku splits by zone *and* kind;
Ranvier splits by bundle; MOO/Evennia never hold "one file" because the DB is the unit. A
single monolithic `world.json` strains on exactly the vectors those systems split to relieve:

- **Diff/merge & concurrent authoring** — one file serializes all edits into one blob; multi-
  author or multi-agent edits collide. Splitting per area (Ranvier) or per kind (Diku)
  localizes diffs and makes review legible.
- **Schema evolution** — Loom has neither Django's migrations nor DGD's versioned snapshots; a
  schema bump is an ad-hoc migration over the whole file. A recorded `format_version` + a
  tolerant loader + pure migration functions (the exact recipe `persistence.md` already
  prescribes for the overlay) is what every durable system eventually built for content too.
- **Whole-file parse/load at scale** — one file must be parsed whole; Diku loads by zone, DGD
  demand-pages, Evennia queries. Not urgent at current size, but the ceiling is real.

None of this argues for abandoning the document model — it argues for the **sharding and
versioning** every mature file-based engine adopted once its worlds grew. Recommended posture:
keep the single file now; add an explicit top-level `format_version`; and keep the loader
indifferent to whether content arrives from one file or a directory of shards, so the split
can happen later without a rewrite.

---

## 9. Build vs adopt — keep the custom model or take an established standard

The decision that motivated this spike. It hinges on one fact the survey settles decisively.

### 9.1 The pivotal fact: there is no engine-neutral world-state standard

Unlike glTF for 3D scenes or Twee for hypertext IF, **there is no portable, cross-engine
format for a persistent multiplayer text world.** Every "standard" in the survey is bound to a
specific engine and imports that engine's game model. So "adopt a standard" is not one option
— it resolves into three different decisions:

- **A — Adopt an existing MUD engine's content format.** In practice this means the
  **Diku/CircleMUD area format** (the only one with a large existing corpus). Adopting it drags
  in Diku's game model: vnums, the room/mob/obj triad, D&D-style stats, the tilde-delimited
  grammar.
- **B — Adopt a narrative/dialogue standard** — **Ink** (inkle), **Yarn Spinner**,
  **Twee/Twine**. These *are* widely used in shipped games, but they model **branching
  narrative and dialogue**, not a world of persistent entities, positions, conditions, and
  inventories. They fit Loom's *quest/dialogue* layer, not its world model.
- **C — Adopt general data-modeling standards** — JSON Schema, semantic `format_version` +
  migrations, sharded documents — applied to *Loom's own* model. This is "keep custom but stop
  being ad-hoc," and it is the §8 recommendation under a different name.

### 9.2 The case for KEEPING the custom model

1. **The world model is co-designed with Loom's differentiators.** The schema-validated action
   layer (the golden rule), LLM-driven NPC minds, first-class conditions, the `worlddraft`
   shadow-validate-apply gate, and the forever-running clock/weather all read and write this
   model. No external format encodes any of them; adopting one bends the engine to a shape
   built for human-typed hack-and-slash.
2. **Adopting Diku's format would *reduce* generality — the opposite of the north star.** Loom
   is game-agnostic by design; the Diku format hard-codes a genre (integer vnums, combat
   stats, rooms-as-integers). Importing the format imports the genre.
3. **The custom JSON model is already the modern consensus shape.** Ranvier independently
   converged on the same document / content-vs-state design. Loom is not behind the state of
   the art — it *is* that shape. Adopting Ranvier's bundles gains little and costs a rewrite
   plus a dependency on a niche, semi-dormant framework.
4. **AI authoring is the decisive reason.** An LLM author + validate-apply gate works best
   against a schema *you own and can evolve*, in the plainest possible structure. Foreign-
   format quirks — tilde grammars, vnum bookkeeping, in-DB softcode — are actively hostile to
   LLM generation and clean schema-validated round-tripping.
5. **Adoption cost is real; the benefit is hypothetical at Loom's scale.** Rewriting loaders,
   migrating content, and learning foreign semantics buys portability and ecosystem Loom does
   not currently need.

### 9.3 The case for ADOPTING a standard

1. **Instant content corpus (Diku).** The Diku area format unlocks *thousands* of play-tested,
   hand-built areas — decades of accumulated content. For a world meant to run *forever* and
   needing breadth, this is the strongest single argument. Parsers already exist
   (`circlemud-world-parser` converts Diku areas to JSON).
2. **Ecosystem and tooling come for free.** Standards ship with editors, validators,
   documentation, communities, and builders who already know them. A custom format means
   building and maintaining every tool yourself — which Loom already is (the workbench).
3. **Longevity and portability.** A documented, widely-used format outlives any single project;
   content authored in it survives Loom's abandonment; contributors arrive already fluent.
4. **Narrative standards are battle-tested for exactly the hardest narrative problems.** Ink
   and Yarn Spinner have shipped in commercial games (Ink: inkle's *80 Days*, *Heaven's
   Vault*; Yarn Spinner: *Night in the Woods*). They solve branching dialogue/quest flow — a
   hard, well-solved problem — and are designed to be *embedded*.
5. **Reduced maintenance surface and "don't reinvent the wheel."** Less bespoke code to own,
   more credibility, easier onboarding.

### 9.4 Weighing them against Loom's constraints → build the core, adopt at the edges

The deciding criteria are Loom's, not generic: **(1)** preserve the golden-rule action layer
and LLM-mind coupling, **(2)** serve AI authoring, **(3)** stay game-agnostic, **(4)** content
availability, **(5)** maintenance, **(6)** longevity. Scored against these the cases do not tie
— they point at a hybrid, the same shape as the [`taleweave-ai`](taleweave-ai.md) verdict
("build; steal patterns, not code") and [`persistence`](persistence.md):

- Criteria **1–3 are non-negotiable, and only the custom model satisfies them.** No foreign
  canonical format survives contact with the golden rule, the AI author, or game-agnosticism.
- Criteria **4–6 are real, and are exactly where the custom-model side is weak** — but every
  one of them can be captured **at the boundary, not the core.**

**Recommendation:**

- **Keep the custom JSON model as the canonical in-engine representation.** It is structurally
  coupled to everything that makes Loom Loom; there is no neutral standard to replace it with,
  and the closest engine format (Diku) would cost generality.
- **Harden it toward standard *practices*, not a standard *format*** (the §8 / option-C work):
  publish a JSON Schema, add `format_version` + tolerant-load + pure migration functions (the
  recipe `persistence.md` already prescribes for the overlay), keep the loader indifferent to
  one-file vs sharded input.
- **Adopt established formats as import/export *adapters* at the edge.** A **Diku-area
  importer** captures criterion 4 (seed the forever-world from the existing corpus) without
  adopting Diku's model as canonical. An optional **Ink/Yarn adapter** for the narrative layer
  captures the one place a widely-shipped standard genuinely fits — *if* quest/dialogue
  authoring outgrows the current approach.
- **Do not adopt any foreign format as the canonical store.** The cost is a rewrite against
  Loom's differentiators; the benefits are all obtainable at the boundary.

Net: the "adopt" arguments are strongest exactly where they can be satisfied *without*
replacing the core — so keep the custom model, and reach for standards as adapters and as
discipline, not as a foundation.

---

## 10. Open gaps (not gate-verified this pass)

The adversarial gate confirmed four families in depth (Diku flat-files, DGD, LambdaMOO/LPMOO,
Evennia). These named families were searched but produced no surviving verified claims —
absence reflects verification coverage, not the true landscape:

- **LPMud/LPC** object-and-inheritance persistence model in depth (`clone_object`, blueprint
  vs clone, `save_object`/`restore_object`).
- **TinyMUCK/MUF**, **PennMUSH/TinyMUSH/RhostMUSH** softcode storage internals.
- **CoffeeMud** (relational/flat hybrid), **ToastStunt** (maintained MOO descendant),
  **AberMUD/TinyMUD** roots.
- **Ranvier** carried as **[recon]** only (§5), on a single primary source cluster.

A focused second research pass on any of these can be run on request.

---

## Primary sources

- **CircleMUD Builder's Manual** — circlemud.org/cdp/building/building-2.html; building.pdf
  (vnums, five file types, building ≠ coding, zone resets).
- **SMAUG** `doc/area.txt` — github.com/bkero/Smaug, github.com/nickgammon/smaugfuss
  (section grammar, vnum range 1–32767, tilde-terminated strings, reset semantics).
- **LambdaMOO Programmer's Manual** — "Checkpointing the Database" (entire DB in RAM,
  `dump_interval` 3600 s, shutdown dump); lisdude.com/moo/lmdb.html (`.db` text anatomy).
- **DGD** — dworkin.nl/dgd; github.com/dworkin/dgd (object DBMS, snapshots, in-place
  recompile, versioned snapshot / migration path).
- **LPMOO** — mars.org/home/rob/proj/lpmoo/differences.html (binary-vs-text dump tradeoff).
- **Evennia** — evennia.com/docs/latest Typeclasses + `evennia.typeclasses.models` API;
  github.com/evennia/evennia (four real tables, proxy models, Attribute table, spawner /
  prototypes wiki).
- **Ranvier** *(recon)* — ranviermud.com/bundles, ranviermud.com/building,
  github.com/RanvierMUD/docs (YAML bundles; content vs player-data split).
- **The Mud Connect FAQ** — mudconnect.com/mudfaq (Merc ASCII pfiles; early schema-evolution
  note). **MUD Wiki** — mud.fandom.com/wiki/Online_Creation, /wiki/Softcode *(secondary)*.
