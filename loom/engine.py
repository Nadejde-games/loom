"""The default game engine: binds sessions to the world and to NPC minds, and
provides a small, extensible command set. Nothing here is specific to any
particular world — a game may subclass this or register extra commands.
"""
from __future__ import annotations
import asyncio
from .session import Session
from .world import World, Player, Npc
from .action import ActionRegistry, ActionContext, ActionIntent, default_registry
from .salience import SalienceGate, SalienceContext, default_gate, is_addressed
from .naming import resolve, Resolved, Ambiguous
from .ai import NpcMind, LLMProvider, ProviderError, Scene
from .ai import intent
from . import command

# Actions the default game offers the *player* but not its NPCs — the NPCs in
# this world don't scavenge the floor. Purely a content choice (not a seam rule):
# a game can widen any NPC's catalogue by giving its NpcMind a different offered
# set. Keeps the NPC action catalogue (and the behavioral harness) unchanged as
# player-only verbs are added to the shared registry.
PLAYER_ONLY_ACTIONS = ("take_item", "drop_item")


class Engine:
    def __init__(self, world: World, provider: LLMProvider, start_location: str,
                 registry: ActionRegistry | None = None,
                 gate: SalienceGate | None = None,
                 intent_fallback: bool = True):
        self.world = world
        self.provider = provider
        self.start_location = start_location
        self.actions = registry or default_registry()
        self.gate = gate or default_gate()
        self.verbs = command.default_verbs()   # the player-command vocabulary
        # B1b: when the deterministic parser fails, an LLM maps the free text onto
        # one command (off = pure deterministic parsing). Index canonical -> Verb
        # for reconstructing a Parse from the model's answer, and the verbs it may
        # map onto — never the meta verbs (a fuzzy read must not quit the player).
        self.intent_fallback = intent_fallback
        self._by_canonical = {v.canonical: v
                              for v in command._distinct_verbs(self.verbs)}
        self._fallback_verbs = [c for c in self._by_canonical
                                if c not in ("quit", "help")]
        self.minds: dict[str, NpcMind] = {}
        self.players: dict[str, Player] = {}    # session id -> player
        self.sessions: dict[str, Session] = {}  # session id -> session
        self._pcount = 0
        self._tasks: set[asyncio.Task] = set()  # in-flight async NPC replies
        # The action catalogue offered to NPCs — the shared registry minus the
        # player-only verbs (see PLAYER_ONLY_ACTIONS). Both the prompt and the
        # constrained-decoding grammar are narrowed to this, so NPC behavior is
        # unchanged as player-only actions join the registry.
        self.npc_actions = [n for n in self.actions.names()
                            if n not in PLAYER_ONLY_ACTIONS]
        # Give every NPC in the world a mind that can act through the registry.
        for ent in world.entities.values():
            if isinstance(ent, Npc):
                self.minds[ent.id] = NpcMind(ent, provider, registry=self.actions,
                                             offered=self.npc_actions)

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
        elif t == "who":
            here = ", ".join(e.name for e in
                             self.world.occupants(player.location_id, exclude=player.id))
            await session.send_text("Here: " + (here or "no one but you"))
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
        lines = [f"== {loc.name} ==", loc.description]
        others = self.world.occupants(loc.id, exclude=player.id)
        if others:
            lines.append("You see: " + ", ".join(o.name for o in others) + ".")
        here_items = self.world.contents(loc.id)
        if here_items:
            lines.append("Items here: " + ", ".join(i.name for i in here_items) + ".")
        if loc.exits:
            lines.append("Exits: " + ", ".join(loc.exits.keys()) + ".")
        await session.send_text("\n".join(lines))

    async def _say(self, session: Session, player, words: str) -> None:
        if not words:
            await session.send_text('Say what? (try: say hello)')
            return
        await session.send_text(f'You say: "{words}"')
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
        if turn.speech:
            await self._broadcast(location_id, "text", f"{npc.name}: {turn.speech}")
        # Validated intents only — the mind has already checked them against the
        # registry; the engine executes and narrates the outcome to the room.
        for intent in turn.actions:
            await self._perform(location_id, npc, mind, intent)

    def _scene_for(self, actor, location_id: str) -> Scene:
        """Compose the read-only perception snapshot the mind gets — the NPC's
        room, its exits, and the other entities present it can see and address."""
        loc = self.world.locations.get(location_id)
        if loc is None:
            return Scene()
        others = [e.name for e in self.world.occupants(location_id, exclude=actor.id)]
        items = [i.name for i in self.world.contents(location_id)]
        inventory = [i.name for i in self.world.contents(actor.id)]
        return Scene(location=loc.name, description=loc.description,
                     exits=list(loc.exits.keys()), others=others, items=items,
                     inventory=inventory)

    async def _perform(self, location_id: str, actor, mind, intent,
                       exclude_session: str | None = None):
        """Execute one validated intent and narrate its outcome. Returns the
        ActionResult, or None if the action was unknown or its handler failed
        (e.g. a give whose item/recipient didn't resolve) — the player-side
        action path uses that None to report failure. ``mind`` may be None when
        the actor is a player rather than an NPC (no memory to write).

        ``exclude_session`` skips one session in the room broadcasts — used for a
        player actor, who has already received a tailored second-person line and
        should not also hear the room's third-person narration of their own act.
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
        if result.narration:
            # Narrate where the actor now is — an action (e.g. move) may have
            # relocated it; emote leaves it put.
            where = getattr(actor, "location_id", None) or location_id
            await self._broadcast(where, "text", result.narration,
                                  exclude=exclude_session)
        # Room-targeted lines: an action that touches more than one room (move's
        # departure + arrival) names each room explicitly. Correct for multiplayer
        # — each room hears only its own line.
        for target, line in result.broadcasts:
            if target and line:
                await self._broadcast(target, "text", line, exclude=exclude_session)
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
        self.world.move(player.id, loc.exits[direction])
        await self._look(session)

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
        await session.send_text(ack.format(**{k: e.name for k, e in resolved.items()}))

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
        await session.send_text("You are carrying: "
                                + ", ".join(i.name for i in held) + ".")

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
                "  say <words> · who · help · quit\n"
                "Phrasing is flexible, and \"the\"/\"a\" are fine.")
