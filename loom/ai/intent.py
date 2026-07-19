"""The free-text intent parser (B1b): a player's prose -> one game command.

The AI-layer counterpart of the syntactic ``loom/command.py``. When the
deterministic parser doesn't recognise the verb, this asks the model to map the
words onto one real command — constrained by the command grammar so it *cannot*
invent a verb or a field — and returns the canonical verb and its object phrases.
The engine turns that into the same ``Parse`` the deterministic parser produces,
so everything downstream (resolution, disambiguation, the seam) is identical.

World-free, like ``command.py`` and ``naming.py``: it is handed a rendered
catalogue and a context string, never the ``World``.
"""
from __future__ import annotations

from .provider import LLMProvider
from .mind import _extract_json   # shared tolerant single-object JSON extraction


def _system_prompt(catalogue: str, context: str) -> str:
    return (
        "You translate a player's free-text input in a text adventure into exactly "
        "one game command. Read the player's INTENT — the action they mean to take — "
        "and pick the single listed command that performs it. Fill each object with "
        "the plain name of the thing meant, drawn from the surroundings; do not invent "
        "things that are not present. Do NOT fall back to 'look': choose 'look', "
        "'inventory', 'who' or 'quests' only when the player truly means to observe or "
        "check, never as a default when a verb of action fits better.\n\n"
        "Reply with a single JSON object and nothing else, in this shape:\n"
        '{"command": {"verb": "<one listed verb>", "dobj": "<object, if any>", '
        '"iobj": "<second object, if any>"}}\n\n'
        "Commands you may choose from:\n" + catalogue + "\n\n"
        "Map the intent, not the exact words — the phrasing varies, the action is "
        "what matters. Examples:\n"
        '- "grab the brass key" -> {"command": {"verb": "take", "dobj": "brass key"}}\n'
        '- "hand the map to Odd" -> {"command": {"verb": "give", "dobj": "map", '
        '"iobj": "Odd"}}\n'
        '- "walk east" -> {"command": {"verb": "go", "dobj": "east"}}\n'
        '- "inspect the old door" -> {"command": {"verb": "examine", "dobj": '
        '"old door"}}\n'
        '- "set the lantern down" -> {"command": {"verb": "drop", "dobj": "lantern"}}\n\n'
        "The player's surroundings right now:\n" + context
    )


async def interpret(provider: LLMProvider, schema: dict, catalogue: str,
                    context: str, text: str) -> tuple | None:
    """Map free text onto a command. Returns ``(verb, dobj, iobj)`` (strings, the
    latter two possibly empty), or ``None`` when nothing command-shaped comes back
    (an unconstrained backend that just chatted, or the FakeProvider) — the engine
    then falls back to its "unknown command" message. Never raises on a bad reply.
    """
    system = _system_prompt(catalogue, context)
    messages = [{"role": "user", "content": text}]
    try:
        raw = await provider.complete(system, messages, schema=schema)
    except Exception as exc:
        # Degrade to None (the caller shows "unknown command") — but SURFACE it. A
        # silent None here made a transient provider error (e.g. a rate-limit) look
        # identical to a genuine "couldn't map it", which masked a throttle as a
        # behaviour failure in the harness. Print so the two are never confused again.
        print(f"[loom] intent interpret failed ({type(exc).__name__}: {exc}); "
              "treating as unmapped")
        return None
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("command")
    if not isinstance(cmd, dict):          # tolerate an unwrapped command object
        cmd = obj
    verb = cmd.get("verb")
    if not isinstance(verb, str) or not verb:
        return None
    dobj = cmd.get("dobj")
    iobj = cmd.get("iobj")
    return (verb,
            dobj.strip() if isinstance(dobj, str) else "",
            iobj.strip() if isinstance(iobj, str) else "")
