# Spike — compound & chained player commands (B11)

*Designed 2026-07-21. The first play-loop-polish papercut: a player types more
than one thing per line — `take lantern and key`, `look at Wren and say what is
this place?` — and the parser honors all of it.*

## The gap

`loom/command.py:parse` is strictly **one verb per line**. It matches a single
verb, then splits the remainder into a direct/indirect object on that verb's
preposition. So a line carrying more than one intent fails two ways:

- **Conjoined objects** — `take lantern and key` hunts for a single item literally
  named *"lantern and key"* (NoMatch), instead of taking both.
- **Chained commands** — `look at Wren and say what is this place?` feeds the whole
  tail to name-resolution, yielding the observed error
  `You see no "Wren and say what is this place?" here.`

B1 (DONE) gave *single-command* synonym/phrasing flexibility. B11 is the
**orthogonal axis**: more than one command, or more than one object, from one line.

## Prior art

The multi-command problem is decades-solved in interactive fiction. Two mature
parsers converge on the same model, and the classic MUDs on a simpler one.

| Source | The idea | What we take | What we leave |
| --- | --- | --- | --- |
| **TADS 3** (`parser.t`, prssum) | A command line is *"a number of commands joined with command separators; in English, command separators are periods, semicolons, commas, and the words `and` and `then`."* Multiple noun phrases are separated by `and`/comma — **the same tokens** as command separation, disambiguated by whether what follows parses as a new command or a noun. | The separator vocabulary (`.` `;` `then` `and` `,`) and the crucial insight: **`and`/`,` are ambiguous**, resolved by grammar context — is the next segment a *verb* (new command) or a *noun* (another object)? | The full sentence grammar / topic system. Ours is a flat verb table. |
| **Inform 7** (RB §6.15, WI §17.20) | Vague/explicit multi-object requests (`GET ALL`, `GET GERBIL, APPLE, AND POMEGRANATE`) build a **multiple objects list**; the action is **iterated per object**, printing the object's name before each result. Comma is an "and-word"; `but` is a "but-word" (exclusion). | The **iterate-per-object** execution with **per-object reporting** — exactly B11's partial-success shape. The comma-as-conjunction rule. | `but`/exclusion, and (this slice) `ALL`-expansion — see the open fork. |
| **DikuMUD / CircleMUD** (`interpreter.c`) | One command per line at the interpreter; multi-object only via `get all`, `get all from corpse`, dotted `all.corpse`. | Confirmation that chaining is an IF nicety, not load-bearing — and that `all` is the one multi-object form the MUD lineage *did* ship. | Dotted `N.item` / `all.item` forms — alien to our name-resolution. |

**The settled lesson:** don't split on `and` naively. Treat `.` `;` `then` as
*unconditional* command separators, and `and`/`,` as **object-conjunction by
default, promoted to a command separator only when the next segment begins with a
known verb** (the TADS/Inform model). A free-text verb (`say`) swallows its
remainder verbatim and is never re-split. This is *verb-led promotion*.

## The design

A new **`parse_line(text, verbs) -> list[Parse]`** beside the existing
`parse`. `parse` is unchanged — it still parses **one segment** into one `Parse`
(so all 553 existing tests and every current caller keep working); `parse_line`
is the new entry the engine calls. It does three things:

1. **Split** the line into command segments (the deterministic splitter below).
2. **Parse** each segment with the existing `parse` (one segment → one `Parse`).
3. **Expand** object-conjunctions: a resolvable-object verb whose direct-object
   phrase is `X and Y` / `X, Y` becomes *N* `Parse`s, one per object, sharing the
   verb and any indirect object (`give sword and shield to Odd` → two gives → Odd).

### The splitter (deterministic, verb-led)

Walk the tokens left to right, original case preserved:

1. **Match a verb** at the cursor (greedy two-word then one-word — the same match
   `parse` already does).
2. **Free-text verb** (`kind == "text"`, i.e. `say`/`tell`): it **swallows the
   entire rest of the line** as its payload. One segment, and the walk stops. So
   `say hello and goodbye` is one utterance; `say hi. go north` is the single
   utterance *"hi. go north"* — a free-text verb is the **last** thing on a line.
3. **Otherwise**, consume tokens for this command until a **boundary**:
   - `.` `;` or the word `then` — an **unconditional** separator; or
   - the word `and` or `,` **immediately followed by a token that matches a known
     verb** — verb-led promotion (`look at Wren and say …` → boundary before `say`).
   At the boundary, close this segment and start the next after the separator.
4. `and`/`,` **not** followed by a known verb stays *inside* the current segment's
   object phrase — it is a conjunction, handled by object-expansion in step 3 of
   `parse_line` (`take lantern and key`, `give sword and shield to Odd`).
5. **Runaway cap:** at most `MAX_COMMANDS` segments per line (a cost fuse, the
   cascade-fuse lesson; default 16). Excess is dropped with a reported notice —
   never silently truncated.

`loom/naming.py` is **unchanged** — the splitter feeds it clean single-object
phrases, exactly as today.

