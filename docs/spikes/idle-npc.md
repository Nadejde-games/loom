# Spike — idle-NPC autonomy (Phase 5, slice 4)

*Designed 2026-07-20. The last Phase-5 thread: a character that moves **first**,
unprompted, from its own goal — the NPC-side mirror of the director's lull.*

## The gap

A quiet room already breathes: the world-clock turns it to dusk, the weather
wanders, the director stirs a lull with an ambient beat — and the NPCs **react**
to all of it through the cascade (`_spawn_reaction` → `_react_to_event` →
`NpcMind.react`). Every one of those is still *reactive*: an event happens, a
character answers. What is missing is **initiative** — a character doing or saying
something with *no triggering event*, because it *wants* to. Odd checks his snares;
Wren, restless, walks toward the ridge she means to map. Nobody prompted it.

B9 deferred this to Phase 5 on purpose: the *mechanism* (a cheap NPC-side lull
mirror) was always easy, but its *quality* depended on mind depth. All three
prerequisites have now landed:

- **the act-gate** (B5/B8, `e94a218`) — the local model over-acts; a silence bar is
  required or an idle NPC natters,
- **reflection** (`27fea59`) — so an idle NPC stirs from a *distilled belief/goal*,
  not mechanical recency,
- **durable identity** (`635ce82`) — an idle musing about "Andrei" now means something
  across sessions.

## Prior art

