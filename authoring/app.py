"""The Explorer (Phase 8, slice 1) — a Textual three-pane workbench over one survey.

Left: a search box + a navigator tree (Rooms / NPCs / Items), the box filtering the tree
live via ``loom.explore.search``. Right: the selected entity's card, and below it an
option-list of *jump-links* — the exits, occupants, and contents of what you are looking
at — so an exit is one keystroke from walking through it. Read-only, no model call: the
whole app rides a single ``atlas.survey`` and the ``loom.explore`` map-model derived from
it. ``loom`` is never imported *into* by this file's dependencies — the arrow points one
way (UI → framework).

Run it: ``python -m authoring [world.json | world-dir]`` (defaults to the shipped world).
"""
from __future__ import annotations
import os

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option

from loom.ai import FakeProvider, OpenRouterProvider
from loom.atlas import survey
from loom.content import load_world
from loom.explore import location_index, map_model, search
from .cards import card, parse_ref
from .play import PlayScreen


def _live_provider():
    """Build the live NPC provider — OpenRouter, the NPC tier, keyed off the environment.
    Explicit (not get_default_provider) so it resolves whenever OPENROUTER_API_KEY is
    present regardless of LOOM_PROVIDER, and OpenRouter-only per the project rule. Returns
    None when no key is set, so the caller can fall back to a dry run."""
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LOOM_OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterProvider(
        model=os.environ.get("LOOM_OPENROUTER_MODEL", "qwen/qwen3.6-35b-a3b"),
        host=os.environ.get("LOOM_OPENROUTER_HOST") or None,
        api_key=key)

# A leaf marker per room state, so the navigator flags trouble at a glance (the survey's
# warnings surfaced where you browse, not only in a lint panel).
_ROOM_MARK = {"unreachable": "⚠", "no-entrance": "⚠", "dead-end": "·"}


