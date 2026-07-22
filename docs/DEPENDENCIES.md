# Dependency policy

Loom is not "dependency-free." That was a proxy for the thing we actually want —
**minimal, stable, conflict-free coupling** — and taken as an absolute it costs more
than it saves (hand-rolled parsers and clients that are fragile, under-tested, and
strictly worse than the boring library that already solves the problem).

The real rule is a **dependency budget**: a short, deliberate allowlist. A dependency
earns its place only when it clears *all* of these:

1. **Cheap and common.** A widely-used, well-audited package with a small, stable
   ("boring") API — not a churny or opinionated one.
2. **Shallow tree.** A shallow, ideally empty, sub-dependency tree.
3. **No native extension in the core.** Pure-Python for anything `loom/` imports —
   no C/Rust wheels that add per-platform build/install surface.
4. **No plausible version conflict.** Its version range would not collide with a
   host application's. (This is the axis that matters most for a library, and it is
   *independent of "native vs pure-Python"* — a pure-Python package with a churny
   pinned API is still a conflict magnet.)
5. **Pays for itself.** It replaces meaningfully more hand-rolled code — or removes a
   real class of bug — than it costs to take on.

## Strict for `loom/`, loose for `game/`

The budget is held to **strictly for `loom/`**, because `loom/` is positioned as a
library: *its dependencies become its consumers' dependencies and version pins*. A
hard, conflicting requirement in the core can make a downstream game unable to use
Loom at all.

It is applied **loosely for `game/`** (the application) and `scripts/` (tooling),
where a dependency affects only this repo and a convenience library is usually the
right call.

The core still runs **fully offline** on the `FakeProvider` with no API key.

## Current budget

Core (`loom/`, in `pyproject.toml → dependencies`):

| Package       | Why it clears the bar |
|---------------|-----------------------|
| `httpx`       | HTTP client for the OpenAI-compatible providers. Native async (drops the thread-executor hack) and connection pooling (a real latency win on remote backends, where each fresh call otherwise pays a TLS handshake). Pure-Python tree (`anyio`, `certifi`, `httpcore`, `idna`, `h11`), no native extension, stable API. Imported lazily in the HTTP provider, so the offline `FakeProvider` path needs it only when actually reaching a server. |
| `json-repair` | Tolerant recovery of a JSON turn-envelope from a messy model reply. Purpose-built for LLM output, **zero sub-dependencies**, and it retires the hand-rolled brace-repair extractor — including the exact missing-close shape behind the B6 leak (a real bug class removed, not merely refactored). Sits behind grammar-constrained decoding as defense-in-depth. |

Application (`game/`, in `pyproject.toml → optional-dependencies.game`):

| Package        | Why |
|----------------|-----|
| `python-dotenv`| Loads a repo-root `.env` (e.g. `OPENROUTER_API_KEY`) in `game/main.py`. Zero sub-dependencies, frozen API, ubiquitous. App-layer, so it never touches the library budget. |

Authoring tool (`authoring/`, in `pyproject.toml → optional-dependencies.authoring`):

| Package   | Why |
|-----------|-----|
| `textual` | The substrate of the Phase 8 authoring workbench — the only Python TUI with clickable widgets (a genuinely selectable map node), the Tree/Input/OptionList the panes need, and one codebase for terminal *and* browser (`textual serve`). It is heavier than the core budget allows (pulls `rich`/`markdown-it-py`/`pygments`) and moves fast — but it is a **tool** dependency, held to the loose policy, not the library one. The rule that keeps it honest: **`loom/` never imports Textual** — the UI in `authoring/` imports `loom` (its survey, its `loom.explore` map-model/search), never the reverse, so the framework stays UI-agnostic and offline-capable. Install only when authoring: `pip install -e ".[authoring]"`. |

Optional (`pyproject.toml → optional-dependencies.anthropic`): `anthropic`, imported
lazily only when an `AnthropicProvider` is instantiated.

## Where we deliberately keep the hand-roll

The **action-schema validator** (`loom/action.py`) stays hand-written on purpose. It
is ~40 lines of stdlib type-checking, and swapping it for `jsonschema` would *fail*
this policy, not satisfy it:

- `jsonschema` pulls `rpds-py`, a **Rust extension**, into the core (rule 3).
- The validator sits on the **security seam** — the golden rule ("never execute raw
  model text") — where reading every line is a feature, not debt.
- Its errors are **tailored, model-facing strings** (`missing required arg "x"`,
  `must be one of [...]`) that are fed straight back into the validate→retry loop.
  A generic library error message would *degrade* that loop.

It emits the same shape it checks (`ActionRegistry.json_schema()` for constrained
decoding is rendered from the same `Param` specs), so there is no drift to a
library to buy. Keeping it is the budget working as intended.
