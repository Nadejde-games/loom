"""An NPC mind: persona core + memory, able to hold a conversation.

Dialogue only, for now. State-changing behaviour (moving, giving loot,
starting a quest) will go through a *separate*, schema-validated tool-call
path — never by parsing free text. That seam is where the game-master and loot
systems plug in later. The golden rule: never execute raw model text.
"""
from __future__ import annotations
from ..world.entity import Npc
from .provider import LLMProvider
from .memory import MemoryStream


class NpcMind:
    def __init__(self, npc: Npc, provider: LLMProvider, memory: MemoryStream | None = None):
        self.npc = npc
        self.provider = provider
        self.memory = memory or MemoryStream()

    def _system_prompt(self) -> str:
        p = self.npc.persona or {}
        parts = [f"You are {self.npc.name}, a character in a living text world."]
        if p.get("backstory"):
            parts.append(str(p["backstory"]))
        if p.get("traits"):
            parts.append("Traits: " + ", ".join(p["traits"]))
        if p.get("goals"):
            parts.append("Goals: " + ", ".join(p["goals"]))
        if p.get("voice"):
            parts.append("Speak like this: " + str(p["voice"]))
        mems = self.memory.recent()
        if mems:
            parts.append("Recent memories:\n" + "\n".join(f"- {m.text}" for m in mems))
        parts.append("Stay in character. Reply with a single short spoken line, "
                     "optionally a brief physical action. Never break character "
                     "or mention being an AI.")
        return "\n\n".join(parts)

    async def hear_and_respond(self, speaker_name: str, utterance: str) -> str:
        self.memory.add(f'{speaker_name} said to me: "{utterance}"', kind="speech")
        system = self._system_prompt()
        messages = [{"role": "user", "content": f'{speaker_name} says: "{utterance}"'}]
        reply = await self.provider.complete(system, messages)
        self.memory.add(f'I replied: "{reply}"', kind="speech")
        return reply
