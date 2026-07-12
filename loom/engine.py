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


class Engine:
    def __init__(self, world: World, provider: LLMProvider, start_location: str,
                 registry: ActionRegistry | None = None,
                 gate: SalienceGate | None = None):
        self.world = world
        self.provider = provider
        self.start_location = start_location
        self.actions = registry or default_registry()
        self.gate = gate or default_gate()
        self.minds: dict[str, NpcMind] = {}
        self.players: dict[str, Player] = {}    # session id -> player
        self.sessions: dict[str, Session] = {}  # session id -> session
        self._pcount = 0
        self._tasks: set[asyncio.Task] = set()  # in-flight async NPC replies
        # Give every NPC in the world a mind that can act through the registry.
        for ent in world.entities.values():
            if isinstance(ent, Npc):
                self.minds[ent.id] = NpcMind(ent, provider, registry=self.actions)

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
        cmd, _, arg = text.strip().partition(" ")
        cmd = cmd.lower()
        arg = arg.strip()
        if cmd in ("look", "l"):
            await self._look(session)
        elif cmd == "say":
            await self._say(session, player, arg)
        elif cmd in ("go", "move"):
            await self._go(session, player, arg)
        elif cmd in ("north", "south", "east", "west", "up", "down",
                     "n", "s", "e", "w", "u", "d"):
            await self._go(session, player, cmd)
        elif cmd in ("take", "get"):
            await self._take(session, player, arg)
        elif cmd == "drop":
            await self._drop(session, player, arg)
        elif cmd == "give":
            await self._give(session, player, arg)
        elif cmd in ("inventory", "inv", "i"):
            await self._inventory(session, player)
        elif cmd in ("who",):
            here = ", ".join(e.name for e in self.world.occupants(player.location_id))
            await session.send_text("Here: " + (here or "no one but you"))
        elif cmd in ("help", "?"):
            await session.send_text(self._help())
        elif cmd in ("quit", "exit"):
            await session.send_system("Goodbye.")
            await session.close()
        else:
            await session.send_text(f'Unknown command: "{cmd}". Type help.')

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

    async def _perform(self, location_id: str, actor, mind, intent):
        """Execute one validated intent and narrate its outcome. Returns the
        ActionResult, or None if the action was unknown or its handler failed
        (e.g. a give whose item/recipient didn't resolve) — the player-side
        give path uses that None to report failure. ``mind`` may be None when
        the actor is a player rather than an NPC (no memory to write)."""
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
            await self._broadcast(where, "text", result.narration)
        # Room-targeted lines: an action that touches more than one room (move's
        # departure + arrival) names each room explicitly. Correct for multiplayer
        # — each room hears only its own line.
        for target, line in result.broadcasts:
            if target and line:
                await self._broadcast(target, "text", line)
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

    # ---- inventory commands ----
    # take/drop are single-object player conveniences that touch only the
    # player's own room and inventory, so — like _go — they call the world
    # model directly. give is different: it is world-mutating and shared with
    # NPCs, so it routes through the action registry (the seam) below.
    async def _take(self, session: Session, player, arg: str) -> None:
        if not arg:
            await session.send_text("Take what?")
            return
        match = resolve(arg, self.world.contents(player.location_id))
        if isinstance(match, Resolved):
            item = match.entity
            if not getattr(item, "portable", True):
                await session.send_text(f"You can't take {item.name}.")
                return
            self.world.place_item(item.id, player.id)
            # Item names carry their own article, so we never prepend another.
            await session.send_text(f"You take {item.name}.")
            await self._broadcast(player.location_id, "text",
                                  f"{player.name} takes {item.name}.",
                                  exclude=session.id)
        elif isinstance(match, Ambiguous):
            await session.send_text(self._which(match))
        else:
            await session.send_text(f'There is no "{arg}" here to take.')

    async def _drop(self, session: Session, player, arg: str) -> None:
        if not arg:
            await session.send_text("Drop what?")
            return
        match = resolve(arg, self.world.contents(player.id))
        if isinstance(match, Resolved):
            item = match.entity
            self.world.place_item(item.id, player.location_id)
            await session.send_text(f"You drop {item.name}.")
            await self._broadcast(player.location_id, "text",
                                  f"{player.name} drops {item.name}.",
                                  exclude=session.id)
        elif isinstance(match, Ambiguous):
            await session.send_text(self._which(match))
        else:
            await session.send_text(f'You are not carrying a "{arg}".')

    async def _give(self, session: Session, player, arg: str) -> None:
        # A deliberately tiny parser — "<item> to <recipient>". The point is
        # not the grammar (that is B1) but the seam: player input becomes a
        # proposed action run through the very same registry + validation +
        # handler the NPCs use. One execution path for "give", whoever acts.
        item_phrase, sep, who_phrase = arg.partition(" to ")
        item_phrase, who_phrase = item_phrase.strip(), who_phrase.strip()
        if not sep or not item_phrase or not who_phrase:
            await session.send_text('Give what to whom? (try: give lantern to Wren)')
            return
        intent = ActionIntent(name="give_item",
                              args={"item": item_phrase, "recipient": who_phrase})
        result = await self._perform(player.location_id, player, None, intent)
        if result is None:
            await session.send_text("You can't give that here.")

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
        return ("Commands: look | say <words> | go <dir> (or n/s/e/w/u/d) | "
                "take <item> | drop <item> | give <item> to <who> | "
                "inventory (i) | who | help | quit")