class WorkbenchApp(App):
    """The Explorer app. Constructed from a prepared :class:`~loom.atlas.AtlasView` (so
    tests build it from a fixture with no file I/O) or from a path via
    :meth:`from_path`."""

    TITLE = "Loom · authoring workbench"

    CSS = """
    #body { height: 1fr; }
    #nav-col { width: 40%; border: round $primary; padding: 0 1; }
    #search { margin-bottom: 1; }
    #nav { height: 1fr; }
    #inspector { width: 1fr; border: round $secondary; padding: 0 1; }
    #card-scroll { height: 1fr; }
    #card { padding: 1 1; }
    #links { height: auto; max-height: 45%; border-top: solid $secondary; }
    """

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("escape", "clear_search", "Clear"),
        ("p", "play", "Play here"),
        ("f", "toggle_dry", "Dry-run"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, view, source_label: str = "", world_path: str | None = None):
        super().__init__()
        self.view = view
        self.map = map_model(view)
        self._source_label = source_label or view.source or "world"
        self._world_path = world_path  # set when loaded from a path — play needs it
        self._dry_run = False          # play default: live minds (OpenRouter)
        self._selected = None          # (kind, id) or None
        self._links = []               # parallel to the option-list, index-addressed
        self._syncing = False          # guard: cursor-move must not re-fire selection

    # -- construction ------------------------------------------------------------
    @classmethod
    def from_path(cls, path: str) -> "WorkbenchApp":
        world, start = load_world(path)
        view = survey(world, start, source=path)
        return cls(view, source_label=path, world_path=path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="nav-col"):
                yield Input(placeholder="search  id · name · alias · tag …",
                            id="search")
                yield Tree(self._source_label, id="nav")
            with Vertical(id="inspector"):
                with VerticalScroll(id="card-scroll"):
                    yield Static("", id="card")
                yield OptionList(id="links")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#nav-col", Vertical).border_title = "navigator"
        self.query_one("#inspector", Vertical).border_title = "inspector"
        self._refresh_subtitle()
        self._rebuild_nav("")
        start = self.view.start or (self.view.rooms[0].id if self.view.rooms else "")
        if start:
            self._select("room", start)

    def _refresh_subtitle(self) -> None:
        s = self.view.summary()
        mode = "dry-run" if self._dry_run else "live"
        self.sub_title = (f"{s['locations']} rooms · {s['npcs']} npcs · {s['items']} items"
                          f" · {s['errors']}e/{s['warnings']}w · play: {mode}")

    # -- navigator ---------------------------------------------------------------
    def _rebuild_nav(self, query: str) -> None:
        """Rebuild the tree — the full world grouped by kind, or, when the box holds a
        query, only the search hits (still grouped, best-first within a kind)."""
        tree = self.query_one("#nav", Tree)
        tree.clear()
        if query.strip():
            groups = {"room": [], "npc": [], "item": []}
            for h in search(self.view, query):
                groups[h.kind].append((h.id, h.name))
        else:
            groups = {
                "room": [(r.id, r.name) for r in self.view.rooms],
                "npc": [(c.id, c.name) for c in self.view.npcs],
                "item": [(it.id, it.name) for it in self.view.items],
            }
        titles = {"room": "Rooms", "npc": "NPCs", "item": "Items"}
        for kind in ("room", "npc", "item"):
            entries = groups[kind]
            cat = tree.root.add(f"{titles[kind]} ({len(entries)})", expand=True)
            for eid, name in entries:
                cat.add_leaf(self._leaf_label(kind, eid, name), data=(kind, eid))
        tree.root.expand()

    def _leaf_label(self, kind: str, eid: str, name: str) -> str:
        if kind == "room":
            node = self.map.node(eid)
            if node and node.is_start:
                return f"★ {name}"
            if node:
                for flag in node.flags:
                    if flag in _ROOM_MARK:
                        return f"{_ROOM_MARK[flag]} {name}"
        return name

    # -- selection ---------------------------------------------------------------
    def _select(self, kind: str, eid: str) -> None:
        self._selected = (kind, eid)
        text, links = card(self.view, self.map, kind, eid)
        self._links = links
        self.query_one("#card", Static).update(text)
        self.query_one("#inspector", Vertical).border_title = f"inspector — {kind}:{eid}"
        ol = self.query_one("#links", OptionList)
        ol.clear_options()
        for label, _ref in links:
            ol.add_option(Option(label))
        self._sync_cursor(kind, eid)

    def _sync_cursor(self, kind: str, eid: str) -> None:
        """Move the navigator cursor onto the selected entity, so a selection made via
        search, a jump-link, or play-target keeps the tree highlight in step with the
        inspector. Guarded so the cursor move never loops back into another selection."""
        node = self._find_node(kind, eid)
        if node is None:                # filtered out of the current tree — leave the cursor
            return
        tree = self.query_one("#nav", Tree)
        self._syncing = True
        try:
            tree.move_cursor(node)
        finally:
            self._syncing = False

    def _find_node(self, kind: str, eid: str):
        tree = self.query_one("#nav", Tree)
        for cat in tree.root.children:
            for leaf in cat.children:
                if leaf.data == (kind, eid):
                    return leaf
        return None

    # -- events ------------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild_nav(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hits = search(self.view, event.value)
        if hits:
            self._select(hits[0].kind, hits[0].id)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if self._syncing:               # our own cursor move, not a user selection
            return
        data = event.node.data
        if data:                        # a leaf (category nodes carry None)
            self._select(*data)

    def on_option_list_option_selected(self,
                                       event: OptionList.OptionSelected) -> None:
        if 0 <= event.option_index < len(self._links):
            kind, eid = parse_ref(self._links[event.option_index][1])
            if kind:
                self._select(kind, eid)

    # -- actions -----------------------------------------------------------------
    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        box = self.query_one("#search", Input)
        box.value = ""
        self._rebuild_nav("")

    def action_toggle_dry(self) -> None:
        self._dry_run = not self._dry_run
        self._refresh_subtitle()

    def action_play(self) -> None:
        """Enter the play sandbox at the selected entity's room (a room → itself; an NPC or
        item → the room it sits in). Live minds by default; the `f` toggle picks a free,
        offline dry run instead."""
        if not self._world_path:
            self.notify("No world file to play — the app was built from a view.",
                        severity="warning")
            return
        room = self._target_room()
        if not room:
            self.notify("Select a room, or an entity that sits in one, to play.",
                        severity="warning")
            return
        provider, mode = self._play_provider()
        self.push_screen(PlayScreen(self._world_path, room, provider, mode))

    def _target_room(self) -> str:
        if not self._selected:
            return self.view.start
        kind, eid = self._selected
        if kind == "room":
            return eid
        return location_index(self.view).get(eid, "") or self.view.start

    def _play_provider(self):
        """(provider, mode_label) for a play session. Dry-run → FakeProvider; live →
        OpenRouter, degrading to a dry run with a clear label when no key is present."""
        if self._dry_run:
            return FakeProvider(), "dry-run (offline, canned NPCs)"
        provider = _live_provider()
        if provider is None:
            return FakeProvider(), "dry-run — no OPENROUTER_API_KEY in the environment"
        return provider, "LIVE — real NPC minds (OpenRouter)"
