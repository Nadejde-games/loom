"""The action seam: how a mind's *intent* becomes a safe world change.

The golden rule (design commitment #4): never execute raw model text. A mind
*proposes* actions; nothing here trusts them. Every proposed action is validated
against a registered schema before its handler is allowed to touch the ``World``.
The mind proposes; the engine disposes.

This module is game-agnostic and dependency-free. The engine registers the
built-in actions; a game — or the game-master (Phase 3) and loot forge (Phase 4)
— registers more against the same ``ActionRegistry`` without editing ``loom/``.
Validation is a few lines of stdlib type-checking, deliberately not pydantic or
jsonschema, so the core keeps zero dependencies.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from .naming import resolve, Resolved


class ActionError(RuntimeError):
    """A proposed action failed validation or execution."""


# --- parameter schema -------------------------------------------------------

# Declared type name -> the Python type(s) an argument must satisfy.
_TYPES: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
}


@dataclass(frozen=True)
class Param:
    """One argument of an action's schema."""
    type: str = "str"                 # str | int | float | bool | enum
    required: bool = True
    choices: tuple | None = None      # required when type == "enum"
    desc: str = ""


@dataclass
class ActionContext:
    """Everything a handler needs: who acts, with what args, in what world.

    ``world`` and ``actor`` are typed ``Any`` on purpose — this module must not
    import ``loom.world`` (it is the lower layer). Handlers know the concrete
    types.
    """
    world: Any
    actor: Any
    args: dict


@dataclass
class ActionResult:
    """The observable outcome of an executed action."""
    narration: str = ""               # room-visible line, e.g. "Odd nods slowly"
    actor_memory: str | None = None   # what the actor remembers having done
    # Room-targeted lines for actions that touch more than the actor's current
    # room — a list of (location_id, text). ``move`` uses this to narrate a
    # departure to the room left behind and an arrival to the room entered.
    # Generalises to any number of rooms without further changes to the seam.
    broadcasts: list = field(default_factory=list)


Handler = Callable[[ActionContext], ActionResult]


@dataclass
class ActionSpec:
    """A registered action: its schema and the handler that performs it."""
    name: str
    description: str
    params: dict            # arg name -> Param
    handler: Handler


@dataclass
class ActionIntent:
    """A *validated* proposal to act — safe for the engine to execute."""
    name: str
    args: dict


# --- validation -------------------------------------------------------------

def _check_type(action: str, pname: str, p: Param, value: Any) -> list[str]:
    if p.type == "enum":
        if p.choices and value not in p.choices:
            return [f'action "{action}": arg "{pname}" must be one of '
                    f'{list(p.choices)}, got {value!r}']
        return []
    expected = _TYPES.get(p.type)
    if expected is None:                       # unknown declared type: don't block
        return []
    # bool is a subclass of int; keep an int/float arg from silently taking a bool.
    if p.type in ("int", "float") and isinstance(value, bool):
        return [f'action "{action}": arg "{pname}" must be {p.type}, got bool']
    if not isinstance(value, expected):
        return [f'action "{action}": arg "{pname}" must be {p.type}, '
                f'got {type(value).__name__}']
    return []


def _param_sig(name: str, p: Param) -> str:
    t = "|".join(p.choices) if (p.type == "enum" and p.choices) else p.type
    return f'{name}{"" if p.required else "?"}: {t}'


# Declared type name -> the JSON Schema fragment that constrains an argument.
# The mirror of ``_TYPES`` (which validates *after* the fact) for the token-level
# constraint: same Param specs, one for checking, one for the grammar.
_JSON_TYPES: dict[str, dict] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
}


def _param_json_schema(p: Param) -> dict:
    """One argument's JSON Schema fragment, from the same ``Param`` ``validate``
    reads. Shape only — no descriptions; the prompt catalogue carries semantics,
    the grammar carries structure, so it stays lean."""
    if p.type == "enum":
        return {"enum": list(p.choices)} if p.choices else {"type": "string"}
    # Unknown declared types don't constrain (mirrors _check_type not blocking).
    return dict(_JSON_TYPES.get(p.type, {}))


