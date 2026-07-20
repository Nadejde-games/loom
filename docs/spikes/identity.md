# Spike — Durable player identity & accretion (prior art for making a player persist)

**Date:** 2026-07-20 · **Question:** Loom's world now survives a restart (persistence,
slice 1) and its NPCs remember and reflect across reboots (memory depth + reflection, slice
2) — but the *player* does not exist between sessions. On TCP connect the engine mints an
ephemeral `Player(id="player:N", name="Wanderer-N")` at `start_location`, keyed by the
socket **session id**; on disconnect `World.remove_entity` erases the body and drops held
items to the floor. A returning player is a brand-new "Wanderer" the world has never seen.
How should Loom give a player a **durable identity** — so held loot, quests, and, above all,
*the world's memory of them* survive a disconnect — and how should a player's history
**accrete** on the same memory substrate as the NPCs? Specifically: what is the identity key
(name vs account vs surrogate id); is authentication needed; what happens to the body on
disconnect (delete vs link-dead); how does reconnect resolve; does the player need a memory
stream of their own, or is a durable name that NPC memories already reference enough; and is
"an NPC remembers you when you return" an emergent property of retrieval or an explicit beat.
· **Method:** two focused prior-art surveys against reference-level sources — the MUD
identity/login/link-dead lineage (CircleMUD C source, Evennia docs, PennMUSH, LPMud/FluffOS)
and the LLM-agent memory-of-the-player literature (the Stanford Generative Agents paper,
verified from ar5iv full text; MemGPT/Letta and Convai keying docs) — then mapped onto Loom's
actual seams (`loom/engine.py` player lifecycle, `loom/world/entity.py`, `loom/persistence.py`,
`loom/ai/memory.py` + `memory_store.py`, `loom/ai/mind.py`). No code written. The persistence
spike (`docs/spikes/persistence.md`) already surveyed the storage/format/authored-vs-mutable
dimensions and **explicitly deferred this thread** with its exact blocker: "durable player
accretion requires a stable player identity (account/name), which the current session-keyed,
disconnect-ephemeral `Player` model does not yet provide; land it when identity lands, on the
very same memory-stream substrate as NPCs." This spike lands it.

---

## Verdict

**Collapse to the DikuMUD core: the player's NAME is the durable login key, identity ==
character (no account object), and one durable player record per name is persisted into the
existing JSON overlay — players stop being excluded from persistence.** On connect, enter an
explicit `GET_NAME` login state instead of auto-minting a Wanderer; a typed name that matches
a saved record restores that player (location + inventory), a new name creates and persists
one. On disconnect, **do not delete-and-drop** — persist the record and detach; on reconnect
as a live-duplicate name, **newest connection wins** (kick the old socket, rebind — Evennia
mode-0 / Circle USURP). **Defer** password authentication (no adversary in a personal/trusted
forever-game — the name key alone delivers durability; reserve a nullable `password_hash` so
auth is a later state-machine insertion, not a schema migration), the account/character split
(pays off only for multi-character or account-level bans, neither of which applies), and
multi-session.

For **accretion**, the survey is decisive and it makes the payoff nearly free: in Generative
Agents the human is *just-another-agent* — their deeds live in the memory streams of the NPCs
who witnessed them, and recall is **emergent**, a prior memory surfacing because the current
situation again involves that person (the Sam/Latoya reunion has no "welcome back" trigger).
Loom's NPCs **already** write `f'{speaker_name} said to me: "…"'` into their streams
(`mind.py:325`); the *only* reason "the world remembers you" fails today is that
`speaker_name` is the throwaway "Wanderer-N". **Make the name durable and every existing
NPC-memory line becomes meaningful across sessions**, surfaced by the relevance retrieval that
already ships. A player therefore needs **no memory stream of their own** for the core payoff
— that is a separate, deferrable "previously, on…" recap feature. The one caveat to engineer
around: pure emergence only fires when the returning player *says* something embedding-relevant
— so on reconnect **seed the present NPCs' perception with the durable name** ("Odd notices
that Andrei has returned") through the existing autonomous-reaction cascade, so their next
retrieval reliably pulls "Andrei promised to return." The minimal proof: connect as *Andrei*,
make a promise to the Hermit, disconnect, restart the server, reconnect as *Andrei* — the
Hermit's memory of that promise surfaces when you speak to him again, and your inventory and
quests are intact.

