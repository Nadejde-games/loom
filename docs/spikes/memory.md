# Spike — Deep memory (importance, embeddings, relevance retrieval, reflection)

**Date:** 2026-07-20 · **Question:** Loom's agents share ONE memory substrate — the
Generative-Agents "memory stream," today a minimal append-only list of
`MemoryEntry(text, kind, t)` with recency-only `recent(k)` retrieval. Design commitment
#6 promises it gains "importance, embeddings, relevance retrieval, and reflection later,"
each slotting behind the same `MemoryStream` interface; the persistence spike
(`docs/spikes/persistence.md`) committed to moving memory from JSON to SQLite *exactly
when importance/embeddings land* — i.e. now. So: how do real systems score importance
affordably; what is the retrieval formula and how are its weights set; **where do
embeddings come from when chat inference is OpenRouter-only and the local vLLM server is
off-limits**; what SQLite schema and vector-storage/search strategy; what is the minimal
useful reflection; and what does the broader prior art (MemGPT/Letta, mem0, A-MEM,
LangChain/LlamaIndex, RAG) have Loom steal or skip? · **Method:** survey of the primary
sources (the Generative Agents paper; the MemGPT/Letta, mem0, and A-MEM papers/docs; the
OpenRouter, Model2Vec, FastEmbed, and sqlite-vec project docs), verified against
reference-level sources rather than blog hand-waving, then mapped onto Loom's actual code
(`loom/ai/memory.py`, `loom/ai/provider.py`, `loom/ai/mind.py`, `loom/ai/director.py`,
`loom/persistence.py`) and its dependency budget (`docs/DEPENDENCIES.md`). No code written.

---

## Verdict

**Land importance + embedding-relevance retrieval on a per-agent SQLite table now;
defer reflection to a second slice.** The crux dissolves on a verified finding:
**OpenRouter now serves a first-class, OpenAI-shaped `/api/v1/embeddings` endpoint**
(models incl. `openai/text-embedding-3-small` at $0.02/M tokens, `baai/bge-m3` and
`qwen/qwen3-embedding` at $0.01/M, and a free NVIDIA option), so embeddings ride the
*same hosted seam and API key as chat* — no torch, no onnxruntime, no local server, no
touching the forbidden vLLM box, and no new core dependency beyond the `httpx` Loom
already has. Put it behind an `EmbeddingProvider` Protocol that mirrors `LLMProvider`
exactly (a `FakeEmbeddingProvider` for offline determinism; an optional in-process
`model2vec` embedder — numpy-only, no torch — for a zero-network deployment), so the
retrieval math cannot tell a fake vector from a real one. Adopt the Generative-Agents
retrieval score verbatim — `score = recency + importance + relevance`, each min-max
normalized to [0,1], weights all 1.0, recency an exponential decay (factor 0.995) — but
score **importance with a cheap deterministic heuristic**, not a model call per memory
(a per-memory LLM rating is the one thing every affordability-minded system avoids;
mem0's whole design is a novelty gate, not a poignancy score). Store memory as **one
SQLite table keyed by `agent_id`**, the vector a float32 `BLOB` packed with stdlib
`array` (no numpy in the core), scanned **brute-force in Python** — which is fine
essentially forever for a MUD, because every query filters `WHERE agent_id = ?` and scans
only that one agent's few-hundred-to-few-thousand rows (sqlite-vec's own brute force
clears 100k vectors in under 75 ms; Loom never approaches that per agent). Incremental
`INSERT` replaces the JSON whole-file rewrite; the existing `state()`/`load_state()` pair
is the one-time JSON->SQLite import bridge. **One honest interface caveat:** importance
upgrades `recent()` with zero caller change, but *relevance is query-driven*, so the two
callers (`mind._base_lines`, `director._system_prompt`) must pass the current
utterance/scene into a new `retrieve(query, k)` — a small, local caller touch, the only
place commitment #6's "without touching callers" does not fully hold. **Reflection**
(the sum-of-importance trigger, question-generation, insight synthesis with citations)
is real and valuable for B10 idle-NPC autonomy, but it is a second LLM subsystem; ship
retrieval first, reflection as slice 2.

---

## 1. Generative Agents — the reference the whole field standardized on

Loom already descends from this design; the task is to implement the parts it deferred.
Verified against the paper (arXiv:2304.03442, fetched via ar5iv), the mechanism is exact
and small:

