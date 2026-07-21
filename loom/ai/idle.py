"""Idle-NPC autonomy — a character that moves *first* (Phase 5, slice 4).

The quiet room already breathes: the clock turns it to dusk, the weather wanders,
the director stirs a lull — and the NPCs *react* to all of it through the cascade.
Every one of those is reactive. This is the missing half: **initiative** — a
character doing or saying something with no triggering event, because its own goal
moves it to (``docs/spikes/idle-npc.md``). Odd mutters of the coming cold; Wren,
restless, walks toward the ridge she means to map. Nobody prompted it.

``Idler`` is the NPC-side mirror of the director's lull, built on the same seam as
the ``Reflector``: a slow, opt-in tick system that, on a pulse, picks one idle NPC
in a quiet, player-occupied room and lets it ``stir`` — a single goal-grounded call
that most often chooses silence. The stir is delivered through the engine's
``_deliver_turn`` and then cascades like any other turn, so nothing downstream can
tell an unbidden beat from a reply.

**Coordination with the director (signed off — peer systems, mutual suppression):**
both read "room quiet" off the *same* chronicle. A director beat records ``kind=
"action"`` into a room, advancing its sequence, which resets that room's idle clock so
the Idler skips it; an idle stir records speech, which the director reads as genuine
activity — so a quiet room gets at most one silence-break per window, sometimes the
world's, sometimes a character's. The two stay distinct in meaning (the director shapes
the world; minds move themselves) and coordinate only in rate.

**Atmosphere does not count as activity.** The quiet clock ignores ``kind="ambient"``
beats (clock/weather/reflection tells) — see ``_ATMOSPHERE``. Otherwise a world with a
running clock and weather beats every ~60-90s and no room ever stays quiet long enough
to stir; and a character speaking up *as* the light fades is the initiative we want, not
noise over a live scene. Real scene activity and the director's beat still suppress.
"""
from __future__ import annotations
import asyncio

from .. import log
from ..world import Npc
from .mind import Turn

# The built-in movement action's name — the one deed an anchored NPC must never take
# on a stir. Enforced as a hard rail below (a move from a non-``wanders`` NPC is
# stripped), never trusted to the prompt.
_MOVE = "move"

# Chronicle events of this kind are pure *atmosphere* — the clock turning to dusk, the
# weather shifting, a reflection's "gaze turns inward" tell. They do NOT count as scene
# activity for the idle-quiet clock: a character musing *as the light fades* is exactly
# the unbidden initiative we want, not something to talk over. Everything else
# (speech/move/action/event, and crucially the director's own beat, which records
# ``kind="action"``) still resets the clock, so a live scene and the director's lull both
# suppress a stir. Without this, a world with a running clock + weather beats every ~60-90s
# and the per-room quiet clock never reaches the bar — the Idler starves.
_ATMOSPHERE = "ambient"