| Source | The idea | What we take | What we leave |
| --- | --- | --- | --- |
| **DikuMUD `mobact.c`** (`mobile_activity`) | A *separate, slower* pulse iterates every mob; each is **dice-gated** per behavior flag (scavenge, wander, aggro, memory); `SENTINEL`/`STAY_ZONE` pin some mobs. | A slower-than-the-turn pulse; per-NPC gating so **most are silent most pulses**; bounded local actions; a per-NPC "stays put" flag. | The dice. Ours is model-judged from goal, not a random roll. |
| **Generative Agents** (Park 2023) | Agents act from a **plan** (day→hour→minute) that reflections feed; they replan on salient perception. | Unprompted action springs from a **goal**, not recency. | The plan *object*. We have none — reflections are `kind="reflection"` memory entries, persona goals are static. We **approximate a plan** by retrieving goal-bearing memory at stir-time. |
| **The Sims** (motive/advertisement) | Idle agents pick the highest-**utility** action for current **needs** from object advertisements. | Idle behavior needs a *drive* signal. | Need meters + utility scoring. Ours is the NPC's goal + salient reflections, judged by the model. |
| **L4D AI Director** | Intensity pacing with enforced **relief**. | Back-off after a stir. | (Already owned — the cascade's cooldown + decaying budget.) |
| **Ultima VII schedules** | Fully **authored** daily routines. | — | Rejected: deterministic, content-heavy, not emergent. (The clock could hook schedules *later* if wanted.) |

## The design

A new **`Idler`** system (`loom/ai/idle.py`) copying the `Reflector` skeleton
exactly — `install(loop)` → `add_system(tick)`; tick gated on `_ticks`/`_running`;
one target per pulse; a guarded background task tracked on `engine._tasks`. It runs
on a **slower pulse than the director** (the mobact lesson).

**Per-room idleness, off the shared chronicle.** Each pulse the Idler reads the
chronicle's most-recent `seq` *per room* (events already carry `location_id`). A
room whose latest `seq` did not advance since last pulse gets its idle counter
incremented; any new event (a player line, an NPC reply, a director beat, a clock
tick) resets it to 0. A room is a stir candidate only once it has been quiet for
`quiet_pulses` **and** has a player present (`world.players_in` — a *per-room*
audience gate, stricter than the director's global one) **and** holds an NPC off
its own cooldown. On first sight a room inits to "not idle" (lazy, like the
reflector's watermark) so attaching the system never stirs a room instantly.

**One stir per pulse**, the most-idle eligible room (deterministic tie-break by id),
one NPC in it — no chorus. `NpcMind.stir(scene, may_wander=…)` recalls the NPC's
goal-bearing memory (persona goals are always in-prompt; reflections surface through
`_recall`), frames the moment as *"nothing has prompted you — act unbidden from who
you are and what you want, or stay silent; silence is the honored default,"* runs a
**single blended compose call** (like `react`, not the two-pass gate — idle pulses
are frequent, so one call), and returns a `Turn` that is usually empty. It is
delivered through **`_deliver_turn`** (chronicles, records memory when engaged) and
then **`_spawn_reaction`** kicks a fresh cascade — so a stir broadcasts and cascades
identically to any other turn.

### Decision 1 — coordination: **peer systems, mutual suppression** *(signed off)*

The director owns *environmental* initiative (a draft, distant sound → NPCs react);
the Idler owns *character* initiative (Odd mutters of the cold → cascade). They stay
distinct in meaning and coordinate in **rate** by reading the *same* room-quiet
signal off the chronicle:

- a **director beat** records into a room → the room's `seq` advances → the Idler's
  idle counter for that room resets → the Idler skips it;
- an **idle stir** records speech into a room → the director reads it as genuine new
  activity (correct) and the Idler resets that room itself → both back off.

So a quiet room gets **at most one silence-break per window**, sometimes the world's,
sometimes a character's. This preserves the settled Phase-3 line — *the director
shapes the world, minds move themselves* — because the Idler is NPC-owned, not the
director reaching into a mind. (Rejected: **B — the director casts an NPC**, which
bends that line and couples character initiative to the director's clock; **C —
Idler primary, director fallback**, which reworks the shipped lull path and hard-orders
the two.) The only uncoordinated edge is a same-tick double-stir of one room (no
shared mutex); harmless — the cascade rails bound it and it reads as a lively moment.

### Decision 2 — idle actions: **also wander** *(signed off)*

An idle NPC may **move to an adjacent room** (mobact's classic wander), grounded in
its goal, using the existing validated `move` action against real exits — so
movement narration (departure + arrival) rides `_deliver_turn` for free. Guarded by
a per-NPC **`wanders` flag** (the `SENTINEL` mirror): authored on the NPC, **opt-in,
default off** — Loom's every-capability-opt-in stance, so the base engine and any
unauthored NPC never empty a room by surprise. Enforced as a **hard rail**: the Idler
strips any `move` from a non-`wanders` NPC's stir turn (never trust the model for a
safety constraint); the prompt tells an anchored NPC it keeps its place as a soft
guide.

In *this* world the content resolves the risk the fork flagged (a quest-giver
walking off, the start room emptying): **Odd the Hermit** — quest-giver, recluse —
anchors his cave (default, no flag); **Wren the Wayfinder** — literally a wanderer —
is authored `wanders: true`. The framework ships the mechanism; the game authors the
policy.

## Slice definition (what gets built)

- `loom/world/entity.py` — `Npc.wanders: bool = False`.
- `loom/content.py` — load `wanders` from authored NPC data.
- `loom/ai/mind.py` — `NpcMind.stir(scene, may_wander) -> Turn` (goal-grounded,
  silence-default, single blended call; records memory only when engaged).
- `loom/ai/idle.py` — the `Idler` orchestrator (per-room idle off the chronicle,
  per-room audience gate, per-NPC cooldown, one stir/pulse, the `wanders` strip rail).
- `loom/engine.py` — `attach_idler(loop, …)`; exports.
- `game/world/world.json` — Wren `wanders: true`.
- `game/main.py` — opt in via `LOOM_IDLE_NPC` (quiet_pulses; 0 = off), `LOOM_IDLE_PERIOD`.
- `tests/test_idle.py` — offline (scripted minds): the skeleton, cadence, per-room
  eligibility (audience/quiet/cooldown), `_running` non-overlap, task tracking,
  delivery through `_deliver_turn` + cascade, silence honored, director-beat
  suppression, the `wanders` strip rail (anchored move dropped; roamer moves).
- `scripts/behavior_probe.py` — an `idle` scenario family: `idle.stirs-from-goal`
  (a goal-relevant line, real model), `idle.stays-silent` (restraint collapse-detector).

## Field tuning (2026-07-21, from live play)

First live session surfaced **zero idle stirs** — diagnosed from the log timestamps, not
guessed. The Idler's quiet clock reset on *every* chronicle event, and with a running clock +
weather beating in the occupied room every ~60-90s the per-room quiet count never reached the
bar (the longest quiet window was 114s against a 240s threshold). Worse, the asymmetry: the
**director's** lull fires on the director's *own* 4-pulse cadence regardless of room activity,
while the **Idler** required the *room* to be dead — so the director always reached its floor
first and the Idler starved.

Two fixes, both preserving the signed-off coordination:
1. **Atmosphere no longer counts as activity.** The quiet clock ignores `kind="ambient"` beats
   (clock/weather/reflection tells; `_ATMOSPHERE` in `loom/ai/idle.py`). A character speaking up
   *as the light fades* is the initiative we want, not noise over a live scene. The director's
   own beat records `kind="action"` — genuine scene activity — so it **still** resets the clock
   and the mutual suppression holds exactly; only pure atmosphere is discounted.
2. **A calmer world** (the play-feel note): clock `factor` 2.0→1.0, weather `period_pulses`
   24→36 / `change_chance` 0.4→0.3 (`world.json`), and the director lull 4→6 pulses
   (`LOOM_DIRECTOR_LULL`, `main.py`) — so ambient turnings come less often and leave room for a
   character to move first.

Net: idle stirs now surface after ~4 pulses of genuine *character*-silence, the director fills a
lull only when no character does, and the world breathes more slowly overall.

## Deferrals (out of this slice)

- **Non-local wandering / pathing toward a goal** (walk *several* rooms to a
  destination). This slice moves at most one adjacent room per stir.
- **A real plan object** (GA hierarchical day-plans). We approximate with retrieval;
  a durable per-NPC plan/agenda is a later depth.
- **Two-pass act-gate on the stir** — a knob if the single-call silence bar proves
  too loose live; deferred until measured.
- **Scheduled routines** (Ultima-VII authored timetables hooked to the clock).

## Appendix — the tension, drawn

```
LULL in a quiet, player-occupied room
  │   (room-quiet read off the shared chronicle by both systems)
  ├── Director  → stages an ENVIRONMENTAL beat  → NPCs REACT (cascade)
  │                 records to chronicle → room no longer quiet → Idler skips
  └── Idler     → a CHARACTER acts from its goal → delivered + cascade
                    records to chronicle → director sees real activity; both back off

  wanders=false (Odd): stir may speak/emote; any move() is STRIPPED (hard rail).
  wanders=true  (Wren): stir may move() to a real exit — departure/arrival via _deliver_turn.
```
