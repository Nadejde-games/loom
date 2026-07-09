"""The default game engine: binds sessions to the world and to NPC minds, and
provides a small, extensible command set. Nothing here is specific to any
particular world — a game may subclass this or register extra commands.
"""
from __future__ import annotations
import asyncio
from .session import Session
from .world import World, Player, Npc
from .ai import NpcMind, LLMProvider, ProviderError


class Engine:
    def __init__(self, world: World, provider: LLMProvider, start_location: str):
        self.world = world
        self.provider = provider
        self.start_location = start_location
        self.minds: dict[str, NpcMind] = {}
        self.players: dict[str, Player] = {}   # session id -> player
        self._pcount = 0
        self._tasks: set[asyncio.Task] = set()  # in-flight async NPC replies
        # Give every NPC in the world a mind.
        for ent in world.entities.values():
            if isinstance(ent, Npc):
                self.minds[ent.id] = NpcMind(ent, provider)

    # ---- Handler protocol (called by GameServer) ----
    async def on_connect(self, session: Session) -> None:
        self._pcount += 1
        pid = f"player:{self._pcount}"
        player = Player(id=pid, name=f"Wanderer-{self._pcount}",
                        location_id=self.start_location, session_id=session.id)
        self.world.add_entity(player)
        self.players[session.id] = player
        session.player_id = pid
        await session.send_system(f"Connected as {player.name}.")
        await session.send_text(self._banner())
        await self._look(session)

    async def on_disconnect(self, session: Session) -> None:
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
        if loc.exits:
            lines.append("Exits: " + ", ".join(loc.exits.keys()) + ".")
        await session.send_text("\n".join(lines))

    async def _say(self, session: Session, player, words: str) -> None:
        if not words:
            await session.send_text('Say what? (try: say hello)')
            return
        await session.send_text(f'You say: "{words}"')
        # Each NPC in the room hears and answers. The LLM call is dispatched as a
        # background task so a slow reply never blocks this player's input loop
        # (nor anyone else's); the reply arrives as a follow-up message.
        for ent in self.world.occupants(player.location_id, exclude=player.id):
            if isinstance(ent, Npc):
                mind = self.minds.get(ent.id)
                if mind:
                    task = asyncio.create_task(
                        self._deliver_npc_reply(session, ent, mind, player.name, words))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def _deliver_npc_reply(self, session: Session, npc: Npc, mind: NpcMind,
                                 speaker_name: str, words: str) -> None:
        # Out-of-band "thinking" beat covers the inference latency.
        await self._safe_send(session.send_system, f"{npc.name} is considering your words…")
        try:
            reply = await mind.hear_and_respond(speaker_name, words)
        except ProviderError as exc:
            print(f"[loom] NPC {npc.id} reply failed: {exc}")
            await self._safe_send(session.send_text, f"{npc.name} frowns, at a loss for words.")
            return
        except Exception as exc:  # a broken reply must never kill the connection
            print(f"[loom] NPC {npc.id} unexpected error: {exc!r}")
            await self._safe_send(session.send_text, f"{npc.name} frowns, at a loss for words.")
            return
        await self._safe_send(session.send_text, f"{npc.name}: {reply}")

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

    # ---- text ----
    def _banner(self) -> str:
        return ("You blink into being. The world hums faintly around you.\n"
                "(Type help for commands.)")

    def _help(self) -> str:
        return "Commands: look | say <words> | go <dir> (or n/s/e/w/u/d) | who | help | quit"
