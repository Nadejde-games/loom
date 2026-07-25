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
import json
import os
from collections import Counter

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, Checkbox, Footer, Header, Input, OptionList,
                             Static, TextArea, Tree)
from textual.widgets.option_list import Option

from loom.ai import FakeProvider, OllamaProvider, OpenRouterProvider
from loom.atlas import survey
from loom.explore import location_index, map_model, search
from loom.worlddraft import Draft, commit, diff_status, edit_entity, propose
from .cards import card, parse_ref
from .play import PlayScreen


# The unsaved-change badge per delta mark: a coloured glyph shown on each navigator entry and in
# the inspector, so added / edited / removed entities read at a glance (Tree labels honour markup;
# built as Rich Text so a bracket in an entity name is never mis-parsed).
_DELTA = {"+": ("✚", "green"), "~": ("✎", "yellow"), "-": ("✖", "red")}
_DELTA_WORD = {"+": "added", "~": "edited", "-": "removed"}


def _is_ollama() -> bool:
    """Whether the workbench should target a local Ollama server (LOOM_PROVIDER=ollama)."""
    return os.environ.get("LOOM_PROVIDER", "").strip().lower() == "ollama"


def _live_provider():
    """Build the live NPC provider, following the game's backend choice. LOOM_PROVIDER=ollama
    → a local Ollama provider (no key needed); otherwise OpenRouter when OPENROUTER_API_KEY is
    set. Explicit (not get_default_provider) so it resolves the NPC tier directly. Returns None
    only when the hosted path has no key, so the caller can fall back to a dry run."""
    if _is_ollama():
        return OllamaProvider(
            model=os.environ.get("LOOM_OLLAMA_MODEL", "qwen3.5:35b-a3b"),
            host=os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434"))
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


def _split_csv(raw: str) -> list:
    """A comma-separated field -> a clean list (traits, goals, aliases)."""
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _join_csv(values) -> str:
    """A list -> a comma-separated field for the form (the inverse of _split_csv)."""
    return ", ".join(str(x) for x in (values or []))


_LABEL_KIND = {"room": "room", "npc": "npc", "item": "item"}


def _diff_refs(diff: list) -> list:
    """Parse ``diff_worlds`` lines — ``+ room id (name)`` / ``~ npc id: fields`` /
    ``- item id (name)`` — into ``(kind, id, mark)`` triples: the entities a proposal touches,
    in diff order. A line the parser cannot read is skipped, never raised on."""
    refs = []
    for line in diff or []:
        if not line or line[0] not in "+-~":
            continue
        mark, rest = line[0], line[1:].strip()
        label, _, tail = rest.partition(" ")
        kind = _LABEL_KIND.get(label)
        if not kind:
            continue
        eid = tail.split(":", 1)[0].split("(", 1)[0].strip()
        if eid:
            refs.append((kind, eid, mark))
    return refs


# Edit-form field id -> the label shown in that field's top border line (its border_title).
_FIELD_TITLES = {
    "#f-name": "name", "#f-description": "description",
    "#f-voice": "voice", "#f-backstory": "backstory",
    "#f-traits": "traits (comma-separated)", "#f-goals": "goals (comma-separated)",
    "#f-disposition": "disposition", "#f-wanders": "wanders",
    "#f-aliases": "aliases (comma-separated)", "#f-portable": "portable",
}


class WorkbenchApp(App):
    """The Explorer app. Constructed from a prepared :class:`~loom.atlas.AtlasView` (so
    tests build it from a fixture with no file I/O) or from a path via
    :meth:`from_path`."""

    TITLE = "Loom · authoring workbench"

    CSS = """
    #body { height: 1fr; }
    #nav-col { width: 40%; border: round $primary; padding: 0 1; }
    #nav-view { height: 1fr; }
    #search { margin-bottom: 1; }
    #nav { height: 1fr; }
    #inspector { width: 1fr; border: round $secondary; padding: 0 1; }
    #card-scroll { height: 1fr; }
    #card { padding: 1 1; }
    #links { height: auto; max-height: 45%; border-top: solid $secondary; }
    #edit-form { display: none; height: auto; padding: 0 1; }
    #edit-form TextArea {
        width: 1fr; height: auto; min-height: 3; max-height: 6;   /* grow to fit, 4 lines then scroll */
        border: round $secondary; margin-bottom: 1;
    }
    #edit-form Checkbox { width: 1fr; border: round $secondary; margin-bottom: 1; }
    #edit-controls { display: none; height: auto; padding: 0 1; }
    #edit-controls Button {
        height: 1; min-width: 0; border: none; padding: 0 1; margin-right: 2;
        color: $text-muted; background: transparent;
    }
    #edit-controls Button:hover { color: $text; background: $boost; }
    /* The proposed-change before/after split — hidden until the agent stages a change. */
    #diff-view { display: none; height: 1fr; }
    #diff-summary { height: auto; color: $text-muted; padding: 0 1; margin-bottom: 1; }
    #diff-before-label, #diff-after-label {
        height: 1; color: $accent; text-style: bold; padding: 0 1;
        border-top: solid $secondary;
    }
    #before-scroll, #after-scroll { height: 1fr; padding: 0 1; }
    """

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("escape", "clear_search", "Clear"),
        ("p", "play", "Play here"),
        ("a", "chat", "Author (agent)"),
        ("e", "edit", "Edit"),
        ("ctrl+s", "save_world", "Save"),
        ("f", "toggle_dry", "Dry-run"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, view, source_label: str = "", world_path: str | None = None,
                 draft=None):
        super().__init__()
        self.view = view
        self.map = map_model(view)
        # The shared staged world the agent edits. When present, the Explorer's view is a
        # survey OF it, so an applied change repaints here the moment the chat closes; the
        # chat operates on this same Draft, so nothing is lost between chat sessions. None in
        # view-only mode (built from a bare view, e.g. a test) — the agent is then unavailable.
        self.draft = draft
        self._source_label = source_label or view.source or "world"
        self._world_path = world_path  # set when loaded from a path — play needs it
        self._dry_run = False          # play default: live minds (OpenRouter)
        self._session = None           # authoring.AuthoringSession — set in compose when editable
        self._selected = None          # (kind, id) or None
        self._links = []               # parallel to the option-list, index-addressed
        self._syncing = False          # guard: cursor-move must not re-fire selection
        self._editing = False          # inspector edit-mode (the hand-editing form)
        # The unsaved-change picture, all measured against the Draft's saved baseline:
        self._status = {}              # (kind,id) -> '+'|'~'|'-' : every entity that differs
        self._after = None             # the working {locations,npcs,items} the deltas are OF
        self.baseline_view = None      # survey of the saved baseline — the 'before' cards
        self._baseline_map = None

    # -- construction ------------------------------------------------------------
    @classmethod
    def from_path(cls, path: str) -> "WorkbenchApp":
        draft = Draft.from_path(path)
        view = draft.view(source=path)
        return cls(view, source_label=path, world_path=path, draft=draft)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="nav-col"):
                with Vertical(id="nav-view"):
                    yield Input(placeholder="search  id · name · alias · tag …",
                                id="search")
                    yield Tree(self._source_label, id="nav")
                # The agent chat is the ALTERNATE view of this column (toggle with `a`), so the
                # inspector on the right stays visible while you author. Built hidden; because it
                # shares this Explorer's Draft, a staged/applied change repaints the inspector at
                # once (the on_session_changed callback). Lazy imports keep the read-only Explorer
                # free of the agent stack until a world with a Draft is actually loaded.
                if self.draft is not None:
                    from .agent import AuthoringSession
                    from .chat import ChatPanel
                    author_provider, engine_provider = self._agent_tool_providers()
                    session = AuthoringSession(
                        draft=self.draft, author_provider=author_provider,
                        engine_provider=engine_provider, world_path=self._world_path)
                    self._session = session          # the app reads .pending to render the split
                    # focus_ref lets the agent see what the inspector is showing each turn, so
                    # 'this room' / 'here' / 'it' resolves to the selected entity without a search.
                    # on_session_changed repaints the inspector when a proposal is staged/resolved.
                    panel = ChatPanel(session=session,
                                      on_session_changed=self._on_session_changed,
                                      focus_ref=lambda: self._selected)
                    panel.display = False
                    yield panel
            with Vertical(id="inspector"):
                with VerticalScroll(id="card-scroll"):
                    yield Static("", id="card")
                    # The hand-editing form (hidden until `e`). name/description apply to any
                    # entity; voice/backstory to an npc; aliases to an item. Every apply runs
                    # through the SAME loom.worlddraft gate the agent uses.
                    with Vertical(id="edit-form"):
                        # Each text field is a wrapping, bordered box with its name in the top
                        # border line (border_title, set in on_mount) — one uniform look; the
                        # two booleans are checkboxes.
                        for fid in ("f-name", "f-description", "f-voice", "f-backstory",
                                    "f-traits", "f-goals", "f-disposition"):
                            yield TextArea("", id=fid, soft_wrap=True, show_line_numbers=False)
                        yield Checkbox(id="f-wanders")
                        yield TextArea("", id="f-aliases", soft_wrap=True,
                                       show_line_numbers=False)
                        yield Checkbox(id="f-portable")
                # The before/after split (shown for any entity that differs from the saved world):
                # the saved version over the current working version, so an unsaved change stays
                # reviewable across navigation and Apply, until Save. markup=False so plain card
                # text (e.g. [one-way]) renders literally. Driven by _show_entity_diff.
                with Vertical(id="diff-view"):
                    yield Static("", id="diff-summary")
                    yield Static("── saved ──", id="diff-before-label")
                    with VerticalScroll(id="before-scroll"):
                        yield Static("", id="card-before", markup=False)
                    yield Static("── working (unsaved) ──", id="diff-after-label")
                    with VerticalScroll(id="after-scroll"):
                        yield Static("", id="card-after", markup=False)
                yield OptionList(id="links")
                with Horizontal(id="edit-controls"):
                    yield Button("apply", id="edit-apply")
                    yield Button("cancel", id="edit-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#nav-col", Vertical).border_title = "navigator"
        self.query_one("#inspector", Vertical).border_title = "inspector"
        for sel, title in _FIELD_TITLES.items():   # the label lives in each field's border line
            self.query_one(sel).border_title = title
        self._recompute_baseline_view()            # the 'before' the deltas are measured against
        self._refresh_subtitle()
        self._rebuild_nav("")
        start = self.view.start or (self.view.rooms[0].id if self.view.rooms else "")
        if start:
            self._select("room", start)

    def _refresh_subtitle(self) -> None:
        s = self.view.summary()
        mode = "dry-run" if self._dry_run else "live"
        self.sub_title = (f"{s['locations']} rooms · {s['npcs']} npcs · {s['items']} items"
                          f" · {s['errors']}e/{s['warnings']}w · play: {mode}"
                          + self._unsaved_tag())

    def _unsaved_tag(self) -> str:
        """A compact ' · unsaved ✚2 ✎1 ✖0' tail for the subtitle — the legend for the badges and
        the at-a-glance 'you have changes to save' signal. Empty when nothing differs from saved."""
        if not self._status:
            return ""
        c = Counter(self._status.values())
        parts = [f"{_DELTA[m][0]}{c[m]}" for m in ("+", "~", "-") if c[m]]
        return " · unsaved " + " ".join(parts)

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
        # Entities removed since the save aren't in the working view, but the author still needs
        # to find them to review/undo — surface them as ghost leaves under their kind.
        removed = self._removed_by_kind(bool(query.strip()))
        titles = {"room": "Rooms", "npc": "NPCs", "item": "Items"}
        for kind in ("room", "npc", "item"):
            entries = list(groups[kind]) + removed.get(kind, [])
            cat = tree.root.add(f"{titles[kind]} ({len(entries)})", expand=True)
            for eid, name in entries:
                cat.add_leaf(self._leaf_label(kind, eid, name), data=(kind, eid))
        tree.root.expand()

    def _removed_by_kind(self, filtering: bool) -> dict:
        """(kind -> [(id, name)]) for entities present in the saved baseline but gone from the
        working world — the '-' deltas. Named from the baseline survey. Skipped while a search is
        active (they are not in the searchable working view). Empty until a delete capability
        exists; wired now so the badge model is complete."""
        out: dict = {}
        if filtering or self.baseline_view is None:
            return out
        names = {}
        for pool in (self.baseline_view.rooms, self.baseline_view.npcs, self.baseline_view.items):
            for e in pool:
                names[e.id] = e.name
        for (kind, eid), mark in self._status.items():
            if mark == "-":
                out.setdefault(kind, []).append((eid, names.get(eid, eid)))
        return out

    def _leaf_label(self, kind: str, eid: str, name: str) -> Text:
        """The navigator leaf: the name, any room mark (★ start / ⚠ trouble), and — prepended —
        the coloured unsaved-change badge (✚ added / ✎ edited / ✖ removed). A Rich Text (not a
        markup string) so a bracket in a name is never parsed as markup."""
        base = name
        if kind == "room":
            node = self.map.node(eid)
            if node and node.is_start:
                base = f"★ {name}"
            elif node:
                for flag in node.flags:
                    if flag in _ROOM_MARK:
                        base = f"{_ROOM_MARK[flag]} {name}"
                        break
        mark = self._status.get((kind, eid))
        if mark in _DELTA:
            glyph, style = _DELTA[mark]
            return Text.assemble((f"{glyph} ", style), base)
        return Text(base)

    # -- selection ---------------------------------------------------------------
    def _select(self, kind: str, eid: str) -> None:
        """Show an entity in the inspector. One that differs from the saved world (a delta) opens
        the persistent before/after split — saved version over working version — so a change stays
        reviewable as you navigate away and back, and even after Apply, until you Save. An
        unchanged entity shows the normal single card."""
        self._selected = (kind, eid)
        mark = self._status.get((kind, eid))
        if self.draft is not None and mark in _DELTA:
            self._show_entity_diff(kind, eid, mark)
        else:
            self._show_entity_card(kind, eid)
        self._sync_cursor(kind, eid)

    def _set_links(self, links) -> None:
        """Fill the jump-link option-list (exits, occupants, contents) — shown in both the card
        and the split, so an exit is one keystroke away however the entity is displayed."""
        self._links = links
        ol = self.query_one("#links", OptionList)
        ol.clear_options()
        for label, _ref in links:
            ol.add_option(Option(label))

    def _show_entity_card(self, kind: str, eid: str) -> None:
        """The normal single card for an unchanged entity."""
        self._exit_diff_mode()
        text, links = card(self.view, self.map, kind, eid)
        self.query_one("#card", Static).update(text)
        self.query_one("#inspector", Vertical).border_title = f"inspector — {kind}:{eid}"
        self._set_links(links)

    def _show_entity_diff(self, kind: str, eid: str, mark: str) -> None:
        """Split the inspector: the entity in the saved world (top) over the working world
        (bottom). '+' has no saved side, '-' has no working side; jump-links come from whichever
        side exists, so navigation still works from a split. The 'before' is the last-saved
        baseline, so the split shows the whole unsaved change — not just the last tweak."""
        if mark == "+":
            before_text = "(new — not in the saved world yet)"
        else:
            before_text = card(self.baseline_view, self._baseline_map, kind, eid)[0]
        if mark == "-":
            after_text = "(removed — no longer in the working world)"
            links = card(self.baseline_view, self._baseline_map, kind, eid)[1]
        else:
            after_text, links = card(self.view, self.map, kind, eid)
        self._set_links(links)
        self.query_one("#card-before", Static).update(before_text)
        self.query_one("#card-after", Static).update(after_text)
        self.query_one("#diff-summary", Static).update(self._diff_summary(kind, eid, mark))
        self.query_one("#inspector", Vertical).border_title = f"{_DELTA_WORD[mark]} — {kind}:{eid}"
        self._enter_diff_mode()

    def _entity_from(self, dicts, kind: str, eid: str):
        if not dicts:
            return None
        key = {"room": "locations", "npc": "npcs", "item": "items"}[kind]
        return next((r for r in dicts.get(key, []) if r["id"] == eid), None)

    def _diff_summary(self, kind: str, eid: str, mark: str) -> str:
        """The line above the split: what changed on this entity, plus the world-wide unsaved
        tally (which doubles as the badge legend)."""
        head = f"{_DELTA_WORD[mark]} since last save"
        if mark == "~":
            b = self._entity_from(self.draft.baseline, kind, eid) or {}
            a = self._entity_from(self._after, kind, eid) or {}
            fields = sorted({f for f in set(a) | set(b) if a.get(f) != b.get(f)})
            if fields:
                head += " · changed: " + ", ".join(fields)
        tag = self._unsaved_tag().replace(" · unsaved ", "")
        return f"{head}\nunsaved: {tag.strip()}" if tag else head

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
        # Only the search box filters the navigator — Input.Changed bubbles up from every input
        # (the edit form's fields, the chat's message box), so scope this to #search or entering
        # edit mode (which populates #f-name) would spuriously filter the tree.
        if event.input.id == "search":
            self._rebuild_nav(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
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
        backend = "Ollama" if _is_ollama() else "OpenRouter"
        return provider, f"LIVE — real NPC minds ({backend})"

    def action_chat(self) -> None:
        """Toggle the left column between the navigator and the authoring-agent chat. The
        inspector on the right stays visible in both modes, so the room/npc/item description is
        never hidden while you author. The chat shares this Explorer's staged Draft, so a staged
        or applied change repaints the inspector at once (the on_session_changed callback)."""
        if self.draft is None:
            self.notify("No world file to author — the app was built from a view.",
                        severity="warning")
            return
        from .chat import ChatPanel
        panel = self.query_one(ChatPanel)
        nav = self.query_one("#nav-view", Vertical)
        to_chat = not panel.display
        panel.display = to_chat
        nav.display = not to_chat
        self.query_one("#nav-col", Vertical).border_title = "agent" if to_chat else "navigator"
        if to_chat:
            panel.focus_input()
        else:
            self._refresh_from_draft()      # back to the navigator — reflect any applied change

    def _recompute_working(self) -> None:
        """Recompute the working world (the staged draft, plus any open agent proposal overlaid)
        and its deltas against the saved baseline. Sets view/map (what the nav + cards render),
        ``_after`` (the working dicts), and ``_status`` (the badges, and which entities open a
        split). A pending proposal is included, so a proposed change badges and previews at once
        and stays seamlessly when Applied."""
        if self.draft is None:                        # view-only mode — no deltas, no baseline
            self._after, self._status = None, {}
            return
        pending = self._session.pending if self._session else None
        if pending is not None:
            self.view, self._after = pending.view, pending.candidate
        else:
            self.view, self._after = self.draft.view(source=self._source_label), self.draft.dicts()
        self.map = map_model(self.view)
        self._status = diff_status(self.draft.baseline, self._after)

    def _recompute_baseline_view(self) -> None:
        """Survey the saved baseline into baseline_view/_baseline_map — the source of every
        'before' card. The baseline changes only on load and save, so this is cheap to call on
        each session move. Falls back to the working view in view-only mode."""
        if self.draft is None or self.draft.baseline is None:
            self.baseline_view, self._baseline_map = self.view, self.map
            return
        b = self.draft.baseline
        bd = Draft(start=self.draft.start, locations=b["locations"],
                   npcs=b["npcs"], items=b["items"], meta=self.draft.meta)
        self.baseline_view = bd.view(source="(baseline)")
        self._baseline_map = map_model(self.baseline_view)

    def _resurvey(self) -> None:
        """Recompute the working world + deltas, then repaint the navigator and subtitle. The
        inspector is left to the caller (a card or a before/after split)."""
        self._recompute_working()
        self._rebuild_nav(self.query_one("#search", Input).value)
        self._refresh_subtitle()

    def _select_current_or_start(self) -> None:
        """Re-select the current entity if it still exists (or is a removed delta), else the
        start room."""
        if self._selected and (self._entity_exists(*self._selected)
                               or self._selected in self._status):
            self._select(*self._selected)
        else:
            start = self.view.start or (self.view.rooms[0].id if self.view.rooms else "")
            if start:
                self._select("room", start)

    def _refresh_from_draft(self) -> None:
        """Recompute and repaint, re-selecting the current entity — which shows its before/after
        split if it now differs from the saved world, else its card. Used when returning from the
        chat and after a hand edit."""
        self._resurvey()
        self._select_current_or_start()

    # -- reacting to the agent session -------------------------------------------
    def _on_session_changed(self) -> None:
        """The chat panel moved the session — a proposal staged/refined, applied, discarded,
        undone, or saved. Recompute the working world + deltas (a save resets the baseline, so
        deltas clear), then focus the entity the agent just touched so the change is in view;
        otherwise keep the current selection. The app is the sole authority on the inspector."""
        self._recompute_baseline_view()      # catch a save issued from the chat panel
        self._resurvey()
        focus = None
        pending = self._session.pending if self._session else None
        if pending is not None:
            focus = self._primary_changed_ref(pending)
        if focus is not None:
            self._select(*focus)
        else:
            self._select_current_or_start()

    def _primary_changed_ref(self, pending):
        """The entity to focus when the agent changes something: the current selection if the
        change touches it (viewing the forge then editing it stays put), else the first
        modified (~) / added (+) / removed (-) entity in the diff. None if there is no delta."""
        refs = _diff_refs(pending.diff)
        if not refs:
            return None
        if self._selected:
            for kind, eid, _mark in refs:
                if (kind, eid) == self._selected:
                    return (kind, eid)
        for want in ("~", "+", "-"):
            for kind, eid, mark in refs:
                if mark == want:
                    return (kind, eid)
        return (refs[0][0], refs[0][1])

    def _enter_diff_mode(self) -> None:
        """Hand the inspector to the before/after split (keeping the jump-links, so you can still
        navigate onward from a diff), closing any open edit form."""
        if self._editing:
            self._set_edit_mode(False)
        self.query_one("#card-scroll", VerticalScroll).display = False
        self.query_one("#diff-view", Vertical).display = True
        self.query_one("#links", OptionList).display = True

    def _exit_diff_mode(self) -> None:
        """Restore the normal single-card inspector (a no-op when the split is already hidden)."""
        self.query_one("#diff-view", Vertical).display = False
        self.query_one("#card-scroll", VerticalScroll).display = True
        self.query_one("#links", OptionList).display = True

    def _entity_exists(self, kind: str, eid: str) -> bool:
        pools = {"room": self.view.rooms, "npc": self.view.npcs, "item": self.view.items}
        return any(e.id == eid for e in pools.get(kind, ()))

    def _agent_tool_providers(self):
        """The engine's loom providers the agent's tools use: the GM/author tier (a generous
        token budget) for region_author, and the NPC tier for preview_play. Backend follows
        LOOM_PROVIDER — a local Ollama server or hosted OpenRouter. Both None when the hosted
        path has no key — region authoring then reports it needs a live model, and preview-play
        falls back to the offline fake. Distinct from the agent's own Pydantic-AI model, which
        the ChatPanel builds itself."""
        if _is_ollama():
            author_tag = (os.environ.get("LOOM_AUTHOR_MODEL") or os.environ.get("LOOM_GM_MODEL")
                          or os.environ.get("LOOM_OLLAMA_MODEL") or "qwen3.6:27b")
            host = os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434")
            author = OllamaProvider(model=author_tag, host=host, max_tokens=1200)
            return author, _live_provider()
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LOOM_OPENROUTER_API_KEY")
        if not key:
            return None, None
        author_slug = (os.environ.get("LOOM_AUTHOR_MODEL") or os.environ.get("LOOM_GM_MODEL")
                       or "qwen/qwen3.6-27b")
        author = OpenRouterProvider(model=author_slug, api_key=key, max_tokens=1200)
        return author, _live_provider()

    # -- hand-editing (the inspector edit form) ----------------------------------
    def _entity_dict(self, kind: str, eid: str):
        pool = {"room": self.draft.locations, "npc": self.draft.npcs,
                "item": self.draft.items}.get(kind, [])
        return next((d for d in pool if d["id"] == eid), None)

    def action_edit(self) -> None:
        """Toggle the inspector between the read card and an edit form for the selected entity.
        name/description apply to any entity; voice/backstory to an npc; aliases to an item.
        Apply runs through the same shadow-validate-apply gate the agent uses."""
        if self._editing:
            self._set_edit_mode(False)
            if self._selected:
                self._select(*self._selected)       # restore the card or the split
            return
        if self.draft is None:
            self.notify("No editable world — the app was built from a view.",
                        severity="warning")
            return
        if not self._selected or self._selected[0] not in ("room", "npc", "item"):
            self.notify("Select a room, npc, or item to edit.", severity="warning")
            return
        kind, eid = self._selected
        self._populate_form(kind, eid)
        self._set_edit_mode(True)
        self.query_one("#f-name", TextArea).focus()

    def _set_edit_mode(self, on: bool) -> None:
        self._editing = on
        if on:                                       # the form lives in card-scroll; leave the split
            self.query_one("#diff-view", Vertical).display = False
            self.query_one("#card-scroll", VerticalScroll).display = True
        self.query_one("#card", Static).display = not on
        self.query_one("#links", OptionList).display = not on
        self.query_one("#edit-form", Vertical).display = on
        self.query_one("#edit-controls", Horizontal).display = on
        self.query_one("#inspector", Vertical).border_title = (
            f"editing {self._selected[1]}" if (on and self._selected) else "inspector")

    def _populate_form(self, kind: str, eid: str) -> None:
        d = self._entity_dict(kind, eid) or {}
        persona = d.get("persona") or {}
        self.query_one("#f-name", TextArea).text = d.get("name", "") or ""
        self.query_one("#f-description", TextArea).text = d.get("description", "") or ""
        self.query_one("#f-voice", TextArea).text = str(persona.get("voice", "") or "")
        self.query_one("#f-backstory", TextArea).text = str(persona.get("backstory", "") or "")
        self.query_one("#f-traits", TextArea).text = _join_csv(persona.get("traits"))
        self.query_one("#f-goals", TextArea).text = _join_csv(persona.get("goals"))
        self.query_one("#f-disposition", TextArea).text = str(persona.get("disposition", "") or "")
        self.query_one("#f-wanders", Checkbox).value = bool(d.get("wanders"))
        self.query_one("#f-aliases", TextArea).text = _join_csv(d.get("aliases"))
        self.query_one("#f-portable", Checkbox).value = bool(d.get("portable", True))
        is_npc, is_item = kind == "npc", kind == "item"
        for wid in ("#f-voice", "#f-backstory", "#f-traits", "#f-goals",
                    "#f-disposition", "#f-wanders"):
            self.query_one(wid).display = is_npc
        for wid in ("#f-aliases", "#f-portable"):
            self.query_one(wid).display = is_item

    def _gather_patch(self, kind: str, eid: str) -> dict:
        patch: dict = {}
        name = self.query_one("#f-name", TextArea).text.strip()
        if name:
            patch["name"] = name
        patch["description"] = self.query_one("#f-description", TextArea).text.strip()
        if kind == "npc":
            persona = dict((self._entity_dict("npc", eid) or {}).get("persona") or {})

            def _set(key, val):                 # set a persona field, or drop it when cleared
                if val:
                    persona[key] = val
                else:
                    persona.pop(key, None)
            _set("backstory", self.query_one("#f-backstory", TextArea).text.strip())
            _set("voice", self.query_one("#f-voice", TextArea).text.strip())
            _set("disposition", self.query_one("#f-disposition", TextArea).text.strip())
            _set("traits", _split_csv(self.query_one("#f-traits", TextArea).text))
            _set("goals", _split_csv(self.query_one("#f-goals", TextArea).text))
            patch["persona"] = persona
            patch["wanders"] = self.query_one("#f-wanders", Checkbox).value
        elif kind == "item":
            patch["aliases"] = _split_csv(self.query_one("#f-aliases", TextArea).text)
            patch["portable"] = self.query_one("#f-portable", Checkbox).value
        return patch

    def _apply_edit(self) -> None:
        """Validate the form against the atlas gate and, if clean, commit it (with an undo
        checkpoint) into the staged Draft — then repaint. A hand edit and an agent edit take
        the identical safety path; a change that would break the world is refused."""
        if not self._selected or self.draft is None:
            return
        kind, eid = self._selected
        # A hand edit and an open agent proposal are conflicting drafts of the same world; the
        # explicit hand edit wins. Set the proposal aside so a later Apply cannot clobber this.
        if self._session is not None and self._session.pending is not None:
            self._session.pending = None
            self.notify("Set aside the agent's open proposal — it was built on the earlier state.",
                        severity="warning")
        try:
            cand = edit_entity(self.draft, eid, self._gather_patch(kind, eid))
        except KeyError:
            self.notify("That entity is gone — reselect.", severity="warning")
            return
        p = propose(self.draft, cand)
        if not p.ok:
            detail = "; ".join(f"{f.code}@{f.where or 'world'}" for f in p.view.errors)
            self.notify(f"Edit rejected (would break the world): {detail}", severity="error")
            return
        commit(self.draft, p)
        self._set_edit_mode(False)
        self._refresh_from_draft()          # repaint, re-selecting the edited entity (now a diff)
        self.notify("Edit applied — unsaved. Ctrl+S to write it to disk.")

    def action_save_world(self) -> None:
        """Write the staged world (all edits, by hand or by the agent) to world.json, applying any
        open agent proposal FIRST so Save never drops proposed work, then reset the diff baseline
        to what was saved — so the unsaved-change badges and before/after splits clear, and future
        deltas are measured against this save."""
        if self.draft is None or not self._world_path:
            self.notify("No world file to save.", severity="warning")
            return
        had_pending = self._session is not None and self._session.pending is not None
        try:
            if self._session is not None:
                self._session.save(self._world_path)   # applies pending, writes, re-baselines
            else:                                      # no agent session — direct write
                with open(self._world_path, "w", encoding="utf-8") as f:
                    json.dump(self.draft.to_world_json(), f, indent=2, ensure_ascii=False)
                    f.write("\n")
                self.draft.mark_saved()
        except Exception as exc:
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self._recompute_baseline_view()
        self._refresh_from_draft()          # badges + splits clear (deltas now empty)
        where = os.path.relpath(self._world_path)
        self.notify(f"Applied the open proposal and saved to {where}." if had_pending
                    else f"Saved to {where}.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Only the inspector's edit controls — the ChatPanel handles its own buttons.
        if event.button.id == "edit-apply":
            self._apply_edit()
        elif event.button.id == "edit-cancel":
            self._set_edit_mode(False)
            if self._selected:
                self._select(*self._selected)       # restore the card or the split
