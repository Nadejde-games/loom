# Spike — Loot generation (prior art for the Phase 4 loot forge)

**Date:** 2026-07-19 · **Question:** how should Loom's Phase 4 "loot forge" generate
dynamic, context-aware items — specifically, where is the seam between LLM-authored
*flavour* and code-owned *balance*, and what does "balance" even mean in a text-first
game with no combat system yet? · **Method:** survey of four prior-art families
(ARPG/Diablo-lineage affix systems, Borderlands part-based generation, DikuMUD/roguelike
object templates, and LLM-driven content generation + the PCG-via-LLMs literature),
verified against reference-level sources rather than marketing; then mapped onto Loom's
actual code (`loom/world/entity.py`, `loom/action.py::_spawn_item`,
`loom/world/conditions.py`, `loom/quest.py`). No code written.

---

## Verdict

**Steal the structure, invert the authorship.** Every mature loot system in the survey
separates *authored flavour* from *tuned mechanical data*, and in all of them the item's
**name and flavour are derived from mechanics that a weighted table rolls, gated by a
level scalar** (Path of Exile's ilvl, Diablo's affix-level, Borderlands' parts). Loom
should keep that separation exactly — code rolls the mechanics — but flip the one thing
that is now cheap: instead of a canned affix→name grammar, the **LLM authors the
name/lore/tags conditioned on the code-rolled mechanics and the current world state**, as
a single flat, schema-constrained JSON object. The model never touches a number, a tier,
or a slot. This is not a compromise; it is precisely where LLMs are strong (situated
flavour text) and where they are documented to fail (numeric balance, constraint
satisfaction). Because Loom has no combat system, "balance" today should mean **a
code-owned rarity/tier ordinal + a code-owned tag/theme classification, with zero combat
numbers** — one lightweight scalar that gates which tables are eligible, standing in
exactly where ilvl stands, ready to gate stat tables later without moving the seam.

---

## 1. ARPG / Diablo-lineage affix systems — the name is *derived from* mechanics gated by a scalar

**Path of Exile** is the cleanest reference for the structural lesson. Items have a fixed
**base type** (always legible in the final name) plus rolled **modifiers**, each generated
as either a **prefix** or a **suffix** ("mod generation type"). Affix *counts* are
hard-capped by rarity: a **magic** item may have **at most 1 prefix + 1 suffix**; a
**rare** item **up to 3 prefixes + 3 suffixes** (6 explicit mods)
([PoE Wiki — Modifiers](https://pathofexile.fandom.com/wiki/Modifiers);
[PoE 2 — Item Modifiers Explained](https://mobalytics.gg/poe-2/guides/item-modifiers)).

Which mods can roll is gated by a **level scalar**: each mod has a **mod level / required
item level (ilvl)** — the minimum item/area/monster level for it to appear — so top-tier
mods only roll on high-ilvl items ([Modifiers](https://pathofexile.fandom.com/wiki/Modifiers)).
Eligibility and probability run through a **tag + spawn-weight** system: mods carry tags,
each entity has tags, and a mod's **spawn weight** for a given base is looked up by tag;
**weight zero discards the mod**, otherwise it enters the weighted roll. A **mod group /
family** prevents two mods of the same family from co-rolling on one item
([Modifiers](https://pathofexile.fandom.com/wiki/Modifiers);
[Craft of Exile — Basics](https://www.craftofexile.com/basics)).

Critically, **the displayed name is a function of the mechanics**. A magic item's name is
literally `Prefix + BaseType + Suffix` (e.g. *Humming* `<base>` *of Calm*) — you can always
read the base and the two affixes out of the name. A **rare** item, carrying too many
affixes to spell out, instead gets a **randomly generated two-word name** that is pure
flavour and decoupled from its stats
([PoE 2 — Item rarity and affixes](https://www.sportskeeda.com/mmo/path-exile-2-item-rarity-affixes-poe2)).

**Diablo II** is the same shape at the data layer. Affixes live in `MagicPrefix.txt` /
`MagicSuffix.txt`; each has an **affix level (alvl)** in the "Level" column that gates when
it can appear; each rolled property is 50/50 prefix or suffix, capped at **3 prefixes + 3
suffixes**, **one per family**; the character-level requirement to equip is **¾ of the
highest alvl** on the item. Magic names are readable affix-by-affix; **rare and crafted
names are fully random and hidden** — two rares with identical stats can carry different
names ([diablo2.io — Item Generation terms](https://diablo2.io/forums/a-guide-to-the-basic-terms-of-item-generation-t8350.html);
[PureDiablo — Item Generation](https://www.purediablo.com/diablo-2/item-generation);
[Diablo Wiki — Prefix](https://diablo2.diablowiki.net/Prefix)).

**Diablo III/IV** confirm the rarity ladder and add "smart loot": tiers run **Normal →
Magic → Rare → Legendary/Set → (Ancient → Mythic)**; magic items carry ~1 affix, rares
begin at 3, legendaries 4 (D4) to 6–7 (D3); **Item Power / item level determines the
numeric *range* affixes roll in**. Uniques/legendaries are the exception that proves the
rule — their affix *types* are **fixed/authored**, only the **numeric value varies by
RNG**, and their **names are hand-authored constants**, not derived
([Diablo IV — Items Overview](https://www.purediablo.com/diablo4/Items_Overview);
[D4 rarity levels](https://www.pcgamesn.com/diablo-4/rarity-levels);
[Item Quality — Diablo Wiki](https://diablo-archive.fandom.com/wiki/Item_Quality)).

**Lesson for Loom:** the affix engine is a **weighted table gated by a level scalar**, with
a **family guard** against contradictions, and the visible **name is an output of the roll,
not an input**. When there are too many mechanics to name literally (rares), the game
**switches to free-form flavour** — which is exactly the regime an LLM should own.

## 2. Borderlands — a combinatorial grammar of parts, each carrying both stats *and* name

Borderlands guns are **assembled from parts** — **barrel, grip, body, magazine, sight,
accessory, element, manufacturer** — and each part contributes **both mechanics and
identity simultaneously**. The **barrel** drives damage/accuracy and, **combined with the
body, selects the gun's title**; the **grip encodes the manufacturer**; the **magazine**
governs capacity/reload (smallest mags boost damage); the **accessory** adds
elemental/damage effects and supplies the **prefix**; the visible name is thus assembled
from the same parts that set the stats ([Borderlands Weapons](https://borderlands.fandom.com/wiki/Borderlands_Weapons);
[Borderlands 2 Weapons](https://borderlands.fandom.com/wiki/Borderlands_2_Weapons)).
Gearbox cited **~17,750,000** variations at launch.

The design commentary is the load-bearing insight: the part structure "is reflected
**visibly in the 3d models and invisibly in the properties of the weapon**," so "you can,
if you're familiar with the system, **tell what properties a gun has by looking at it**,"
and the **manufacturer** is called out as the **coherence/lore device** — "a really strong
flavor to the different categories" — with each manufacturer *not* producing certain
weapon types, deliberately breaking symmetry
([procedural-generation.tumblr — Borderlands 2](https://procedural-generation.tumblr.com/post/156045652087/borderlands-2-the-really-interesting-thing-about)).

**Lesson for Loom:** a small vocabulary of **authored parts/tags, each simultaneously
mechanical and flavourful**, plus a **top-level identity (manufacturer/theme)** that keeps
the whole thing coherent, yields enormous variety from few pieces. The theme is what stops
a procedurally-assembled item from reading as random noise.

## 3. MUD / roguelike templates — items are data templates over a fixed mechanical vocabulary

The DikuMUD/CircleMUD **object prototype** is the canonical "item as data row." One object
is a flat record keyed by **vnum** (virtual number, its identity, referenced from zone
files and cloned at runtime): `#vnum`, keyword/**alias list**, short desc, long desc,
action desc, a **type flag** (23 types — WEAPON, ARMOR, LIGHT, SCROLL, CONTAINER,
POTION, KEY…), an **extra/effects bitvector** (item flags — GLOW, MAGIC, NODROP,
ANTI_GOOD…), a **wear bitvector** (slots — WEAR_TAKE, WEAR_BODY, WEAR_WIELD…), **four
`value` fields** whose meaning is keyed by the type flag, then weight/cost/rent, extra
descriptions, and up to **6 Affect fields** ([CircleMUD Builder's Manual — Object Files](https://www.circlemud.org/cdp/building/building-5.html)).

The **Affect field is the template's mechanical payload and is worth copying exactly**:
each is a `(apply-location, modifier)` pair — `A <location> <value>`, e.g. `A 12 50` =
+50 max mana — drawn from **25 enumerable apply locations** (STR, DEX, CON, HIT, MANA, AC,
HITROLL, DAMROLL, saving throws…). So an item's mechanical effect is a **short list of
`(enumerated field, number)` pairs from a fixed authored vocabulary**, instantiated from a
template at load ([Object Files](https://www.circlemud.org/cdp/building/building-5.html);
[DikuMUD Wiki — Objects](https://wiki.dikumud.net/wiki/Manual:Game_Mechanics/Objects) —
search-verified only; host was unreachable for live fetch). LPMud's parallel is
`clone_object(blueprint)`: one authored template, many runtime clones.

Roguelikes (NetHack/DCSS) add the **flavour/mechanics decoupling under uncertainty**: an
item type has a **fixed mechanical identity** but a **randomized appearance** ("swirly
potion", "hexagonal amulet") reshuffled per game, plus **ego/brand** modifiers and
**enchantment (+N)** levels — and **identification** is the process of binding the flavour
label back to the known mechanics. The appearance is disposable flavour; the mechanics are
a fixed vocabulary.

**Lesson for Loom:** items are **data templates over a small, enumerable mechanical
vocabulary**, instantiated at runtime — which is already precisely what `World.spawn_item`
does (`loom/world/world.py`). The `(location, modifier)` apply-pair is the exact shape a
future Loom stat system should adopt: **code-owned, enumerable, never a free string.**

## 4. LLM-driven content generation — strong at flavour, documented-weak at numbers

The academic picture is consistent. The recent PCG survey notes LLMs "are **mostly used to
create game narratives**" and are the natural fit for "stories, quests, and character
dialogue," while flagging **loss of control, hallucination, and consistency** as the open
problems — and that **hybrid methods** (LLM + RL/search, LLM proposes, algorithm
validates) are emerging precisely to regain control
([*PCG in Games: A Survey with Insights on Emerging LLM Integration*, arXiv:2410.15644](https://arxiv.org/abs/2410.15644)).

The **numeric/constraint weakness is measured, not folkloric.** On constraint-satisfaction
problems, "**constraint satisfaction, not objective optimization, is the primary
bottleneck**": the best model reaches only ~**65% feasibility**, and **no model exceeds
30.5%** on joint feasibility-and-optimality; models "**systematically underestimate**"
numeric quantities and misparse how numeric constraints work
([*A Reality Check of LLMs as Formalizers on Constraint Satisfaction Problems*, arXiv:2505.13252](https://arxiv.org/pdf/2505.13252);
*ConstraintBench*, arXiv:2602.22465). This is the empirical basis for **never letting the
model author a tier, a stat, or a balance number.**

The counter-tool is **grammar-constrained / structured decoding**: JSON-Schema/GBNF
grammars compiled to a finite-state machine that **constrains the token set at every step,
guaranteeing schema-valid output and eliminating the validate-retry loop** — shipped in
vLLM/SGLang via **XGrammar/Outlines**, and in llama.cpp via **GBNF** (enum values
supported) ([llama.cpp — Grammar and Structured Output](https://deepwiki.com/ggml-org/llama.cpp/8.1-grammar-and-structured-output);
[*JSONSchemaBench*, arXiv:2501.10868](https://arxiv.org/pdf/2501.10868)). This is Loom's
existing golden rule; the loot forge is a direct application. One caveat the literature
flags — a **"constraint tax":** heavier/more-branching schemas can degrade generation
quality — which argues for keeping the loot schema **flat and minimal** (arXiv:2606.25605).

Shipped/prototype prior art shows the split working in practice: **AI Roguelite** advertises
that "every location, NPC, enemy, item, crafting recipe" is AI-determined **but wraps them
in real code mechanics** — "inventory, skill checks, and combat"
([AI Roguelite — Steam](https://store.steampowered.com/app/1889620/AI_Roguelite/));
**RogueLLM** generates items/enemies from an LLM against **JSON config files that hold the
mechanics** ([dpasca/roguellm](https://github.com/dpasca/roguellm)); **Tile-Crawler** runs
the LLM as a dungeon master over a coded state model
([Tile-Crawler](https://www.zingnex.cn/en/forum/thread/tile-crawler)). The instructive
anti-pattern is **AI Dungeon**: because nothing is code-grounded, "items" are just words the
narrator may forget next paragraph — the exact drift Loom's schema-validated action seam
exists to prevent.

**Lesson for Loom:** put the LLM only on the flavour side of the seam, behind constrained
decoding, with a **flat schema**; keep every number and category in code.

## 5. The cross-cutting principle — data-driven design / content-code separation, gated by a scalar

Every system above is an instance of one pattern: **data-driven design** — game behaviour,
properties and balance live in **external data (tables/JSON/CSV)**, interpreted by generic
code, so **designers tune balance without touching the codebase** and iterate fast
([Data-Driven Design for Game Development](https://peerdh.com/blogs/programming-insights/using-data-driven-design-for-game-development);
[Cornell CS3152 — Data-Driven Design, Lecture 14](https://www.cs.cornell.edu/courses/cs3152/2014sp/lectures/14-DataDriven.pdf)).
The loot-specific refinement across PoE, Diablo, Borderlands and Diku is: **the mechanical
data lives in weighted tables gated by a single difficulty/level scalar (ilvl / alvl / item
power / area level), and the visible name/flavour is derived downstream from what those
tables rolled.** Loom's twist: replace the derivation step (affix→name grammar) with an
LLM, keeping the tables-gated-by-a-scalar core untouched.

---

## Comparison

| Axis | PoE / Diablo (ARPG) | Borderlands (parts) | DikuMUD / roguelike | LLM-content (AI Roguelite, etc.) | **Loom loot forge (proposed)** |
|---|---|---|---|---|---|
| **What's authored (by hand/tables)** | Affix pool, spawn weights, mod groups, base types | Parts (barrel/grip/…), manufacturer identity | Object prototypes: type, flags, slots, apply-pairs | JSON config / code mechanics | Tier ordinal, tag/theme tables, item-type enum, (future) apply-pairs |
| **What's generated at runtime** | Which affixes roll + values | Which parts combine | Which template is cloned | Names + descriptions (LLM) | **Names + lore + tags (LLM)** |
| **Balance gate (the scalar)** | ilvl / alvl / Item Power | (level-scaled part pools) | mob/zone level picks vnum | code-side rules | **`tier` ordinal — the ilvl analog** |
| **How the name is derived** | magic = `Prefix Base Suffix`; rare = random 2-word | title from body+barrel; prefix from accessory | short desc authored per prototype | LLM free text | **LLM authors, *conditioned on* the code-rolled brief + world context** |
| **Coherence device** | base type + mod families | manufacturer | item type | (weak / none) | **`theme` tag (manufacturer analog) shared by name, lore, tags** |
| **Numbers authored by the generator?** | never (tables) | never (parts) | never (template) | should be no; sometimes drifts | **never — code owns every number/category** |
| **Fit to a text-first LLM engine** | tables port cleanly; name-grammar is the part to replace | theme-as-coherence ports well | template-instantiation *is already `spawn_item`* | validates the split; shows the anti-pattern | — |

---

## Recommendation for Loom

**The flavour/balance split — who authors what, and why.**
The model authors, as one flat schema-constrained JSON object, exactly four **flavour**
fields:
- `name` (string) — the item's name;
- `description` (string) — its lore/appearance;
- `aliases` (bounded array of short strings) — extra nouns for name-resolution, feeding the
  existing `Item.aliases` used by `loom/naming.py`;
- optionally `theme` (a single **enum echo**, not free text) — the model confirming which
  authored theme it wrote to, so code can assert agreement.

Code owns everything mechanical: **`tier`** (the rarity/power ordinal), **`type`/`slot`**
(enum), the **theme + tag set** (rolled from tables), **level gating**, and any future
**stats** as `(field, number)` apply-pairs. The justification is doubly grounded: prior art
**universally derives name from mechanics via tables gated by a scalar** (§1–§3, §5), and
the LLM literature shows the model is **strong exactly on the flavour half and measurably
unreliable on the numeric/constraint half** (§4, arXiv:2505.13252). Loom keeps the industry
separation and simply moves the LLM into the one slot where it beats a canned grammar —
writing a *situated* name/lore instead of "Prefix Base Suffix."

**What "balance" means with no combat system — take (b) plus the minimal piece of (a).**
Do **not** invent combat stats to have something to balance. Adopt a **code-owned
rarity/tier + tag classification with no combat numbers**, but **do introduce one
lightweight scalar now**: a `tier` ordinal (recommend a 3–5 value enum, e.g.
`common | uncommon | rare | fabled`, backed by an int). This scalar is Loom's **ilvl/alvl
analog** and its only job today is to **gate which tag/theme tables are eligible and how
lavish the flavour prompt may be** — richer name, longer lore, rarer tags at higher tier.
It carries **zero combat math**. The payoff: when a stat system eventually lands, the *same*
scalar gates stat-range tables (PoE's Item-Power role) with no change to the seam. This is a
clear position: **(b) + exactly one ordinal scalar, no stats.**

**How it rides the existing architecture.**
Extend the seam that already exists and already defers this: `loom/action.py::_spawn_item`
explicitly notes *"Authoring balanced, lore-rich loot is Phase 4."* Add a **`forge_item`**
action alongside it, in two stages:
1. **The forge (code).** Given context, roll a **brief**: `{tier, type, theme, tags[]}` —
   `tier` first, then draw `tags` from tables **gated by `tier` and filtered by the
   context's own tags** (location/region conditions, active-quest tags), with a
   **family/group guard** so tags don't contradict (PoE mod-groups; §1). This is PoE's
   *tag + spawn-weight + ilvl* pipeline with **damage numbers removed**.
2. **The flavour (LLM).** One **flat** grammar-constrained JSON object (`name`,
   `description`, `aliases`, optional `theme` enum) — **never a `oneOf`** over
   weapon/armour/etc. branches. Weak models collapse a `oneOf` to its simplest branch; any
   categorical choice the model is allowed must be a **single enum field on the flat
   object**, and here even that is code-decided — the model only *echoes* `theme`. The brief
   is injected into the prompt as constraints the prose must honour; the model cannot alter
   `tier`/`type`/`tags`.
3. **Assembly (code).** Validate the flat JSON (non-empty `name`/`description`; `aliases`
   bounded and within vocabulary; `theme` matches the brief), then call the existing
   `World.spawn_item`, **adding the code-owned `tier`/`type`/`tags` as new fields on `Item`**
   (`loom/world/entity.py`). The affix/tag-table-gated-by-a-scalar pattern maps cleanly:
   `tier` = the scalar, the weighted tag tables = the mod pool, the family guard =
   mod-groups, the flat JSON = the DikuMUD "one item, one record" template — but with
   model-authored strings replacing authored strings.

**Triggering & context — where the situation comes from.**
The context sources already exist in the engine; the forge only has to read them:
- **Player history & quests** → `loom/quest.py` (`QuestBook`, per-player goals and log)
  supplies quest theme/tags and a natural firing moment; the chronicle/memory supplies a
  digest of the player's recent deeds.
- **Location** → the room and its region.
- **Current world state** → `loom/world/conditions.py` — the conditions registry folds
  **weather** (tag `weather`) and **time-of-day** (tag `time`, from `loom/clock.py`) plus
  region conditions into every place; these become the tags that filter the mechanical roll
  *and* the one-line situational blurb in the flavour prompt.
- **Firing paths** (director already has the reach — it runs on a slow cadence with
  `spawn_item`/`offer_quest`): (a) **quest reward** on completion, themed by the quest's
  tags; (b) a **director beat** during a lull/foreshadow, dropping an item that matches the
  current weather/time/place; (c) a **discovery** seeded in a location. Code gathers context
  → rolls the brief (tier + tags gated by that context) → the model authors a name/lore that
  *references the situation* (e.g. a rain-slick, storm-tagged trinket forged as a quest
  reward in a flooded quarter), while code guarantees mechanical fitness.

**A minimal first slice (prove the seam with the lightest thing).**
Ship one `forge_item` firing from **a single path — quest completion** (because `QuestBook`
already gives per-player context and a clean trigger):
1. code rolls `tier` from a **3-value enum** and draws **1–2 tags** from a tiny table gated
   by the **current location's conditions** (weather/time);
2. the model returns a **flat `{name, description, aliases}`** constrained JSON conditioned
   on `{tier, tags, one-line context}`;
3. code validates and calls the existing `world.spawn_item`, attaching `tier` + `tags` as
   code-owned `Item` fields.
That end-to-end path exercises the whole seam (context → brief → constrained flavour →
validated assembly → a real perceivable item through the existing `look`/`take`/`Scene`
paths) with the smallest possible surface.

**Defer:** combat stats and `(field, number)` apply-pairs (no combat system to balance
against); the full weighted mod-pool with deep tiers and tuned spawn weights; rarity-scaled
numeric ranges; unique/set fixed items; identification; and a wide item-type/slot
vocabulary. All of these grow the **tables** later **without moving the seam**.

---

## What to steal / what to skip

**Steal**
- **The level-scalar-gated weighted table with a family guard** (PoE/Diablo, §1) — becomes
  Loom's `tier`-gated tag tables + contradiction guard, minus damage numbers.
- **"Name derived from mechanics" — but inverted** (§1–§2): keep the direction (mechanics
  first, name second) and let the **LLM** perform the derivation with world context, where
  PoE used a grammar and Borderlands used parts.
- **Manufacturer-as-coherence-device** (Borderlands, §2) → a single **`theme` tag** that
  keeps name, lore, and tags internally consistent and non-random.
- **Item-as-data-template over a fixed enumerable mechanical vocabulary** (DikuMUD, §3) —
  already how `World.spawn_item` works; adopt the **`(location, modifier)` apply-pair** shape
  verbatim as the future stat vocabulary (code-owned, enumerable).
- **Flat schema + enums under grammar-constrained decoding** (§4) — guaranteed-valid, no
  retry loop, keep it minimal to dodge the "constraint tax."

**Skip (for now)**
- **Combat/damage numbers** — do not invent stats merely to have balance; the scalar + tags
  are enough until combat exists.
- **The LLM authoring any number, tier, or slot** — the measured failure mode (§4,
  arXiv:2505.13252); the model authors flavour only.
- **`oneOf` / polymorphic item schemas** — weak models collapse them to the simplest branch;
  use one flat object with enum fields.
- **Full PoE mod-tier depth, spawn-weight tuning, identification, unique/set fixed items** —
  premature; grow the tables later.
- **AI-Dungeon-style ungrounded generation** — an "item" with no code-side mechanical
  identity is exactly what Loom's golden rule forbids.

---

## Appendix — sources & verification status

| Claim area | Source | Status |
|---|---|---|
| PoE affix counts (magic 1p+1s, rare 3p+3s), mod-level/ilvl gating, spawn-weight tags, mod groups, `Prefix Base Suffix` naming | [PoE Wiki — Modifiers](https://pathofexile.fandom.com/wiki/Modifiers), [Mobalytics PoE2](https://mobalytics.gg/poe-2/guides/item-modifiers), [Sportskeeda PoE2 rarity](https://www.sportskeeda.com/mmo/path-exile-2-item-rarity-affixes-poe2), [Craft of Exile](https://www.craftofexile.com/basics) | Verified (Fandom mirror + guides; **poewiki.net blocked by bot-wall**, not fetched live) |
| Diablo II alvl gating, 3+3 cap, one-per-family, ¾-alvl clvl req, random rare names | [diablo2.io](https://diablo2.io/forums/a-guide-to-the-basic-terms-of-item-generation-t8350.html), [PureDiablo Item Generation](https://www.purediablo.com/diablo-2/item-generation), [Diablo Wiki Prefix](https://diablo2.diablowiki.net/Prefix) | Verified |
| Diablo III/IV rarity ladder, affix counts, Item Power ranges, fixed-affix uniques | [PureDiablo D4 Items](https://www.purediablo.com/diablo4/Items_Overview), [PCGamesN D4 rarity](https://www.pcgamesn.com/diablo-4/rarity-levels), [Diablo Wiki Item Quality](https://diablo-archive.fandom.com/wiki/Item_Quality) | Verified |
| Borderlands parts→stats+name, title from body+barrel, manufacturer coherence, 17.75M variations | [Borderlands Weapons](https://borderlands.fandom.com/wiki/Borderlands_Weapons), [BL2 Weapons](https://borderlands.fandom.com/wiki/Borderlands_2_Weapons), [procedural-generation tumblr](https://procedural-generation.tumblr.com/post/156045652087/borderlands-2-the-really-interesting-thing-about) | Verified |
| DikuMUD/CircleMUD object prototype: vnum, type/flags/wear, value0-3, 6 affect fields, 25 apply-locations | [CircleMUD Builder's Manual — Object Files](https://www.circlemud.org/cdp/building/building-5.html) | Verified (primary manual); [DikuMUD Wiki](https://wiki.dikumud.net/wiki/Manual:Game_Mechanics/Objects) **search-snippet only — host unreachable** |
| NetHack/DCSS random appearances, ego/brand, enchantment, identification | canonical roguelike knowledge | **Not fetched live this spike** — well-established; flag if precise numbers ever needed |
| LLM strong-at-narrative / weak-at-numeric+constraint; hybrid methods | [PCG-via-LLM survey, arXiv:2410.15644](https://arxiv.org/abs/2410.15644); [Reality Check on CSPs, arXiv:2505.13252](https://arxiv.org/pdf/2505.13252); ConstraintBench arXiv:2602.22465 | Survey + Reality Check verified via fetch/search; **ConstraintBench & "constraint tax" (arXiv:2606.25605) from search snippets only** |
| Grammar-constrained decoding guarantees valid output (GBNF/Outlines/XGrammar, JSON-Schema→FSM) | [llama.cpp grammar](https://deepwiki.com/ggml-org/llama.cpp/8.1-grammar-and-structured-output), [JSONSchemaBench, arXiv:2501.10868](https://arxiv.org/pdf/2501.10868) | Verified |
| Existing LLM-item games keep mechanics in code | [AI Roguelite](https://store.steampowered.com/app/1889620/AI_Roguelite/), [RogueLLM](https://github.com/dpasca/roguellm), [Tile-Crawler](https://www.zingnex.cn/en/forum/thread/tile-crawler) | Verified (product/repo pages) |
| Data-driven design / content-code separation gated by a scalar | [peerdh — Data-Driven Design](https://peerdh.com/blogs/programming-insights/using-data-driven-design-for-game-development), [Cornell CS3152 Lecture 14](https://www.cs.cornell.edu/courses/cs3152/2014sp/lectures/14-DataDriven.pdf) | Verified |
| Loom seam facts (`_spawn_item` defers "balanced, lore-rich loot"; `Item` = name/description/holder/aliases/portable; conditions fold weather+time per place; `QuestBook` per-player) | `loom/action.py`, `loom/world/entity.py`, `loom/world/world.py`, `loom/world/conditions.py`, `loom/quest.py` | Verified against working tree |
