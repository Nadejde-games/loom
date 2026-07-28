"""World-model primitives. Pure data — no AI, no networking. These are the
nouns an authoring agent creates and edits."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str
    name: str
    description: str = ""


@dataclass
class Character(Entity):
    location_id: str | None = None
    # Optional RPG character sheet (Phase 9, Tier 0) — a ``loom.rpg.sheet.Sheet`` when this
    # character has stats/pools, else ``None``. Kept an opaque slot so the pure world model
    # stays decoupled from the opt-in ``loom.rpg`` layer: a sheetless character (every
    # narrative NPC, an anonymous player before a game attaches the RPG layer) is unchanged,
    # and the engine touches ``.sheet`` only by duck type. Never serialised into ``world.json``
    # (its live delta rides the save overlay); the authored base is built by the RPG layer.
    sheet: object | None = None


@dataclass
class Npc(Character):
    # Free-form persona the AI layer turns into a system prompt.
    # Conventional keys: backstory, traits[], goals[], voice.
    persona: dict = field(default_factory=dict)
    # Whether this NPC may wander of its own accord (idle-NPC autonomy, Phase 5):
    # the SENTINEL mirror. Opt-in, default off — a base-engine or unauthored NPC
    # never leaves its room on a lull. Authored true for a roamer (a wayfinder);
    # a rooted character (a hermit, a quest-giver) stays put and only speaks/emotes
    # in place. Enforced as a hard rail by the ``Idler`` (a move from a non-wanderer
    # is stripped), never merely by the prompt.
    wanders: bool = False


@dataclass
class Player(Character):
    session_id: str | None = None


@dataclass
class Item(Entity):
    # What holds this item: the id of a location (lying on the floor), a
    # character (in their inventory), or later another item (a container).
    # This is the source of truth for where the item is; ``World`` keeps a
    # reverse index for fast lookup — exactly as ``Character.location_id`` is
    # the truth mirrored by ``Location.occupants``.
    holder: str | None = None
    # Extra nouns the item answers to, beyond its name — e.g. ["key", "brass"]
    # for "the ornate brass key". Used by name-resolution (loom/naming.py).
    aliases: list = field(default_factory=list)
    # Whether it can be picked up, dropped, or given. A fixed feature of the
    # scene (a wall carving, a fountain) is not portable.
    portable: bool = True
    # --- code-owned mechanical classification (the loot forge, Phase 4) ---
    # Set by CODE, never by the model — the golden rule extended to loot: the forge
    # rolls these from tables and attaches them, while the model authors only the
    # flavour above (name/description/aliases). An authored or director-spawned item
    # leaves them empty. ``tier`` is the rarity/power ordinal — the single scalar
    # (PoE's ilvl analog) that gates which tables are eligible today and will gate a
    # future stat system with no change to this shape; ``tags`` is the rolled
    # classification; ``theme`` the coherence handle name, lore, and tags share.
    tier: str = ""
    tags: list = field(default_factory=list)
    theme: str = ""
    # --- code-owned gear mechanics (the RPG layer, Phase 9, Tier 1) ---
    # An equippable item declares a ``slot`` (an authored slot name — "main_hand", "head")
    # and a list of ``modifiers`` (``{target, value, op, type}`` apply-pairs). Like tier/tags
    # above, these are mechanical and code-read (the golden rule): the RPG layer folds an
    # equipped item's modifiers into the wearer's sheet. Empty ⇒ a plain, non-equippable item.
    slot: str = ""
    modifiers: list = field(default_factory=list)
