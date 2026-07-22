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

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option

from loom.atlas import survey
from loom.content import load_world
from loom.explore import map_model, search
from .cards import card, parse_ref

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
        ("q", "quit", "Quit"),
    ]

    def __init__(self, view, source_label: str = ""):
        super().__init__()
        self.view = view
        self.map = map_model(view)
        self._source_label = source_label or view.source or "world"
        self._selected = None          # (kind, id) or None
        self._links = []               # parallel to the option-list, index-addressed

    # -- construction ------------------------------------------------------------
    @classmethod
    def from_path(cls, path: str) -> "WorkbenchApp":
        world, start = load_world(path)
        view = survey(world, start, source=path)
        return cls(view, source_label=path)

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
        s = self.view.summary()
        self.sub_title = (f"{s['locations']} rooms · {s['npcs']} npcs · "
                          f"{s['items']} items · {s['errors']}e/{s['warnings']}w")
        self._rebuild_nav("")
        start = self.view.start or (self.view.rooms[0].id if self.view.rooms else "")
        if start:
            self._select("room", start)

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

    # -- events ------------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild_nav(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        hits = search(self.view, event.value)
        if hits:
            self._select(hits[0].kind, hits[0].id)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
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
