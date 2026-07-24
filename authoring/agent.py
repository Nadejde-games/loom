"""The authoring agent (Phase 8, slice 3): build a world by asking.

A SEPARATE stack from the engine's minds (decision 2026-07-22): this reaches
``qwen/qwen3.6-27b`` over OpenRouter through **Pydantic-AI** with **native tool-calling** —
not the engine's slim ``loom.ai.provider`` waist. It is free to carry the heavier deps that
buys (the ``authoring`` extra); the framework core never imports it.

The division of labour is the safety story:

  * **The tools are thin adapters over the pure ``loom/`` capabilities.** Reads
    (``zone_survey`` / ``world_search`` / ``preview_play``) run free. Writes
    (``region_author`` / ``entity_edit`` / ``entity_spawn``) never mutate anything — they
    build a candidate, run it through the ``loom.worlddraft`` gate (``propose``), and, only
    if it surveys clean, **stage it for the human's confirmation**. ``world_revalidate``
    reports structural health.
  * **The agent PROPOSES; the human DISPOSES.** ``commit`` is not a tool — it is a
    :class:`AuthoringSession` method the UI calls after the author confirms a diff. So the
    agent has *no path* to change the staged world; the atlas + the gate are the sole merge
    authority, exactly as in tested ``loom/`` code and never on the model's word.

Offline-testable to the hilt: Pydantic-AI's ``FunctionModel`` drives the whole agent with
scripted tool calls and no network, so the loop, the read-free/write-gated split, and the
propose→confirm flow are all exercised in the unittest gate (see ``tests/test_agent.py``).
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext

from loom import explore
from loom.ai.provider import FakeProvider
from loom.sandbox import Sandbox
from loom.worlddraft import (
    Draft, edit_entity, spawn_entity, fold_region, propose, commit, undo,
)

AGENT_MODEL = "qwen/qwen3.6-27b"          # the GM/director tier — a planning/tool-use task
# Bound the ReAct loop against a runaway/read spiral, but leave headroom for legitimate
# grounding (a search + a survey) THEN a write THEN a bounded retry — 8 proved too tight (a
# backstory edit that grounded a few times exhausted it before proposing). Raised 2026-07-23.
_REQUEST_LIMIT = 20


# --------------------------------------------------------------------------- session
@dataclass
class AuthoringSession:
    """Everything a run of the agent needs — and the human-driven side of the gate.

    ``draft`` is the staged world (the agent's tools read and propose against it). ``pending``
    is the single validated-but-unconfirmed :class:`~loom.worlddraft.Proposal` (the agent
    proposes one change at a time; ``confirm``/``reject`` are the human's move). The two
    ``*_provider`` slots are the *engine's* LLM providers — a loom provider for region
    authoring (the GM tier) and one for ``preview_play`` — distinct from the agent's own
    Pydantic-AI model; None degrades to the offline fake."""
    draft: Draft
    author_provider: object = None        # loom LLMProvider for region_author (GM tier)
    engine_provider: object = None        # loom LLMProvider for preview_play
    world_path: str | None = None
    pending: object = None                # loom.worlddraft.Proposal | None

    @classmethod
    def from_path(cls, path: str, **kw) -> "AuthoringSession":
        return cls(draft=Draft.from_path(path), world_path=path, **kw)

    # ---- the human's side of the gate ----
    def confirm(self) -> bool:
        """Commit the pending proposal into the staged world (pushing an undo checkpoint).
        Returns True on commit, False if there was nothing pending or it was refused."""
        if self.pending is None:
            return False
        ok = commit(self.draft, self.pending)
        self.pending = None
        return ok

    def reject(self) -> bool:
        """Discard the pending proposal unchanged. Returns True if there was one."""
        had = self.pending is not None
        self.pending = None
        return had

    def undo(self) -> bool:
        """Pop the last committed change off the staged world's undo stack."""
        return undo(self.draft)

    def save(self, path: str | None = None) -> str:
        """Write the staged world to ``path`` (default ``world_path``) as a full ``world.json``
        — the explicit save. The authored file is untouched until this is called."""
        path = path or self.world_path
        if not path:
            raise ValueError("no path to save the world to")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.draft.to_world_json(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        return path


# --------------------------------------------------------------------- prose helpers
def _summary_line(view) -> str:
    s = view.summary()
    return (f"{s['locations']} rooms · {s['npcs']} npcs · {s['items']} items · "
            f"reachable {s['reachable']}/{s['locations']} · "
            f"{s['errors']} error(s) · {s['warnings']} warning(s)")


def _findings_prose(findings) -> str:
    """Atlas findings as human-readable remediation lines (small models act on prose far more
    reliably than on tracebacks — the tool-design research)."""
    if not findings:
        return "No findings — structurally clean."
    return "\n".join(f"[{f.severity}] {f.code} @ {f.where or 'world'}: {f.message}"
                     for f in findings)


def _propose_and_stash(session: AuthoringSession, candidate: dict, what: str) -> str:
    """The shared tail of every write tool: run the candidate through the gate, and — only if
    it surveys clean — stage it for confirmation. A dirty candidate is NEVER staged; its
    findings are returned so the agent can fix and re-propose. Nothing is ever committed here."""
    if session.pending is not None:
        return ("A change is already proposed and awaiting confirmation. Ask the author to "
                "confirm or reject it before proposing another.")
    p = propose(session.draft, candidate)
    if not p.ok:
        return (f"The change to {what} did NOT survey clean, so it was NOT applied:\n"
                + _findings_prose(p.view.errors)
                + "\nRevise it and propose again.")
    session.pending = p
    diff = "\n".join(p.diff) or "(no structural change)"
    warns = p.view.warnings
    note = ("\nWarnings (allowed, but worth a glance): "
            + "; ".join(f"{f.code}@{f.where or 'world'}" for f in warns)) if warns else ""
    return (f"Proposed change to {what} — surveys clean. Nothing has changed yet.\n"
            f"Diff:\n{diff}{note}\nAwaiting the author's confirmation to apply.")


# --------------------------------------------------------------------------- the tools
async def zone_survey(ctx: RunContext[AuthoringSession], room_id: str = "") -> str:
    """Survey the staged world, or one room's neighbourhood when ``room_id`` is given. Returns
    a summary, structural findings, and (for a room) its exits, badges, and contents. A READ —
    it changes nothing."""
    view = ctx.deps.draft.view()
    if not room_id:
        return _summary_line(view) + "\n" + _findings_prose(view.findings)
    node = explore.map_model(view).node(room_id)
    if node is None:
        return f'No room "{room_id}". Use world_search to find room ids.'
    lines = [f"{node.name} [{node.id}]" + (" (start)" if node.is_start else "")]
    lines.append("Exits: " + (", ".join(f"{e.direction}->{e.target}" for e in node.exits)
                              or "none"))
    if node.flags:
        lines.append("Flags: " + ", ".join(node.flags))
    lines.append(f"Here: {node.occupants} character(s), {node.items} item(s) on the floor.")
    return "\n".join(lines)


async def world_search(ctx: RunContext[AuthoringSession], query: str,
                       kinds: str = "") -> str:
    """Search the world's entities (rooms, npcs, items) by id / name / alias / tag /
    description. ``kinds`` optionally narrows to a comma-list of room/npc/item. A READ."""
    view = ctx.deps.draft.view()
    want = tuple(k.strip() for k in kinds.split(",") if k.strip()) if kinds else ()
    hits = explore.search(view, query, kinds=want, limit=15)
    if not hits:
        return f'No matches for "{query}".'
    return "\n".join(f"- {h.kind} {h.id} ({h.name})"
                     + (f" — in {h.where}" if h.where else "") for h in hits)


async def preview_play(ctx: RunContext[AuthoringSession], room_id: str,
                       line: str = "look") -> str:
    """Boot the game at ``room_id`` in a throwaway sandbox on the STAGED world and run one
    command (default 'look'), returning the transcript. A READ — the world is not touched
    (the sandbox runs on a deep copy and writes nothing)."""
    s = ctx.deps
    if room_id not in {l["id"] for l in s.draft.locations}:
        return f'No room "{room_id}" to play from. Use world_search to find room ids.'
    out: list = []

    def _emit(text, system=False):
        out.append(("· " + text) if system else text)

    sb = Sandbox(s.draft.world(), room_id, s.engine_provider or FakeProvider(), _emit)
    await sb.start()
    if line and line.strip().lower() != "look":
        await sb.send(line)
        await sb.drain(timeout=30.0)
    await sb.close()
    return "\n".join(out) or "(no output)"


async def region_author(ctx: RunContext[AuthoringSession], brief: str,
                        attach_to: str = "") -> str:
    """Author a whole new region from a prose brief and propose folding it into the world at
    ``attach_to`` (a room id; default the start). Use this for a new AREA — several rooms with
    their inhabitants — not a single entity. Proposes a validated change for confirmation."""
    from loom.ai.author import author_region
    s = ctx.deps
    if s.author_provider is None:
        return "Region authoring needs a live authoring model, which is not configured here."
    anchor = attach_to or s.draft.start
    if anchor not in {l["id"] for l in s.draft.locations}:
        return f'No room "{anchor}" to attach to. Use world_search to find a room id.'
    result = await author_region(s.author_provider, brief, base_world=s.draft.world(),
                                 start=s.draft.start, attach_to=anchor)
    if not result.ok:
        return f"Could not author a clean region: {result.reason}"
    cand = fold_region(s.draft, result.region, result.attach)
    return _propose_and_stash(s, cand, f"a new region attached to {anchor}")


async def entity_edit(ctx: RunContext[AuthoringSession], entity_id: str,
                      name: str = "", description: str = "", backstory: str = "",
                      voice: str = "", traits: str = "", goals: str = "") -> str:
    """Propose an edit to an existing entity. ``name`` and ``description`` apply to a room, npc,
    or item. ``backstory``, ``voice``, ``traits``, ``goals`` are an NPC's inner life and apply to
    a character only — they are MERGED into its existing persona, not replaced (so adding a
    backstory keeps its traits). ``traits`` and ``goals`` are comma-separated lists. Does NOT
    change the world — it validates the edit and stages it for confirmation."""
    draft = ctx.deps.draft
    patch = {}
    if name:
        patch["name"] = name
    if description:
        patch["description"] = description
    persona_updates = {}
    if backstory:
        persona_updates["backstory"] = backstory
    if voice:
        persona_updates["voice"] = voice
    if traits:
        persona_updates["traits"] = [t.strip() for t in traits.split(",") if t.strip()]
    if goals:
        persona_updates["goals"] = [g.strip() for g in goals.split(",") if g.strip()]
    if persona_updates:
        npc = next((n for n in draft.npcs if n["id"] == entity_id), None)
        if npc is None:
            return (f'"{entity_id}" is not a character, so it has no backstory/voice/traits/'
                    "goals — those apply to npcs only. Use world_search to find the npc's id.")
        merged = dict(npc.get("persona") or {})
        merged.update(persona_updates)                 # merge — keep the fields not being edited
        patch["persona"] = merged
    if not patch:
        return "Nothing to edit — give a new name, description, or (for an npc) a persona detail."
    try:
        cand = edit_entity(draft, entity_id, patch)
    except KeyError:
        return f'No entity "{entity_id}". Use world_search to find its id.'
    return _propose_and_stash(ctx.deps, cand, entity_id)


async def entity_spawn(ctx: RunContext[AuthoringSession], kind: str, name: str,
                       where: str, description: str = "") -> str:
    """Propose spawning a new npc or item into an existing room. ``kind`` is 'npc' or 'item';
    ``where`` is the room id it appears in. Does NOT change the world — it validates and stages
    the addition for confirmation."""
    if kind not in ("npc", "item"):
        return "kind must be 'npc' or 'item'."
    cand, new_id = spawn_entity(ctx.deps.draft, kind, name=name,
                                description=description, where=where)
    return _propose_and_stash(ctx.deps, cand, f'new {kind} "{name}" ({new_id})')


async def world_revalidate(ctx: RunContext[AuthoringSession]) -> str:
    """Re-survey the whole staged world and report its structural health — the summary and
    every finding. A READ."""
    view = ctx.deps.draft.view()
    return _summary_line(view) + "\n" + _findings_prose(view.findings)


_TOOLS = [zone_survey, world_search, preview_play, region_author,
          entity_edit, entity_spawn, world_revalidate]

_SYSTEM = (
    "You are the world-author's assistant for a text-adventure game. You help build and "
    "refine the game world by calling tools — never by inventing world facts yourself.\n\n"
    "How you work:\n"
    "- GROUND FIRST, THEN ACT — do not over-read. One search or survey to find an entity's id "
    "is enough; once you know the id, PROPOSE the change. Never repeat a read you have already "
    "done, and reserve preview_play for when you genuinely need to feel the room in play — it is "
    "not a way to look up an id (world_search is). Never guess ids — look them up once.\n"
    "- To enrich a CHARACTER — a backstory, a way of speaking, traits, or goals — use entity_edit "
    "with its backstory / voice / traits / goals fields (they merge into the npc's persona). That "
    "is the tool for 'give this npc a history / make them superstitious / add a motive'.\n"
    "- You PROPOSE; the author DISPOSES. The write tools (entity_edit, entity_spawn, "
    "region_author) do NOT change the world — they validate a change and stage it for the "
    "author's confirmation. Say plainly what you proposed and that it awaits confirmation; "
    "never claim a change was made when it was only proposed.\n"
    "- Echo the resolved referent: when the author says 'this room' or 'the blacksmith', name "
    "the concrete id you resolved it to.\n"
    "- Right tool for the scope: a whole new area (several rooms and who lives in them) -> "
    "region_author with a prose brief; a single new character or object in an existing room "
    "-> entity_spawn; renaming or rewording one thing -> entity_edit.\n"
    "- One change at a time — propose a single change and let it be confirmed before the next.\n"
    "- Ask a brief clarifying question ONLY when a required detail or a referent is genuinely "
    "ambiguous; otherwise ground, then propose.\n"
    "- If a proposed change does not survey clean, the tool tells you why — fix it and propose "
    "again."
)


# ------------------------------------------------------------------- model / agent / run
def _is_ollama() -> bool:
    """Whether the authoring stack should target a local Ollama server (LOOM_PROVIDER=ollama),
    rather than the hosted OpenRouter default. One switch shared by the agent's own model and
    the tool-tier loom providers, so the whole workbench follows the game's backend choice."""
    return os.environ.get("LOOM_PROVIDER", "").strip().lower() == "ollama"


def _ollama_v1() -> str:
    """The base_url for Ollama's OpenAI-compatible endpoint (LOOM_OLLAMA_HOST + /v1)."""
    return os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1"


def author_model_name() -> str:
    """The director/author tier tag for the current backend — the model that drives the
    authoring agent. On Ollama it is the game-master tier (LOOM_GM_MODEL), falling back to the
    NPC tag then a dense default; on OpenRouter it is the hosted GM slug (AGENT_MODEL)."""
    if _is_ollama():
        return (os.environ.get("LOOM_GM_MODEL") or os.environ.get("LOOM_OLLAMA_MODEL")
                or "qwen3.6:27b")
    return os.environ.get("LOOM_GM_MODEL") or AGENT_MODEL


def build_model(model_name: str | None = None, api_key: str | None = None):
    """The agent's OWN model — Pydantic-AI native tool-calling. The backend follows the game's
    LOOM_PROVIDER: a local Ollama server (``ollama``) via its OpenAI-compatible ``/v1`` endpoint,
    else the hosted OpenRouter default. Thinking is bounded on tool turns (a reasoning model can
    otherwise swallow a tool call into its reasoning channel). Returns None only when the hosted
    path has no key, so a caller can fall back to a fake for offline work. This is the authoring
    stack's provider, NOT ``loom.ai.provider``."""
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
    if _is_ollama():
        # Local backend: point Pydantic-AI's OpenAI model at Ollama's /v1. Qwen routes
        # chain-of-thought into a separate channel counted against max_tokens, so send
        # reasoning_effort "none" to keep the turn on tool-calling (the OllamaProvider does
        # the same for the engine). Local tool-calling quality for authoring is live-verified.
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            model_name or author_model_name(),
            provider=OpenAIProvider(base_url=_ollama_v1(), api_key="ollama"),
            settings=OpenAIChatModelSettings(
                extra_body={"reasoning_effort": "none"}, temperature=0.2),
        )
    from pydantic_ai.providers.openrouter import OpenRouterProvider
    key = (api_key or os.environ.get("OPENROUTER_API_KEY")
           or os.environ.get("LOOM_OPENROUTER_API_KEY"))
    if not key:
        return None
    return OpenAIChatModel(
        model_name or author_model_name(),
        provider=OpenRouterProvider(api_key=key),
        settings=OpenAIChatModelSettings(
            extra_body={"reasoning": {"enabled": False}}, temperature=0.2),
    )


def build_agent(model) -> Agent:
    """The authoring agent over ``model`` (a Pydantic-AI model, or a ``FunctionModel``/
    ``TestModel`` for the offline gate) — the tool catalogue and the one comprehensive rule
    set, typed on :class:`AuthoringSession` deps."""
    return Agent(model, deps_type=AuthoringSession, system_prompt=_SYSTEM,
                 tools=_TOOLS, retries=1)


async def run_turn(agent: Agent, session: AuthoringSession, message: str, *,
                   history=None, request_limit: int = _REQUEST_LIMIT, event_handler=None):
    """Run one author turn — the ReAct loop, bounded by ``request_limit`` model requests
    against a repair spiral. Returns the Pydantic-AI run result (``.output`` is the reply,
    ``.all_messages()`` the transcript to thread into the next turn).

    ``event_handler`` is Pydantic-AI's ``event_stream_handler`` — an
    ``async (RunContext, AsyncIterable[AgentStreamEvent]) -> None`` the UI passes to surface
    live progress (a tool started / returned, streamed text). When given, the run streams its
    model requests; a backend that cannot stream falls back to a plain (non-streamed) turn, so
    progress is a graceful bonus, never a hard requirement."""
    from pydantic_ai.usage import UsageLimits
    limits = UsageLimits(request_limit=request_limit)
    common = dict(deps=session, message_history=history, usage_limits=limits)
    if event_handler is not None:
        try:
            return await agent.run(message, event_stream_handler=event_handler, **common)
        except AssertionError:
            # the model does not support streamed requests (e.g. a non-streaming test double)
            # — degrade to a plain turn: the turn still runs, only the live progress is lost.
            pass
    return await agent.run(message, **common)
