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

## B4 — NPCs choose whether to react
*Noticed 2026-07-10.*

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

*Add new observations below with an ID (`B6…`), a date, and the same
what / where-it-lands / considerations shape.*
