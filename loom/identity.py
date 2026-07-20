"""Durable player identity (Phase 5, slice 3).

Until now a player was session-ephemeral: on connect the engine minted a
``Player(id="player:N", name="Wanderer-N")`` keyed by the socket session, and on
disconnect ``World.remove_entity`` erased the body and dropped held items to the floor. A
returning player was a stranger the world had never seen. This module makes a player
*durable*, drawing the DikuMUD line the persistence spike named (``docs/spikes/identity.md``):

  * **The name is the key; identity == character.** No account object — a typed name maps,
    via a normalized *slug*, to one stable id ``player:<slug>`` and one ``PlayerRecord``. The
    same name always returns the same player. Name-only for now; a nullable ``password_hash``
    is reserved so authentication is a later state-machine insertion, not a data migration.
  * **The record travels with the player, not the floor.** A ``PlayerRecord`` carries the
    player's last location and the *full records of their held items*, so a reconnect restores
    them where they stood with what they carried — the persist-and-detach model (never
    delete-and-drop).

This is pure data + normalization; the lifecycle (the login prompt, reconnect/usurp,
detach, the name-seeded recall) lives in the engine, and persistence folds ``PlayerRecord``
into the same JSON overlay everything else rides. Framework-internal, dependency-free.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .world import Item

# Slugs a player may not claim — reserved framework keys and roles (``director`` is the
# reserved memory/agent key; the rest avoid confusing self/system references as a name).
RESERVED_SLUGS = frozenset({"director", "world", "system", "me", "self", "someone"})

_ID_PREFIX = "player:"
_MAX_NAME = 32
# One or more non-alphanumeric characters collapse to a single hyphen — so "Odd the
# Wary!!" and "odd-the-wary" slug alike, and the slug is a clean, stable key.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A display name -> its durable key: lowercased, non-alphanumeric runs collapsed to
    single hyphens, ends trimmed. ``''`` if nothing usable remains (all punctuation)."""
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")


def display_name(name: str) -> str:
    """The typed name cleaned for display: internal whitespace collapsed, length-capped.
    The slug is the key; this is what the world calls the player and what NPC memories
    record — so it is preserved close to what was typed (not the hyphenated slug)."""
    return " ".join((name or "").split())[:_MAX_NAME]


def player_id(slug: str) -> str:
    """The durable entity id for a player slug. The ``player:`` prefix cannot collide with
    an authored entity/location id (those carry no such prefix), so identity is total."""
    return f"{_ID_PREFIX}{slug}"


def slug_of(pid: str) -> str:
    """The slug behind a ``player:<slug>`` id (or the id unchanged if not a player id)."""
    return pid[len(_ID_PREFIX):] if pid.startswith(_ID_PREFIX) else pid


def validate_name(name: str) -> tuple[str, str | None]:
    """Vet a typed name for login. Returns ``(slug, error)``: on success a non-empty slug
    and ``None``; on failure ``("", <human message>)``. A name must have at least one
    letter or digit and not be reserved. The engine additionally re-prompts on this error."""
    display = display_name(name)
    if not display:
        return "", "A name is required. By what name are you known?"
    slug = slugify(display)
    if not slug:
        return "", "That name has no letters or digits. Choose another."
    if slug in RESERVED_SLUGS:
        return "", "That name is reserved. Choose another."
    return slug, None


def item_record(item: Item) -> dict:
    """A held item's durable record — the fields needed to re-mint it onto the player on
    reconnect. The item travels *with* the player (into the ``PlayerRecord``), so it is
    excluded from the world's global item snapshot and never drops to the floor."""
    return {"id": item.id, "name": item.name, "description": item.description,
            "aliases": list(item.aliases), "portable": bool(item.portable),
            "tier": item.tier, "tags": list(item.tags), "theme": item.theme}


def rebuild_item(rec: dict, holder: str) -> Item:
    """Reconstruct a held ``Item`` from its record, homed to ``holder`` (the reconnecting
    player). Reuses the item's original id, so its identity is stable across the offline
    gap; ``World._id_counter`` (restored from the overlay) has already advanced past it, so
    a fresh forge can never collide with a re-minted held item."""
    iid = rec.get("id") or "item"
    return Item(id=iid, name=rec.get("name", iid), description=rec.get("description", ""),
                holder=holder, aliases=list(rec.get("aliases", [])),
                portable=bool(rec.get("portable", True)), tier=rec.get("tier", ""),
                tags=list(rec.get("tags", [])), theme=rec.get("theme", ""))


@dataclass
class PlayerRecord:
    """The durable state of one player, keyed by slug and persisted in the JSON overlay.

    Location and held items are refreshed from the live body on every snapshot and on
    detach, so a returning player resumes where they stood with what they carried. Quests
    are *not* here — ``QuestBook`` already keys them by the (now durable) player id, so they
    persist for free. ``password_hash`` is reserved (``None`` today): the name is the key,
    and auth is a deferred later slice."""
    id: str
    name: str
    location_id: str | None = None
    held: list = field(default_factory=list)      # list[item_record dict]
    created_t: float = 0.0
    last_seen_t: float = 0.0
    password_hash: str | None = None              # reserved — auth is a later slice

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "location_id": self.location_id,
                "held": list(self.held), "created_t": self.created_t,
                "last_seen_t": self.last_seen_t, "password_hash": self.password_hash}

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerRecord":
        return cls(
            id=d.get("id", ""), name=d.get("name", d.get("id", "")),
            location_id=d.get("location_id"), held=list(d.get("held", [])),
            created_t=float(d.get("created_t", 0.0)),
            last_seen_t=float(d.get("last_seen_t", 0.0)),
            password_hash=d.get("password_hash"))
