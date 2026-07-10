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

    def describe(self) -> str:
        """A compact catalogue for the system prompt."""
        if not self._specs:
            return "(no actions available)"
        lines = []
        for spec in self._specs.values():
            sig = ", ".join(_param_sig(n, p) for n, p in spec.params.items()) or "no args"
            lines.append(f"- {spec.name}({sig}): {spec.description}")
        return "\n".join(lines)


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


def default_registry() -> ActionRegistry:
    """A registry preloaded with the built-in actions every world gets."""
    reg = ActionRegistry()
    reg.register(ActionSpec(
        name="emote",
        description=('perform a brief, visible physical action or gesture; give '
                     'the action text only, as a third-person verb phrase without '
                     'your own name, e.g. "nods slowly" or "gestures at the path"'),
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
    return reg
