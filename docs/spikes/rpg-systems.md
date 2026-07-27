# Spike — RPG systems & stakes (the mechanics arc)

**Date:** 2026-07-25 · **Question:** the world can talk, move, and trade, but nothing is
*at stake* — there are no characters to build, no numbers to grow, nothing that can hurt
you. How do we add a full text-RPG mechanics layer (backgrounds, races, classes, HP/mana,
hunger, gear, a stat-point system, abilities, combat, mobs, real quests, XP/leveling) to a
text-first, LLM-driven world **without** breaking the two things that make Loom what it is —
its game-agnostic framework mandate and its golden rule? · **Method:** a focused prior-art
survey (DikuMUD/CircleMUD, D&D 5e SRD, Evennia's TraitHandler, safe-formula-evaluation
patterns), mapped onto Loom's existing seams. Living document — Tier 0 is designed and
signed off; later tiers are stubs filled as we work through them. No code written yet.

---

## Verdict

**This is one coherent layer — "stakes" — and Loom was built to receive it.** Every item on
the list hangs off a single new primitive: a **character sheet** (stats + resource pools +
level/xp) that hard mechanics read and write, and that the soft LLM world *perceives
qualitatively*. Two invariants hold across the entire arc and are not re-decided per system:

1. **Mechanism in `loom/`, rules-as-data in `game/`.** Loom ships the *machinery* — a stat
   engine, resource pools, a modifier/effect stack, a combat resolver, an XP-curve evaluator,
   equipment slots. The *game* supplies every actual race, class, number, and formula as world
   data. No `STR/DEX` enum, no HP formula, no `class Fighter` in the framework. This is exactly
   the split the clock (phases are data), weather (the chain is data), and the loot forge
   (tiers/tags are data) already use — and it is the split both **Evennia** (its Traits contrib
   ships only generic value-storage; stats/formulas live in game code) and **CircleMUD** (ability
   effects are rows in `constants.c` tables, not code) independently converged on.

2. **Code resolves, the model narrates.** The golden rule extended to numbers. HP, damage,
   hit/miss, XP, level-ups are computed by deterministic, **seeded, offline-testable** code.
   The LLM never emits a number that changes state — it chooses *intent* through the validated
   action seam (`attack`, `cast`, `flee`) and *narrates* the outcome. This is the loot forge's
   proven shape (code rolls, model authors flavour), and it keeps the whole combat system
   testable with zero network.

The dependency order is forced by the data flow and gives us the build sequence:

| Tier | Systems | Depends on |
|------|---------|-----------|
| **0 — the sheet** | stat vocabulary · resource pools · the sheet · character creation (races/classes/backgrounds as templates) | — |
| **1 — progression & gear** | XP + leveling · **use-based skills** · equipment slots + stat modifiers · the modifier/effect stack | Tier 0 |
| **2 — action & combat** | abilities/spells · the combat resolver · death & stakes | Tier 1 |
| **3 — the world reacts** | mobs · hunger/thirst · real quests/missions | Tier 2 |

Content-rich world-building happens **after** the mechanisms exist, not before.

---

## Tier 0 — the character sheet foundation  *(designed · signed off 2026-07-25)*

Scope: the stat block, the resource pool, the derivation mechanism, the sheet component,
perception, persistence, and the player `score` surface. Character creation (races/classes/
backgrounds) is the immediately-following conversation and completes this tier. Gear/modifiers
are Tier 1; combat/death are Tier 2.

### A. The stat block
A **stat** is a named integer with `min` / `max` / `default`. Loom hardcodes **no** attributes.
The *set* is declared as world data (a `"stats"` block in `world.json` meta), so the world can
use the classic six or its own vocabulary without touching the framework — the same freedom
Evennia's arbitrary-named traits and RPG-in-a-Box's stats-editor give. A **stat block** is a
`{name: value}` map validated against the declared vocabulary and bounds, reusing the hand-rolled
validator discipline the action seam already uses. **Modifiers** (`con_mod = (con-10)//2`, the
D&D 5e form) are themselves declared derivations, exposed by name in the formula namespace.