### Engine dispatch

`Engine.on_input` (`engine.py:426`) changes from *parse-one* to *parse-many*:

```
parses = command.parse_line(text, self.verbs)
for p in parses:
    if p.verb is None and p.unknown and self.intent_fallback:
        p = await self._interpret(player, <that segment's text>) or p
    await self._dispatch(session, player, p)
```

- **In order**, so a chain that moves rooms re-perceives naturally — each command
  runs against the world state the previous one left (`look and go north and look`).
- **Per-command reporting** is inherent: each `_dispatch` sends its own line, so an
  unresolved object in command 2 (a "no such thing" / a "which do you mean?") is
  reported for *that* command and the rest of the chain still runs. **No abort** —
  the Inform iterate-and-report shape.
- The **B1b LLM fallback fires per segment**, and only on an *unknown first verb* —
  unchanged trigger. A single-command line with no separators is one segment = the
  whole line, so the current behavior is preserved byte-for-byte.

### Decision 1 — the `and`-as-chain rule *(signed off: A — verb-led promotion)*

**A — verb-led promotion (recommended).** `and`/`,` chain a new command *iff* the
next token is a known verb, else conjoin objects. Handles **both** flagged examples
deterministically, no model call — and matches how the user actually typed the bug
(`look at Wren **and** say …`). The proven TADS/Inform rule. Residual risk is
vanishing: only a line like `take gold and <verb>` where the player *meant* an item
literally named after a verb — and the free-text swallow removes the `say` case.

**B — explicit markers only.** `.` `;` `then` chain; `and`/`,` *only ever* conjoin
objects. Zero heuristic, fully predictable — but it **does not fix the reported bug
as typed**: the player must retype `look at Wren **then** say …`. Simpler, at the
cost of the more natural phrasing.

### Decision 2 — does this slice include bare `all`? *(signed off: A — include now)*

`take all` / `drop all` / `take all from chest` is the one multi-object form the MUD
lineage shipped, and the single *most-wanted* one. It rides the **exact same**
iterate-per-object + partial-success machinery as conjoined objects — the only new
part is expanding the keyword `all`/`everything` against the verb's own scope (all
floor items for `take`, all held items for `drop`), which the engine already
enumerates for name-resolution.

**A — include `all` now.** One coherent "multiple objects" slice; `take all` is the
command players reach for first. Small engine addition (scope → concrete set).

**B — defer `all` to B12.** Keep B11 tightly on the two flagged behaviors
(conjunction + chaining); ship `all` as a clean follow-up on the same rails.

### Deferrals (out of this slice, regardless of the forks)

- **LLM decompose mode** — extending B1b to break a hard line into a *command list*.
  The deterministic splitter + the existing unknown-verb fallback cover the known
  cases; a decompose mode is speculative cost/non-determinism until a real line
  defeats the splitter. Deferred (a backlog "later").
- **`but`/exclusion** (`take all but the sword`) — Inform's but-word. Only meaningful
  once `all` lands; deferred even if `all` is in.
- **Interactive disambiguation across a chain** — today an ambiguous object reports
  *"which do you mean?"* statelessly (no pending-answer state in the engine); in a
  chain it reports for that command and the chain continues. A stateful "hold the
  rest of the line until you answer" is a larger change, deferred.
- **Dotted `N.item` / `all.item`** MUD forms — alien to `naming.resolve`; not adopted.

## Slice definition (what gets built)

- `loom/command.py` — `parse_line(text, verbs) -> list[Parse]`; an internal
  `split_commands` (the verb-led splitter with the free-text-swallow rule and the
  runaway cap) and object-conjunction expansion. `parse` unchanged.
- `loom/engine.py` — `on_input` loops the parsed sequence, per-segment B1b fallback,
  in-order dispatch. *(If Decision 2 = include:)* `all`/`everything` scope-expansion
  in the object-resolution path.
- `tests/test_command.py` — the splitter's natural home (deterministic): free-text
  swallow, verb-led promotion vs conjunction, unconditional separators, object
  expansion (with and without an IO), the runaway cap, and the single-command
  identity (one segment in → one `Parse` out, unchanged). Engine-level tests for
  in-order dispatch + partial success. *(If `all`:)* `all`-expansion + empty-scope.
- `scripts/behavior_probe.py` — **only if** the LLM decompose path is built (it is
  not, this slice) — so no new live scenario; B11 is deterministic and offline-gated.

## Appendix — the splitter, drawn

```
take lantern and key            → [take lantern] [take key]        (and+noun → conjoin)
give sword and shield to Odd    → [give sword→Odd] [give shield→Odd] (conjoin, IO shared)
look at Wren and say what is..?  → [examine Wren] [say "what is..?"] (and+VERB → chain; say swallows)
say hello and goodbye           → [say "hello and goodbye"]         (free-text swallows verbatim)
n. e. take lamp                 → [go north] [go east] [take lamp]  (. → unconditional chain)
take gold and go north          → [take gold] [go north]            (and+VERB → chain)
```
