# Backlog — noticed improvements (captured, not scheduled)

A running list of refinements noticed during review, to pick up at the opportune
time. These are **not** designed in full and **not** on the current phase plan —
they are captured so the intent isn't lost. When one is scheduled, promote it
into `docs/PLAN.md` with a real design.

Cross-references to the roadmap phases in `docs/PLAN.md` are noted per item.

---

## B1 — Richer, more flexible command vocabulary
*Noticed 2026-07-10.*

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

## B6 — Degrade-to-speech leaks a broken JSON envelope to the player
*Noticed 2026-07-11 (during B4 live testing).*

**Status (2026-07-11): scheduled for structural fix.** Promoted into `docs/PLAN.md`
as *Phase 2 hardening — constrained decoding*: grammar-constrained generation makes
a malformed envelope impossible at the token level, so this leak cannot occur on any
backend that supports the constraint. The considerations below remain the fallback
design for backends without constraint support and the offline `FakeProvider` path,
where validate-and-retry stays the last line of defense.

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

**Related:** Phase 7 (authoring), Phase 6 (map/entities channels), the inventory
world-model (2026-07-12, gives item locations), design commitment #2 (editable
data). Deferred here at the user's request (2026-07-12) rather than built now.

---

*Add new observations below with an ID (`B8…`), a date, and the same
what / where-it-lands / considerations shape.*
