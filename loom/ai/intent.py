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
        "one game command. Choose the single command that best matches the player's "
        "intent, using only the commands listed. Fill each object with the plain "
        "name of the thing meant, drawn from the surroundings; do not invent things "
        "that are not present.\n\n"
        "Reply with a single JSON object and nothing else, in this shape:\n"
        '{"command": {"verb": "<one listed verb>", "dobj": "<object, if any>", '
        '"iobj": "<second object, if any>"}}\n\n'
        "Commands you may choose from:\n" + catalogue + "\n\n"
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
    except Exception:
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