### B. Derivations — the one load-bearing mechanism  *(sub-decision resolved: expressions)*
Pool maxima, regen, and later every XP and combat number are **derived** from stats + level.
A derivation is a **sandboxed arithmetic expression** string, e.g.

```json
"pools": {
  "hp":   {"max": "10 + hit_die*level + con_mod*level", "regen": "con_mod + 1", "floor": 0},
  "mana": {"max": "wis_mod*level + 4",                  "regen": "wis_mod"}
}
```

evaluated by one small hand-rolled evaluator over stdlib `ast` (no new dependency): parse in
`eval` mode, walk a **whitelist** of node types — `Expression, Constant, Name (load-only),
BinOp, UnaryOp, BoolOp, Compare, IfExp` — plus a whitelisted operator set and a small set of
named safe functions (`min, max, floor, ceil, clamp`). **No `Call` to anything but those,
no `Attribute`, `Subscript`, `Lambda`, comprehension, or assignment.** Names resolve only from
a caller-supplied namespace (the entity's stats + declared modifiers + `level` + `hit_die` + skill
levels); an unknown name raises. Cap the `**` exponent as the residual DoS guard. This is the
`simpleeval` design pattern reduced to arithmetic-only — ~40–60 lines, deterministic, trivially
unit-tested, and a natural sibling of the existing action-validator/worlddraft gate. Trust
level: a spreadsheet cell.

*Refinement from the survey:* a derivation may **also** be a **lookup table** (Diku's `con_app`
threshold→bonus arrays are pure data and dodge float/rounding edge cases; the condition
descriptor in E is itself such a table). Both forms are data; support the expression form first,
add table-lookup as a second derivation kind when a system needs it.