class Idler:
    """Hangs idle-NPC stirs off the game loop on a slow, opt-in, per-room cadence.

    Every ``period_ticks`` ticks it considers one stir: a room is eligible once it has
    been quiet for ``quiet_pulses`` pulses (no new chronicle event), has a player
    present to witness (a per-room audience gate, stricter than the director's global
    one), and holds an NPC off its own ``cooldown_pulses`` rest. The most-idle eligible
    room's lowest-id available NPC stirs — one per pulse, on a background task,
    non-overlapping (``_running``), exactly like the ``Reflector``. Off until a game
    attaches it; the base engine has no idle initiative.

    Room-quiet is measured off the shared chronicle (per-room max sequence), so the
    director's beats and the Idler's stirs suppress each other for free.
    """

    def __init__(self, engine, *, period_ticks: int = 12,
                 quiet_pulses: int = 4, cooldown_pulses: int = 3) -> None:
        self.engine = engine
        self.period_ticks = max(1, period_ticks)
        # How many quiet pulses a room must hold before a character breaks the
        # silence — the liveliness floor (≫ the per-NPC cooldown). Kept comparable
        # to the director's lull so the two are genuine peers, neither always first.
        self.quiet_pulses = max(1, quiet_pulses)
        # A character's rest after it stirs (or is probed and stays silent), so one
        # NPC does not monopolise the room and a silent one is not re-probed every
        # pulse — the mobact "did its turn" back-off.
        self.cooldown_pulses = max(1, cooldown_pulses)
        # location_id -> last observed max chronicle seq for that room.
        self._room_seq: dict[str, int] = {}
        # location_id -> consecutive quiet pulses.
        self._room_idle: dict[str, int] = {}
        # npc id -> remaining cooldown pulses.
        self._cooldown: dict[str, int] = {}
        self._ticks = 0
        self._running = False

    def install(self, loop) -> None:
        """Register this idler as a system on the game loop."""
        loop.add_system(self.tick)

    # ---- room bookkeeping ----
    def _npcs_in(self, location_id: str) -> list:
        """The minded NPCs in a room, lowest id first (deterministic pick order)."""
        return sorted(
            (e for e in self.engine.world.occupants(location_id)
             if isinstance(e, Npc) and e.id in self.engine.minds),
            key=lambda e: e.id)

    def _candidate_rooms(self) -> list:
        """Rooms with a player to witness *and* a minded NPC to stir — the only
        places an idle beat is both wanted and possible. Perceivable-only, like the
        clock/weather/reflection tells: no stir into a place no player can see."""
        world = self.engine.world
        return [loc.id for loc in world.locations.values()
                if world.players_in(loc.id) and self._npcs_in(loc.id)]

    def _latest_seq_by_room(self) -> dict:
        """The most recent *substantive* chronicle sequence id per room, from the
        rolling window. Chronological order means last-seen wins, so this is each
        room's max seq — but pure atmosphere (``_ATMOSPHERE``: clock/weather/reflection
        tells) is skipped, so the weather turning does not reset a room's quiet clock
        (a character may stir as the light fades). Genuine scene activity and the
        director's beat still count (see ``_ATMOSPHERE``)."""
        latest: dict[str, int] = {}
        for e in self.engine.chronicle.recent():
            if e.location_id and e.kind != _ATMOSPHERE:
                latest[e.location_id] = e.seq
        return latest

    def _observe(self) -> None:
        """Advance each candidate room's idle clock. A room whose chronicle sequence
        grew since last pulse (a player line, an NPC reply, a director beat, a clock
        tick) resets to 0; an unchanged one ages by one. First sight inits to *now,
        not idle* (lazy, like the reflector watermark) so attaching never stirs a
        room instantly. Rooms that are no longer candidates (emptied of players or
        NPCs) are forgotten, so a stale idle count does not fire the moment someone
        walks back in."""
        latest = self._latest_seq_by_room()
        active = set()
        for loc in self._candidate_rooms():
            active.add(loc)
            cur = latest.get(loc, 0)
            if loc not in self._room_seq:
                self._room_seq[loc] = cur
                self._room_idle[loc] = 0
            elif cur > self._room_seq[loc]:
                self._room_seq[loc] = cur
                self._room_idle[loc] = 0
            else:
                self._room_idle[loc] = self._room_idle.get(loc, 0) + 1
        for loc in list(self._room_seq):
            if loc not in active:
                self._room_seq.pop(loc, None)
                self._room_idle.pop(loc, None)

    def _pick(self):
        """The stir for this pulse: the most-idle eligible room's lowest-id NPC that
        is off cooldown, or ``None``. Returns ``(npc, mind, location_id)``. One per
        pulse — no chorus. Ties break by room id for reproducibility."""
        candidates = []
        for loc in self._candidate_rooms():
            if self._room_idle.get(loc, 0) < self.quiet_pulses:
                continue
            free = [e for e in self._npcs_in(loc)
                    if self._cooldown.get(e.id, 0) <= 0]
            if free:
                candidates.append((self._room_idle[loc], loc, free[0]))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-c[0], c[1]))   # most idle, then room id
        _idle, loc, npc = candidates[0]
        return npc, self.engine.minds[npc.id], loc

    async def tick(self, dt: float) -> None:
        """The loop callback. Counts ticks; on the period, ages the idle clocks and
        cooldowns and, if a room has been quiet long enough, stirs one NPC on a
        background task. Cheap: no model call unless a room crossed the quiet bar,
        and only one stir runs at a time."""
        self._ticks += 1
        if self._ticks < self.period_ticks or self._running:
            return
        self._ticks = 0
        for nid in list(self._cooldown):        # every NPC's rest ages one pulse
            self._cooldown[nid] -= 1
            if self._cooldown[nid] <= 0:
                del self._cooldown[nid]
        self._observe()
        target = self._pick()
        if target is None:
            return
        npc, mind, loc = target
        # A probe is not yet a beat — most stay silent — so this is debug-only; the
        # landed stir announces itself as an event below (and through _deliver_turn),
        # keeping the console to what actually happened.
        log.debug(f"idle probe: {npc.name} in {loc} "
                  f"(quiet {self._room_idle.get(loc, 0)} pulses)")
        # Spend the NPC's chance now, synchronously — engaged or silent, it rests
        # before it is probed again — and reserve the single in-flight slot.
        self._cooldown[npc.id] = self.cooldown_pulses
        self._running = True
        task = asyncio.create_task(self._guarded_stir(npc, mind, loc))
        # Track on the engine so shutdown can await/cancel in-flight work, mirroring
        # the reflector and director; harmless if there is no such set.
        tasks = getattr(self.engine, "_tasks", None)
        if tasks is not None:
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    def _strip_moves(self, turn: Turn) -> Turn:
        """Drop any ``move`` from a stir — the hard rail for an anchored NPC (one
        without ``wanders``). Never trust the prompt for a safety constraint: even
        if the model proposes leaving, an anchored character stays. A stir left with
        only a stripped move becomes silent, honoured as such."""
        kept = [a for a in turn.actions if a.name != _MOVE]
        if len(kept) == len(turn.actions):
            return turn
        return Turn(speech=turn.speech, actions=kept)

    async def _guarded_stir(self, npc, mind, location_id: str) -> None:
        try:
            may_wander = bool(getattr(npc, "wanders", False))
            scene = self.engine._scene_for(npc, location_id)
            turn = await mind.stir(scene, may_wander=may_wander)
            if not may_wander:
                turn = self._strip_moves(turn)
            if turn.is_silent:
                log.debug(f"{npc.name} stays still — nothing to do")
                return
            log.event(f"{npc.name} acts unbidden (idle stir)")
            observed = await self.engine._deliver_turn(location_id, npc, mind, turn)
            # An unbidden beat is itself an event the room may answer — a fresh
            # cascade, exactly as a reply or a world change spawns one. No-op unless
            # the game enabled autonomous reactions.
            self.engine._spawn_reaction(location_id, npc.id, observed)
        except Exception as exc:     # a bad stir must never break the loop
            log.event(f"idle stir failed for {npc.id}: {exc!r}")
        finally:
            self._running = False
