"""The authoring agent (Phase 8, slice 3) — offline, no network.

Drives the whole agent on Pydantic-AI's ``FunctionModel`` (scripted tool calls) so the loop,
the read-free / write-gated split, and the propose→confirm flow are all exercised with no
model and no OpenRouter call. The live gate (a real model choosing the tools) is
``scripts/behavior_probe.py author-agent``; this is the structural half.
"""
import json
import os
import tempfile
import unittest

try:
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart
    _HAS_PYDANTIC_AI = True
except ImportError:                       # the authoring extra is optional
    _HAS_PYDANTIC_AI = False

from loom.world import World, Location, Npc, Item

if _HAS_PYDANTIC_AI:
    from authoring.agent import AuthoringSession, build_agent, run_turn
    from loom.worlddraft import Draft


def _world() -> World:
    w = World()
    w.add_location(Location(id="hall", name="The Hall", description="A stone hall.",
                            exits={"north": "cave"}))
    w.add_location(Location(id="cave", name="The Cave", description="A damp cave.",
                            exits={"south": "hall"}))
    w.add_entity(Npc(id="keeper", name="The Keeper", description="An old keeper.",
                     location_id="hall", persona={"backstory": "Guards the hall."}))
    w.add_entity(Item(id="key", name="a brass key", description="A worn key.",
                      holder="hall", aliases=["key"]))
    return w


def _scripted(steps, final="Done."):
    """A FunctionModel that emits each ``(tool_name, args)`` in ``steps`` as a native tool
    call, in order, then returns ``final`` as text. The step index is the count of prior model
    responses in the history, so it advances one per round no matter the tool output."""
    def fn(messages, info):
        i = sum(1 for m in messages if isinstance(m, ModelResponse))
        if i < len(steps):
            name, args = steps[i]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart(final)])
    return FunctionModel(fn)


@unittest.skipUnless(_HAS_PYDANTIC_AI, "pydantic-ai (the authoring extra) is not installed")
class AgentToolLoopTests(unittest.IsolatedAsyncioTestCase):
    def _session(self):
        return AuthoringSession(draft=Draft.from_world(_world(), "hall"))

    async def test_read_tool_runs_free_no_pending(self):
        s = self._session()
        agent = build_agent(_scripted([("world_search", {"query": "keeper"})]))
        await run_turn(agent, s, "find the keeper")
        self.assertIsNone(s.pending)              # a read stages nothing

    async def test_clean_edit_stages_then_confirm_commits(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})]))
        await run_turn(agent, s, "rename the hall to The Great Hall")
        self.assertIsNotNone(s.pending)           # staged, not committed
        self.assertTrue(s.pending.ok)
        self.assertEqual({l["id"]: l["name"] for l in s.draft.locations}["hall"],
                         "The Hall")              # staged world unchanged until confirm
        self.assertTrue(s.confirm())
        self.assertIsNone(s.pending)
        self.assertEqual({l["id"]: l["name"] for l in s.draft.locations}["hall"],
                         "The Great Hall")

    async def test_dirty_write_is_not_staged(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_spawn", {"kind": "npc", "name": "Ghost", "where": "void"})]))
        await run_turn(agent, s, "put a ghost in the void")
        self.assertIsNone(s.pending)              # bad-location error -> never staged

    async def test_persona_edit_merges_into_npc(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "keeper", "voice": "hushed and wary"})]))
        await run_turn(agent, s, "make the keeper speak in a hushed way")
        self.assertIsNotNone(s.pending)
        self.assertTrue(s.confirm())
        keeper = next(n for n in s.draft.npcs if n["id"] == "keeper")
        self.assertEqual(keeper["persona"]["voice"], "hushed and wary")     # added
        self.assertEqual(keeper["persona"]["backstory"], "Guards the hall.")  # kept (merged)

    async def test_persona_edit_on_non_npc_is_rejected(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "hall", "backstory": "nope"})]))
        await run_turn(agent, s, "give the hall a backstory")
        self.assertIsNone(s.pending)          # a room has no persona -> nothing staged

    async def test_ground_then_act(self):
        s = self._session()
        agent = build_agent(_scripted([
            ("world_search", {"query": "hall"}),
            ("entity_spawn", {"kind": "item", "name": "a torch", "where": "hall"}),
        ]))
        await run_turn(agent, s, "add a torch to the hall")
        self.assertIsNotNone(s.pending)
        self.assertTrue(any(line.startswith("+ item") for line in s.pending.diff))

    async def test_second_write_refused_while_one_pending(self):
        s = self._session()
        agent = build_agent(_scripted([
            ("entity_spawn", {"kind": "item", "name": "a torch", "where": "hall"}),
            ("entity_spawn", {"kind": "item", "name": "a lamp", "where": "hall"}),
        ]))
        await run_turn(agent, s, "add a torch and a lamp")
        self.assertIsNotNone(s.pending)
        self.assertIn("torch", "\n".join(s.pending.diff))   # first held; second refused
        self.assertNotIn("lamp", "\n".join(s.pending.diff))

    async def test_reject_discards_pending(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "hall", "name": "X"})]))
        await run_turn(agent, s, "rename")
        self.assertIsNotNone(s.pending)
        self.assertTrue(s.reject())
        self.assertIsNone(s.pending)
        self.assertEqual({l["id"] for l in s.draft.locations}, {"hall", "cave"})

    async def test_undo_after_confirm(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "hall", "name": "Renamed"})]))
        await run_turn(agent, s, "rename")
        s.confirm()
        self.assertTrue(s.undo())
        self.assertEqual({l["id"]: l["name"] for l in s.draft.locations}["hall"],
                         "The Hall")

    async def test_preview_play_is_a_read(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("preview_play", {"room_id": "hall", "line": "look"})]))
        await run_turn(agent, s, "let me look around the hall")
        self.assertIsNone(s.pending)              # preview changes nothing

    async def test_save_writes_full_world_json(self):
        s = self._session()
        agent = build_agent(_scripted(
            [("entity_edit", {"entity_id": "hall", "name": "Saved Hall"})]))
        await run_turn(agent, s, "rename")
        s.confirm()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "world.json")
            s.save(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["start_location"], "hall")
        self.assertEqual({l["id"]: l["name"] for l in data["locations"]}["hall"],
                         "Saved Hall")