### C. The resource pool
A **pool** = `{key, current, max, regen, floor}` where `max` and `regen` are derivations from B.
HP, mana, stamina, hunger, thirst are the *same* mechanism differing only in data (regen sign;
what depletion means). Modeled as Evennia's **gauge** trait: `max` is read-only (recomputed from
the derivation), `current` is the mutable value, clamped to `[floor, max]`. On hitting `floor`
the pool fires a generic **"pool depleted"** event; what that *means* (death, cannot-cast, a
penalty) is a per-pool **policy tag** consumed by later systems — at Tier 0 we model only the
pool, its regen, and the event. Regen rides the existing heartbeat: a `PoolRegen` loop system
mirroring `WorldClock`/`Director`, opt-in via `engine.attach_regen(...)`, deterministic, off by
default. *(Diku's insight — posture/afflictions supply a regen multiplier — is a Tier 2/3
refinement, since rest/combat states don't exist yet; the `regen` derivation is the seam for it.)*

### D. The sheet — an *optional* component
`Sheet{stats, pools, level, xp, skills}` attaches to a `Character`/`Player` in `loom/world/entity.py`.
**Opt-in:** Odd, Wren, and every purely-narrative NPC keep no sheet and behave exactly as today,
so all 773 existing tests are unregressed. Authored base in `world.json`; live pool values in the
**save overlay** (mutable runtime state, like positions/conditions/quests), maxima recomputed from
derivations on load.

### E. Perception — the golden rule, applied to minds
An LLM mind must **never** see `HP 12/50`. The engine derives a **qualitative condition** from
each pool via a game-declared threshold→string table (Diku's `diag_char_to_char`, computed from
`100*current/max`) — `healthy → grazed → wounded → badly wounded → near death`; `fed → hungry →
starving` — and *that phrase* enters the `Scene`. A wounded NPC can then choose to flee and a
starving one to seek food, reasoning in prose. **Code owns the numbers; the mind reasons over
cues.** Same for a player looking at a mob: a health *descriptor* ("bloodied," "near death"), the
classic MUD convention — never exact HP. Exact numbers appear only on your own sheet.

### F. Player surface
A read-only `score` / `sheet` / `stats` query verb renders the player's own sheet exactly — a
query like `quests`, no seam change.

### G. Persistence & testing
Sheet live-state in the overlay (`SAVE_VERSION` bump + tolerant load — the established pattern).
All deterministic → pure offline tests (bounds, derivation evaluation incl. the evaluator's
reject-list, pool clamp/regen/floor-event, qualitative thresholds, `score` render, save/restore).
No behavioral-harness scenario yet — that arrives when minds *act* on condition (combat/hunger).

### H. Character creation — templates + point-buy  *(designed · signed off 2026-07-25)*
Races, classes, and backgrounds are one architectural thing: **templates** — named data bundles
that contribute to a starting sheet (adjust base stats, grant pools + their derivations, set
starting level, later grant abilities/gear). The framework knows one mechanism: **compose an
ordered template stack into a sheet** (base → race → class → background). "Race"/"class"/
"background" are labels the *game* declares and constrains; the framework is agnostic to how many
and what they're called. This is the **same layered-composition seam** gear and buffs plug into at
Tier 1 (base → +modifiers → derived) — built once, here.

Base stats are set by **point-buy**: the framework ships a point-buy mechanism (a budget + a
per-point cost table, both data). **Decisions (signed off):**
- **Attribute vocabulary = the classic six** — STR / DEX / CON / INT / WIS / CHA, declared in
  `world.json` (the framework stays enum-free; the game declares them with min/max/default). The
  D&D 5e math drops straight in: `mod = (score-10)//2`, `prof = 2 + (level-1)//4`, HP off the
  hit-die + CON.
- **Base-stat generation = point-buy** — but as the *authoring/balancing discipline*, not a
  connect-time player screen. The author builds the single **default player sheet** against the
  budget, and every NPC/mob sheet is balanced against the same budget. The mechanism is ready to
  power a player-facing creation flow when one is added.
- **Player start = one assigned default sheet; build through play** — no character-creation flow
  now. Identity and power emerge from play: the **stat-points system** the user listed is the
  growth engine — you *earn* points by leveling (Tier 1) and spend them via the same point-buy
  mechanism. Player-facing race/class *selection* is deferred with the creation flow; the template
  mechanism itself is still built (mobs/NPCs use it), and a player grows *into* a class rather than
  picking one at spawn.

### Proposed Loom shape
A new opt-in subpackage `loom/rpg/` (pure Python, dependency-light, attached by the game like
clock/weather — **not** a pip extra, since it adds no deps): `expr.py` (the evaluator),
`stats.py` (stat vocabulary + block + point-buy), `pool.py` (the gauge + regen loop system),
`sheet.py` (the component + qualitative perception), `template.py` (template composition),
`skills.py` (use-based skill progression), and later `progression.py`, `combat.py`, `effects.py`. The base engine never attaches it; sheetless
entities are unchanged.

---

## Tier 1 — progression & gear  *(designed · signed off 2026-07-25; a focused prior-art survey precedes the build)*

### Progression — XP, levels, and the stat-points system
`xp` and `level` live on the sheet. Crossing the threshold for the next level — a **data XP
curve**, an expression `xp_to_level(level)` through the Tier 0 evaluator (or a table) — raises
`level`. Because `level` is already a variable in the pool derivations, **HP/mana grow with level
automatically**, no new wiring. The **stat-points system** (signed off: *player-directed*) is the
growth engine: each level grants **build points** (a data `points_per_level`) accumulated on the
sheet; a `train`/`spend` verb raises an attribute, paying from the balance via the **same point-buy
cost table** used at authoring (escalating cost as a stat climbs) — one mechanism spans
creation-budget and play-growth. Raising a stat recomputes effective values + pool maxima.
XP *sources* (kills, quests) arrive at Tier 2/3; Tier 1 builds the XP→level→points machinery.

### Skills — use-based progression  *(signed off: on-use + success bonus; attribute & skill both contribute)*
A **second, parallel growth axis** beside attribute-building (the Elder Scrolls / RuneScape / UO
model — *you get better at what you do*). A **skill** is a data-declared proficiency (unarmed,
blades, archery, **firearms**, a magic school per element, lockpicking, …); the vocabulary is game
data, so guns and spells coexist. Each skill is a per-character `{xp, level}` on the sheet, using the
**same XP-curve evaluator** as character level, and it rises **through use**: every skill-tagged
action awards its skill XP **on use, with a bonus on success**. A skill level enters the **evaluator
namespace** beside stats and `level`, so action formulas read it directly and **both an attribute and
its skill contribute** to effectiveness (`bow to-hit = dex_mod + archery`; `spell power = int_mod +
firemagic`). Distinct from the build-point economy: **build points buy attributes (deliberate);
skills grow by use (organic)** — two complementary tracks.

### Gear & the modifier/effect stack  *(signed off: additive-first)*
Equipment **slots** are data-declared (head, body, main-hand, …). An `Item` becomes equippable via
a `slot` + a list of **modifiers** `{target, op, value}` (`+2 STR`, `+5 max_hp`). Equipping is a
holder/state change on the existing item model — worn, not dropped; unequip returns it to inventory.
The **modifier/effect stack** is the shared computation: effective sheet = base stats → apply all
active modifiers (gear + buffs + debuffs + conditions) → recompute derivations. **Additive
stacking** (bonuses to a target sum); a `type` field is reserved on modifiers for later typed/capped
stacking *without changing the mechanism*. Gear bonuses, ability buffs/debuffs (Tier 2), and hunger
penalties (Tier 3) are **all modifiers** — one stack, one recompute path. Equip/unequip and
buff-expiry trigger recompute; pool `current` clamps to the new `max`. Effects with a
duration/source (buffs/debuffs) are modifiers that expire on the loop — the seam abilities and
hunger reuse.

### Loom shape
`loom/rpg/progression.py` (XP-curve eval, level-up, the points/`train` mechanism),
`loom/rpg/skills.py` (the per-skill XP/level track + the use-based award hook),
`loom/rpg/effects.py` (the modifier stack + effective-value recompute); `Item` gains
`slot` + `modifiers`; `Sheet` gains `xp`/`points`/`skills` + an equipment map; a `train` verb. All
deterministic → offline tests (curve eval, multi-level level-up, point-spend + cost gating, skill-XP
award on use/success + skill-level-into-formula, equip/unequip recompute, additive stacking,
clamp-on-max-change).

---

## Tier 2 — action & combat  *(designed · signed off 2026-07-25; a focused prior-art survey precedes the build)*

All three parts obey *code resolves, model narrates*.

### Abilities & spells
An ability is data: `{name, cost (resource + amount), target, effect, cooldown}`. Invoked through a
new validated seam action (`cast`/`use`); the engine checks the resource, resolves the effect
(damage / heal / apply-a-buff — a Tier 1 modifier with a duration / move), seeds any roll, and the
model narrates. Reuses the Tier 1 effect stack, and each ability is **skill-governed** — it declares
a governing skill, its effect scales with that skill, using it trains that skill, and a skill
threshold can unlock new abilities (so abilities improve by *use*, not build points). Usable in or
out of combat.

### The combat resolver  *(signed off: real-time rounds + two-stage resolution)*
A `Combat` loop system mirroring `Director`/`WorldClock`: an `attack`/`kill` seam action engages a
target and forms an **encounter**; the encounter **ticks each round** (~few seconds) on the
heartbeat — mobs attack of their own accord, players queue commands (flee/cast) resolved next
round. This is the living-world MUD model and fits Loom's loop + autonomous-world ethos. Each
attack is resolved **two-stage**: an attack roll vs the target's defense (AC from DEX + armor),
then, on a hit, a damage roll mitigated by armor → applied to the HP pool → its floor event. All
numbers are **data formulas through the Tier 0 evaluator + seeded dice** (deterministic, offline-
testable). Narration is **deterministic for routine blows** (like the clock's beats) and reserved
for the **LLM only on notable moments** (crit, kill, ability) — bounding narration cost. Fleeing,
targeting, and multi-combatant rooms live here. Mobs (Tier 3) act each round from their existing
LLM mind's intent handed to the resolver.

### Death & stakes  *(signed off: game policy; default respawn + penalty)*
HP hitting its floor fires the pool-depleted event; combat turns that into an outcome through a
**death-policy hook** — the framework fires the outcome, the *game* decides what it means (the
user's call: death is game-specific). The game ships **respawn + penalty** as the default (wake at
a bind point, lose some XP and/or drop gear as a recoverable corpse); incapacitation or permadeath
are a config swap with **no framework change**. A clean second example of the mechanism-in-`loom`/
policy-as-data invariant.

### Loom shape
`loom/rpg/combat.py` (the `Combat` loop system: encounter state, round tick, to-hit + damage,
the death-policy hook), `loom/rpg/abilities.py` (catalogue + `cast` action + effect application);
seam actions `attack`/`kill`, `cast`/`use`, `flee`. Deterministic (seeded RNG) → offline tests
(to-hit thresholds, damage/mitigation, round progression, HP-floor→death, respawn policy, ability
cost/effect/cooldown, flee). **First behavioral-harness scenarios of the arc land here**: a wounded
NPC chooses to flee (reads the qualitative condition cue), a mob presses an attack, an ability used
in context.

---

## Tier 3 — the world reacts  *(designed · signed off 2026-07-25; a focused prior-art survey precedes the build)*

Mostly this tier *extends* systems already built.

### Mobs  *(signed off: hybrid)*
A mob = an entity with a sheet (Tier 0) + combat (Tier 2) + **optionally its existing LLM mind**
(per-mob data) + XP/loot on death. Routine round attacks resolve deterministically; the mind is
consulted only for **decisions** (engage / flee when wounded, reading its Tier 0 condition cue /
switch target / use an ability / parley). Simple mobs (a rat) are cheap pure-stat blocks; notable
mobs (a bandit captain) get a mind and can be talked to or reasoned with — *talk your way out of a
fight* preserved, cost bounded. Death awards XP to the killer and **forges loot through the
existing Phase 4 loot forge** (a kill-reward mirrors the quest-reward). Respawn is a data
**spawner** on the clock (DikuMUD zone-reset); aggression (aggressive / neutral / defensive) is
per-mob data.

### Hunger & thirst  *(signed off: real penalty, non-lethal)*
Pools (Tier 0) with **negative regen** decaying on the clock; the floor event applies a **non-lethal
debuff** (no HP regen, stat penalties) through the Tier 1 modifier stack until fed — it never drives
HP to death (a policy, game-swappable to lethal). `eat`/`drink` seam actions on food items restore
the pool. The qualitative cue (fed → hungry → starving) is already Tier 0, so a starving NPC seeks
food of its own volition.

### Real quests/missions  *(signed off: authored backbone + improvised spice)*
Extend `loom/quest.py` from "reach a place" to a list of **objectives** — kill N of X, collect N of
Y, deliver Y to Z, reach P, talk to W — each tracked by progress; completion is **engine-detected on
seam events** (a kill, a pickup, a give, an arrival), never model-adjudicated (the existing
philosophy). Rewards = the loot forge + XP. Quests come **both** ways: **authored** on NPCs (the
reliable backbone, surfaced through dialogue — the mind role-plays the giving, the quest is authored
so always schema-valid) **and** opt-in **mind-improvised** offers via the validated `offer_quest`
action (the director already proves it works). Multi-objective now; chains/prerequisites later.

### Loom shape
Mobs reuse `Character` + `Sheet` + combat (a `spawner` loop system for respawn); hunger/thirst reuse
the pool + regen mechanism + `eat`/`drink` actions; quests extend `loom/quest.py` (objective types +
progress + seam-event detection hooks). Deterministic → offline tests (spawner reset,
mob-death→XP+forge, objective progress/completion per type, hunger decay→debuff→restore). Behavioral
scenarios: a mind-ful mob parleys/flees; a starving NPC seeks food; an NPC surfaces an authored quest
in dialogue.

---

## Prior art (the survey, 2026-07-25 — Tier 0)

- **DikuMUD / CircleMUD** — the canonical open-source text-RPG. Ability effects are **table
  rows**, not code (`constants.c`: `str_app[]`, `con_app[]` → `{save_bonus, hitp_bonus}` per
  score). Pools (`max_hit/mana/move`) stored explicitly, set at creation/level-up. Regen in
  `limits.c::point_update()` once per tick = `base_rate × posture_multiplier`, `/4` when
  hungry/thirsty. Onlooker health = a threshold→string table (`diag_char_to_char`, 100% "excellent
  condition" … <0% "bleeding awfully"). Hunger/thirst = bounded counters ticking to 0.
  → *We data-drive what Diku hardcoded in C.*
  Sources: CircleMUD `constants.c`; DikuMUD wiki (Game Mechanics).
- **D&D 5e SRD** — the default derivation math the *game data* will likely use: `mod =
  floor((score-10)/2)`; L1 HP `= hit_die_max + con_mod`; per-level `+ avg_die + con_mod`;
  `prof = 2 + floor((level-1)/4)`. Sources: 5thsrd.org (Abilities, Proficiency Bonus).
- **Evennia — TraitHandler** (closest analog). A generic handler with **static** (`base+mod`),
  **counter** (bounded, mutable `current`, optional `rate`), and **gauge** (read-only
  `max=base+mod`, depletes) trait types; time-based regen is a `rate` property of the trait.
  Critically, the contrib ships *only* the mechanism — no STR, no HP formula, no classes; those
  live in game code. → *Validates gauge-for-pools and mechanism-in-core/rules-as-data.*
  Source: Evennia Contrib-Traits.
- **Safe formula evaluation** — consensus: parse to AST, whitelist node types, fixed variable
  namespace, forbid `Call/Attribute/Subscript`, cap `**`, no/seeded RNG (`simpleeval` is the
  reference; VTTs like Foundry/Roll20 use dedicated dice-formula grammars — "parse a tiny DSL,
  don't eval the host language"). Source: `danthedeckie/simpleeval`.
- **Attribute set as data** — Evennia (no enum at all) and RPG-in-a-Box (a stats editor for
  arbitrary designer-declared stats) both treat the attribute vocabulary as config, not code.

---

## Status

- **Signed off (via AskUserQuestion, 2026-07-25):** the two invariants; start bottom-up at
  Tier 0; derivations as sandboxed arithmetic expressions; the classic-six attribute vocabulary;
  point-buy as the authoring/balancing discipline; one assigned default player sheet with
  growth-through-play (the stat-points system) rather than a connect-time creation flow;
  **Tier 1** — player-directed stat growth via `train` (reusing point-buy) and additive-first
  modifier stacking (with a reserved `type` field); **skills** — a parallel *use-based* progression
  axis (skill XP on use + a success bonus; attribute and skill both feed action formulas; abilities
  are skill-governed); **Tier 2** — real-time combat rounds on the
  loop, two-stage to-hit-then-damage resolution, and a death-policy hook (default respawn +
  penalty, game-swappable); **Tier 3** — hybrid mob AI (per-mob optional mind, deterministic
  rounds), non-lethal hunger/thirst penalties, and multi-objective quests offered both authored-
  through-dialogue and mind-improvised.
- **The whole arc (Tiers 0–3) is designed and signed off.** Design phase complete.
- **Next:** *build*, bottom-up from Tier 0. Each tier gets a focused prior-art survey before its
  build (Tier 0's is done, above) and lands behind BOTH gates. Then grow the world with real,
  interesting things to do.
- **Roadmap entry:** `docs/PLAN.md` Phase 9.
