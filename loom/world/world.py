"""Container for all world state, plus simple spatial queries."""
from __future__ import annotations
from .entity import Entity, Character, Item, Player
from .location import Location
from .conditions import Conditions
from ..quest import QuestBook


class World:
    def __init__(self):
        self.locations: dict[str, Location] = {}
        self.entities: dict[str, Entity] = {}  # characters, npcs, players, items
        # Reverse index of containment: holder id -> ids of items it holds. The
        # source of truth is each Item.holder; this mirrors it for O(1) lookup,
        # exactly as Location.occupants mirrors Character.location_id.
        self._contents: dict[str, set] = {}
        # World-level authored configuration that isn't a structural entity — any
        # top-level key in the world data beyond locations/npcs/items (e.g. the
        # game-master director persona, world tone, shared tables). A generic
        # blob so the framework stays agnostic to what a game chooses to put here.
        self.meta: dict = {}
        # Standing environmental conditions, keyed by location id — the persistent
        # counterpart to the transient chronicle. The game-master director sets and
        # clears these; they color perception (look, and every mind's Scene) until
        # lifted. Held here at the world level, not on Location, so region-wide
        # weather and a world-clock extend the same shape later (see conditions.py).
        self.conditions = Conditions()
        # Per-player quest log — a director-offered goal, tracked and readable. A
        # world-held, per-key state primitive on the same footing as ``conditions``:
        # empty until the director offers a quest (offer_quest), read by the player's
        # ``quests`` command, completed by a deterministic engine check (arrival).
        self.quests = QuestBook()
        # Monotonic counter behind ``fresh_id`` — the source of unique, deterministic
        # ids for entities spawned at runtime (a director's spawned item), so they
        # never collide with authored ids and tests stay reproducible.
        self._id_counter = 0

    # --- mutation ---
    def add_location(self, loc: Location) -> None:
        self.locations[loc.id] = loc

    def add_entity(self, ent: Entity) -> None:
        self.entities[ent.id] = ent
        loc_id = getattr(ent, "location_id", None)
        if loc_id and loc_id in self.locations:
            self.locations[loc_id].occupants.add(ent.id)
        # An item declares its holder directly; index it regardless of load
        # order (the holder entity/location may not be added yet).
        holder = getattr(ent, "holder", None)
        if holder:
            self._contents.setdefault(holder, set()).add(ent.id)

    def remove_entity(self, entity_id: str) -> None:
        ent = self.entities.get(entity_id)
        if ent is None:
            return
        # Read the location BEFORE removing (location_of would return None once
        # the entity is gone — this also fixes stale occupant cleanup).
        loc = self.location_of(entity_id)
        # Drop whatever this entity was carrying onto the floor, so items are
        # not lost when a character disconnects or is removed.
        for item_id in list(self._contents.get(entity_id, ())):
            if loc is not None:
                self.place_item(item_id, loc.id)
            else:
                self._contents.get(entity_id, set()).discard(item_id)
        self._contents.pop(entity_id, None)
        # If the entity is itself a held item, unlink it from its holder.
        holder = getattr(ent, "holder", None)
        if holder and holder in self._contents:
            self._contents[holder].discard(entity_id)
        self.entities.pop(entity_id, None)
        if loc is not None:
            loc.occupants.discard(entity_id)

    def fresh_id(self, prefix: str = "e") -> str:
        """A unique, deterministic id for an entity spawned at runtime — a thing the
        director brings into being (spawn_item), not authored in world data. Monotonic
        per world, so ids never collide and offline tests are reproducible; skips any
        id already taken, in case authored content shares the prefix."""
        while True:
            self._id_counter += 1
            candidate = f"{prefix}_{self._id_counter}"
            if candidate not in self.entities and candidate not in self.locations:
                return candidate

    def spawn_item(self, name: str, description: str = "",
                   holder_id: str | None = None, portable: bool = True, *,
                   aliases: list | None = None, tier: str = "",
                   tags: list | None = None, theme: str = "") -> Item:
        """Mint and place a new ``Item``, returning it. The World-layer creator behind
        the director's ``spawn_item`` action (so ``loom/action.py`` never imports the
        world model): the item gets a fresh id, is indexed onto its holder via
        ``add_entity`` (a location = the floor, a character = an inventory), and is a
        real, persistent, perceivable thing from that moment — nothing spawn-specific
        downstream. The keyword-only ``aliases`` (model-authored extra nouns) and the
        code-owned ``tier``/``tags``/``theme`` are how the Phase 4 loot forge mints a
        lore-rich, classified item through this same path; the director's plain
        ``spawn_item`` omits them and gets a bare object exactly as before."""
        item = Item(id=self.fresh_id("item"), name=name, description=description,
                    holder=holder_id, portable=portable,
                    aliases=list(aliases or []), tier=tier,
                    tags=list(tags or []), theme=theme)
        self.add_entity(item)
        return item

    def place_item(self, item_id: str, holder_id: str) -> bool:
        """Re-home an item to a new holder — a location, a character, or a
        container item. The single mutator behind take, drop, and give: each is
        just a change of holder, keeping the reverse index consistent. Returns
        False if the item is unknown, not portable, or the holder does not
        exist — nothing moves on a bad request."""
        item = self.entities.get(item_id)
        if not isinstance(item, Item) or not getattr(item, "portable", True):
            return False
        if holder_id not in self.locations and holder_id not in self.entities:
            return False
        old = item.holder
        if old and old in self._contents:
            self._contents[old].discard(item_id)
        item.holder = holder_id
        self._contents.setdefault(holder_id, set()).add(item_id)
        return True

    def move(self, entity_id: str, dest_id: str) -> bool:
        ent = self.entities.get(entity_id)
        if not isinstance(ent, Character) or dest_id not in self.locations:
            return False
        if ent.location_id and ent.location_id in self.locations:
            self.locations[ent.location_id].occupants.discard(entity_id)
        ent.location_id = dest_id
        self.locations[dest_id].occupants.add(entity_id)
        return True

    # --- queries ---
    def occupants(self, location_id: str, exclude: str | None = None) -> list[Entity]:
        loc = self.locations.get(location_id)
        if not loc:
            return []
        return [self.entities[i] for i in loc.occupants
                if i in self.entities and i != exclude]

    def players_in(self, location_id: str) -> list[Player]:
        """The connected players present in a location — the audience a
        location-addressed director action (offer_quest) reaches. Only ``Player``s
        (session-bound), never NPCs: a quest is a goal for someone playing, and it is
        written into that player's own log."""
        return [e for e in self.occupants(location_id) if isinstance(e, Player)]

    def location_of(self, entity_id: str) -> Location | None:
        ent = self.entities.get(entity_id)
        loc_id = getattr(ent, "location_id", None)
        return self.locations.get(loc_id) if loc_id else None

    def contents(self, holder_id: str) -> list[Item]:
        """The items a holder currently holds (a location's floor, a
        character's inventory, a container's insides). Sorted by name for
        stable display and deterministic tests."""
        ids = self._contents.get(holder_id, ())
        items = [self.entities[i] for i in ids
                 if isinstance(self.entities.get(i), Item)]
        return sorted(items, key=lambda i: i.name)

    def scope(self, actor_id: str) -> list[Entity]:
        """Everything an actor can currently see and refer to: the other
        occupants of its room, the items lying in that room, and the items it
        is itself holding. This is the candidate set for name-resolution — the
        same set a command parser (player or NPC) resolves a noun phrase
        against (loom/naming.py)."""
        actor = self.entities.get(actor_id)
        loc_id = getattr(actor, "location_id", None)
        result: list = []
        if loc_id:
            result.extend(self.occupants(loc_id, exclude=actor_id))
            result.extend(self.contents(loc_id))
        result.extend(self.contents(actor_id))
        return result
