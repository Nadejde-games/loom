# Backlog — noticed improvements (captured, not scheduled)

A running list of refinements noticed during review, to pick up at the opportune
time. These are **not** designed in full and **not** on the current phase plan —
they are captured so the intent isn't lost. When one is scheduled, promote it
into `docs/PLAN.md` with a real design.

Cross-references to the roadmap phases in `docs/PLAN.md` are noted per item.

---

## B1 — Richer, more flexible command vocabulary  — DONE 2026-07-12 (B1a + B1b)
*Noticed 2026-07-10.*

**Status (2026-07-12): both tiers shipped.** B1b (the LLM free-text fallback) —
when the deterministic parser doesn't recognise the verb, the engine hands the
free text to the model constrained by a *command grammar* (`command.command_schema`
over the whole verb table — the verb-side counterpart of the action registry's
`json_schema`) and gets back a canonical `{verb, dobj, iobj}` (`loom/ai/intent.py`,
world-free). That is rebuilt into the *same* `Parse` the deterministic parser
produces, so it flows through the identical dispatch — one path. Trigger is an
*unknown verb* only; a recognised verb with an unresolvable object stays
deterministic (a legitimate "no such thing" / disambiguation). Toggleable
(`Engine(intent_fallback=…)`); degrades to "unknown command" when the model can't
map it. Live on `qwen3.5:35b-a3b`: free-text phrasings the table misses ("offer …
to Wren" → give, "scoop up …" → take, "head north" → go, "peer at …" → examine)
map correctly — 4 new harness `command.*` scenarios, 4/4. Offline now 191 total
(19 new for B1b). Movement/look/say are reachable by the fallback too, since it
targets the full verb vocabulary, not only the registry actions.

**B1a (the deterministic tier, same day):** `loom/command.py` is a game-agnostic,
world-free *syntactic* parser: a verb table with synonyms (`take/get/grab`,
`pick up`, `put down`, `hand`) and multi-word verbs, a `verb + DO + prep + IO`
grammar, and symbolic per-slot scopes the engine maps to real candidate sets.
The engine resolves the object phrases against scope (reusing `naming.resolve`,
so disambiguation — "Which do you mean: brass key, iron key?" — is inherited) and
routes world-changing verbs through the *same* `ActionRegistry` the NPCs use.
New player reach: `look at X` / `examine X`, `take X [from Y]`, phrasing tolerance
and articles (`take the lantern`), plus the existing `give X to Y` / `drop X`.
`take`/`drop` were promoted into registry actions (`take_item`, `drop_item`) so
every player world-change runs one path; a **per-mind offered-action subset**
(`NpcMind(offered=…)`, narrowing both `describe()` and `json_schema()`) keeps the
NPC catalogue — and the behavioral harness — unchanged by the new player-only
verbs. 43 new offline tests. The deterministic tier covers the common case with
no model at all; the B1b fallback above catches the rest.

**Want:** a much richer and more flexible player command set — complex,
multi-object commands like `say something to X`, `look at X`, `take X from
inventory`, `give X to Y` — and tolerant of phrasing, so the player is **not**
constrained to exact syntax.

**Where it lands:** the engine command layer (`engine.py on_input`, currently a
fixed first-word verb + rest, exact-match dispatch).

**Considerations for later:**
- A real command grammar: verb + direct object + preposition + indirect object
  (`give` `sword` `to` `Odd`), with **entity-name resolution** against what's in
  scope (the room, the player's inventory).
- And/or an **LLM-assisted intent parser**: free text → a *validated command*.
  Note the synergy — this is the player-side mirror of the NPC action seam
  (`action.py`). Player input could become "proposed actions" run through the
  same schema-validation + retry machinery. Worth unifying rather than building
  a second parser.
- **RESEARCH FIRST (before choosing grammar vs. LLM):** survey how existing
  text-game / MUD / interactive-fiction engines parse player commands, as a third
  option beside "hand-build a deterministic grammar" and "LLM intent parser".
  Look at: Inform 7 / TADS (IF parsers with world-model scope resolution and
  disambiguation — decades of prior art on exactly the `verb + DO + prep + IO`,
  "which key do you mean?" problem), Evennia's command system (`Command` classes,
  `CmdSet`, and its `MuxCommand` arg/switch parsing), classic DikuMUD/LPMud verb
  tables, and any small tokenizer libs. Decide whether to adopt/adapt a proven
  parsing model, borrow its disambiguation approach, or build our own — informed,
  not from scratch. This follows the standing "research prior art before laying
  foundations" preference. Output: a short comparison + a recommendation before
  any parser code.
- Prereq: an item/inventory world-model (shared with `give_item`, Phase 4) and
  a name-resolution helper ("X" → the entity meant, with disambiguation).

**Related:** the action seam (Phase 2), inventory (Phase 4).

---

## B2 — Fuse an NPC's speech and action into a single line  — DONE 2026-07-17
*Noticed 2026-07-10.*

**Status (2026-07-17): shipped.** A single composition layer (`loom/compose.py`,
`compose_beat`) turns a validated `Turn` into one room beat — action-first,
speech-attribution style: `Odd shakes his head slowly and says, "…"`. The
structured `Turn`, the chronicle, the actor's memory, and the reaction cascade's
`observed` string are all unchanged — **only the room broadcast is composed**. The
engine splits execute-from-render (`_perform(broadcast=False)` + a shared
`_resolve_lines`): deeds shown in the actor's own room fuse with the speech, while a
deed shown *elsewhere* — a move's arrival at its destination — is broadcast there on
its own, since that room never heard the speech (multiplayer-correct). Graceful
templates for the four cases (action+speech, speech-only, action-only, and many
actions with the repeated name elided) plus the director's bodiless voice. Verified
live on qwen3.6/vLLM — a guide asked to lead rendered *"Wren leaves, heading north
and says, 'I'd be delighted to show you the way!'"* in the origin, and only the bare
arrival in the room ahead. Offline +11 (`tests/test_compose.py`). The composed line
is also the unit **B3** styles. *(Design note below kept as the record.)*

**Status (2026-07-12): still open, and now more pressing.** The Phase 3 director
adds a *third* rendered form — an unattributed ambient line — beside the NPC's
separate speech and emote lines. As the rendered surfaces multiply (and before
Phase 6's rich transport), a single composition layer that turns a `Turn` into the
right line(s) — and knows the director's bodiless voice — is worth doing.

**Want:** an NPC currently emits two separate lines — the spoken line, then the
emote. Combine them into one:

```
  now:      Odd the Hermit: The hills know my name better than they do yours.
            Odd the Hermit shakes head slowly

  wanted:   Odd the Hermit shakes head slowly and says: The hills know my name
            better than they do yours.
```

**Where it lands:** presentation/composition only — `engine._deliver_npc_reply`
/ `_perform`, and how a `Turn` (speech + validated actions) is *rendered* to the
room.

**Considerations for later:**
- Keep the structured `Turn` internally (speech and validated actions stay
  separate data — the seam is unchanged). Only the **rendering** merges them.
- Need graceful templates for the cases: action-with-speech, speech-only,
  action-only, and multiple actions. Word-joining ("… and says: …") is a
  formatting rule, not a data change.
- The composed line is what broadcasts to the whole room (multiplayer-correct).

**Related:** the action seam (Phase 2); interacts with B3 (formatting) and B1
(addressing).

---

## B3 — Rich text formatting (color, italics, bold)  — DONE 2026-07-19
*Noticed 2026-07-10.*

**Status (2026-07-19): all player-facing surfaces styled.** The remaining slices
landed: player action **acks** (take/drop/give — items as `item`, characters as
`name`, keyed by each verb slot's scope), the **`say` echo** (the player's own words
as `speech`), **`inventory`** (items), **`who`** (names), the **quests journal**
(titles in a new `quest` role), and — the "third rendered form" B2 flagged — the
**world's own ambient voice** in a new `ambient` role (dim-italic): the director's
bodiless beats (`_perform` for the director actor) and the clock/weather turnings
(`apply_world_condition`), so atmosphere reads distinctly from a character speaking.
Vocabulary grew by `quest` + `ambient` (the `Style` set is designed to grow; the
client theme gains a colour for each). Deliberately left plain: `examine` and `help`
(freeform prose / a static reference, no entities to tag), and the third-person
*room* narration of a player's own item-actions (bystanders see plain; an NPC's is
already name-styled via `compose_beat`). `danger` stays a reserved role with no
emitter until the game has a hostile beat to raise it. Offline +7 styling assertions
(`tests/test_engine.py::EngineStylingTests`). Pairs with Phase 6 (rich clients).

**Status (2026-07-18): route (b) foundation + first surface shipped.** Chosen route
is **(b)** — semantic styled segments —
realised as a *polymorphic* `text` payload: `d` is a plain string (as before) OR a
styled line, a list of `{"t","s"}` spans. New `loom/style.py` holds the semantic
vocabulary (`name`/`speech`/`emote`/`item`/`exit`/`place`/`danger`) and the pure
`span`/`styled`/`join_styled`/`plain` helpers. The engine tags *semantic roles
only*; the **client owns the theme** — `client/terminal.py` maps role→ANSI and
degrades to plain on `NO_COLOR`, a non-TTY, or an unknown role. Styling is **opt-in
per line**, so plain lines and simpler clients keep working untouched, and `plain()`
flattens a styled line for the chronicle and logs. First styled surface: the
**perception output** — the B2 composed beat (name · gesture · speech) and `look`
(place · occupants · items · exits). The envelope is unchanged; the polymorphic
payload is documented in `protocol.py`. Offline +15 (`tests/test_style.py` + style
assertions in `tests/test_compose.py`); the flatten kept every substring assertion
green. **Next slices (all plain and working today):** the player action lines
(take/drop/give), the director's ambient beats, `examine`/`inventory`/`quests`/
`help`, and system notices; `danger` has a role reserved but no emitter yet. Pairs
with Phase 6 (rich transport & clients).

**Want:** rich formatting on emitted text — colors, italics, bold — to
**highlight different things** (character names, speech, emotes, items, exits,
danger, etc.).

**Where it lands:** the wire protocol (`protocol.py`) and client rendering
(`client/terminal.py`).

**Considerations for later — two routes to weigh:**
- **(a) Inline markup** in the `text` channel (a lightweight markup, or raw
  ANSI). Simple, but couples style into the prose and forces every client to
  parse it.
- **(b) Structured styled segments** — carry text as a list of `{text, style}`
  spans (or a parallel style channel). This fits the "structured side-channels"
  philosophy (design commitment #1): a terminal degrades to plain text, a rich
  client renders fully. **Preferred for the framework.** The terminal client
  already dims `system` lines with ANSI — precedent for style being applied at
  the client edge, not baked into content.
- Define a **semantic** style vocabulary (`name`, `speech`, `emote`, `item`,
  `exit`, `danger`) rather than raw colors, so the theme lives client-side and
  can be swapped.

**Related:** Phase 6 (rich transport & clients); pairs with B2's composed line.

---

## B4 — NPCs choose whether to react  — PROMOTED / LANDED 2026-07-11
*Noticed 2026-07-10.*

**Status (2026-07-11): shipped, with one half model-limited.**
- **Silence is first-class (done).** `Turn.is_silent`; the mind is told it may
  reply with `{"speech":"","actions":[]}`; the engine renders that as nothing (no
  stock line — the exception fallback stays distinct). Proven offline.
- **Salience gate (done, deterministic).** New `loom/salience.py` — a swappable
  `SalienceGate` (default = directed address): if a present NPC is named, only the
  named engage; bystanders are gated out **before** any thinking beat or LLM call.
  Wired into `engine._say`; exported from `loom`. This is the primary win for the
  two-NPC room ("Wren, …" → only Wren answers).
- **Model self-silence (largely fixed via three prompt levers).** A first pass
  had `qwen3.5:35b-a3b` respond **10/10** to undirected remarks — never the empty
  turn, even for the wary hermit. Three changes to `mind.py` fixed it without a
  second LLM call: (1) a *silent few-shot example* (`{"speech":"","actions":[]}`)
  so the empty turn is modeled, not just permitted; (2) *ambient framing* — an
  unaddressed line is presented as "You overhear … say to the room …" rather than
  a question put to the NPC (`converse(addressed=…)`, set from `is_addressed`),
  loosening the assistant reflex; (3) a persona *disposition* field (the silence
  prior). Re-tested live: reticent Odd now stays silent on **3/5** idle remarks
  and speaks only when a topic touches him; gregarious Wren still answers 5/5.
  Character-appropriate, not random. It is a *tendency*, not a guarantee (sampling
  varies) — a `should_engage` LLM override remains the lever if hard filtering is
  ever needed. Skipped for now as overkill.

**Want:** not every NPC should react to everything all the time. There needs to
be **choice on the NPC's side** whether (and when) to respond.

**Where it lands:** the AI layer (`mind.converse`) and the engine dispatch
(`engine._say`, which currently fires every NPC in the room at every `say`).

**Considerations for later:**
- Make **"no reaction" a first-class turn outcome** — an empty/none `Turn` that
  the engine renders as *nothing*. The model can already choose silence (empty
  speech, no actions); the engine should honor it and emit nothing rather than a
  stock line.
- A **cheap salience gate** before the full generation: was the NPC addressed
  (see B1's `say to X`)? is the topic relevant to its goals/mood? — so we don't
  spend a full LLM call per NPC per utterance when most would stay silent.
  Meaningful latency/VRAM saving at scale.
- Later, the game-master/director (Phase 3) could mediate attention across the
  room.

**Related:** Phase 3 (director), Phase 5 (deeper minds); enabled by B1
(addressing a specific NPC).

---

## B5 — The model under-selects world-mutating actions (move) vs. emote  — DONE 2026-07-13 (two-pass act-gate)
*Noticed 2026-07-10.*

**Update 2026-07-13 — RESOLVED by the two-pass act-gate.** The structural lever the
prompt tweaks could not reach is built: `NpcMind(act_gate=…)` (opt-in). When on,
`converse` runs two passes — a cheap, low-temperature, constrained pass that decides
the *deed* alone (where the cosmetic `emote` is not a flavour attractor competing with
dialogue), then the *proven blended turn* supplies the spoken line, its own action
discarded for the decided one. So the action is **authoritative** from the focused pass
(the plan→act split), and the move-when-asked rate becomes the decision's accuracy, not
the blended compose's. Measured live on `qwen3.5:35b-a3b`: **move-when-asked 8/8 gated
vs ~6/8 (~70%) blended** — the ceiling cleared — while every other behaviour held
(silence 5/5, give_item 5/5, ground-items 3/3, no over-eager move on idle chatter 4/4).
A first cut used a *dedicated* speech pass told the decided deed; it regressed silence
(the reticent hermit spoke 5/5 where it should often stay quiet), so the speech was
handed back to the blended turn — which owns the B4 silence machinery — and silence was
restored. Same two-call cost as the blended path with its retry (bounded by salience —
only engaged NPCs pay it). Guarded by `move.guide-leads-gated` +
`move.guide-no-idle-move-gated` + regression guards (`silence.*-gated`,
`give.*-gated`, `perception.*-gated`). Wired through `Engine(npc_act_gate=…)`; the game
defaults it **on** (`LOOM_NPC_ACT_GATE`). The director's binary act-gate (B8) proved the
machinery; this is its *authoritative-action* variant for the NPC selection fault.

**Status (2026-07-12): partly landed — prompt levers applied, honest ceiling
measured, and now GUARDED by the behavioral harness.**
- Two prompt levers in `mind.py`, no second LLM call: (1) a `move` few-shot
  example beside the emote one (the sole prior example was an emote, which primed
  gesture-over-move); (2) the `emote` action description no longer offers
  "gestures at the path" as its example — that was the exact phrase the model
  substituted for a move — and now says "to go somewhere, use move". A third
  change conditions move on the *player's* intent to travel, so a willing guide
  moves when asked to lead but not on idle chatter.
- A gap that *looked* like B5 was really a data gap: an NPC asked to hand over an
  item it held refused ("I don't have a map") because `Scene` carried only floor
  items, never the NPC's own inventory. Fixed (`Scene.inventory`) — `give_item`
  selection went 2/5 → 5/5. Not a sampling issue at all.
- **Measured ceiling:** move-when-asked is ~70% single-pass on `qwen3.5:35b-a3b`
  (6/8, 8/8, 6/8 across runs), up from ~0/5. The behavioral harness
  (`scripts/behavior_probe.py`, `move.*`) now guards it — the threshold catches a
  *collapse* or a regression, not to force the model higher. In real play the
  effective rate runs higher (accumulating memory makes an NPC repeat its move).
- ~~**Still open (the real B5 lever)**~~ — **DONE 2026-07-13** (see the resolution
  note at the top of B5). Raising the ceiling above ~70% needed a structural change,
  not another prompt tweak — the two-pass turn: a cheap, low-temp constrained pass
  decides the deed, then the blended turn speaks. The *director* proved the two-pass
  machinery first (B8, `DirectorMind.decide`); B5's variant makes the decided action
  **authoritative** (rather than the director's binary wait/act) — the honest fix for
  a *selection* fault. Note lowering temperature alone would entrench the *emote* mode
  on some phrasings, so it was never the lever.

**Want:** when an NPC's *intent* is to act on the world (e.g. a guide asked to
lead should **walk**), it should reliably emit that action — not settle for a
cosmetic `emote` ("gestures toward the trail") that changes nothing.

**Observed:** with `qwen3.5:35b-a3b`, a willing guide (Wren) produced enthusiastic
lead-the-way *speech* every time but emitted the `move` action only on a fraction
of turns (≈1–3 in 5, high run-to-run variance), frequently substituting or pairing
an `emote`. A stubborn NPC (Odd) never moved — correct for *him*, but the willing
one should. The split tracks the model's action-selection bias and sampling
temperature, **not** the persona wording (tuning the goal text moved the rate
around within the noise). So this is a framework lever, not a content fix.

**Where it lands:** the AI layer — `mind._action_instructions` (how actions are
described/weighted) and the provider's sampling for the action decision. Related
to the salience/intent gate in B4.

**Considerations for later:**
- **Lower temperature for the action decision** (or a two-pass turn: a cheap,
  low-temp "should I act, and how?" then the in-character speech). Overlaps B4's
  salience gate — the same gate that decides *whether* to react can decide
  *whether an action is warranted*.
- **Rebalance the catalogue prompt:** `emote`'s invitation may over-attract;
  make world-mutating actions first-class, and/or add a short few-shot example
  that includes a `move` so the shape is primed (careful: negative phrasing like
  "don't just point" backfires — it primes "point").
- **Pairing is often good, not a bug:** the guide that *both* gestured and moved
  ("gestures toward the trail" + `move north`) read best. The goal is reliable
  *inclusion* of the world action, not suppression of the flavor emote — which
  also motivates B2 (fuse the two into one rendered line).

**Related:** the action seam (Phase 2), B4 (choice-to-react / salience gate),
B2 (fused rendering), Phase 3 (a director could also arbitrate/insist).

---

## B6 — Degrade-to-speech leaks a broken JSON envelope to the player  — RESOLVED 2026-07-12
*Noticed 2026-07-11 (during B4 live testing).*

**Status (2026-07-12): retired by construction.** Shipped as *Phase 2 hardening —
constrained decoding* (see `docs/PLAN.md`): `ActionRegistry.json_schema()` emits the
turn-envelope grammar and `NpcMind` hands it to the provider, so on any backend that
supports the constraint (verified live on Ollama `/v1` with `qwen3.5:35b-a3b`) a
malformed envelope is impossible at the token level — the leak cannot occur. Guarded
by the `envelope.well-formed` behavioral scenario (5/5). The considerations below
remain the fallback design for backends *without* constraint support and the offline
`FakeProvider` path, where the validate → retry → degrade-to-speech layer stays the
last line of defense (kept deliberately as defense-in-depth, not removed).

**Want:** when the model emits a *malformed* turn envelope that the tolerant
parser cannot recover, the player should never see raw JSON. Observed once in 10
live turns: qwen3.5:35b-a3b produced an action array with a missing object-close
(`…"}]}]` where `…"}}]}` was meant); `_extract_json` failed every recovery path
and `_parse_turn` fell back to treating the *whole raw reply as speech* — so the
literal `{"speech": "…", "actions": [ … ]}` was spoken to the room.

**Where it lands:** `loom/ai/mind.py` — `_extract_json` (recovery) and
`_parse_turn` (the degrade-to-speech branch).

**Considerations for later:**
- The degrade-to-speech fallback is correct for genuine prose (a model that
  ignored the schema and just talked). The bug is only when the raw is *clearly a
  failed envelope* — starts with `{` and contains `"speech"`. In that case:
  prefer the **retry** path (feed "your JSON was malformed" back) over dumping it;
  and if the retry also fails, emit the extracted `speech` string only, never the
  braces. A cheap detector (`stripped.startswith("{") and '"speech"' in stripped`)
  distinguishes the two cases.
- Consider a slightly more forgiving recovery: balance-scan for an at-most-one
  missing `}` before `]`/end. Keep it bounded — don't build a JSON repairer.
- Related to **B5**: both are symptoms of the model's shaky structured-output
  discipline; a lower-temperature action pass would reduce both.

**Related:** the action seam (Phase 2), B4 (silence — a leaked envelope is the
opposite of clean silence), B5 (action-selection reliability).

---

## B7 — World atlas / explorer (render a world.json into a readable overview)
*Noticed 2026-07-12. Lands in **Phase 7 (authoring tools)**.*

**Want:** a way to *see* a whole world at a glance without playing through it — a
map (rooms + exits), a character sheet per NPC (the persona: backstory, traits,
goals, voice, disposition, and what they hold), and item locations. Answers the
creator's question "how do I explore the world, characters, and story?" from the
data side, and doubles as a validator for a world an AI author generates.

**Where it lands:** authoring/tooling (Phase 7) — the *read* side of the authoring
loop (authoring writes the schema; the atlas reads it back). Game-agnostic: it
consumes the `world.json` schema, so it works on any world and grows with it.
Start as a standalone script, no engine or GPU needed.

**Considerations for later:**
- **Start small and text-only:** a `scripts/atlas.py` over `content.load_world`
  (or the raw JSON) that prints, per room, its description + exits + occupants +
  floor items, then a character sheet per NPC and an item table. Read-only; zero
  new deps. This alone is immediately useful for the dev.
- **The map is the hard part.** A real 2D/graph layout from the `exits` adjacency
  is non-trivial; begin with a simple adjacency listing (room → direction → room)
  and a per-room detail block, and defer a drawn map. When a drawn map is wanted,
  it is the same data the Phase 6 `map`/`entities` channels will carry — share it.
- **Item holders** resolve via the containment model (`World.contents`): show
  floor items under their room and inventory items under their holder.
- **Two surfaces, one renderer:** a terminal text view now; later a richer visual
  view (a shareable atlas page, or the rich client's map). Keep the data-gathering
  separate from the rendering so both can sit on it.

Also surface **world-level config** now that `world.json` carries it: the loader
captures any non-structural top-level key into `world.meta` (the `"director"`
persona today), so the atlas should render that block too — the GM persona is part
of "explore the world, characters, and story."

**Related:** Phase 7 (authoring), Phase 6 (map/entities channels), the inventory
world-model (2026-07-12, gives item locations), `world.meta` / the director block
(2026-07-12), design commitment #2 (editable data). Deferred here at the user's
request (2026-07-12) rather than built now.

---

## B8 — The director under-weights restraint (stages a beat almost every pulse)
*Noticed 2026-07-12 (during Phase 3, the director's first slice).*

**Status (2026-07-12): partly landed — a deterministic orchestrator gate caps the
rate; the model-side judgment lever is still open.** The `Director` now holds
frequency down in code, independent of the model: a beat needs BOTH
`min_new_events` new chronicle events since its last beat AND `cooldown_pulses`
pulses of breathing room since it (defaults 3 / 2; tunable via
`attach_director(...)` or `LOOM_DIRECTOR_MIN_EVENTS` / `LOOM_DIRECTOR_COOLDOWN`).
So most pulses the director spends no model call and does nothing — the observable
goal (sparse intervention) is met even though the model, when consulted, still
takes a beat nearly every time. Proven: a model that *always* stages was held to
~7 beats over 20 pulses; three new offline tests guard the gate. Also fixed a
perception gap found here — NPC *speech* is now recorded to the chronicle (only
NPC actions were), so the director perceives conversation, not just movement.
**Still open (until 2026-07-13):** making the director *choose* wisely (not just be
rate-limited) — the model-side two-pass "should I act?" pass below.

**Update 2026-07-13 — the model-side act-gate has landed, been measured, and works.**
The open lever is built: `DirectorMind.decide` — a cheap, low-temperature (0.2),
tightly-constrained *wait/act* pass run *before* the full compose, layered **after**
the deterministic ceiling/floor (so nothing regresses; opt-in, off by default via
`attach_director(act_gate=…)` / `LOOM_DIRECTOR_ACT_GATE`). The whole open question was
whether asking the same over-staging model "should you act?" would simply yield "yes"
too — it does **not**. Measured live on `qwen3.5:35b-a3b`: on the exact bare scene the
ungated director staged 6/6, the gate now judges **wait 8/8**; on an evocative scene it
still **acts 8/8**. The lever is real but the band is narrow — two framings bracketed it
(an over-restraint frame gave bare 8/8-wait but evocative only 2/8-act; an enrichment
frame gave the inverse), and holding both poles at once took a concrete *discriminator*
("someone merely entering a place is ordinary, not a cue; act only when the scene hands
you something to answer") **plus** feeding the gate the director's own *goals* so it
judges against what the director is *for* (atmosphere), not a generic "intervene only if
broken" bar. Verified E2E through the real orchestrator: a bare pulse drew no beat; an
evocative exchange earned one grounded, in-tone omen. Now GUARDED by a behavioral
*tension pair* (`director.restraint` + `director.gate-acts-on-cue`) — a collapse to
always-act OR always-wait fails one of them. Cost note: on a "wait" the gate *saves* the
full compose (a tiny decision call instead), so on a world that should mostly wait it is
cheaper, not dearer. **The NPC `move` ceiling (B5) is a *typed* variant of this same
machinery — deferred until wanted** (the binary gate does not fix a *selection* fault;
see B5). **Scoped to the *activity* path:** a first cut gated the B9 lull too, but
`decide` on a quiet lull scene judged 'wait' 8/8 — the model would silence the very
liveliness floor the lull exists to be. So the gate governs only activity-driven beats
and the lull stays a *deterministic* floor. (This retires the earlier hope, in the note
just below, that a shared gate would make the lull judgment-based — measured false: on
this model, a judgment-based lull is a dead lull.) The framework default stays **off**
(nothing regresses); the **game now opts in** (`LOOM_DIRECTOR_ACT_GATE`, default on) —
the capability is proven and, on a 'wait', cheaper than composing.

**Update 2026-07-12 — reconciled with B9's lull.** The B9 lull trigger (a liveliness
*floor* — stir a quiet room after N idle pulses) is the natural counter-force to this
*ceiling*, and the two were built to *balance*, not fight: the ceiling is on
activity-driven beats, the floor applies only in the *absence* of activity, and the
lull is opt-in so this restraint behaviour is preserved exactly when off. The
model-side act-gate below would upgrade *both* — restraint (usually "no") and the
lull (occasionally "yes") are two answers to the same "is a beat warranted now?"
question, so a shared gate closes this and makes the lull judgment-based at once.

**Want:** the game-master director should intervene *sparingly* — most slow
pulses the world needs nothing from it, and it should reply with an empty turn
(watch and do nothing). The prompt says exactly this ("intervene sparingly; most
beats the world needs nothing from you"), and an empty `actions` list is a
first-class, tested outcome (`DirectorMind` returns `Turn.is_silent`; the engine
performs nothing).

**Observed:** with `qwen3.5:35b-a3b`, given a scene with players present, the
director staged an ambient `stage_event` on nearly every pulse — 7/8 on an
evocative scene *and* 6/6 on a deliberately quiet one (a bare arrival, nothing of
note). The beats themselves are grounded and in-tone; the problem is *frequency*,
not quality. In continuous play at the default cadence this would over-narrate.
This is the director's analogue of **B5** — the model's action-selection bias
under-weights the "do nothing" option, and persona/prompt wording alone does not
fix it (the same lesson as B5: negative or scarce-phrasing priors are weak levers).

**Where it lands:** the AI layer — `DirectorMind` (how the watch-and-wait option
is weighted/primed) and, structurally, the director's turn shape. Not a grammar
issue (the empty turn is already valid and reachable); a *selection* issue.

**Considerations for later:**
- ~~**A two-pass director turn**~~ — **DONE 2026-07-13** (`DirectorMind.decide`; see
  the update above). A cheap, low-temp "is a beat warranted right now?" gate before
  composing one — the TaleWeave two-phase plan→act split, generalized. Proven live to
  restrain the director without collapsing it (bare 8/8 wait, evocative 8/8 act). The
  NPC `move` ceiling (B5) would reuse the same machinery with a *typed* decision, not
  this binary one — deferred until wanted.
- **A cooldown / budget in the orchestrator** (`Director`), independent of the
  model — DONE 2026-07-12: `min_new_events` + `cooldown_pulses` require both M new
  chronicle events and N pulses since the last beat. Cheap, deterministic, tested.
  Next dial to consider: a per-day/per-scene *budget*, or scaling the floor with
  how many players are present.
- **Salience for the director** — reuse the B4 idea: score whether the recent
  chronicle actually *calls* for a beat (a lull, a player stuck, a dramatic turn)
  rather than pulsing on any change.
- ~~Currently **not gated** by the behavioral harness~~ — **now gated (2026-07-13).**
  The act-gate made restraint a verified behavior, so it entered the harness as a
  *tension pair*: `director.restraint` (a bare scene → the gate waits) AND
  `director.gate-acts-on-cue` (an evocative scene → the gate still acts). Testing
  both poles is deliberate — a gate that always waits would pass the first alone and
  read as "restraint working" when the director had simply gone dead.

**Related:** Phase 3 (director), B5 (the same action-selection ceiling / two-pass
lever), B4 (salience gate — choosing whether to engage).

---

## B9 — The director is reactive, not autonomous (no ambient life on its own)  — RESOLVED 2026-07-12 (line drawn); idle-NPC autonomy moved to Phase 5
*Noticed 2026-07-12 (playing the Phase 3 director).*

**CLOSED 2026-07-12.** Four slices landed (world-clock, director lull, off-screen
staging, weather; details below). The remaining thread — *purely unprompted* NPC
initiative (idle-NPC autonomy) — was **decided into Phase 5** (2026-07-12): B9's thesis
("the world feels alive when the player is idle") is already met, since an idle room now
breathes (time turns, weather wanders, the director stirs on a lull) *and* the NPCs stir
by **reacting** to all of it through the cascade. What's left is only unprompted
initiative, whose *quality* depends on Phase 5 mind depth (reflection, evolving goals,
plans) and the model-side act-gate (the local model over-acts, B5/B8), so a naive idle
trigger now would risk a noisy/mechanical room. A thin "NPC lull-stir" mechanism (the
NPC-side mirror of the director lull, reusing `_deliver_turn` + the cascade) remains a
cheap drop-in if wanted, but it is a *refinement*, not a gap. See Phase 5 in `docs/PLAN.md`.

**Frames the settled design decision (PLAN, Phase 3): the director shapes the
*world*, not minds; NPCs react on their own.** This item is that principle's
to-do list — the director needs an autonomous way to change the world (a
world-clock / environmental events), and NPCs need to *react* to those changes of
their own volition (the NPC-autonomy half).

**Update 2026-07-12 — the world-*shaping* half AND the NPC-reaction half have both
landed; one gap remains.** (a) The director can raise and lift *standing
conditions* (`set_condition` / `clear_condition`; a storm, nightfall) that persist
in perception (PLAN, Phase 3 "world-shaping slice"). (b) NPCs now *react* to those
changes and to each other of their own volition — the re-entrant reaction path
(`engine._react_to_event`), a bounded cascade under the directive below (PLAN,
Phase 3 "reaction path"). What is **still missing** is the **autonomous trigger**:
nothing yet makes the world stir without player-driven activity. The reaction path
is reactive — it fires on a director beat or an NPC's reply, both ultimately
player-driven — so a *still* room (no one typing, no director beat) still produces
nothing. A **world-clock / lull** that writes to the chronicle on its own is the
remaining work, plus (at scale, deferred) a cheap deterministic salience *pre-gate*
before the model when a room is crowded.

**Update 2026-07-12 (later) — the autonomous trigger has landed: the world-clock.**
`loom/clock.py`'s `WorldClock` hangs off the loop and advances an abstract
minute-of-day *decoupled from any player* (the DikuMUD weather-daemon shape); on
crossing into a new `Phase` (a table the game authors in `world.json`'s `"clock"`
block) it drives `engine.apply_time_of_day`, which upserts a **world-scope**
condition (a new `Conditions.set_world` scope, folded into every place's look /
`Scene` / snapshot) and, in each occupied room, lands one **deterministic** ambient
beat + records it to the chronicle + `_spawn_reaction(…, "world", …)`. So a still
world now turns to dusk on its own and the characters feel it, through the *same*
reaction path — the beat is deterministic, the life is the minds. It *feeds* the B8
restraint floor (a real new event) rather than fighting it. Off by default
(`engine.attach_clock`); the game opts in. Gates: 290 offline (`tests/test_clock.py`
+ world-scope registry tests), 18 behavioral (`npc.reacts-to-nightfall`, a
collapse-detector on a mild stimulus); verified live E2E (an idle player, the clock
turned day→dusk, reticent Odd shrank into the shadows of his own volition).
**Sub-gap 1 below is resolved;** what remains on B9 is the *director-side* follow-ons.

**NPC-reaction design (directive, 2026-07-12): cascade is the feature; the limiter
is engine-enforced *appropriateness*, not a count.** NPCs must be able to react to
each other — a remark drawing a reply drawing a mutter — because a hard one-level
cap is artificial and kills the emergent room-dynamics worth having. The engine
strictly gates every potential reaction on whether it is *genuinely warranted*
(salience re-judged at each hop), so an NPC that would only "react to react" is
stopped at the seam; a cascade runs exactly as long as each hop is worth reacting
to and ends when appropriateness runs out — never because a counter tripped. Rails
are cheap and non-artificial: a **self-guard** (never react to one's own line), a
**per-NPC cooldown** (a real back-and-forth is fine; machine-gun ping-pong is not),
a **decaying reaction budget** per originating event so liveliness winds down, and
a **high runaway fuse** (a generous ceiling that never fires in normal play — the
Diku recursion-cap lesson) purely as a cost circuit-breaker. This supersedes the
"strictly one-level (depth-0)" option the prior-art survey offered as the simplest
safe default.

**Want:** the world should feel alive even when the player is idle — the director
able to set a beat on a *lull*, and ambient / world-clock events (weather turning,
night falling, something stirring off-screen) that arise without a player action.
Today the director is purely **reactive**: the chronicle only fills from
player-driven activity (arrivals, the player's speech, NPC replies to it,
player/NPC moves), so a still room produces no new events, the B8 restraint floor
never clears, and the director never speaks. Correct *as restraint* — but it means
the world goes inert the moment you stop typing.

**Two sub-gaps:**
- ~~**No autonomous event source.**~~ **RESOLVED 2026-07-12 by the world-clock**
  (`loom/clock.py`; see the update above). Time-of-day now advances on the loop with
  no player input, writing a beat to the chronicle and turning a standing condition.
  Its siblings on the same machinery are still open (idle-NPC activity; probabilistic
  weather as a bounded random walk).
- ~~**The director can only stage into occupied rooms.**~~ **RESOLVED 2026-07-12 by
  off-screen staging.** `world_snapshot(include_adjacent=True)` now also lists the
  *empty* rooms one exit from an occupied one, marked `[ahead, empty]`; `Director`
  gains `foreshadow` (opt-in, `LOOM_DIRECTOR_FORESHADOW`) and the prompt invites
  shaping a place ahead sparingly (preferring a *standing condition* that outlasts
  the walk). Gates: 303 offline (`WorldSnapshotAdjacencyTests` +
  `DirectorForeshadowTests`) + a `director.foreshadows` live scenario (a
  collapse-detector; ~37% — foreshadowing is a *sometimes* touch). *Still deferred:*
  the **non-adjacent** off-screen stir (a distant place the player is nowhere near)
  needs a hear-from-afar propagation mechanism — a bigger step, not built.

**Where it lands:** `loom/ai/director.py` (`Director` — a time/lull trigger beside
the activity trigger; a snapshot that can include chosen empty rooms) and likely a
small **world-clock** system on the game loop (`loop.py`) as an autonomous event
source that writes to the chronicle.

**Considerations for later (the remaining director-side follow-ons):**
- ~~A **lull trigger**~~ — **DONE 2026-07-12.** Built deterministically on the
  existing `Director`: `_beat_reason()` returns `"activity"` | `"lull"` | `None`; the
  lull path fires after `lull_pulses` quiet pulses (≫ cooldown) with players present,
  and `observe(lull=True)` swaps in a gentler nudge. **Opt-in** (`lull_pulses=0` = off,
  so the B8 "quiet world never beats" guarantee is preserved exactly); the game turns
  it on via `LOOM_DIRECTOR_LULL` (default 4). It is the deterministic B9 *floor* that
  *balances* the B8 *ceiling* (they no longer fight). Gates: 295 offline
  (`DirectorLullTests`) + a `director.lull-beat` live scenario (5/5); verified live
  E2E (an idle player, no clock, nothing happening → the director stirred the quiet
  room with gentle sensory beats). The model-side "should I act?" act-gate (B8/B5)
  remains the separate open lever that would make the lull *judgment*-based rather
  than timer-based.
- ~~A **world clock**~~ — **DONE** (see the update above).
- ~~probabilistic **weather**~~ — **DONE 2026-07-12.** `loom/weather.py`'s
  `WeatherSystem`: a bounded random walk over a chain of sky-states (clear ↔ cloudy ↔
  rain ↔ storm — DikuMUD's `weather.c` model), rolling every `period_pulses` with
  probability `change_chance`, bounded at the ends. It rides the *same* engine bridge
  as the clock — `apply_time_of_day` generalised to `apply_world_condition(tag, …)`, so
  the clock (tag `"time"`) and weather (tag `"weather"`) coexist; `"clear"` carries an
  empty condition, so it *lifts* the weather tag. Deterministic (injected seeded RNG,
  pulse-counted). Opt-in: `engine.attach_weather(loop, states, …)` reading the
  `"weather"` block; base engine has no weather. Gates: 313 offline
  (`tests/test_weather.py`, scripted RNG) + no new behavioral scenario (weather stirs
  the world exactly as the clock does → the reaction path, already `npc.reacts-to-world`;
  full harness re-run 20/20 regardless).
- ~~**Idle NPC autonomy**~~ — **LANDED 2026-07-20 as Phase 5 slice 4 (uncommitted at
  write; both gates green).** NPCs that act or speak unprompted (DikuMUD `mobact.c`: a
  separate, slower, per-NPC-gated pulse). Its *mechanism* was a cheap NPC-side lull mirror;
  its *quality* wanted Phase 5's mind depth + act-gate — all prerequisites landed first
  (act-gate `e94a218`, reflection `27fea59`, identity `635ce82`). Built as a new `Idler`
  system (`loom/ai/idle.py`, the `Reflector` skeleton) + `NpcMind.stir`: one NPC in a quiet,
  player-occupied room stirs from its own goal (reflections surface via retrieval), most
  often silent, delivered through `_deliver_turn` and cascading like any turn. **Peer to the
  director's lull** (signed off) — both read room-quiet off the shared chronicle, so they
  suppress each other; the settled "director shapes the world, minds move themselves" line
  holds. **Wandering** (signed off): a roamer (authored `wanders` — Wren) may walk an exit,
  an anchored one (Odd) is held by a hard rail. Gates: offline **552** (`tests/test_idle.py`,
  17 tests) + live `idle.stirs-unbidden` 3/8 & `idle.reticent-stays-still` 6/8 (the
  engagement/restraint pair). Spike: `docs/spikes/idle-npc.md`. **B9 is now fully closed** —
  every autonomy thread, world-side and character-side, is in.

**Related:** Phase 3 (director), B8 (restraint — the counter-force to balance),
Phase 5 (deeper minds / autonomy).

---

## B10 — The `loom-gm` wide-context model variant is wired but never exercised
*Noticed 2026-07-12 (Phase 3 director follow-up).*

**Want:** run the director on its own large-context model variant
(`ops/modelfiles/loom-gm.Modelfile` — `qwen3.6:27b` + `PARAMETER num_ctx 32768`)
as designed, and verify it end-to-end: that a wider baked context actually improves
the director's beats, and that the KV-cache VRAM budget ("one card") holds on the
2× RTX 6000 Ada alongside the NPC model. The plumbing exists (`LOOM_GM_MODEL` →
`_director_provider()` → `attach_director(provider=…)`), but the variant has never
been built, run, or measured — the director has only ever run on the shared NPC
model (`qwen3.5:35b-a3b`).

**Where it lands:** ops + a live measurement, not code — the wiring is done.

**Considerations for later:**
- Build and run it: `ollama create loom-gm -f ops/modelfiles/loom-gm.Modelfile`,
  then `LOOM_GM_MODEL=loom-gm python game/main.py`; confirm both cards stay within
  budget (`nvidia-smi`) and the director stays responsive at the wider context.
- A/B the beats: does 27b-dense + wide context read better for the GM role than
  35b-a3b, at the looser latency the slow cadence allows? (Phase 1's model notes
  predicted the dense tier for the GM.)
- If VRAM is tight, `OLLAMA_KV_CACHE_TYPE=q8_0` (already noted in the Modelfile).

**Related:** Phase 3 (director), Phase 1 (model/backend decisions and the GM-tier
prediction), B9 (autonomy — a wider context helps the director reason over more
world at once).

---

*Add new observations below with an ID (`B11…`), a date, and the same
what / where-it-lands / considerations shape.*