**Retrieval score.** `score = alpha_recency * recency + alpha_importance * importance +
alpha_relevance * relevance`, and **all three alpha weights are set to 1.0** in the
implementation. Each raw component is **normalized to [0,1] with min-max scaling** across
the candidate pool before summing. ([Generative Agents,
arXiv:2304.03442](https://arxiv.org/abs/2304.03442) via
[ar5iv](https://ar5iv.labs.arxiv.org/html/2304.03442) — **Verified, fetched and quoted.**)

- **Recency** = **exponential decay, factor 0.995**, over game-hours since the memory was
  last *accessed* (retrieval bumps last-access, so a recalled memory stays warm). Verified.
- **Importance** = an **LLM rating 1-10** assigned **at write time**, with the literal
  prompt: *"On the scale of 1 to 10, where 1 is purely mundane (e.g., brushing teeth,
  making bed) and 10 is extremely poignant (e.g., a break up, college acceptance), rate the
  likely poignancy..."* — example outputs 2 ("cleaning up the room"), 8 ("asking your crush
  out"). Verified, quoted.
- **Relevance** = **cosine similarity** between the query embedding and the memory's
  embedding. Verified.

**What importance buys in retrieval:** it lets a salient-but-old, salient-but-off-topic
memory (a promise, a death, a betrayal) outrank the flood of mundane recent chatter that
recency-alone and relevance-alone would surface. It is the signal that keeps a long-lived
NPC from forgetting the one thing that mattered. For Loom this is the whole point of going
past `recent(k)`.

**Reflection.** Triggers **"when the sum of the importance scores for the latest events
perceived by the agents exceeds a threshold (150 in our implementation)"** — in practice
"roughly two or three times a day." Question-generation queries the LLM with **"the 100
most recent records"** and asks for **"3 most salient high-level questions."** Insights are
synthesized with **evidence citations in the form "insight (because of 1, 5, 3)"** where
the numbers index the memories cited, and each insight is **inserted back into the stream
as a memory** (a non-leaf node). Reflections form a **tree**: leaves are observations,
non-leaf nodes are progressively more abstract thoughts, and reflections can themselves be
reflected upon. Verified, quoted.

**Cost reality (the affordability question):** Generative Agents rates *every* memory with
a model call and reflects on a 100-record window — affordable in a research sandbox with a
handful of agents, expensive as a per-memory tax in a forever-MUD with many NPCs each
accreting memory continuously. The affordable-systems answer (Sections 4-6) is: **do not
rate every memory with an LLM**; use a heuristic proxy, batch, or a novelty gate.

## 2. MemGPT / Letta — the core-vs-archival split Loom already half-has

MemGPT ("Towards LLMs as Operating Systems," arXiv:2310.08560) frames memory as an OS
managing virtual memory: a fixed context window (RAM) plus **system calls that page data
in and out of larger stores**. Its tiers ([Letta docs — MemGPT](https://docs.letta.com/letta-memgpt);
**Verified via docs + search**):

- **Core memory** — small, always in-context (the agent's persona + salient user facts),
  and **self-edited** by the agent through memory-editing tools.
- **Archival memory** — unbounded, **backed by a vector DB** (Chroma/pgvector), searched
  on demand.
- **Recall memory** — searchable raw conversation history (a disk cache).

**What Loom steals:** the mental model, which Loom *already embodies* — `memory.recent(k)`
is core (always injected into the prompt via `_base_lines`), and a SQLite-backed
`retrieve(query)` is archival (searched on demand). Naming the split clarifies the design:
recent = always-in-context, retrieved = paged-in-on-relevance. **What Loom skips:** the
*self-editing* memory tools and the whole agent-as-OS paging apparatus. Self-editing is a
second action surface on the security seam ("never execute raw model text") and a large
build; Loom's memory is an *observed* log, not an agent-rewritten scratchpad. Skip it.

## 3. mem0 — the "don't store everything" lesson, and how to avoid per-memory LLM cost

mem0 (arXiv:2504.19413; [docs.mem0.ai](https://docs.mem0.ai)) is a two-method interface
(`add`, `search`) over a **two-phase pipeline** (**Verified via search + arXiv**):

- **Extraction** — an LLM pass identifies the *salient candidate facts* worth storing from
  a raw exchange (most of the raw text is discarded).
- **Update** — each candidate is compared by vector similarity to existing memories, and an
  LLM decides **ADD / UPDATE / DELETE / NOOP** — so near-duplicates merge and contradictions
  overwrite rather than piling up.

**What Loom steals (conceptually, deferred):** mem0 replaces Generative-Agents' *importance
score* with an *importance gate* — it never scores 1-10, it decides whether a memory is
novel enough to keep at all. That is the affordability answer in a different shape, and a
future dedup pass (an NPC should not store "the player greeted me" 40 times) is a cheap,
high-value win. **What Loom skips now:** the LLM-on-every-add extraction/update pipeline
(two model calls per observation is exactly the per-memory tax Loom must avoid at MUD
volume) and the graph variant (Mem0g). Loom's observations are already short, salient,
engine-authored strings (`'X said to me: "..."'`, `'I noticed: ...'`), not raw transcripts
needing extraction — so the extraction phase buys Loom little today.

## 4. A-MEM — agentic notes and link-evolution (interesting, defer)

A-MEM (arXiv:2502.12110, NeurIPS 2025; [github.com/WujiangXu/A-mem](https://github.com/WujiangXu/A-mem))
applies the **Zettelkasten** method: each new memory becomes a **structured note**
(LLM-generated contextual description + keywords + tags); the system **generates links** to
related historical notes; and adding a memory can **evolve** the attributes of existing
notes (**Verified via search + arXiv**). It is the richest form of "memory as an evolving
network."

**What Loom steals:** nothing structural for the first slice — but the *tags/keywords per
memory* idea is a cheap lexical retrieval booster that composes with embeddings later.
**What Loom skips:** the per-memory LLM note-construction and the link-evolution machinery
— three LLM touches per memory and a graph to maintain, far past what a MUD needs. A-MEM
is the "if reflection is not enough" escalation, noted and shelved.

## 5. LangChain / LlamaIndex / standard RAG — the retrieval-practice baseline

The framework norm confirms Loom's chosen shape rather than changing it (**Verified via
docs knowledge; live doc fetch was unavailable this spike — flag if exact API names become
load-bearing**):

- **LangGraph long-term memory** provides a `BaseStore` (an `InMemoryStore` and DB-backed
  stores) with **optional semantic search**: you supply an `index` config with an embedding
  function and a dimension, and `store.search(namespace, query=...)` returns items ranked by
  cosine similarity. This is precisely the `EmbeddingProvider` + `retrieve(query)` shape
  recommended below — the engine owns the embedder, the store owns the scan.
  ([LangGraph memory docs](https://langchain-ai.github.io/langgraph/concepts/memory/).)
- **LangChain classic** `VectorStoreRetrieverMemory` and **LlamaIndex** `VectorMemory` are
  the same idea: embed each memory, retrieve top-k by cosine. LangChain's older
  `ConversationSummaryMemory` is the reflection analogue (periodically summarize history
  into a compact note).
- **Standard RAG practice:** top-k cosine over normalized embeddings; **MMR**
  (maximal-marginal-relevance) when result *diversity* matters; optional reranking at
  larger scale. For Loom's small per-agent pools, plain top-k weighted by the GA score is
  right; MMR/reranking are deferrable scale concerns.

**Steal:** the top-k-cosine baseline and the pluggable-embedder pattern (both already
Loom's plan). **Skip:** adopting a framework — LangChain/LlamaIndex are heavy dependency
trees welded to their own abstractions, the antithesis of Loom's budget; take the *pattern*,
not the package.

## 6. Where embeddings come from — the crux, resolved

The task's central worry was "does OpenRouter serve an embeddings endpoint at all?" **It
does, and this changes the recommendation.**

**(a) OpenRouter embeddings — VERIFIED, and the primary path.** OpenRouter exposes
`POST https://openrouter.ai/api/v1/embeddings` in OpenAI request/response shape, behind the
*same API key and base-url family* Loom's chat already uses. Named models and prices
([OpenRouter embeddings docs](https://openrouter.ai/docs/api_reference/embeddings);
[OpenRouter embedding models](https://openrouter.ai/collections/embedding-models) —
**Verified, fetched**):

| Model | Price / M input tokens | Context |
|---|---|---|
| `openai/text-embedding-3-small` | $0.02 | 8K |
| `openai/text-embedding-3-large` | $0.13 | 8K |
| `qwen/qwen3-embedding-8b` | $0.01 | 32K |
| `baai/bge-m3` | $0.01 | (large) |
| `perplexity/...embed` | $0.004 | (cheapest paid) |
| `nvidia/llama-nemotron-embed-vl-1b-v2` | Free | |

This squares perfectly with every constraint: **it is not the forbidden vLLM box** (that is
a *local chat* server; this is hosted OpenRouter, the sanctioned provider); it adds **no new
core dependency** (one `httpx` POST — `httpx` is already core); it needs **no torch, no
onnxruntime, no GPU, no in-process model**; and **cost is negligible** — memory texts are
short (~20-50 tokens each), so even a million memories is ~30M tokens ~= $0.60 on
`text-embedding-3-small`, or $0.00 on the free NVIDIA model. Batch support (arrays of up to
~96 inputs per request) makes bulk backfill cheap. The one real cost is a **network
dependency for embedding** — addressed by embedding *off the loop* (never in the synchronous
`add()`) and by degrading retrieval to recency+importance when the embedder is unreachable.
*Caveat to flag:* embeddings are a **recent** OpenRouter addition (mid-2026); the model
list and pricing above may drift — which is exactly why it must sit behind a swap seam
(below), not be hard-wired.

**(b) In-process local embedder — the optional, dependency-weighed alternative.** For a
deployment that wants *zero network* for memory, an in-process library is permissible (it is
not the vLLM chat server). Weighed against the dependency budget
(`docs/DEPENDENCIES.md`: no native extension in `loom/` core):

- **`model2vec`** ([MinishLab/model2vec](https://github.com/MinishLab/model2vec);
  [PyPI](https://pypi.org/project/model2vec/) — **Verified via search**) — static
  distilled embeddings whose **only major dependency is numpy** (no torch). ~15x smaller,
  ~500x faster on CPU than the source transformer, retaining ~85-95% of its benchmark
  quality; embeddings are *uncontextualized* (mean of token vectors), so it misses nuance a
  full transformer catches. **The lightest real semantic embedder** and the right optional
  local pick — but numpy is a native wheel, so it stays an **optional extra**, never core.
- **`fastembed`** ([qdrant/fastembed](https://github.com/qdrant/fastembed) — **Verified via
  search**) — ONNX Runtime, **no PyTorch, no CUDA**, "doesn't download GBs of PyTorch."
  Higher quality than model2vec, heavier (onnxruntime is a native wheel + downloads a
  quantized model). A reasonable optional extra when quality matters more than footprint.
- **`sentence-transformers` / torch** — the heaviest (multi-GB torch tree). **Rejected** for
  Loom even as an extra when model2vec/fastembed exist.

**(c) Dependency-free fallback — how badly quality suffers.** A hashing / char-ngram / TF-IDF
vectorizer needs zero dependencies and is fully deterministic, but it is **lexical only**:
it matches "the storm" to "the storm," never to "the tempest" or "the gathering dark." So
relevance collapses to keyword overlap — fine for exact-term recall, useless for the
paraphrase generalization that makes semantic memory feel intelligent. Its correct role is
**the offline test stub and the last-resort degrade**, not the production embedder.

**The seam (mirroring `LLMProvider`).** Add an `EmbeddingProvider` Protocol beside
`LLMProvider` in `loom/ai/provider.py` (or a new `loom/ai/embedding.py`):

```
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    dim: int
```

Concretes: `OpenRouterEmbeddingProvider` (the POST), `FakeEmbeddingProvider` (deterministic
hash -> fixed-dim L2-normalized vector, for tests), optional `LocalEmbeddingProvider`
(model2vec/fastembed, lazy-imported like `AnthropicProvider`). A `get_default_embedder()`
resolves from env exactly as `get_default_provider()` does. The retrieval math is identical
whatever vectors come back — "the engine can't tell fake from real," commitment #3 extended
to embeddings.

## 7. Storage — SQLite, packed BLOB vectors, brute-force cosine

The persistence spike already committed JSON now -> SQLite when embeddings land, behind the
unchanged `MemoryStream` interface. This is that moment.

**Schema — one table, keyed by agent_id** (not one table per agent — simpler, one index):

```sql
CREATE TABLE memory (
    id         INTEGER PRIMARY KEY,
    agent_id   TEXT NOT NULL,          -- npc id, or the reserved "director"
    text       TEXT NOT NULL,
    kind       TEXT NOT NULL,          -- observation | speech | event | reflection | ...
    t          REAL NOT NULL,          -- creation timestamp (time.time())
    importance INTEGER NOT NULL,       -- 1..10 heuristic (LLM-rated later, same column)
    embedding  BLOB                    -- float32-packed vector, NULL until embedded
);
CREATE INDEX memory_agent ON memory(agent_id);
```

**How to store the vector — stdlib `array`, no numpy.** Pack with
`array('f', vector).tobytes()` and unpack with `array('f').frombytes(blob)` — float32, 4
bytes per dimension, no dependency. A 384-dim vector is 1536 bytes; even 100k of them is
~150 MB, and per agent it is kilobytes. **numpy is not needed for storage** and should stay
out of the core (native wheel; `docs/DEPENDENCIES.md` rule 3). numpy *would* speed the
cosine scan, but only enough to matter past scales a MUD never reaches per agent — keep it
an optional acceleration, not a requirement.

**How to search — brute-force cosine in Python, and it is fine essentially forever for a
MUD.** The decisive fact is **partitioning by `agent_id`**: a retrieval scans only *one
agent's* rows (`WHERE agent_id = ?`), which is a few hundred to a few thousand memories over
a long NPC life. Cosine over 2k pre-normalized 384-dim vectors is a couple thousand dot
products — sub-millisecond to low-milliseconds in Python, negligible off the loop. For
calibration, **sqlite-vec's own brute force clears 100k vectors in under 75 ms** and only
"degrades significantly" past ~1M ([Alex Garcia — sqlite-vec stable
release](https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html) —
**Verified, fetched and quoted**; the author: "Only brute-force search for now... Most of
my little data analysis projects deal with thousands of vectors, maybe hundreds of
thousands"). So:

- **Brute-force Python (optionally numpy-accelerated):** the recommendation. Fine to
  ~10k-100k rows scanned per query; per-agent partitioning keeps the real scan far below
  that indefinitely.
- **`sqlite-vec`** (a runtime-loadable **C extension, zero deps**, `pip install sqlite-vec`,
  brute-force in C, ANN not yet shipped): the *next* step if a single scan ever crosses
  ~100k rows. It is a compiled binary (a native-extension budget concern), so **defer** it
  until scale demands — it changes nothing about the schema (it reads the same BLOB), so
  adopting it later is local.
- **hnswlib / faiss (ANN index):** overkill and native/heavy — **skip indefinitely**; a MUD
  has no per-agent corpus that needs approximate search.

**Incremental append vs the JSON whole-file rewrite.** This is the concrete payoff that
justifies moving now: today the persistence overlay serializes every mind's entire memory
(`mind.memory.state()`) into one JSON blob and **rewrites the whole file on every autosave**
(`persistence.save_atomic`). SQLite makes a new memory a single `INSERT` — O(1), no rewrite,
no whole-stream re-serialization — which is what a forever-growing, continuously-appended
log needs. Memory leaves the JSON overlay; the rest of the overlay (positions, items,
conditions, quests, clock/weather, chronicle) stays JSON, which suits it.

**Migration — the existing `state()`/`load_state()` pair is the bridge.** The just-shipped
persistence slice already writes memory as JSON via `MemoryStream.state()`. The one-time
JSON->SQLite import is therefore trivial and needs no new serialization format: on first
boot after the upgrade, if the SQLite `memory` table is empty and the JSON overlay carries a
`memory` block, call `load_state(block)` per agent (rebuilding `MemoryEntry`s in memory) and
flush them to SQLite. `restore()` in `loom/persistence.py` keeps calling `load_state`; only
where the entries *live afterward* changes. Because `MemoryStream` hides its store, `snapshot`
simply stops embedding the `memory` block once SQLite is authoritative — the callers
(`mind`, `director`, `engine`) never see it. Tolerant, versioned, one-directional.

## Comparison

| System | Importance | Retrieval | Embeddings source | Storage | Reflection / evolution | Loom verdict |
|---|---|---|---|---|---|---|
| **Generative Agents** | LLM 1-10 per memory, at write | recency(0.995 decay)+importance+relevance, min-max, weights 1/1/1 | LM embeddings, cosine | flat log | sum-importance>150 -> questions -> insights (cited) -> re-inserted; tree | **steal the retrieval formula verbatim; heuristic importance, not per-memory LLM; reflection deferred depth-1** |
| **MemGPT / Letta** | n/a (self-edited) | vector search over archival | vector DB (Chroma/pgvector) | core / archival / recall tiers | self-editing memory tools | steal the core-vs-archival *mental model* (Loom has it); skip self-edit + paging |
| **mem0** | novelty *gate* (ADD/UPDATE/DELETE/NOOP) | top-k cosine | vector DB | vector (+ optional graph) | LLM extract+update per exchange | steal the dedup/novelty idea later; skip per-add LLM pipeline + graph |
| **A-MEM** | tags/keywords per note | cosine + note links | vector DB | Zettelkasten note graph | LLM note-construction + link-evolution | shelve; borrow per-memory tags as a cheap lexical booster if wanted |
| **LangChain / LlamaIndex / RAG** | none (or metadata) | top-k cosine (+MMR/rerank) | pluggable embedder | pluggable vector store | summary memory | steal the pluggable-embedder pattern (already the plan); skip the framework |
| **Loom (proposed)** | **heuristic 1-10 at write (LLM later)** | **GA score: recency+importance+relevance, min-max, 1/1/1** | **OpenRouter `/embeddings` (fake+local-optional behind a seam)** | **one SQLite table by agent_id; float32 BLOB; brute-force** | **deferred; depth-1 with citations when it lands** | — |

---

## Recommendation for Loom — positions on the six questions, mapped to the code

### Q1 — Importance scoring: heuristic at write, LLM optional and batched later

**Rate importance with a cheap, deterministic heuristic inside `MemoryStream.add()`**, not a
model call per memory. A per-memory LLM rating is exactly what the affordable systems avoid
(mem0 replaces it with a novelty *gate*; a poignancy score at MUD volume is a continuous tax
across many NPCs). Loom's memories already carry a `kind`
(`observation`/`speech`/`event`/`reflection`) and engine-authored structure, which is enough
signal for a good heuristic: weight by kind (a `reflection` or an `event` outranks idle
`observation`), by presence of salience cues (a name, a gift, a promise, a threat, a
first-person commitment), and lightly by length/novelty. Deterministic -> offline-testable ->
no network -> no cost. **Rate-on-write, not batch**, because the heuristic is free.
*Deferred:* an **optional batched LLM importance pass** off the loop (rate N unscored
memories in one call) for higher fidelity, gated behind the same env switch as the rest —
and the `remember_fact` action (deferred from Phase 2) which writes a high-importance memory
explicitly. In retrieval, importance buys the thing recency-only cannot: an old, off-topic,
*salient* memory (the promise, the death) surviving the flood of recent trivia.
**Maps to:** `MemoryEntry` gains `importance: int`; `MemoryStream.add()` computes it inline
via a small pure `_score_importance(text, kind)` helper.

### Q2 — Retrieval formula: the Generative-Agents score, adopted verbatim

`retrieve(query, k)` scores each candidate as **`recency + importance + relevance`**, each
component **min-max normalized to [0,1] across the candidate pool**, **weights 1.0/1.0/1.0**
(exposed as constants so a game can retune, per the paper's own note that weights "should
potentially be learned"). **Recency** = `0.995 ** hours_since(t)` (map GA's game-hours onto
Loom's wall-clock `t`; a configurable decay base). **Importance** = the heuristic 1-10,
normalized. **Relevance** = cosine of the query embedding against each memory's embedding;
when a memory is unembedded or the embedder is down, relevance contributes 0 and the score
degrades gracefully to recency+importance. Return the top-k. *Simplification for slice 1:*
score on **creation time `t`**, not GA's last-*access* time — skip the retrieval-time
write-back that keeps recalled memories warm (a defer, noted). **Maps to:** a new
`MemoryStream.retrieve(query, k)` beside `recent(k)`; `recent(k)` stays as the query-free
path and the fallback.

### Q3 — Embeddings: OpenRouter primary, behind an `EmbeddingProvider` seam

**Primary: the OpenRouter `/api/v1/embeddings` endpoint** (Section 6a) — same key, same
provider family, OpenAI-shaped, `openai/text-embedding-3-small` (or the $0.01 `bge-m3` /
free NVIDIA model) — no new core dependency, no torch, no local server, not the forbidden
vLLM. **Fallback/offline: a `FakeEmbeddingProvider`** (deterministic hash vector) for tests
and a graceful degrade. **Optional: an in-process `model2vec` embedder** (numpy-only, an
*extra*, never core) for a zero-network deployment. All three sit behind an
`EmbeddingProvider` Protocol mirroring `LLMProvider`, resolved by `get_default_embedder()`
from env. **Embedding happens off the synchronous `add()` path** — either lazily at first
retrieval or in a background enrichment task (the engine already runs minds and director
beats off the loop; embedding rides the same pattern, batching up to ~96 texts per request).
**Maps to:** `EmbeddingProvider` + concretes in `loom/ai/provider.py`; the engine constructs
the default embedder alongside the chat provider and injects it into each `MemoryStream`.

### Q4 — Storage: one SQLite table by agent_id, packed BLOB, brute-force

Per Section 7: **one `memory` table keyed by `agent_id`**, vector a **float32 `BLOB` via
stdlib `array`** (no numpy in core), **brute-force cosine in Python** scanning only the
queried agent's rows. `INSERT` per memory replaces the JSON whole-file rewrite. **Brute
force stays fine to ~100k rows per scan; per-agent partitioning keeps the real scan in the
hundreds-to-thousands indefinitely** — reconsider `sqlite-vec` (C brute force, same BLOB,
zero-dep loadable extension) only if a single scan ever crosses ~100k; skip ANN
(hnswlib/faiss) indefinitely. **Migration:** reuse `state()`/`load_state()` as the one-time
JSON->SQLite import (Section 7). **Maps to:** `MemoryStream` grows a store backend (a
`SqliteMemoryStore` the stream holds); `loom/persistence.py::snapshot` stops embedding the
`memory` block once SQLite is authoritative; `restore` keeps calling `load_state` (now a
flush into SQLite). The DB path is a game-opt-in like the rest of persistence
(`Engine.attach_persistence`); tests use `:memory:`.

### Q5 — Reflection: deferred to slice 2, then the minimal depth-1 form

Reflection is real and it is what **B10 idle-NPC autonomy** wants (an idle beat should flow
from reflected memory, not mechanical recency). But it is a *second LLM subsystem* — two
model calls (questions, then synthesis) on a cadence — and the first slice proves more value
without it. **Defer to slice 2.** When it lands, the minimal useful form: **trigger** on
accumulated importance since the last reflection crossing a threshold (GA's 150 is tuned to
their scale; Loom sets its own, or simply every N salient memories); **generate** 1-3
questions from the recent stream; **retrieve** per question via `retrieve()`; **synthesize**
1-3 insight statements and **insert them back as `MemoryEntry(kind="reflection")`** with high
importance and the cited memory ids in the text. **Depth 1 only** — no
reflections-on-reflections tree, no daily-planning use. It runs off the loop on a slow
cadence, exactly like the director, and is opt-in. **Maps to:** a `Reflector` beside
`Director` (a slow loop system) calling `MemoryStream.add(..., kind="reflection")`; no seam
change — a reflection is just another memory.

### Q6 — Broader prior art: what Loom takes

From MemGPT the **core-vs-archival mental model** (Loom already has it: `recent()` = core,
`retrieve()` = archival); from mem0 the **novelty/dedup idea** as a cheap future win (do not
store the same greeting 40 times); from A-MEM **per-memory tags** as an optional lexical
booster; from RAG the **pluggable-embedder + top-k-cosine** baseline (already the plan). Loom
skips every framework and every heavyweight (self-editing tools, paging, graph memory,
per-add LLM pipelines, torch, ANN libs, vector-DB services).

### The interface honesty check (commitment #6, verified against the code)

Commitment #6 promises importance/embeddings/retrieval/reflection slot in "without touching
callers." **Verified against `mind.py` and `director.py`:** callers use `self.memory.recent()`
(prompt assembly) and `self.memory.add()` (recording). Precisely:

- **Importance** slots in with **zero caller change** — computed inside `add()`, and it can
  even upgrade `recent()` in place (recency+importance needs no query).
- **Embeddings/storage** slot in with **zero caller change** — hidden entirely inside
  `MemoryStream`; `add()`/`recent()`/`state()`/`load_state()` keep their signatures.
- **Relevance retrieval is the one exception, and it is inherent, not incidental:** relevance
  needs a *query*, which `recent()` does not take. So the two callers must pass the current
  context (`mind._base_lines` -> the incoming utterance/scene; `director._system_prompt` -> the
  chronicle digest) into a new `retrieve(query, k)`. This is a **small, local, honest caller
  touch** — the only place the "without touching callers" promise bends, and it bends because
  query-free relevance is a contradiction in terms. Flag it in the build plan; do not pretend
  it is free.

### Determinism / testability (offline, no GPU, no network)

- **`FakeEmbeddingProvider`** — a pure function `text -> vector`: tokenize, hash tokens into a
  fixed-dim vector, L2-normalize. Same text -> same vector, deterministic, no network. Tests
  assert `retrieve("a query about the storm")` ranks a storm memory above an unrelated one,
  and that an important-but-old memory outranks recent trivia — the exact value claim.
- **Heuristic importance** — deterministic (kind + cue + length); unit-tested directly.
- **SQLite `:memory:`** — the store runs in-process with no file; the whole retrieval path is
  offline-provable with `FakeProvider` + `FakeEmbeddingProvider`, matching the existing 444
  offline-test discipline.

---

## The tight first slice (and its proof)

**Slice: importance (heuristic) + embedding-relevance retrieval on SQLite. Reflection
deferred.** The smallest end-to-end increment that proves value over recency-only:

1. **`MemoryEntry` gains `importance: int` and `embedding: list[float] | None`;** `state()`/
   `load_state()` extend to round-trip them (trivial — they already serialize per-field).
2. **`EmbeddingProvider` seam** in `loom/ai/provider.py` with `OpenRouterEmbeddingProvider`
   and `FakeEmbeddingProvider`; `get_default_embedder()` from env; the engine injects it.
3. **`MemoryStream` backed by SQLite** (one `memory` table by `agent_id`; float32 BLOB via
   stdlib `array`). `add()` computes heuristic importance inline and `INSERT`s one row;
   embedding is filled off the loop. **`retrieve(query, k)`** = brute-force GA score
   (recency 0.995-decay + importance + relevance cosine, min-max normalized, weights 1/1/1).
   `recent(k)` unchanged as the fallback.
4. **Migration:** the JSON overlay's `memory` block imports once into SQLite via `load_state`.
5. **Caller touch:** `mind._base_lines` and `director._system_prompt` call `retrieve(query)`
   with the current utterance/scene/chronicle instead of `recent()` (recency-only stays the
   graceful degrade when there is no embedder).

**The proof (offline, deterministic, with `FakeEmbeddingProvider`):** seed an NPC's stream
with one high-importance memory early — *"The player swore to bring me the black key."* —
then bury it under a dozen mundane recent observations so it has fallen out of `recent(8)`.
When the player later says something about *the key*, `retrieve()` **surfaces the buried
promise** (relevance + importance rank it into the retrieved set) where `recent()` cannot
see it. That is the crisp, single-assertion demonstration that importance + relevance
retrieval beats recency-only — the whole reason the slice exists. Mechanical (the retrieval
math) is proven offline; the live gate then confirms a real embedder ranks paraphrase
("the dark key," "that iron thing you promised") the way the fake ranks exact terms.

---

## Defer (with reasons)

- **Reflection** (Section 5 / Q5) — a second LLM subsystem; ship retrieval first, reflection
  as slice 2. It is the prerequisite for the *rich* form of B10 idle-NPC autonomy.
- **LLM importance scoring** — the heuristic suffices and is free; a batched off-loop LLM
  pass is an optional fidelity upgrade, not a first-slice need. A per-memory LLM rating is
  rejected outright on cost.
- **In-process local embedder** (model2vec/fastembed) — OpenRouter embeddings are the primary
  path; the local library is an optional *extra* for zero-network deployments, behind the same
  seam. Never in `loom/` core (native wheels).
- **numpy in the core** — stdlib `array` packs the BLOB and pure-Python cosine scans a MUD's
  per-agent pool fast enough; numpy is an optional acceleration only.
- **`sqlite-vec` / ANN indexes** — brute force with per-agent partitioning is fine to a scale
  a MUD never reaches; adopt `sqlite-vec` only past ~100k rows/scan (same BLOB, local change),
  skip hnswlib/faiss indefinitely.
- **mem0-style novelty/dedup gate and A-MEM link-evolution** — valuable later (an NPC should
  not store the same greeting repeatedly), but each is an LLM-per-add cost or a graph to
  maintain; not the first slice.
- **Last-access recency (GA's retrieval write-back)** — score on creation `t` first; add
  last-access warming later if recall patterns warrant it.
- **MMR / reranking** — scale-driven retrieval-quality refinements; unnecessary at per-agent
  pool sizes.
- **`remember_fact` action** — lands here (deferred from Phase 2) once retrieval exists; a
  thin high-importance `add()`, not a first-slice blocker.

---

## What to steal / what to skip

**Steal**
- **The Generative-Agents retrieval score, verbatim** — `recency + importance + relevance`,
  min-max normalized, weights 1/1/1, recency an exp-decay (0.995). It is the field standard
  and Loom already descends from it (Section 1).
- **Importance as a salience signal, but scored by heuristic** — the *effect* GA's importance
  buys (old-but-salient memory survives), without GA's per-memory LLM cost; mem0's lesson that
  the affordable systems gate/proxy rather than rate every memory (Sections 1, 3, Q1).
- **OpenRouter embeddings on the existing hosted seam** — the verified finding that dissolves
  the crux: embeddings with no new dependency, no torch, no local server, not the vLLM box
  (Section 6a).
- **The `EmbeddingProvider` Protocol mirroring `LLMProvider`** — fake/local/hosted swappable,
  the engine can't tell fake from real; RAG's pluggable-embedder pattern (Sections 5, 6, Q3).
- **SQLite rows + packed-BLOB vector + brute-force cosine** — the plan's own "SQLite +
  brute-force cosine," confirmed correct for MUD scale by sqlite-vec's benchmarks; incremental
  `INSERT` over the JSON whole-file rewrite (Section 7).
- **`state()`/`load_state()` as the JSON->SQLite migration bridge** — the persistence slice
  already built it; the move costs no new format (Section 7, Q4).
- **The MemGPT core-vs-archival mental model** — name the split Loom already has:
  `recent()` = core, `retrieve()` = archival (Section 2).

**Skip (for now)**
- **A model call per memory** for importance — the per-memory tax every affordable system
  avoids; heuristic first, batched-LLM optional later (Q1).
- **mem0's LLM extract+update pipeline and graph, A-MEM's note-construction + link-evolution**
  — LLM-per-add cost and graphs to maintain; Loom's memories are already short salient strings
  (Sections 3, 4).
- **MemGPT self-editing memory tools + the agent-as-OS paging apparatus** — a second security-
  seam action surface and a large build; Loom's memory is observed, not agent-rewritten (Section 2).
- **torch / sentence-transformers in the core; ANN libs (faiss/hnswlib); vector-DB services
  (Chroma/pgvector/Qdrant)** — all heavy and/or native, against the dependency budget; SQLite
  brute force suffices (Sections 6, 7).
- **Adopting LangChain/LlamaIndex** — take the retrieval *pattern*, not the framework
  (Section 5).
- **Reflection trees deeper than one level, daily-planning, last-access warming, MMR** — real
  but not first-slice (Q5, Defer).

---

## Appendix — sources & verification status

| Claim area | Source | Status |
|---|---|---|
| GA retrieval: score = recency+importance+relevance, weights all 1.0, min-max [0,1] normalized | [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) via [ar5iv](https://ar5iv.labs.arxiv.org/html/2304.03442) | **Verified (fetched, quoted)** |
| GA recency = exp decay factor 0.995 over game-hours since last access | same | **Verified (fetched, quoted)** |
| GA importance = LLM 1-10 at write, exact prompt ("purely mundane... extremely poignant"), examples 2 and 8 | same | **Verified (fetched, quoted)** |
| GA reflection: trigger sum-importance > 150 (~2-3x/day); 100 recent records -> 3 questions; "insight (because of 1,5,3)"; tree of increasingly abstract nodes | same | **Verified (fetched, quoted)** |
| OpenRouter serves `POST /api/v1/embeddings`, OpenAI-shaped, models incl. text-embedding-3-small/large, qwen3-embedding, bge-m3, nvidia nemotron (free) | [OpenRouter embeddings docs](https://openrouter.ai/docs/api_reference/embeddings), [embedding models](https://openrouter.ai/collections/embedding-models) | **Verified (fetched)**; recent addition — model list/pricing may drift |
| Embedding prices: 3-small $0.02/M, 3-large $0.13/M, qwen3-8b $0.01/M, bge-m3 $0.01/M, perplexity $0.004/M, nvidia free | [OpenRouter embedding models](https://openrouter.ai/collections/embedding-models) | **Verified (fetched)** |
| Model2Vec: static distilled embeddings, only major dep numpy (no torch), ~15x smaller / ~500x faster CPU, ~85-95% quality, uncontextualized | [MinishLab/model2vec](https://github.com/MinishLab/model2vec), [PyPI](https://pypi.org/project/model2vec/) | **Verified (search)** |
| FastEmbed: ONNX Runtime, no PyTorch/CUDA, "doesn't download GBs of PyTorch" | [qdrant/fastembed](https://github.com/qdrant/fastembed), [Qdrant docs](https://qdrant.tech/documentation/fastembed/) | **Verified (search)** |
| sqlite-vec: runtime-loadable C extension, zero deps, `pip install sqlite-vec`, float+bit vectors as BLOB in shadow tables, brute-force only (ANN on roadmap), 100k vectors <75ms, degrades past 1M | [Alex Garcia — sqlite-vec stable release](https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html) | **Verified (fetched, quoted)** |
| Brute-force fine for thousands-to-hundreds-of-thousands; float vector = 4 bytes/dim | [sqlite-vec blog](https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html), [DEV: SQLite as a vector DB](https://dev.to/zoricic/sqlite-as-a-vector-database-yes-really-4cm4) | **Verified (fetched + search)** |
| MemGPT/Letta: core (persona+user, self-edited) / archival (vector DB) / recall (conversation) tiers; paging via tools | [Letta docs — MemGPT](https://docs.letta.com/letta-memgpt), [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) | **Verified (docs + search)** |
| mem0: add/search two-phase (extraction -> update); ADD/UPDATE/DELETE/NOOP dedup; vector (+graph Mem0g) | [arXiv:2504.19413](https://arxiv.org/html/2504.19413v1), [docs.mem0.ai](https://docs.mem0.ai) | **Verified (search)** |
| A-MEM: Zettelkasten; LLM note-construction (context/keywords/tags), link-generation, memory-evolution; NeurIPS 2025 | [arXiv:2502.12110](https://arxiv.org/abs/2502.12110), [WujiangXu/A-mem](https://github.com/WujiangXu/A-mem) | **Verified (search)** |
| LangGraph long-term memory `BaseStore` with optional semantic search (embed config, cosine); LangChain `VectorStoreRetrieverMemory`; RAG top-k + MMR | [LangGraph memory docs](https://langchain-ai.github.io/langgraph/concepts/memory/) | **Not fetched live (web fetch unavailable this spike); based on official-docs knowledge — flag if exact API names become load-bearing** |
| Dependency budget: no native extension in `loom/` core (numpy, sqlite-vec, onnxruntime, torch are native wheels); `httpx` already core | `docs/DEPENDENCIES.md`, `pyproject.toml` | **Verified against working tree** |
| Loom code facts: `MemoryStream`/`MemoryEntry(text,kind,t)` + `add`/`recent`/`state`/`load_state`; callers use `self.memory.recent()`/`.add()` (`mind._base_lines`, `director._system_prompt`); `LLMProvider` Protocol + `get_default_provider`; persistence JSON overlay serializes `mind.memory.state()` and rewrites whole-file; Phase 5 = "SQLite + brute-force cosine" | `loom/ai/memory.py`, `loom/ai/mind.py`, `loom/ai/director.py`, `loom/ai/provider.py`, `loom/persistence.py`, `docs/PLAN.md`, `docs/spikes/persistence.md` | **Verified against working tree** |