@unittest.skipUnless(_HAS_PYDANTIC_AI, "pydantic-ai (the authoring extra) is not installed")
class BackendSelectionTests(unittest.TestCase):
    """The authoring stack follows the game's LOOM_PROVIDER: a local Ollama server (no key) or
    hosted OpenRouter. All offline — building a model/provider makes no network call."""

    _KEYS = ("LOOM_PROVIDER", "LOOM_OLLAMA_HOST", "LOOM_OLLAMA_MODEL", "LOOM_GM_MODEL",
             "LOOM_AUTHOR_MODEL", "OPENROUTER_API_KEY", "LOOM_OPENROUTER_API_KEY")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- the agent's own Pydantic-AI model -----------------------------------
    def test_ollama_backend_builds_without_a_key(self):
        from authoring.agent import build_model
        os.environ["LOOM_PROVIDER"] = "ollama"          # and no OPENROUTER_API_KEY
        model = build_model()
        self.assertIsNotNone(model)                     # local path never needs a key
        base_url = str(model._provider.base_url)
        self.assertIn("11434", base_url)
        self.assertIn("/v1", base_url)

    def test_ollama_host_override(self):
        from authoring.agent import build_model
        os.environ["LOOM_PROVIDER"] = "ollama"
        os.environ["LOOM_OLLAMA_HOST"] = "http://gpu-box:9999"
        base_url = str(build_model()._provider.base_url)
        self.assertIn("gpu-box:9999", base_url)
        self.assertIn("/v1", base_url)

    def test_author_model_name_tiers(self):
        from authoring.agent import author_model_name
        os.environ["LOOM_PROVIDER"] = "ollama"
        self.assertEqual(author_model_name(), "qwen3.6:27b")            # dense default
        os.environ["LOOM_OLLAMA_MODEL"] = "qwen3.5:35b-a3b"
        self.assertEqual(author_model_name(), "qwen3.5:35b-a3b")        # falls to NPC tag
        os.environ["LOOM_GM_MODEL"] = "loom-gm"
        self.assertEqual(author_model_name(), "loom-gm")               # director over NPC
        os.environ["LOOM_AUTHOR_MODEL"] = "qwen3.5:27b-mlx"
        self.assertEqual(author_model_name(), "qwen3.5:27b-mlx")       # author var wins over all

    def test_openrouter_without_key_is_none(self):
        from authoring.agent import build_model
        self.assertIsNone(build_model())               # default backend, no key → fake fallback

    def test_openrouter_with_key_builds(self):
        from authoring.agent import build_model
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        self.assertIsNotNone(build_model())

    # -- the loom tool-tier providers (in app.py) ----------------------------
    def test_live_provider_ollama_needs_no_key(self):
        from authoring.app import _live_provider
        from loom.ai import OllamaProvider
        os.environ["LOOM_PROVIDER"] = "ollama"
        os.environ["LOOM_OLLAMA_MODEL"] = "qwen3.5:35b-a3b"
        provider = _live_provider()
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.name, "ollama:qwen3.5:35b-a3b")

    def test_live_provider_openrouter_without_key_is_none(self):
        from authoring.app import _live_provider
        self.assertIsNone(_live_provider())


if __name__ == "__main__":
    unittest.main()
