"""The authoring workbench (Phase 8) — a Textual text-mode UI over the atlas + author.

A TOOL, not part of the framework: this package imports ``loom`` (its survey, its
map-model and search in ``loom.explore``); ``loom`` never imports this or Textual, so the
core stays UI-agnostic and offline-capable. Textual lives behind the ``authoring`` extra
(``pip install -e ".[authoring]"``). Run the Explorer with ``python -m authoring``.

Slice 1 is the Explorer: read-only navigation over one ``atlas.survey`` — a filterable
navigator, per-entity cards, and jump-links that turn exits/occupants/contents into
one-click moves. No model call. Later slices add play-in-editor and the authoring agent.
"""
