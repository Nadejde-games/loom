"""The default game engine: binds sessions to the world and to NPC minds, and
provides a small, extensible command set. Nothing here is specific to any
particular world — a game may subclass this or register extra commands.
"""
from __future__ import annotations
import asyncio
import random
from dataclasses import dataclass, field
from string import Formatter
from .session import Session
from .world import World, Player, Npc
from .action import ActionRegistry, ActionContext, ActionIntent, default_registry
from .salience import SalienceGate, SalienceContext, default_gate, is_addressed
from .naming import resolve, Resolved, Ambiguous
from .chronicle import Chronicle
from .clock import WorldClock, Phase
from .weather import WeatherSystem, Weather
from .quest import COMPLETE
from .compose import compose_beat
from .style import Style, span, styled, join_styled
from .ai import NpcMind, LLMProvider, ProviderError, Scene
from .ai import intent
from .ai.director import DirectorMind, Director
from .ai import loot as loot_ai
from . import command
from . import loot
from . import persistence
from .loot import LootTables, LootForge, flavour_schema

# Actions the default game offers the *player* but not its NPCs — the NPCs in
# this world don't scavenge the floor. Purely a content choice (not a seam rule):
# a game can widen any NPC's catalogue by giving its NpcMind a different offered
# set. Keeps the NPC action catalogue (and the behavioral harness) unchanged as
# player-only verbs are added to the shared registry.
PLAYER_ONLY_ACTIONS = ("take_item", "drop_item")

# Actions reserved for the game-master director — the unseen hand that shapes the
# scene. Offered only to the DirectorMind (never to NPCs or players), the same
# per-mind subset lever PLAYER_ONLY_ACTIONS uses. Excluded from the NPC catalogue
# below so a character can never stage an ambient beat or change the weather.
# stage_event narrates a one-off beat; set_condition/clear_condition raise and
# lift a *standing* condition (a storm, nightfall) that persists in perception;
# spawn_item brings a real object into a room; offer_quest sets a gentle,
# per-player goal (tracked in world.quests, completed by the arrival hook below).
# nudge_npc — writing a goal/memory into a character — is deliberately NOT here:
# our director shapes the world, not minds (see the Phase 3 design decision); it
# stays a framework-optional action, unregistered and so offered to no one.
DIRECTOR_ONLY_ACTIONS = ("stage_event", "set_condition", "clear_condition",
                         "spawn_item", "offer_quest")

# Autonomous-reaction rails (BACKLOG B9). NPCs react to what happens around them of
# their own volition, and to each other — a cascade. The limiter is engine-enforced
# *appropriateness* (each NPC's own high-bar judgement in NpcMind.react), NOT a hard
# depth cap; these are only the cheap safety rails that keep a genuine runaway from
# spending forever. REACT_BUDGET is the shared, decaying reaction budget per
# originating event (each reaction spends one; when it hits zero the cascade winds
# down). REACT_COOLDOWN is how many hops an NPC that just reacted sits out — long
# enough to stop machine-gun ping-pong, short enough that a real back-and-forth
# still happens. REACT_FUSE is the hard circuit-breaker: a ceiling on total
# reactions per originating event that should never fire in normal play.
REACT_BUDGET = 5
REACT_COOLDOWN = 2
REACT_FUSE = 16


@dataclass
class _Cascade:
    """The shared, mutable state threaded through one originating event's cascade
    of reactions. One per originating event; passed down the recursion so budget,
    cooldowns, and the fuse are shared across the whole tree of reactions."""
    budget: int                                     # reactions remaining (decaying)
    fuse: int                                       # hard ceiling on total reactions
    spent: int = 0                                  # reactions delivered so far
    cooldown: dict = field(default_factory=dict)    # npc id -> hops still to sit out


