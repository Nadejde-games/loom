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

## B2 — Fuse an NPC's speech and action into a single line
*Noticed 2026-07-10.*

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

## B3 — Rich text formatting (color, italics, bold)
*Noticed 2026-07-10.*

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

## B5 — The model under-selects world-mutating actions (move) vs. emote
*Noticed 2026-07-10.*

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
- **Still open (the real B5 lever):** raising the ceiling above ~70% needs a
  structural change, not another prompt tweak — a two-pass turn (a cheap, low-temp
  "act? and how?" decision, then the speech) or a more capable model. Deferred.
  Note lowering temperature alone would entrench the *emote* mode on some
  phrasings, so it is not the lever.

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
**Still open:** making the director *choose* wisely (not just be rate-limited) —
the model-side two-pass "should I act?" pass below, shared with B5.

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
- **A two-pass director turn** — a cheap, low-temp "is a beat warranted right
  now?" gate before composing one. This is the *same* lever B5 wants (a
  should-I-act? pass distinct from the act), and the TaleWeave spike's two-phase
  plan→act split is the pattern. A shared "act gate" could serve both the NPC
  `move` ceiling and the director's restraint.
- **A cooldown / budget in the orchestrator** (`Director`), independent of the
  model — DONE 2026-07-12: `min_new_events` + `cooldown_pulses` require both M new
  chronicle events and N pulses since the last beat. Cheap, deterministic, tested.
  Next dial to consider: a per-day/per-scene *budget*, or scaling the floor with
  how many players are present.
- **Salience for the director** — reuse the B4 idea: score whether the recent
  chronicle actually *calls* for a beat (a lull, a player stuck, a dramatic turn)
  rather than pulsing on any change.
- Currently **not gated** by the behavioral harness — restraint is not a verified
  working behavior, and the discipline is one scenario per *verified* behavior. Add
  a `director.restraint` scenario once a lever above makes it real.

**Related:** Phase 3 (director), B5 (the same action-selection ceiling / two-pass
lever), B4 (salience gate — choosing whether to engage).

---

## B9 — The director is reactive, not autonomous (no ambient life on its own)
*Noticed 2026-07-12 (playing the Phase 3 director).*

**Frames the settled design decision (PLAN, Phase 3): the director shapes the
*world*, not minds; NPCs react on their own.** This item is that principle's
to-do list — the director needs an autonomous way to change the world (a
world-clock / environmental events), and NPCs need to *react* to those changes of
their own volition (the NPC-autonomy half). Neither is built yet.

**Want:** the world should feel alive even when the player is idle — the director
able to set a beat on a *lull*, and ambient / world-clock events (weather turning,
night falling, something stirring off-screen) that arise without a player action.
Today the director is purely **reactive**: the chronicle only fills from
player-driven activity (arrivals, the player's speech, NPC replies to it,
player/NPC moves), so a still room produces no new events, the B8 restraint floor
never clears, and the director never speaks. Correct *as restraint* — but it means
the world goes inert the moment you stop typing.

**Two sub-gaps:**
- **No autonomous event source.** Nothing advances the world without player
  input — no world clock, no idle NPC activity, no scheduled beats. The chronicle
  is the only trigger and it is entirely player-fed.
- **The director can only stage into occupied rooms.** `world_snapshot()` shows
  only rooms with someone present, so the director cannot foreshadow into a room
  the player is about to enter, nor let a distant place stir off-screen.

**Where it lands:** `loom/ai/director.py` (`Director` — a time/lull trigger beside
the activity trigger; a snapshot that can include chosen empty rooms) and likely a
small **world-clock** system on the game loop (`loop.py`) as an autonomous event
source that writes to the chronicle.

**Considerations for later:**
- A **lull trigger**: if N pulses pass with players present but too few events to
  clear the B8 floor, allow one *low-key* ambient beat anyway (a gentler budget
  than the reactive one), so restraint doesn't tip into deadness. Note the direct
  tension with B8 — the two must be balanced, not fought.
- A **world clock**: scheduled or probabilistic environmental beats (time of day,
  weather) recorded to the chronicle — turning "reactive" into "reactive to a
  living world," which is the cleaner framing than the director inventing time.
- **Idle NPC autonomy** (a larger step, overlaps Phase 5 minds): NPCs that act or
  speak unprompted, which the director then perceives and shapes around.

**Related:** Phase 3 (director), B8 (restraint — the counter-force to balance),
Phase 5 (deeper minds / autonomy).

---

## B10 — The `loom-gm` wide-context model variant is wired but never exercised
*Noticed 2026-07-12 (Phase 3 director follow-up).*

**Want:** run the director on its own large-context model variant
(`ops/modelfiles/loom-gm.Modelfile` — `qwen3.5:27b` + `PARAMETER num_ctx 32768`)
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