---

## 1. The account ↔ character identity model

**DikuMUD / CircleMUD player files — the closest model to Loom, and the one to copy.** The
durable player is a single per-player record; **the durable key is the NAME**. Login is
name-first: `nanny()` enters `CON_GET_NAME`, takes the typed name, looks it up in the player
index — a hit routes to password/auth for an existing character, a miss routes to new-character
creation. There is **no separate account object: identity == character, keyed by the name
string** ([CircleMUD `interpreter.c` — `nanny`, `CON_GET_NAME`](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c);
[`structs.h` xref — `char_file_u`](https://www.circlemud.org/cdp/cxref/structs.h.html)). This
is exactly the shape Loom wants: a name typed at a prompt, mapped to a stable per-player record.
Loom already carries surrogate internal ids (`player:N`); the move is to make the *login key* the
name and derive a stable id from it, mirroring Diku's name-as-key.

**The modern account-vs-character SPLIT — Evennia — and why to skip it.** Evennia splits
identity into `AccountDB` (the out-of-character login identity, *no in-game presence*) and the
in-game `Character`/`ObjectDB`; an account enters the world only by **puppeting** an object
(`puppet_object`/`unpuppet_object`, hooks `at_post_puppet`/`at_post_unpuppet`). The documented
justification is that **permissions and bans live on the account and outlive any body**, and one
login can own many characters ([Evennia — Accounts](https://www.evennia.com/docs/latest/Components/Accounts.html)).
Every payoff of the split — multi-character, account-level bans, one-login-many-bodies — is
absent in a single-avatar personal forever-game. **Collapse identity == character for slice-1**;
the Evennia split is the clean documented upgrade path if those needs ever arise.

**Surrogate-id lineages worth knowing.** PennMUSH keys a player by a `dbref` (`#123`) with the
password an attribute on the object (hashed via OpenSSL digest, auto-upgraded on login —
[PennMUSH 1.8.5 notes](http://community.pennmush.org/node/1005)); LPMud persists the player
object with `save_object()` to a per-player `.o` file, conventionally name-keyed, modern FluffOS
hashing passwords with SHA512-crypt ([FluffOS](https://github.com/fluffos/fluffos)). The split
across the survey is **name-keyed (Diku, LP)** vs **surrogate-id-keyed (Penn dbref, Evennia PK)**.
Loom takes the pragmatic middle: the *login key* is the name (what the human types), mapped to a
stable internal id derived from it, so the human-facing key and the machine key never drift.

## 2. Authentication in a text game — and why to defer it (safely)

**How Diku/Circle store & check passwords.** The password is stored **hashed with the C library
`crypt()`**; verification in `CON_PASSWORD` crypts the typed input using the stored hash as salt
and compares. New players set it in `CON_NEWPASSWD` → `CON_CNFPASSWD`. The canonical login FSM is
`CON_GET_NAME → CON_PASSWORD (or CON_NEWPASSWD → CON_CNFPASSWD) → … → CON_MENU → CON_PLAYING`
([`interpreter.c`](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c)).
The load-bearing lesson is not the crypt — it is the **explicit per-connection login state**: a
connection is *not* a player until it has passed through `GET_NAME`.

**Is password auth essential? Not here.** Every *public multiplayer* server surveyed (Diku,
Circle, Penn, LP, Evennia) ships mandatory passwords — because the threat model is impersonation
and griefing among strangers, and a durable name is a contestable, valuable resource. **That
threat model is absent in a single-player or trusted-friends forever-game.** *(This "personal
games may skip passwords" call is an inference from the threat model, not a fetched prescription
— flagged.)* Name-only login delivers the one thing durability actually needs — a **stable string
key** mapping a returning connection to a saved record; a password only defends that key against
*other people*, which is pure friction with no adversary. Evennia itself ships **guest accounts**
and auto-login precisely to cut credential friction. **Decision: name-only login now; reserve a
nullable `password_hash` field so adding auth later is an FSM insertion (`GET_NAME →
[GET_PASSWORD] → PLAYING`), not a data migration.**

## 3. Link-dead handling and reconnection — the highest-value section

**What happens to the body when the socket drops.** Canonical Diku/Circle `close_socket()` on a
`CON_PLAYING` descriptor does **not** extract the character: it detaches the body (`desc = NULL`),
leaves it **link-dead in its room**, tells the room "*X has lost his link.*", and runs
`save_char()` on disconnect. Only descriptors still at the login prompt free the character
outright ([`comm.c` — `close_socket`](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/comm.c)).
*(Idle-void auto-extraction of a link-dead body is a derivative add-on, not stock — flagged.)*
**This is the single biggest correction to Loom's current behavior**, which deletes the entity and
drops its items on the floor. The right move: on drop, **persist and detach — never delete-and-drop**.

**Reconnection / session takeover.** After a successful name (for Loom; name+password in Circle),
Circle's `perform_dupe_check()` resolves duplicates in three modes: **RECON** — a link-dead body
with the same id and no live descriptor: rebind it to the new connection (`"Reconnecting."`);
**USURP** — the id is *actively playing* on another live socket: take it over, kick the old socket
(`"You take over your own body, already in use!"`, old socket gets `"Multiple login detected --
disconnecting."`); **UNSWITCH** — an immortal `switch` restore. The essential move is that the new
descriptor **adopts the existing body** rather than spawning a second one, and the old socket is
cut ([`interpreter.c` — `perform_dupe_check`](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c)).
Evennia's `MULTISESSION_MODE 0` (default) is the same rule stated plainly: *"When connecting with a
new session the old one is disconnected"* ([Evennia — Connection Styles](https://www.evennia.com/docs/latest/Concepts/Connection-Styles.html)).
**Loom adopts mode-0: newest connection wins; a reconnect rebinds the existing durable player, never
spawns a duplicate.**

**Where a returning player re-enters.** Circle prefers a **saved per-character load room**, falling
back to a global mortal start room; a link-dead *reconnect* is moot because the body never left
([`interpreter.c` — `GET_LOADROOM`](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c)).
**Loom persists last location on the record; reconnect restores you where you stood, with
`start_location` the fallback when none is saved.**

## 4. When player state is saved

Circle saves the player **on quit/disconnect** (`save_char()` in `close_socket`), on a **periodic
autosave** (`autosave_time` default 5 min), and via the separate rent/crash-save system for carried
inventory (`config.c`; [FAQ](https://www.circlemud.org/cdp/faq/)). **Loom already unifies what Diku
split across pfile + rent files** into one JSON overlay + SQLite memory DB, and already has a
shutdown save + a coarse autosave on the game loop. The move is simply to **fold the player record
into that existing overlay** and to write it on disconnect — no new cadence, no rent machinery.

## 5. Player-as-agent memory accretion — the payoff is nearly free

**The human is just-another-agent (Generative Agents §3.2).** *"End users can also enter the sandbox
world of Smallville as an agent… The inhabitants will treat the user-controlled agent no differently
than they treat each other. They recognize its presence, initiate interactions, and remember its
behavior before forming opinions about it."* There is **no separate "player memory" concept** — what
persists about the player lives in the streams of the agents who witnessed them
([ar5iv:2304.03442](https://ar5iv.labs.arxiv.org/html/2304.03442)).

**Recall of a named individual is emergent via relevance retrieval (§3.4.2).** Sam, meeting Latoya
again, opens with *"Hi, Latoya. How is your project going?"* — **no welcome-back trigger**; the prior
memory surfaces because the current situation again involves Latoya, and relevance retrieval
(`score = recency + importance + relevance`, relevance = cosine of the memory against the *current
query*) pulls the embedding-similar memory. Relationship/acquaintance is a *measured emergent property*
of accumulated memories, **not a separate table**.

**This maps onto Loom with one change.** NPCs already record the speaker's name in their memory text
(`mind.py:325`, `:328`), and retrieval already ranks by relevance (`memory.py`). The durable name is
the whole mechanism: "Andrei promised to return" is a natural-language entry in Odd's stream that
resurfaces when Andrei speaks to Odd again. The keying pattern is confirmed by the agent-framework
norm — Convai scopes memory by a stable `EndUserID`, Letta keeps a dedicated `human` memory block —
both "a durable id the system keys the person's facts to." **A player therefore needs no stream of
their own for the payoff.** A player-owned stream serves only a *recap* ("previously, on…", the AI
Dungeon Memory-Bank use case) and is nearly free on the shared substrate — a fine later slice, but
the core payoff must never be gated on it.

**The one engineering caveat, fixed inside the existing mechanism.** Pure emergence only fires when
the returning player *says/does* something embedding-relevant; a player who merely walks up can get a
generic greeting. The fix is **not** a new subsystem: on reconnect, seed the present NPCs' perception
with the player's durable name (record/announce "Andrei has returned"), so the existing autonomous
reaction cascade runs a retrieval already primed with the name and reliably surfaces the old memory.
A *guaranteed* recall beat, if wanted, is a thin deterministic wrapper over the same retrieval — one
thresholded `retrieve(query=<name>)` per present NPC — not a parallel memory system.

---

## Comparison

| Axis | DikuMUD / CircleMUD | Evennia | PennMUSH / LPMud | Generative Agents | **Loom (proposed, slice-1)** |
|---|---|---|---|---|---|
| **Identity key** | **Name** (identity == character) | Account PK ↔ puppeted char (split) | dbref / name-keyed `.o` | Agent == person (no separate player id) | **Name → derived stable id; identity == character** |
| **Auth** | `crypt()` password, `CON_PASSWORD` FSM | Django auth on the account | Hashed attr / SHA512-crypt | — | **Name-only; reserve `password_hash`, defer** |
| **On disconnect** | **Link-dead body stays; `save_char()`** | Session drop; account persists | save_object on quit | User simply leaves; memories remain | **Persist record + detach; never delete-and-drop** |
| **Reconnect** | `perform_dupe_check`: RECON / USURP | mode-0: new session kicks old | — | Re-encounter surfaces memories | **Newest-wins; rebind the durable player, no duplicate** |
| **Re-entry** | Saved load-room, else start room | Last puppet location | — | — | **Saved last location, else `start_location`** |
| **Player memory** | pfile stats (no NL memory) | Attributes on the char | Attributes | **Lives in witnesses' streams; recall emergent** | **Durable NAME in NPC streams; no player stream (defer)** |

---

## Recommendation for Loom — the design

### Identity: name is the key, identity == character, one durable record per name

- **Login FSM, not auto-mint.** `on_connect` no longer mints a Wanderer and places it. It sends a
  name prompt ("By what name are you known?") and puts the session in a `GET_NAME` login state. The
  first input line is taken as the name (normalized: trimmed, collapsed whitespace, case-folded to a
  **slug** for the key while preserving the typed display form). Only then is the player placed and the
  banner/look/start-quests shown. This is Diku's `CON_GET_NAME`, minus the password branch — the FSM is
  a small per-session `login_state` field and one branch at the top of `on_input`.
- **The durable id is derived from the name:** `player:<slug>` (e.g. *Andrei* → `player:andrei`). Same
  name → same id → same record → (later) same memory stream. Renaming is unsupported this slice, exactly
  as name-is-key in Diku implies. Reserved/colliding slugs (`director`, an authored NPC id, empty) are
  rejected with a re-prompt.
- **The durable `PlayerRecord`** (per slug): `{id, name, location_id, held: [item records], quests,
  created_t, last_seen_t, password_hash: null}`. It is persisted in a **new `players` block in the JSON
  overlay** (`persistence.snapshot`/`restore`) — players stop being excluded (removing the
  `# no durable identity yet` caveats in `persistence.py`). Held items travel *with the record* rather
  than being dropped to the floor (the current `_item_record` player→floor rewrite is replaced for
  identified players). Quests, already keyed by player id in `QuestBook`, become durable for free once
  the id is stable.

### Lifecycle: persist-and-detach on disconnect, newest-wins on reconnect

- **On connect (name captured):** look up the record by slug. **Match** → restore the body at its saved
  `location_id` with its inventory and quests (a *returning* player). **No match** → create the record +
  body at `start_location`, offer start quests (a *new* player). If the slug is **already live** in
  `self.players` (a duplicate/zombie session), **USURP**: kick the old socket, rebind the existing body
  to the new session, do not spawn a second — Evennia mode-0.
- **On disconnect:** write/update the record from the live body (location + held items + quests), then
  detach — **never `remove_entity` a player to the floor**. (See the sign-off fork below on whether the
  body also leaves the live world or lingers link-dead.)
- **Save cadence:** on disconnect and on shutdown (already wired), plus the existing coarse autosave —
  no new machinery.

### Accretion: the durable name is the mechanism; recall is emergent, name-seeded

- **No player memory stream this slice.** The payoff — "the world remembers you" — is delivered entirely
  by the durable name flowing into NPC streams (already written at `mind.py:325`) and surfaced by the
  relevance retrieval that already ships. This *is* the north-star "history accretes through play": the
  player's history accretes into the world's memory *of* them, held by the NPCs who witnessed it — the
  GA-faithful realization. A player-owned stream (for a recap) is a clean, near-free later slice, not a
  prerequisite.
- **Reconnect recall, name-seeded through the existing cascade.** When a *returning* player arrives, seed
  each present NPC's perception with the durable name ("Odd notices that Andrei has returned") via the
  existing `_spawn_reaction` path, so any NPC with salient memory of *Andrei* may recall it of its own
  volition — reusing B9 entirely, near-zero new code. Whether to add a *guaranteed* recall beat on top is
  the second sign-off fork.

### The minimal proof (both gates)

1. Connect; at the name prompt, become **Andrei**. Make a promise to Odd the Hermit ("I'll bring the key
   back"). Take a forged item. Disconnect.
2. **Restart the server.**
3. Reconnect; become **Andrei** again. You are restored at your last location with the item still in hand;
   your quest is intact; and when you speak to Odd, his memory of your promise surfaces (relevance
   retrieval), colouring his reply. A *different* name gets a stranger's reception.
   The offline gate proves the FSM, the record round-trip through save/restore, the USURP rebind, and the
   name-seeded reconnect perception deterministically (FakeProvider); the live gate proves the memory
   actually resurfaces across a real restart (`behavior_probe`, real embedder).

---

## Two decisions for sign-off

> **Signed off (2026-07-20):** (1) **Persist-and-remove** — a dropped player's record is saved and
> the body removed from the live world; no lingering link-dead ghost. (2) **Emergent, name-seeded** —
> reconnect recall reuses the autonomous-reaction cascade (seed present NPCs with "<name> has
> returned"); no guaranteed deterministic beat this slice.

1. **Disconnect model — does a dropped player leave a body behind?**
   - **Persist-and-remove (recommended).** On disconnect, write the durable record (location + inventory
     + quests) and *remove* the body from the live world — no frozen ghost standing for days, inventory
     travels with the record, reconnect restores you at your saved location. Cleanest for a solo/trusted
     forever-game where sessions are far apart; the item-ownership-while-offline lives entirely in the
     record, not as a dangling holder in the world.
   - **Link-dead (Diku-faithful).** The body lingers in place, detached and marked link-dead; reconnect
     rebinds in place; persisted by extending the overlay's `positions`/`items` to include players.
     Simpler persistence code (reuses the character-snapshot path), truest to the MUD lineage, and the
     right model the day the world is busy and drops are brief — at the cost of an inert body occupying a
     room (and a possible director/NPC "occupant" perceiving it) across long offline gaps.

2. **Reconnect recall — emergent, or a guaranteed beat?**
   - **Emergent + name-seeded (recommended).** Reuse the autonomous-reaction cascade: seed present NPCs
     with "<name> has returned" so recall emerges through normal play when you interact. Minimal,
     GA-faithful, near-zero new code — but not *guaranteed* to fire on any given reconnect (reaction budget
     gates it).
   - **Guaranteed recall beat.** On a returning connect, for each present NPC run `retrieve(query=<name>)`;
     if the top memory clears a salience threshold, that NPC opens with an in-voice recall line grounded in
     it. A guaranteed, theatrical "the world remembers you" moment — at the cost of a thresholded retrieval
     per present NPC and one model call to phrase the line. (Can be layered *on top of* the emergent path.)

---

## What to steal / what to skip

**Steal**
- **Name-as-durable-key, identity == character** (Diku/Circle, §1) — a name prompt mapped to one
  per-player record; derive the internal id from the name so human key and machine key never drift.
- **An explicit login state** (`nanny()`/`CON_GET_NAME`, §2) — a connection is not a player until it has
  passed `GET_NAME`; even name-only wants the state, not an implicit mint-on-connect.
- **Persist-and-detach, never delete-and-drop** (`close_socket`, §3) — the biggest correction to Loom's
  current disconnect behavior.
- **Newest-wins reconnect / rebind the existing player** (`perform_dupe_check` / Evennia mode-0, §3) — a
  reconnect adopts the durable player, never spawns a duplicate.
- **Saved last location with a start-room fallback** (`GET_LOADROOM`, §3).
- **The whole Generative-Agents accretion model** (§5) — player == agent, history lives in witnesses'
  streams, recall emergent from relevance; the durable name is the entire mechanism.

**Skip (for now)**
- **Password authentication** (§2) — no adversary in a personal/trusted game; reserve `password_hash` and
  add the `GET_PASSWORD` state later. *(Flagged: this rests on the threat model, not a fetched prescription.)*
- **The account/character split** (Evennia, §1) — pays off only for multi-character or account-level
  bans/permissions; keep identity == character, the split is the documented upgrade path.
- **Multi-session / multi-character** — stay mode-0 (one session, newest wins).
- **A player's own memory stream** (§5) — not needed for the payoff; a near-free later slice for a recap.
- **Rent/crash-file inventory machinery** (§4) — Loom's unified overlay + SQLite already subsumes it.
- **A bespoke reputation table or a parallel "welcome-back memory"** (§5) — acquaintance is emergent; a
  guaranteed beat, if chosen, wraps the *existing* retrieval, it does not add a second memory system.

**Flagged caveats:** "personal games may skip passwords" is an inference from the threat model, not a
fetched line; the exact Circle takeover string varies by version; idle-void auto-extraction of a link-dead
body is a derivative add-on, not stock; and the player-stream-for-recap value rests on secondary sources
(AI Dungeon / Inworld), not the primary paper.

---

## Appendix — sources & verification status

| Claim area | Source | Status |
|---|---|---|
| Diku/Circle: name-keyed identity==character; `nanny`/`CON_GET_NAME` login FSM; `char_file_u` record | [interpreter.c](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c), [structs.h xref](https://www.circlemud.org/cdp/cxref/structs.h.html) | Verified (fetched source) |
| Circle: `crypt()` password store/check; `CON_PASSWORD`/`CON_NEWPASSWD`/`CON_CNFPASSWD` states | [interpreter.c](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c) | Verified (fetched source) |
| Circle: link-dead body stays on `close_socket`, `save_char()` on disconnect | [comm.c](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/comm.c) | Verified (fetched source) |
| Circle: `perform_dupe_check` RECON/USURP/UNSWITCH, rebind existing body, kick old socket | [interpreter.c](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c) | Verified (fetched source) |
| Circle: `GET_LOADROOM` saved re-entry, mortal start-room fallback | [interpreter.c](https://raw.githubusercontent.com/Yuffster/CircleMUD/master/src/interpreter.c) | Verified (fetched source) |
| Circle: save on quit/disconnect; `autosave_time` 5 min; rent/crash timeouts | [config.c](https://github.com/Yuffster/CircleMUD/blob/master/src/config.c), [FAQ](https://www.circlemud.org/cdp/faq/) | Verified (search + source) |
| Evennia: AccountDB vs Character, puppet/unpuppet, permissions on the account | [Accounts](https://www.evennia.com/docs/latest/Components/Accounts.html) | Verified (docs) |
| Evennia: `MULTISESSION_MODE 0` — new session disconnects the old | [Connection Styles](https://www.evennia.com/docs/latest/Concepts/Connection-Styles.html) | Verified (docs) |
| PennMUSH dbref-keyed player, hashed password attr; LPMud/FluffOS `save_object`/SHA512-crypt | [PennMUSH notes](http://community.pennmush.org/node/1005), [FluffOS](https://github.com/fluffos/fluffos) | Verified (search/repo) |
| GA: human is just-another-agent; NPCs remember its behavior (§3.2) | [ar5iv:2304.03442](https://ar5iv.labs.arxiv.org/html/2304.03442) | Verified (fetched, quoted) |
| GA: named-individual recall emergent via relevance retrieval (§3.4.2, Sam/Latoya); score formula (§4) | [ar5iv:2304.03442](https://ar5iv.labs.arxiv.org/html/2304.03442) | Verified (fetched, quoted) |
| Durable-id keying norm: Convai `EndUserID`, Letta `human` block | Convai LTM docs; Letta memory-blocks docs | Verified (primary docs, medium-high) |
| Player-owned stream serves a recap (AI Dungeon Memory Bank; Inworld synthesis) | AI Dungeon help; Inworld blog | Secondary — flagged folklore, not load-bearing |
| Loom seams: session-ephemeral player; `mind.py:325` speaker-name memory write; overlay excludes players | `loom/engine.py`, `loom/ai/mind.py`, `loom/persistence.py`, `loom/ai/memory.py` | Verified against working tree |