class Engine:
    def __init__(self, world: World, provider: LLMProvider, start_location: str,
                 registry: ActionRegistry | None = None,
                 gate: SalienceGate | None = None,
                 intent_fallback: bool = True,
                 autonomous_reactions: bool = False,
                 npc_act_gate: bool = False,
                 embedder=None, memory_store=None):
        self.world = world
        self.provider = provider
        self.start_location = start_location
        # The embedding provider (an ``EmbeddingProvider``, or None), shared by every
        # mind's memory stream so retrieval ranks by relevance as well as
        # recency/importance (Phase 5 memory depth). None = recency+importance only —
        # every offline test and the base engine run this way, unchanged.
        self.embedder = embedder
        # The SQLite backing for memory (a ``MemoryStore``, or None; slice 1b), shared
        # by every mind keyed by its agent id. With it, a memory is an incremental
        # INSERT and survives a restart in the DB rather than the JSON overlay; without
        # it, streams are plain in-memory lists persisted through the overlay as before.
        self.memory_store = memory_store
        self.actions = registry or default_registry()
        self.gate = gate or default_gate()
        self.verbs = command.default_verbs()   # the player-command vocabulary
        # B1b: when the deterministic parser fails, an LLM maps the free text onto
        # one command (off = pure deterministic parsing). Index canonical -> Verb
        # for reconstructing a Parse from the model's answer, and the verbs it may
        # map onto — never the meta verbs (a fuzzy read must not quit the player).
        self.intent_fallback = intent_fallback
        # Autonomous NPC reactions (B9). A framework capability, off by default: a
        # game opts in (our game does, in game/main.py). When on, a world event or
        # an NPC's own turn offers the other NPCs present a chance to react of their
        # own volition, cascading among them under the rails above. Off keeps NPCs
        # purely responsive to being spoken to — the behaviour every prior test and
        # scenario was written against, so enabling this regresses nothing existing.
        self.autonomous_reactions = autonomous_reactions
        # The NPC two-pass act-gate (B5), off by default: when on, each NPC decides
        # its action in a cheap low-temp pass before the blended turn speaks, so a
        # willing guide reliably *moves* when asked, not just talks of it (raising the
        # ~70% blended ceiling). A framework capability the game opts into; off keeps
        # the single blended turn every prior test was written against.
        self.npc_act_gate = npc_act_gate
        self.react_budget = REACT_BUDGET
        self.react_cooldown = REACT_COOLDOWN
        self.react_fuse = REACT_FUSE
        self._by_canonical = {v.canonical: v
                              for v in command._distinct_verbs(self.verbs)}
        self._fallback_verbs = [c for c in self._by_canonical
                                if c not in ("quit", "help")]
        self.minds: dict[str, NpcMind] = {}
        self.players: dict[str, Player] = {}    # session id -> player
        self.sessions: dict[str, Session] = {}  # session id -> session
        self._pcount = 0
        self._tasks: set[asyncio.Task] = set()  # in-flight async NPC replies
        # The running record of salient world beats — arrivals, speech, actions,
        # moves. The game-master director (attach_director) reads it on its slow
        # cadence as its perception of what has changed since it last looked.
        self.chronicle = Chronicle()
        self.director: Director | None = None
        # The world's own clock, if a game attaches one (attach_clock; B9). It
        # advances time-of-day on the loop whether or not a player is present, so a
        # still world stirs on its own. None until wired — the base engine has no
        # clock, exactly as it has no director.
        self.clock: WorldClock | None = None
        # The world's weather, if a game attaches one (attach_weather; B9) — the
        # clock's sibling, a bounded random walk over sky-states on the same bridge.
        self.weather: WeatherSystem | None = None
        # The loot forge, if a game attaches one (attach_loot; Phase 4). Code rolls an
        # item's mechanical brief (tier/tags/theme) from authored tables gated by the
        # place's conditions; the model authors only its flavour under a flat constrained
        # schema; the forge assembles a real Item. None until wired — the base engine
        # forges nothing, exactly as it has no director, clock, or weather. Fires today
        # on quest completion (a per-player reward).
        self.loot: LootForge | None = None
        # Starting quests (opt-in): opening goals every connecting player is handed, so
        # the world has a purpose from the first step — and a deterministic way to reach
        # the loot forge (walk to a quest's destination → it completes → a reward is
        # forged). Authored as world content (the "start_quests" block); empty = the base
        # engine hands out nothing.
        self.start_quests: list = []
        # Persistence (Phase 5): where the mutable runtime overlay is saved, and the
        # autosave cadence. None/0 until a game opts in (attach_persistence) — the
        # base engine persists nothing; the authored world.json is always the
        # definition reloaded beneath any saved delta (see loom/persistence.py).
        self.save_path: str | None = None
        self._autosave_pulses = 0
        self._autosave_counter = 0
        # The action catalogue offered to NPCs — the shared registry minus the
        # player-only verbs and the director-only verbs. Both the prompt and the
        # constrained-decoding grammar are narrowed to this, so NPC behavior is
        # unchanged as player-only or director-only actions join the registry.
        self.npc_actions = [n for n in self.actions.names()
                            if n not in PLAYER_ONLY_ACTIONS
                            and n not in DIRECTOR_ONLY_ACTIONS]
        # The director's own subset — the actions only the unseen hand may take.
        self.director_actions = [n for n in self.actions.names()
                                 if n in DIRECTOR_ONLY_ACTIONS]
        # Give every NPC in the world a mind that can act through the registry.
        for ent in world.entities.values():
            if isinstance(ent, Npc):
                self.minds[ent.id] = NpcMind(ent, provider, registry=self.actions,
                                             offered=self.npc_actions,
                                             act_gate=self.npc_act_gate,
                                             embedder=self.embedder,
                                             store=self.memory_store)

    # ---- Handler protocol (called by GameServer) ----
    async def on_connect(self, session: Session) -> None:
        self._pcount += 1
        pid = f"player:{self._pcount}"
        player = Player(id=pid, name=f"Wanderer-{self._pcount}",
                        location_id=self.start_location, session_id=session.id)
        self.world.add_entity(player)
        self.players[session.id] = player
        self.sessions[session.id] = session
        session.player_id = pid
        await session.send_system(f"Connected as {player.name}.")
        await session.send_text(self._banner())
        await self._look(session)
        loc = self.world.locations.get(self.start_location)
        where = loc.name if loc else self.start_location
        self.chronicle.record(f"{player.name} arrived in {where}.",
                              location_id=self.start_location, kind="arrival")
        await self._offer_start_quests(session, player)

    async def _offer_start_quests(self, session: Session, player) -> None:
        """Hand a newly-arrived player their opening goals and name them, so the journey
        has a direction from the first breath. No-op unless the game authored starting
        quests (attach_start_quests). Reaching one's destination completes it through the
        very same arrival hook every director-offered quest uses — and forges a reward if
        a loot forge is attached."""
        offered = False
        for d in self.start_quests:
            q = self.world.quests.offer(
                player.id, title=str(d["title"]), summary=str(d.get("summary", "")),
                giver=str(d.get("giver", "")), destination=str(d["destination"]))
            if q is not None:
                offered = True
                await session.send_text(styled(
                    "A purpose settles on you — ", span(q.title, Style.QUEST),
                    ". " + q.summary))
        if offered:
            await session.send_system("(Type quests to see your journal.)")

    async def on_disconnect(self, session: Session) -> None:
        self.sessions.pop(session.id, None)
        player = self.players.pop(session.id, None)
        if player:
            self.world.remove_entity(player.id)

    async def on_input(self, session: Session, text: str) -> None:
        player = self.players.get(session.id)
        if not player:
            return
        # Parse first (B1a): flexible player text -> a canonical verb + object
        # phrases. World-changing verbs go through the same registry seam the
        # NPCs use; queries are handled directly; free-text verbs (say) keep
        # their words. Noun resolution against scope happens here in the engine.
        p = command.parse(text, self.verbs)
        # B1b: an *unrecognised verb* gets one LLM interpretation against the
        # command grammar before we give up. A recognised verb whose object
        # doesn't resolve is NOT sent to the model — that is a legitimate "no
        # such thing" / disambiguation, handled deterministically below.
        if p.verb is None and p.unknown and self.intent_fallback:
            interpreted = await self._interpret(player, text)
            if interpreted is not None:
                p = interpreted
        await self._dispatch(session, player, p)

    async def _dispatch(self, session: Session, player, p: command.Parse) -> None:
        """Route a parsed command — whether it came from the deterministic parser
        or the LLM fallback — by verb kind. One path for both."""
        if p.verb is None:
            if p.unknown:
                await session.send_text(f'Unknown command: "{p.unknown}". Type help.')
            return
        if p.verb.kind == "text":               # say
            await self._say(session, player, p.words)
        elif p.verb.kind == "action":           # take / drop / give — the seam
            await self._player_action(session, player, p)
        else:                                   # a read-only query
            await self._query(session, player, p)

    async def _interpret(self, player, text: str) -> command.Parse | None:
        """Free-text fallback (B1b): map unrecognised input onto a Parse via the
        command grammar. Returns None if the model can't map it — the caller then
        renders the plain "unknown command" message."""
        catalogue = command.describe_verbs(self.verbs, self._fallback_verbs)
        schema = command.command_schema(self.verbs, self._fallback_verbs)
        res = await intent.interpret(self.provider, schema, catalogue,
                                     self._scope_context(player), text)
        if res is None:
            return None
        verb_name, dobj, iobj = res
        verb = self._by_canonical.get(verb_name)
        if verb is None:                        # model named a verb we don't allow
            return None
        if verb.kind == "text":
            return command.Parse(verb=verb, surface=verb_name, words=dobj)
        return command.Parse(verb=verb, surface=verb_name, dobj=dobj, iobj=iobj)

    def _scope_context(self, player) -> str:
        """A compact description of what the player can see and hold — the context
        the intent parser grounds its target choices on."""
        w, loc_id = self.world, player.location_id
        loc = w.locations.get(loc_id)
        others = [e.name for e in w.occupants(loc_id, exclude=player.id)]
        lines = ["Here with you: " + (", ".join(others) if others else "no one")]
        floor = [i.name for i in w.contents(loc_id)]
        if floor:
            lines.append("On the ground: " + ", ".join(floor))
        held = [i.name for i in w.contents(player.id)]
        if held:
            lines.append("You are carrying: " + ", ".join(held))
        if loc and loc.exits:
            lines.append("Exits: " + ", ".join(loc.exits.keys()))
        return "\n".join(lines)

    async def _query(self, session: Session, player, p: command.Parse) -> None:
        """Dispatch a read-only command (no world mutation, no seam needed)."""
        t = p.verb.target
        if t == "look":
            await self._look(session)
        elif t == "examine":
            await self._examine(session, player, p.dobj)
        elif t == "go":
            await self._go(session, player, p.dobj)
        elif t == "inventory":
            await self._inventory(session, player)
        elif t == "quests":
            await self._quests(session, player)
        elif t == "who":
            names = [e.name for e in
                     self.world.occupants(player.location_id, exclude=player.id)]
            if names:
                await session.send_text(
                    styled("Here: ", *join_styled(names, Style.NAME)))
            else:
                await session.send_text("Here: no one but you")
        elif t == "help":
            await session.send_text(self._help())
        elif t == "quit":
            await session.send_system("Goodbye.")
            await session.close()

    # ---- commands ----
    async def _look(self, session: Session) -> None:
        player = self.players[session.id]
        loc = self.world.locations.get(player.location_id)
        if not loc:
            await session.send_text("You are nowhere.")
            return
        # Built as a styled line (B3): the place heading, the occupants, the floor
        # items, and the exits carry their semantic roles; a plain terminal reads
        # the same prose (loom.style.plain flattens it byte-for-byte).
        parts: list = [span(f"== {loc.name} ==", Style.PLACE), "\n", loc.description]
        # Standing conditions read as part of the place, appended after the base
        # description at look-time so they show on every look until they lift (not
        # just when they began): first the world-wide ones (the clock's time of
        # day), then any condition specific to this place (a storm the director set).
        for cond in self.world.conditions.world_texts():
            parts += ["\n", cond]
        for cond in self.world.conditions.texts(loc.id):
            parts += ["\n", cond]
        others = self.world.occupants(loc.id, exclude=player.id)
        if others:
            parts += ["\n", "You see: ",
                      *join_styled([o.name for o in others], Style.NAME), "."]
        here_items = self.world.contents(loc.id)
        if here_items:
            parts += ["\n", "Items here: ",
                      *join_styled([i.name for i in here_items], Style.ITEM), "."]
        if loc.exits:
            parts += ["\n", "Exits: ",
                      *join_styled(list(loc.exits.keys()), Style.EXIT), "."]
        await session.send_text(styled(*parts))

    async def _say(self, session: Session, player, words: str) -> None:
        if not words:
            await session.send_text('Say what? (try: say hello)')
            return
        await session.send_text(styled("You say: ", span(f'"{words}"', Style.SPEECH)))
        self.chronicle.record(f'{player.name} said: "{words}"',
                              location_id=player.location_id, kind="speech")
        # Each NPC in the room *may* hear and answer. A cheap salience gate runs
        # first (B4): an NPC that isn't engaged — e.g. a bystander when someone
        # else was addressed by name — stays silent, costing no thinking beat and
        # no LLM call. Engaged NPCs reply on a background task so a slow reply
        # never blocks this player's input loop (nor anyone else's); speech and
        # any actions arrive as follow-up messages.
        loc_id = player.location_id
        present = [e.name for e in self.world.occupants(loc_id, exclude=player.id)
                   if isinstance(e, Npc)]
        for ent in self.world.occupants(loc_id, exclude=player.id):
            if not isinstance(ent, Npc):
                continue
            mind = self.minds.get(ent.id)
            if not mind:
                continue
            ctx = SalienceContext(npc=ent, speaker_name=player.name,
                                  utterance=words, present_npcs=present)
            if not self.gate.should_engage(ctx):
                continue
            # Was this NPC named? The mind frames a directly-addressed line as a
            # question to answer, and an unaddressed one as merely overheard —
            # which makes chosen silence more likely on ambient chatter (B4).
            addressed = is_addressed(ent.name, words)
            task = asyncio.create_task(
                self._deliver_npc_reply(loc_id, ent, mind, player.name, words,
                                        addressed))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _deliver_npc_reply(self, location_id: str, npc: Npc, mind: NpcMind,
                                 speaker_name: str, words: str,
                                 addressed: bool = True) -> None:
        # Out-of-band "thinking" beat covers the inference latency.
        await self._broadcast(location_id, "system", f"{npc.name} is considering your words…")
        scene = self._scene_for(npc, location_id)
        try:
            turn = await mind.converse(speaker_name, words, scene=scene,
                                       addressed=addressed)
        except ProviderError as exc:
            print(f"[loom] NPC {npc.id} reply failed: {exc}")
            await self._broadcast(location_id, "text", f"{npc.name} frowns, at a loss for words.")
            return
        except Exception as exc:  # a broken reply must never kill the connection
            print(f"[loom] NPC {npc.id} unexpected error: {exc!r}")
            await self._broadcast(location_id, "text", f"{npc.name} frowns, at a loss for words.")
            return
        observed = await self._deliver_turn(location_id, npc, mind, turn)
        # The NPC's own word or deed is itself something the others present may
        # answer, of their own volition (B9) — kick off a bounded cascade from it.
        # (No-op unless the game enabled autonomous_reactions.)
        self._spawn_reaction(location_id, npc.id, observed)

    async def _deliver_turn(self, location_id: str, npc, mind, turn) -> str:
        """Deliver a validated turn to the room — compose its speech and deeds into
        the line(s) the room reads, chronicle it, execute its validated actions —
        and return a one-line description of what was observed (the speech, plus any
        action narration), which a reaction can cascade from. Empty string if the
        turn was silent. Shared by the player-triggered reply path and the
        autonomous reaction cascade, so a turn is delivered identically however it
        arose.

        Rendering fuses the speech with the deeds performed *in this room* into one
        beat (B2: ``Odd shakes his head slowly and says, "..."``); a deed that shows
        in another room — a move's arrival at its destination — is broadcast there on
        its own, since that room never heard the speech. Only the room broadcast is
        composed: the structured turn, the chronicle, the actor's memory, and the
        observed string are all unchanged."""
        observed: list[str] = []
        if turn.speech:
            # NPC dialogue is salient world activity — the director should perceive
            # what characters say, not only what they physically do. Chronicled as
            # the plain spoken line, independent of how the room renders the beat.
            self.chronicle.record(f"{npc.name}: {turn.speech}",
                                  location_id=location_id, kind="speech")
            observed.append(f'{npc.name} said: "{turn.speech}"')
        # Validated intents only — the mind has already checked them against the
        # registry. Execute each without broadcasting (broadcast=False) and collect
        # its visible lines split by room: deeds shown here fuse with the speech;
        # deeds shown elsewhere (a move's arrival) go to their own room, unfused.
        home_deeds: list[str] = []
        away_lines: list = []
        for intent in turn.actions:
            result = await self._perform(location_id, npc, mind, intent,
                                         broadcast=False)
            if result is None:
                continue
            if result.narration:
                observed.append(result.narration)
            for room, line in self._resolve_lines(location_id, npc, result):
                if room == location_id:
                    home_deeds.append(line)
                else:
                    away_lines.append((room, line))
        # The beat for this room: speech woven with the deeds shown here, as a
        # styled line (B3). A silent-but-acting turn keeps each deed as its own line;
        # a deed shown in another room (a move's arrival) is styled the same way.
        if turn.speech:
            beat = compose_beat(npc.name, turn.speech, home_deeds)
            if beat:
                await self._broadcast(location_id, "text", beat)
        else:
            for deed in home_deeds:
                await self._broadcast(location_id, "text",
                                      compose_beat(npc.name, "", [deed]))
        for room, line in away_lines:
            await self._broadcast(room, "text", compose_beat(npc.name, "", [line]))
        return " ".join(observed)

    def _spawn_reaction(self, location_id: str, source_id: str,
                        event_text: str) -> None:
        """Kick off a bounded reaction cascade from an event (an NPC's turn, or a
        world change) on a background task, so it never blocks the caller. A fresh
        budget per originating event. No-op unless the game opted into autonomous
        reactions, or if there is nothing to react to."""
        if not self.autonomous_reactions or not event_text:
            return
        cascade = _Cascade(budget=self.react_budget, fuse=self.react_fuse)
        task = asyncio.create_task(
            self._react_to_event(location_id, source_id, event_text, cascade))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _react_to_event(self, location_id: str, source_id: str,
                              event_text: str, cascade: _Cascade) -> None:
        """Offer the NPCs present a chance to react to an event of their own
        volition, and let a reaction itself become an event the others may answer —
        a cascade among the characters. Bounded by the rails on ``cascade``: a
        shared decaying budget, a per-NPC cooldown, a self-guard (the source never
        reacts to its own event), and a hard fuse. The cascade ends when
        appropriateness runs out (every present NPC stays silent) or the budget /
        fuse is spent — never at a fixed depth, so NPCs answer each other as long
        as it stays warranted."""
        if not event_text or cascade.budget <= 0 or cascade.spent >= cascade.fuse:
            return
        # A new hop: every cooldown ticks down one, so an NPC that reacted recently
        # becomes eligible again after a beat — a real back-and-forth, not a lockout.
        for nid in list(cascade.cooldown):
            cascade.cooldown[nid] -= 1
            if cascade.cooldown[nid] <= 0:
                del cascade.cooldown[nid]
        # Deterministic order (occupants is a set) so a cascade is reproducible.
        present = sorted((e for e in self.world.occupants(location_id)
                          if isinstance(e, Npc)), key=lambda e: e.id)
        for npc in present:
            if cascade.budget <= 0 or cascade.spent >= cascade.fuse:
                break
            if npc.id == source_id:          # self-guard: not to your own line
                continue
            if cascade.cooldown.get(npc.id, 0) > 0:
                continue                      # sitting out a cooldown beat
            mind = self.minds.get(npc.id)
            if mind is None:
                continue
            scene = self._scene_for(npc, location_id)
            try:
                turn = await mind.react(event_text, scene=scene)
            except Exception as exc:          # a bad reaction must never break the loop
                print(f"[loom] NPC {npc.id} reaction failed: {exc!r}")
                continue
            if turn.is_silent:
                continue                      # the appropriateness gate: it chose not to
            cascade.budget -= 1
            cascade.spent += 1
            cascade.cooldown[npc.id] = self.react_cooldown
            observed = await self._deliver_turn(location_id, npc, mind, turn)
            # This reaction is itself an event the others present may answer.
            await self._react_to_event(location_id, npc.id, observed, cascade)

    def _scene_for(self, actor, location_id: str) -> Scene:
        """Compose the read-only perception snapshot the mind gets — the NPC's
        room, its exits, and the other entities present it can see and address."""
        loc = self.world.locations.get(location_id)
        if loc is None:
            return Scene()
        others = [e.name for e in self.world.occupants(location_id, exclude=actor.id)]
        items = [i.name for i in self.world.contents(location_id)]
        inventory = [i.name for i in self.world.contents(actor.id)]
        # World-wide conditions (the clock's time of day) plus this place's own —
        # so a mind reacting "to the storm" or acting by nightfall perceives both.
        conditions = (self.world.conditions.world_texts()
                      + self.world.conditions.texts(location_id))
        return Scene(location=loc.name, description=loc.description,
                     exits=list(loc.exits.keys()), others=others, items=items,
                     inventory=inventory, conditions=conditions)

    # ---- the director (game-master) ----
    def _snapshot_detail(self, loc) -> str:
        """The exits / floor items / standing conditions suffix for one place in the
        director's snapshot — shared by the occupied places and the empty ones just
        ahead of the players. Conditions are shown *with* their tags, since the tag
        is the handle ``clear_condition`` needs (players/NPCs see only the text)."""
        detail = ""
        if loc.exits:
            detail += "; exits: " + ", ".join(loc.exits.keys())
        floor = [i.name for i in self.world.contents(loc.id)]
        if floor:
            detail += "; on the ground: " + ", ".join(floor)
        conds = self.world.conditions.at(loc.id)
        if conds:
            detail += "; conditions: " + "; ".join(
                f'{c.tag} ("{c.text}")' for c in conds)
        return detail

    def world_snapshot(self, include_adjacent: bool = False) -> str:
        """A compact current-state view for the game-master director: the places
        with someone present — who is there, the exits, the floor items, any
        standing conditions — each keyed by its location *id* (the handle the
        director names an action by), with the human title alongside. The
        still-frame that complements the chronicle's record of what changed.

        With ``include_adjacent`` (B9 off-screen staging), it also lists the *empty*
        rooms one exit from an occupied one — where a wanderer may step next —
        marked ``[ahead, empty]``, so the director can foreshadow into a place
        before the player arrives (a standing condition there outlasts the walk)."""
        lines = []
        # World-wide conditions (the clock's time of day) hold everywhere, so they
        # head the snapshot — context for every place below, with their tags shown.
        world_conds = self.world.conditions.world_at()
        if world_conds:
            lines.append("Everywhere: " + "; ".join(
                f'{c.tag} ("{c.text}")' for c in world_conds))
        occupied = [loc for loc in self.world.locations.values()
                    if self.world.occupants(loc.id)]
        for loc in occupied:
            occ = self.world.occupants(loc.id)
            line = f"- {loc.id} ({loc.name}): " + ", ".join(e.name for e in occ)
            lines.append(line + self._snapshot_detail(loc))
        if include_adjacent:
            # Empty rooms one exit from an occupied one — the way the players may
            # walk next. Deduped, and never a room that is itself occupied (it is
            # already listed above). The director may quietly shape these ahead.
            seen = {loc.id for loc in occupied}
            for loc in occupied:
                for dest in loc.exits.values():
                    aloc = self.world.locations.get(dest)
                    if aloc is None or dest in seen or self.world.occupants(dest):
                        continue
                    seen.add(dest)
                    lines.append(f"- {aloc.id} ({aloc.name}) [ahead, empty]"
                                 + self._snapshot_detail(aloc))
        return "\n".join(lines) if lines else "(no one is anywhere in the world)"

    def attach_director(self, loop, persona: dict | None = None,
                        provider: LLMProvider | None = None,
                        period_ticks: int = 12,
                        name: str = "the Director",
                        min_new_events: int = 3,
                        cooldown_pulses: int = 2,
                        lull_pulses: int = 0,
                        foreshadow: bool = False,
                        act_gate: bool = False) -> Director:
        """Give this world an unseen game-master and hang it off ``loop``.

        The director rides the same seam as everyone else, offered only its own
        ``director_actions``. ``provider`` may point at a larger-context model
        variant (e.g. ``loom-gm``) for the GM's wider view; it defaults to the
        engine's provider. ``period_ticks`` is how many loop ticks pass between
        the director's slow pulses; ``min_new_events`` / ``cooldown_pulses`` hold
        its intervention rate down (the activity *ceiling*; BACKLOG B8).
        ``lull_pulses`` (0 = off) is the opt-in liveliness *floor* (B9): after that
        many quiet pulses since its last beat the director stirs a still room with
        one gentle beat. ``foreshadow`` (opt-in, B9) lets it also see and shape the
        empty rooms just ahead of the players. ``act_gate`` (opt-in, B8) adds a
        cheap model-side wait/act decision before each warranted beat, layered after
        the deterministic ceiling/floor. Returns the ``Director`` (also on
        ``self.director``)."""
        mind = DirectorMind(persona=persona, provider=provider or self.provider,
                            registry=self.actions, offered=self.director_actions,
                            name=name, embedder=self.embedder,
                            store=self.memory_store)
        self.director = Director(self, mind, period_ticks=period_ticks, name=name,
                                 min_new_events=min_new_events,
                                 cooldown_pulses=cooldown_pulses,
                                 lull_pulses=lull_pulses, foreshadow=foreshadow,
                                 act_gate=act_gate)
        self.director.install(loop)
        return self.director

    # ---- autonomous world-scope changes (clock, weather) ----
    async def apply_world_condition(self, tag: str, condition_text: str,
                                    ambient_text: str) -> None:
        """Turn a standing *world-scope* condition and announce the change where it
        is seen — the shared bridge the autonomous world systems drive through (the
        ``WorldClock``'s time-of-day, the ``WeatherSystem``'s sky). A deterministic,
        autonomous world beat with no model call (B9).

        Upserts the world-wide ``tag`` condition (or lifts it if the new state has
        no standing text — e.g. clear sky, or an untagged phase) so every place reads
        it from now on. Then, in each place with someone present, it broadcasts the
        one-shot ambient line, records it to the chronicle — so the director perceives
        it and may build on it under its own restraint (B8) — and offers the
        characters there a chance to react of their own volition. The source
        ``"world"`` is no NPC, so the cascade's self-guard blocks no one; the reaction
        is a no-op unless the game enabled autonomous_reactions."""
        if condition_text:
            self.world.conditions.set_world(tag, condition_text)
        else:
            self.world.conditions.clear_world(tag)
        if not ambient_text:
            return
        beat = styled(span(ambient_text, Style.AMBIENT))   # the world's own voice (B3)
        for loc in self.world.locations.values():
            if not self.world.occupants(loc.id):
                continue                 # perceivable-only: no beat into an empty place
            await self._broadcast(loc.id, "text", beat)
            self.chronicle.record(ambient_text, location_id=loc.id, kind="ambient")
            self._spawn_reaction(loc.id, "world", ambient_text)

    def attach_clock(self, loop, phases, *, factor: float = 1.0,
                     start_minute: float | None = None,
                     tag: str = "time") -> WorldClock:
        """Give this world its own autonomous clock and hang it off ``loop`` (B9).

        ``phases`` is the game's time-of-day table — a list of ``Phase`` or of the
        plain dicts the loader captured in ``world.meta['clock']['phases']`` (keys:
        ``name``, ``start``, and optional ``condition`` / ``ambient``). On each loop
        pulse the clock advances ``factor`` game-minutes per real second; crossing
        into a new phase lands that phase's standing condition and one ambient beat
        — deterministically, no model call — which the reaction path and the
        director then give life. Off by default: only a game that calls this has a
        living clock. Returns the ``WorldClock`` (also on ``self.clock``)."""
        specs = [p if isinstance(p, Phase)
                 else Phase(name=p["name"], start=int(p["start"]),
                            condition=p.get("condition", ""),
                            ambient=p.get("ambient", ""))
                 for p in phases]
        self.clock = WorldClock(self, specs, factor=factor,
                                start_minute=start_minute, tag=tag)
        self.clock.install(loop)
        return self.clock

    def attach_weather(self, loop, states, *, period_pulses: int = 12,
                       change_chance: float = 0.4, start_index: int = 0,
                       tag: str = "weather", rng=None) -> WeatherSystem:
        """Give this world a wandering sky and hang it off ``loop`` (B9) — the
        clock's sibling.

        ``states`` is the game's sky-chain — a list of ``Weather`` or of the plain
        dicts the loader captured in ``world.meta['weather']['states']`` (keys:
        ``name``, and optional ``condition`` / ``ambient``; empty ``condition`` =
        clear). Every ``period_pulses`` loop pulses the sky *may* step one place along
        the chain (probability ``change_chance``), landing the new state's standing
        condition + ambient beat — deterministically, no model call — which the
        reaction path and the director then give life. Pass a seeded ``rng`` for a
        reproducible walk. Off by default; only a game that calls this has weather.
        Returns the ``WeatherSystem`` (also on ``self.weather``)."""
        specs = [s if isinstance(s, Weather)
                 else Weather(name=s["name"], condition=s.get("condition", ""),
                              ambient=s.get("ambient", ""))
                 for s in states]
        self.weather = WeatherSystem(self, specs, period_pulses=period_pulses,
                                     change_chance=change_chance,
                                     start_index=start_index, tag=tag, rng=rng)
        self.weather.install(loop)
        return self.weather

    def attach_loot(self, config: dict, *, provider: LLMProvider | None = None,
                    seed: int | None = None,
                    max_aliases: int = 4) -> LootForge | None:
        """Give this world a loot forge (Phase 4) — dynamic, context-aware items on the
        golden rule. ``config`` is the authored ``"loot"`` block (``tiers`` / ``themes``
        / ``tags``); code rolls an item's mechanical brief from those tables, gated by
        the ``tier`` scalar and by the place's current conditions, and the model
        (``provider``, default the engine provider — pass the denser game-master tier for
        richer flavour) authors only its name and lore under a flat constrained schema.
        Pass a ``seed`` for a reproducible roll (else fresh variety each run). Returns the
        ``LootForge`` (also on ``self.loot``), or ``None`` if the config has no tiers to
        roll. Opt-in — the base engine forges nothing; unlike the director/clock/weather
        it hangs off no loop, firing instead on a seam event (today: quest completion)."""
        tables = LootTables.from_config(config)
        if tables.is_empty():
            return None
        self.loot = LootForge(tables=tables, provider=provider or self.provider,
                              rng=random.Random(seed),
                              schema=flavour_schema(max_aliases),
                              max_aliases=max_aliases)
        return self.loot

    def attach_start_quests(self, defs: list | None) -> list:
        """Hand every connecting player a set of opening goals (opt-in). ``defs`` is a
        list of ``{title, summary, destination}`` — a ``reach`` quest each — authored as
        the ``"start_quests"`` block in world.json. Offered on connect (``on_connect`` →
        ``_offer_start_quests``), so the world has a purpose from the first step and the
        loot forge has a deterministic trigger. A def whose destination is not a real
        place, or which has no title, is dropped (it could never complete). Returns the
        kept defs (also on ``self.start_quests``); base engine hands out nothing."""
        self.start_quests = [d for d in (defs or [])
                             if d.get("title")
                             and d.get("destination") in self.world.locations]
        return self.start_quests

    # ---- persistence (make the world survive a restart; Phase 5) ----
    def attach_persistence(self, loop, path: str, *,
                           autosave_pulses: int = 0) -> None:
        """Make this world durable: save its mutable runtime overlay to ``path`` on
        shutdown (call ``save()`` from the shutdown handler) and, if
        ``autosave_pulses > 0``, every that-many loop pulses. Opt-in — the base engine
        persists nothing. Restore is a separate step (``restore``), run once after the
        world and all ``attach_*`` wiring are in place and before serving, so the
        overlay lands on a fully-composed world (see loom/persistence.py)."""
        self.save_path = path
        self._autosave_pulses = max(0, int(autosave_pulses))
        self._autosave_counter = 0
        if self._autosave_pulses:
            loop.add_system(self._autosave_tick)

    async def _autosave_tick(self, dt: float) -> None:
        """The autosave loop callback: every ``_autosave_pulses`` pulses, dump the
        overlay. A forever-world will crash; shutdown-only would lose everything since
        boot. The write is cheap and atomic; the loop swallows any error it raises."""
        self._autosave_counter += 1
        if self._autosave_counter < self._autosave_pulses:
            return
        self._autosave_counter = 0
        self.save()

    def save(self) -> bool:
        """Atomically write the runtime overlay to ``save_path``. Returns False (a
        no-op) if persistence was never attached. Blocking file I/O — negligible for a
        small JSON overlay, and only ever called on the shutdown path or a coarse
        autosave, never per player action."""
        if not self.save_path:
            return False
        persistence.save_atomic(self.save_path, persistence.snapshot(self))
        return True

    def restore(self, path: str | None = None) -> bool:
        """Compose a saved overlay onto this (already-built, already-wired) engine.
        Returns True if a save was found and applied, False if there was none (a first
        run). Reads ``save_path`` unless an explicit ``path`` is given."""
        p = path or self.save_path
        if not p:
            return False
        save = persistence.load(p)
        if save is None:
            return False
        persistence.restore(self, save)
        return True

    def _spawn_forge_reward(self, session: Session, player, quest,
                            location_id: str) -> None:
        """Forge a per-player reward for a completed quest on a background task, so the
        arrival that triggered it is never blocked on a model call — the same off-loop
        dispatch an NPC reply uses. No-op unless a loot forge is attached."""
        if self.loot is None:
            return
        task = asyncio.create_task(
            self._forge_reward(session, player, quest, location_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _forge_reward(self, session: Session, player, quest,
                            location_id: str) -> None:
        """Gather context → roll the brief → have the model author flavour → assemble a
        real Item into the player's own inventory, and tell them what they earned. The
        forge's orchestration, which the engine owns because only it holds the world and
        the trigger. A failed model call degrades to a brief-only item — the reward is
        always real — and any error is contained, never breaking the arrival."""
        forge = self.loot
        if forge is None:
            return
        try:
            # Context that themes the reward: the place's standing conditions (weather,
            # time, anything the director set) give the resonant tags *and* the
            # situational blurb; the quest names why the thing appears.
            conds = (self.world.conditions.at(location_id)
                     + self.world.conditions.world_at())
            context_tags = [c.tag for c in conds]
            loc = self.world.locations.get(location_id)
            where = loc.name if loc else location_id
            situation = "; ".join(c.text for c in conds)
            blurb = (f'a reward for completing the quest "{quest.title}", at {where}'
                     + (f' — {situation}' if situation else ''))
            brief = loot.roll_brief(forge.tables, forge.rng,
                                    context_tags=context_tags, context_blurb=blurb)
            if brief is None:
                return
            raw = await loot_ai.author_flavour(forge.provider, forge.schema, brief)
            flavour = loot.parse_flavour(raw, max_aliases=forge.max_aliases)
            if flavour is None:                       # model unreachable/unusable
                flavour = loot.fallback_flavour(brief)
            item = self.world.spawn_item(
                flavour["name"], flavour["description"], holder_id=player.id,
                aliases=flavour["aliases"], tier=brief.tier, tags=brief.tags,
                theme=brief.theme)
        except Exception as exc:                      # a broken forge never breaks play
            print(f"[loom] loot forge failed for {getattr(player, 'id', '?')}: {exc!r}")
            return
        # The reward is personal — into the player's own inventory (a quest is per
        # player). Tell them, the item styled as an item (B3).
        await self._safe_send(
            session.send_text,
            styled("For seeing it through, you receive ",
                   span(item.name, Style.ITEM), "."))

    def _resolve_lines(self, location_id: str, actor, result) -> list:
        """The room-addressed lines an executed action makes visible: a
        ``(location_id, text)`` for each. The single definition of *what shows
        where* — used both to broadcast directly (players, the director) and, for
        an NPC turn, to hand the lines to the composition layer that fuses them with
        speech (B2). Narration shows where the actor now is (a move may have
        relocated it); explicit ``broadcasts`` name their own room."""
        lines: list = []
        if result.narration:
            where = getattr(actor, "location_id", None) or location_id
            lines.append((where, result.narration))
        for target, line in result.broadcasts:
            if target and line:
                lines.append((target, line))
        return lines

    async def _perform(self, location_id: str, actor, mind, intent,
                       exclude_session: str | None = None,
                       broadcast: bool = True):
        """Execute one validated intent and narrate its outcome. Returns the
        ActionResult, or None if the action was unknown or its handler failed
        (e.g. a give whose item/recipient didn't resolve) — the player-side
        action path uses that None to report failure. ``mind`` may be None when
        the actor is a player rather than an NPC (no memory to write).

        ``exclude_session`` skips one session in the room broadcasts — used for a
        player actor, who has already received a tailored second-person line and
        should not also hear the room's third-person narration of their own act.

        ``broadcast`` (default True) sends the action's room line(s) here, as
        players and the director do. An NPC turn passes ``broadcast=False`` and
        renders the lines itself — fusing them with the speech into one beat (B2)
        via ``_resolve_lines`` — so the world change, memory, chronicle, and any
        director cascade still happen here; only the room broadcast is deferred.
        """
        spec = self.actions.get(intent.name)
        if spec is None:      # defense in depth; the mind should never send this
            print(f"[loom] dropped unknown action {intent.name!r} from {actor.id}")
            return None
        try:
            result = spec.handler(ActionContext(self.world, actor, intent.args))
        except Exception as exc:  # a bad handler must not kill the connection
            print(f"[loom] action {intent.name} by {actor.id} failed: {exc!r}")
            return None
        if mind is not None and result.actor_memory:
            mind.memory.add(result.actor_memory, kind="action")
        # The action's room line(s), each aimed at the room it shows in — narration
        # where the actor now is (emote stays put; move relocated it), plus the
        # explicit multi-room broadcasts (move's departure + arrival). Broadcast now
        # unless the caller is composing them into a fused beat itself.
        if broadcast:
            # The bodiless director's lines are the world's own voice — styled
            # AMBIENT so they read as atmosphere, not a character speaking (B3).
            ambient = getattr(actor, "id", None) == "director"
            for room, line in self._resolve_lines(location_id, actor, result):
                payload = styled(span(line, Style.AMBIENT)) if ambient else line
                await self._broadcast(room, "text", payload, exclude=exclude_session)
        # Record the beat for the chronicle — the perception feed the director
        # reads. Use the room-visible narration, or a move's first broadcast line.
        beat = result.narration or (result.broadcasts[0][1]
                                    if result.broadcasts else "")
        if beat:
            beat_loc = (getattr(actor, "location_id", None) or location_id
                        or (result.broadcasts[0][0] if result.broadcasts else None))
            self.chronicle.record(beat, location_id=beat_loc, kind="action")
        # A director world-change (a condition raised or lifted, a staged beat) is
        # something the NPCs present may react to of their own volition (B9). Only
        # the bodiless director triggers a cascade here; an NPC's or player's own
        # action already cascades through _deliver_turn, so there is no double
        # fan-out. No-op unless the game enabled autonomous_reactions.
        if getattr(actor, "id", None) == "director":
            for target, line in result.broadcasts:
                self._spawn_reaction(target, "director", line)
        return result

    async def _broadcast(self, location_id: str, kind: str, text: str,
                         exclude: str | None = None) -> None:
        """Send to every connected player currently in a location. ``exclude``
        skips one session id — used when the actor already got a personalised
        line and should not also hear the room's third-person version."""
        for sid, player in list(self.players.items()):
            if sid == exclude:
                continue
            if getattr(player, "location_id", None) != location_id:
                continue
            session = self.sessions.get(sid)
            if session is None:
                continue
            send = session.send_system if kind == "system" else session.send_text
            await self._safe_send(send, text)

    @staticmethod
    async def _safe_send(send, payload: str) -> None:
        # The player may have disconnected while the NPC was thinking.
        try:
            await send(payload)
        except Exception:
            pass

    async def _go(self, session: Session, player, direction: str) -> None:
        alias = {"n": "north", "s": "south", "e": "east", "w": "west",
                 "u": "up", "d": "down"}
        direction = alias.get(direction, direction)
        loc = self.world.locations.get(player.location_id)
        if not loc or direction not in loc.exits:
            await session.send_text("You can't go that way.")
            return
        dest_id = loc.exits[direction]
        self.world.move(player.id, dest_id)
        await self._look(session)
        dest = self.world.locations.get(dest_id)
        self.chronicle.record(f"{player.name} goes {direction} to "
                              f"{dest.name if dest else dest_id}.",
                              location_id=dest_id, kind="move")
        # Reaching a place completes any reach-quest aimed here (the deterministic
        # completion check, bound to this arrival — the only path a player moves).
        await self._check_arrival_quests(session, player, dest_id)

    async def _examine(self, session: Session, player, phrase: str) -> None:
        """look at / examine a specific thing in scope — a read-only description."""
        if not phrase:
            await self._look(session)          # "look" with no object = the room
            return
        match = resolve(phrase, self.world.scope(player.id))
        if isinstance(match, Resolved):
            desc = getattr(match.entity, "description", "")
            await session.send_text(
                desc or f"You see nothing special about {match.entity.name}.")
        elif isinstance(match, Ambiguous):
            await session.send_text(self._which(match))
        else:
            await session.send_text(f'You see no "{phrase}" here.')

    # ---- the player action path (the seam, player side) ----
    # take / drop / give are world-mutating and shared with NPCs, so they route
    # through the same ActionRegistry the NPCs use — one execution path, whoever
    # acts. The engine resolves the object phrases against scope first, purely to
    # disambiguate ("which key?") and to acknowledge in the second person; the
    # action *handler* re-resolves the same phrases as the authoritative gate.
    async def _player_action(self, session: Session, player,
                             p: command.Parse) -> None:
        v = p.verb
        resolved: dict = {}
        # Indirect object first, so a dependent direct object (take X from Y:
        # the item lives in Y's contents) has its source resolved in hand.
        if v.iobj is not None and p.iobj:
            cands = self._candidates(player, v.iobj.scope, resolved)
            match = resolve(p.iobj, cands)
            if isinstance(match, Ambiguous):
                await session.send_text(self._which(match))
                return
            if not isinstance(match, Resolved):
                await session.send_text(f'There is no "{p.iobj}" here.')
                return
            resolved[v.iobj.arg] = match.entity
        elif v.iobj is not None and not v.dobj_from_iobj:
            # A required indirect object is missing (give needs a recipient).
            await session.send_text(self._usage(v))
            return

        if not p.dobj:
            await session.send_text(self._usage(v))
            return
        dscope = (command.IOBJ_CONTENTS
                  if (v.dobj_from_iobj and v.iobj is not None
                      and v.iobj.arg in resolved)
                  else v.dobj.scope)
        cands = self._candidates(player, dscope, resolved)
        match = resolve(p.dobj, cands)
        if isinstance(match, Ambiguous):
            await session.send_text(self._which(match))
            return
        if not isinstance(match, Resolved):
            await session.send_text(self._no_such(v, p.dobj, resolved))
            return
        item = match.entity
        resolved[v.dobj.arg] = item
        if not getattr(item, "portable", True):
            await session.send_text(f"You can't take {item.name}; it is fixed here."
                                    if v.target == "take_item"
                                    else f"You can't move {item.name}.")
            return

        # Build the intent from the raw phrases; the handler re-resolves them —
        # the seam's guarantee holds whether an NPC or a player proposed the act.
        args = {v.dobj.arg: p.dobj}
        if v.iobj is not None and p.iobj:
            args[v.iobj.arg] = p.iobj
        intent = ActionIntent(name=v.target, args=args)
        result = await self._perform(player.location_id, player, None, intent,
                                     exclude_session=session.id)
        if result is None:
            await session.send_text("You can't do that here.")
            return
        # The source-form ack ("… from Wren") when an optional source resolved.
        ack = (v.ack_source if v.ack_source and v.iobj is not None
               and v.iobj.arg in resolved else v.ack)
        await session.send_text(self._style_ack(ack, v, resolved))

    def _style_ack(self, template: str, verb: command.Verb, resolved: dict) -> list:
        """Render a second-person action ack (e.g. ``You take {item} from {source}.``)
        as a styled line — each substituted entity in its semantic role (an item as
        ITEM, a character as NAME, keyed by the slot's scope), the literal frame as
        default text (B3)."""
        role = {}
        for slot in (verb.dobj, verb.iobj):
            if slot is not None:
                role[slot.arg] = (Style.NAME
                                  if slot.scope in (command.OCCUPANTS, command.PRESENT)
                                  else Style.ITEM)
        parts: list = []
        for literal, field_name, _spec, _conv in Formatter().parse(template):
            if literal:
                parts.append(literal)
            if field_name is not None:
                ent = resolved.get(field_name)
                parts.append(span(ent.name, role.get(field_name, Style.ITEM))
                             if ent is not None else "{" + field_name + "}")
        return styled(*parts)

    def _candidates(self, player, scope: str, resolved: dict) -> list:
        """Map a symbolic scope (from the verb table) to real candidate entities."""
        w, loc = self.world, player.location_id
        if scope == command.FLOOR:
            return w.contents(loc)
        if scope == command.INVENTORY:
            return w.contents(player.id)
        if scope == command.OCCUPANTS:
            return w.occupants(loc, exclude=player.id)
        if scope == command.PRESENT:
            return list(w.occupants(loc, exclude=player.id)) + list(w.contents(loc))
        if scope == command.SCOPE:
            return w.scope(player.id)
        if scope == command.IOBJ_CONTENTS:
            # Only the indirect object is resolved at this point (take X from Y).
            src = next(iter(resolved.values()), None)
            return w.contents(src.id) if src is not None else []
        return []

    @staticmethod
    def _usage(v: command.Verb) -> str:
        return {
            "take_item": "Take what? (try: take lantern, or take map from Wren)",
            "drop_item": "Drop what? (try: drop lantern)",
            "give_item": "Give what to whom? (try: give lantern to Wren)",
        }.get(v.target, f"{v.canonical.capitalize()} what?")

    @staticmethod
    def _no_such(v: command.Verb, phrase: str, resolved: dict) -> str:
        if v.target == "take_item":
            src = resolved.get("source")
            return (f'{src.name} has no "{phrase}".' if src is not None
                    else f'There is no "{phrase}" here to take.')
        if v.target in ("drop_item", "give_item"):
            return f'You are not carrying a "{phrase}".'
        return f'There is no "{phrase}" here.'

    async def _inventory(self, session: Session, player) -> None:
        held = self.world.contents(player.id)
        if not held:
            await session.send_text("You are carrying nothing.")
            return
        await session.send_text(styled(
            "You are carrying: ",
            *join_styled([i.name for i in held], Style.ITEM), "."))

    async def _quests(self, session: Session, player) -> None:
        """The player's quest journal (a read-only query, no seam) — what the
        director has set before them and what they have seen through. Empty until a
        quest is offered; a completed one stays, marked, as a record of the doing."""
        entries = self.world.quests.for_player(player.id)
        if not entries:
            await session.send_text("You are pursuing nothing in particular.")
            return
        # One styled line with embedded newlines (as `look` builds): each quest
        # title in the QUEST role, the summary as plain text (B3).
        parts: list = ["== Your journal =="]
        for q in entries:
            mark = "✓" if q.status == COMPLETE else "•"
            parts += ["\n" + mark + " ", span(q.title, Style.QUEST), " — " + q.summary]
            if q.status == COMPLETE:
                parts.append("  (done)")
        await session.send_text(styled(*parts))

    async def _check_arrival_quests(self, session: Session, player,
                                    location_id: str) -> None:
        """The deterministic quest-completion check, bound to the player-arrival
        seam event: reaching a place completes any active ``reach`` quest aimed
        there, and the player is told. Idempotent (an already-done quest never
        re-fires), so wandering back through the destination does nothing."""
        done = self.world.quests.complete_reached(player.id, location_id)
        for q in done:
            await session.send_system(f'✓ Quest complete: "{q.title}".')
            # A completed quest earns a forged reward — a context-themed item authored
            # off the loop (Phase 4). No-op unless a loot forge is attached.
            self._spawn_forge_reward(session, player, q, location_id)

    @staticmethod
    def _which(match) -> str:
        names = ", ".join(getattr(e, "name", "?") for e in match.candidates)
        return f"Which do you mean: {names}? Be more specific."

    # ---- text ----
    def _banner(self) -> str:
        return ("You blink into being. The world hums faintly around you.\n"
                "(Type help for commands.)")

    def _help(self) -> str:
        return ("Commands:\n"
                "  look (l) · look at <thing> · examine <thing>\n"
                "  go <dir> — or just n/s/e/w/u/d\n"
                "  take <item> [from <who>] (get/grab/pick up) · drop <item> (put down)\n"
                "  give <item> to <who> (hand) · inventory (i)\n"
                "  say <words> · who · quests (journal) · help · quit\n"
                "Phrasing is flexible, and \"the\"/\"a\" are fine.")