class ActionRegistry:
    """The set of actions the world will accept, keyed by name.

    Holds two halves of the seam: the *schema* (used by a mind to validate its
    own proposals, purely, with no world access) and the *handler* (used by the
    engine to actually mutate the world). Registering is additive and composable.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ActionSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def validate(self, name: Any, args: Any) -> list[str]:
        """Return a list of human-readable errors; empty means valid.

        The error strings are fed straight back to the model on retry, so they
        are phrased to be actionable.
        """
        spec = self._specs.get(name) if isinstance(name, str) else None
        if spec is None:
            valid = ", ".join(self._specs) or "(none)"
            return [f'unknown action "{name}"; valid actions: {valid}']
        if not isinstance(args, dict):
            return [f'action "{name}": args must be an object, '
                    f'got {type(args).__name__}']
        errors: list[str] = []
        for pname, p in spec.params.items():
            if pname not in args:
                if p.required:
                    errors.append(f'action "{name}": missing required arg "{pname}"')
                continue
            errors += _check_type(name, pname, p, args[pname])
        for extra in args:
            if extra not in spec.params:
                errors.append(f'action "{name}": unknown arg "{extra}"')
        return errors

    def _selected(self, names: list | None) -> list:
        """The specs to expose, optionally narrowed to a subset of names (in
        registration order). A subset is how one actor is *offered* fewer actions
        than the registry holds — the player sees `take`/`drop`, a given NPC need
        not — without a second registry. Unknown names are skipped, not an error.
        """
        if names is None:
            return list(self._specs.values())
        wanted = set(names)
        return [s for n, s in self._specs.items() if n in wanted]

    def describe(self, names: list | None = None) -> str:
        """A compact catalogue for the system prompt, optionally narrowed to a
        subset of action names."""
        specs = self._selected(names)
        if not specs:
            return "(no actions available)"
        lines = []
        for spec in specs:
            sig = ", ".join(_param_sig(n, p) for n, p in spec.params.items()) or "no args"
            lines.append(f"- {spec.name}({sig}): {spec.description}")
        return "\n".join(lines)

    def _action_json_schema(self, spec: ActionSpec) -> dict:
        """One action as a JSON Schema branch: its ``name`` pinned to a const and
        its ``args`` an object of the exact typed params ``validate`` enforces."""
        props = {pname: _param_json_schema(p) for pname, p in spec.params.items()}
        required = [pname for pname, p in spec.params.items() if p.required]
        args_schema: dict = {
            "type": "object",
            "properties": props,
            "additionalProperties": False,
        }
        if required:
            args_schema["required"] = required
        return {
            "type": "object",
            "properties": {"name": {"const": spec.name}, "args": args_schema},
            "required": ["name", "args"],
            "additionalProperties": False,
        }

    def json_schema(self, names: list | None = None) -> dict:
        """The turn envelope as a JSON Schema, for grammar-constrained decoding.

        The token-level twin of ``describe()`` + ``validate()``: both are rendered
        from the same registered ``Param`` specs, so the shape the model is *forced*
        to emit and the shape the engine *checks* can never drift. A turn is
        ``{"speech": <string>, "actions": [<one of the registered actions>...]}``;
        each action's ``name`` is a const discriminator over a ``oneOf`` and its
        ``args`` are the typed, required parameters. Guarantees shape, never choice
        — which action fits, or whether to stay silent, is still the prompt's job.

        ``names`` narrows the grammar to the same subset ``describe(names)`` shows
        an actor, so the shape it is constrained to and the catalogue it is told
        about stay one and the same.
        """
        specs = self._selected(names)
        if specs:
            actions_schema = {
                "type": "array",
                "items": {"oneOf": [self._action_json_schema(s) for s in specs]},
            }
        else:
            # No actions registered: the only valid ``actions`` value is empty.
            actions_schema = {"type": "array", "maxItems": 0}
        return {
            "type": "object",
            "properties": {
                "speech": {"type": "string"},
                "actions": actions_schema,
            },
            "required": ["speech", "actions"],
            "additionalProperties": False,
        }


# --- built-in, game-agnostic actions ---------------------------------------

def _emote(ctx: ActionContext) -> ActionResult:
    """A purely expressive action: visible gesture, no world-state change.

    The tightest possible proof of the seam — it exercises schema, validation,
    execution, room broadcast, and the actor's memory without touching the
    world model.
    """
    text = str(ctx.args["text"]).strip()
    name = getattr(ctx.actor, "name", "Someone")
    return ActionResult(narration=f"{name} {text}", actor_memory=f"I {text}.")


def _move(ctx: ActionContext) -> ActionResult:
    """Walk the actor through one of its current room's exits.

    The first action that mutates real world state and speaks to *two* rooms —
    a departure line to the room left behind and an arrival line to the room
    entered. The schema only guaranteed ``direction`` is a string; this handler
    is the world-aware gate that resolves it against the actor's actual exits,
    raising ``ActionError`` (which the engine drops silently) when it doesn't
    fit. Nothing here trusts the direction until the world confirms it.
    """
    direction = str(ctx.args["direction"]).strip().lower()
    world, actor = ctx.world, ctx.actor
    aid = getattr(actor, "id", "actor")
    origin_id = getattr(actor, "location_id", None)
    origin = world.locations.get(origin_id) if origin_id else None
    if origin is None or direction not in getattr(origin, "exits", {}):
        raise ActionError(f'{aid} has no exit "{direction}" from {origin_id!r}')
    dest_id = origin.exits[direction]
    dest = world.locations.get(dest_id)
    if dest is None:
        raise ActionError(f'exit "{direction}" leads nowhere ({dest_id!r})')
    if not world.move(aid, dest_id):
        raise ActionError(f'{aid} could not move to {dest_id!r}')
    name = getattr(actor, "name", "Someone")
    # Arrival direction: the destination's own exit that points back to origin,
    # so asymmetric / one-way worlds narrate correctly rather than assuming a
    # fixed opposite. None ⇒ a one-way passage, so we narrate a plain arrival.
    back = next((d for d, t in dest.exits.items() if t == origin_id), None)
    arrival = f"{name} arrives from the {back}." if back else f"{name} arrives."
    return ActionResult(
        actor_memory=f"I left {origin.name} and went {direction} to {dest.name}.",
        broadcasts=[
            (origin_id, f"{name} leaves, heading {direction}."),
            (dest_id, arrival),
        ],
    )


def _give_item(ctx: ActionContext) -> ActionResult:
    """Hand an item the actor is holding to another character present with it.

    The seam in miniature for inventory, and the first *multi-object* action:
    the schema only guaranteed two strings; this handler resolves them against
    the real world — the item against what the actor actually holds, the
    recipient against who is actually present — and refuses (``ActionError``,
    which the engine drops) when either is unknown or ambiguous. Nothing moves
    on the model's say-so; both ends are confirmed against the world first.
    """
    world, actor = ctx.world, ctx.actor
    aid = getattr(actor, "id", "actor")
    item_phrase = str(ctx.args["item"]).strip()
    who_phrase = str(ctx.args["recipient"]).strip()

    r_item = resolve(item_phrase, world.contents(aid))
    if not isinstance(r_item, Resolved):
        raise ActionError(f'{aid} holds no single clear "{item_phrase}" to give')

    loc_id = getattr(actor, "location_id", None)
    present = world.occupants(loc_id, exclude=aid) if loc_id else []
    r_who = resolve(who_phrase, present)
    if not isinstance(r_who, Resolved):
        raise ActionError(f'no single clear "{who_phrase}" here to receive it')

    item, recipient = r_item.entity, r_who.entity
    if not world.place_item(item.id, recipient.id):
        raise ActionError(f'{aid} could not give {item.id!r} to {recipient.id!r}')

    aname = getattr(actor, "name", "Someone")
    # Item names carry their own article ("a brass key"), so we never prepend
    # another — "gives a brass key to", not "gives the a brass key to".
    return ActionResult(
        narration=f"{aname} gives {item.name} to {recipient.name}.",
        actor_memory=f"I gave {item.name} to {recipient.name}.",
    )


def _take_item(ctx: ActionContext) -> ActionResult:
    """Pick up an item — from the floor of the actor's room, or from a source
    (a character or container present) when one is named.

    The counterpart of ``give_item`` on the taking side, and the same seam
    guarantee: the schema promises the phrases; this handler resolves them
    against the real world — the source against what is present, the item
    against what that source actually holds — and refuses (``ActionError``) when
    either is unknown, ambiguous, or not portable. Backs the player's take verb,
    and any world that chooses to offer it to NPCs.
    """
    world, actor = ctx.world, ctx.actor
    aid = getattr(actor, "id", "actor")
    loc_id = getattr(actor, "location_id", None)
    item_phrase = str(ctx.args["item"]).strip()
    source_phrase = str(ctx.args.get("source", "")).strip()

    if source_phrase:
        present = world.occupants(loc_id, exclude=aid) if loc_id else []
        floor = world.contents(loc_id) if loc_id else []
        r_src = resolve(source_phrase, list(present) + list(floor))
        if not isinstance(r_src, Resolved):
            raise ActionError(f'no single clear "{source_phrase}" here to take from')
        source_id, src_name = r_src.entity.id, r_src.entity.name
    else:
        source_id, src_name = loc_id, None   # from the floor of the room

    pool = world.contents(source_id) if source_id else []
    r_item = resolve(item_phrase, pool)
    if not isinstance(r_item, Resolved):
        raise ActionError(f'no single clear "{item_phrase}" to take')
    item = r_item.entity
    if not world.place_item(item.id, aid):
        raise ActionError(f'{aid} could not take {item.id!r} (not portable?)')

    name = getattr(actor, "name", "Someone")
    tail = f" from {src_name}" if src_name else ""
    return ActionResult(narration=f"{name} takes {item.name}{tail}.",
                        actor_memory=f"I took {item.name}{tail}.")


def _drop_item(ctx: ActionContext) -> ActionResult:
    """Set down an item the actor is holding onto the floor of its room.

    Symmetric with ``take_item``: resolves the item against the actor's own
    inventory and re-homes it to the room, refusing when the actor holds no such
    single clear item.
    """
    world, actor = ctx.world, ctx.actor
    aid = getattr(actor, "id", "actor")
    loc_id = getattr(actor, "location_id", None)
    item_phrase = str(ctx.args["item"]).strip()

    r_item = resolve(item_phrase, world.contents(aid))
    if not isinstance(r_item, Resolved):
        raise ActionError(f'{aid} holds no single clear "{item_phrase}" to drop')
    item = r_item.entity
    if not loc_id or not world.place_item(item.id, loc_id):
        raise ActionError(f'{aid} could not drop {item.id!r}')

    name = getattr(actor, "name", "Someone")
    return ActionResult(narration=f"{name} drops {item.name}.",
                        actor_memory=f"I dropped {item.name}.")


def _stage_event(ctx: ActionContext) -> ActionResult:
    """Narrate an ambient beat into a room — the game-master's lightest touch.

    The director's counterpart of ``emote``: the tightest proof of *its* seam. It
    has no body and acts *on* a room rather than from one, so — unlike every actor
    action above — the target location is an explicit argument, not the actor's
    own position. The schema only guaranteed two strings; this handler is the
    gate: it confirms the named location actually exists before anything is said,
    raising ``ActionError`` (which the engine drops) otherwise. No world state
    changes — the beat is pure narration broadcast to whoever is present, exactly
    where the director aimed it. Its stronger, world-mutating siblings (spawn a
    thing, offer a quest) build on this same location-addressed shape.
    """
    world = ctx.world
    location_id = str(ctx.args["location"]).strip()
    text = str(ctx.args["text"]).strip()
    if location_id not in getattr(world, "locations", {}):
        raise ActionError(f'no such location "{location_id}" to stage a beat in')
    if not text:
        raise ActionError("stage_event needs a non-empty line to narrate")
    return ActionResult(
        broadcasts=[(location_id, text)],
        actor_memory=f'I set a scene in {location_id}: "{text}"',
    )


def default_registry() -> ActionRegistry:
    """A registry preloaded with the built-in actions every world gets."""
    reg = ActionRegistry()
    reg.register(ActionSpec(
        name="emote",
        description=('perform a brief, visible physical action or gesture that '
                     'does not move you or change the world; give the action '
                     'text only, as a third-person verb phrase without your own '
                     'name, e.g. "nods slowly" or "narrows her eyes". To go '
                     'somewhere, use move, not this.'),
        params={"text": Param("str", required=True,
                              desc="the gesture as a verb phrase, no name")},
        handler=_emote,
    ))
    reg.register(ActionSpec(
        name="move",
        description=('walk to an adjacent place through one of the exits in your '
                     'surroundings; give the direction only, e.g. "north" or '
                     '"down". Use only an exit you can currently see.'),
        params={"direction": Param("str", required=True,
                                   desc="a single exit direction from your "
                                        "current location, e.g. north")},
        handler=_move,
    ))
    reg.register(ActionSpec(
        name="give_item",
        description=('hand over an item you are holding to another character '
                     'here with you; name the item and the recipient, e.g. '
                     'item "brass key", recipient "Odd". Only give something you '
                     'actually hold, to someone actually present.'),
        params={
            "item": Param("str", required=True,
                          desc="the item you are holding, by name"),
            "recipient": Param("str", required=True,
                               desc="who receives it — a character present "
                                    "with you, by name"),
        },
        handler=_give_item,
    ))
    reg.register(ActionSpec(
        name="take_item",
        description=('pick up an item from the floor here, or from a source '
                     'present with you; name the item, and optionally the source, '
                     'e.g. item "brass key", or item "map", source "Wren". Only '
                     'take something that is actually here.'),
        params={
            "item": Param("str", required=True,
                          desc="the item to pick up, by name"),
            "source": Param("str", required=False,
                            desc="optional: who or what to take it from, by name; "
                                 "omit to take from the floor"),
        },
        handler=_take_item,
    ))
    reg.register(ActionSpec(
        name="drop_item",
        description=('set down an item you are holding onto the floor here; name '
                     'the item, e.g. item "lantern". Only drop something you '
                     'actually hold.'),
        params={"item": Param("str", required=True,
                              desc="the item you are holding, by name")},
        handler=_drop_item,
    ))
    reg.register(ActionSpec(
        name="stage_event",
        description=('as the unseen game-master, narrate a brief ambient beat '
                     'into a place — a sound, a shift in weather or light, a sign '
                     'the world is alive; name the place by its id and give the '
                     'line as it should read to those present, e.g. location '
                     '"clearing", text "A cold wind gutters the lanterns.". Sets '
                     'the scene only; it moves and changes nothing.'),
        params={
            "location": Param("str", required=True,
                              desc="the id of the place to narrate into, as shown "
                                   "in your view of the world"),
            "text": Param("str", required=True,
                          desc="the ambient line, as those present will read it"),
        },
        handler=_stage_event,
    ))
    return reg
