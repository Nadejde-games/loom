"""The embedding abstraction — the sibling of ``LLMProvider`` for turning text into
vectors, so memory retrieval can rank by *meaning* (relevance) and not only recency.

The whole retrieval path depends only on ``EmbeddingProvider``; which concrete
embedder answers is a swappable detail, exactly as with chat. Three concretes ship:

  * FakeEmbeddingProvider    — offline, deterministic, zero deps or network. A stable
                               bag-of-words hash, so the retrieval math is fully
                               testable and "the engine can't tell fake from real."
  * OpenRouterEmbeddingProvider — the hosted path: OpenRouter's OpenAI-shaped
                               ``/api/v1/embeddings`` on the *same key and base-url
                               family as chat*. No new server, not the vLLM box, no
                               torch — one ``httpx`` POST (httpx is already core).
  * (a local in-process embedder — model2vec/fastembed — is a deliberate DEFERRED
    option for a zero-network deployment; it stays out of the core, see the spike.)

Design note: embeddings are a *derived cache* of a memory's text. In this first slice
they are held on the ``MemoryEntry`` in memory and re-computed on demand after a
restart (cheap, off the loop) rather than persisted — so this seam adds no dependency
and no storage change. See ``docs/spikes/memory.md``.
"""
from __future__ import annotations
import math
import os
import zlib
from typing import Protocol, runtime_checkable

from .provider import ProviderError


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turn texts into fixed-length vectors. ``embed`` takes a batch and returns one
    vector per input, in order. ``dim`` is the vector length (informational — the
    retrieval math never assumes a particular size, only that a query and a memory
    share one embedder)."""
    dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


class FakeEmbeddingProvider:
    """A deterministic, network-free embedder for offline tests and a graceful
    degrade. It is a **stable bag-of-words hash**: each word is hashed (via
    ``zlib.crc32`` — stable across processes, unlike the salted built-in ``hash``)
    into one of ``dim`` buckets, the buckets counted, then L2-normalized. Two texts
    that share words get overlapping vectors (non-zero cosine); unrelated texts get
    near-orthogonal ones — enough to prove that relevance retrieval surfaces a memory
    about *the key* for a query about *the key*, where recency cannot. It captures no
    real semantics (no paraphrase), which is exactly why it is a test stub, not the
    production embedder — that is the live gate's job."""
    name = "fake-embed"

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in _WORDS(text):
            vec[zlib.crc32(word.encode("utf-8")) % self.dim] += 1.0
        return _l2_normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


def _WORDS(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens — the fake embedder's vocabulary."""
    out, cur = [], []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


class OpenRouterEmbeddingProvider:
    """Hosted embeddings via OpenRouter's OpenAI-shaped ``/api/v1/embeddings`` — the
    same key, base-url family, and ``httpx`` transport the chat provider already uses
    (verified live: a `baai/bge-m3` call returns a 1024-dim vector at ~1e-7 per short
    memory). Not the forbidden local vLLM box; no new dependency.

    Batches: one request embeds many texts (``input`` as an array), returned in
    ``data[i].index`` order. Transient failures retry with linear backoff; an
    exhausted call raises ``ProviderError`` so the retrieval path can degrade to
    recency+importance rather than break a turn."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "baai/bge-m3", api_key: str | None = None,
                 host: str | None = None, dim: int = 1024, timeout: float = 30.0,
                 retries: int = 3):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.url = (host or self.BASE_URL).rstrip("/") + "/embeddings"
        self.dim = dim
        self.timeout = timeout
        self.retries = max(1, retries)
        self.name = f"openrouter-embed:{model}"
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import asyncio
        import httpx
        payload = {"model": self.model, "input": list(texts)}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = self._get_client()
        last: object = None
        for attempt in range(self.retries):
            try:
                resp = await client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last = exc
                if attempt < self.retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(f"cannot reach {self.url}: {exc}") from exc
            if resp.status_code >= 400:
                if resp.status_code in (429, 503, 529) and attempt < self.retries - 1:
                    last = f"HTTP {resp.status_code}"
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(
                    f"HTTP {resp.status_code} from {self.url}: {resp.text[:200]}")
            data = resp.json()
            try:
                items = sorted(data["data"], key=lambda d: d.get("index", 0))
                return [list(it["embedding"]) for it in items]
            except (KeyError, TypeError) as exc:
                raise ProviderError(f"unexpected embeddings shape: {data!r}") from exc
        raise ProviderError(f"embeddings failed after {self.retries} tries: {last}")


def get_default_embedder() -> EmbeddingProvider | None:
    """Resolve an embedder from the environment (explicit, no network probing), or
    ``None`` when none is configured — in which case retrieval degrades to
    recency+importance (still a real upgrade over recency alone).

    * LOOM_EMBEDDER = fake | openrouter | none  — force a choice.
    * else an OpenRouter key present            — hosted OpenRouter embeddings.
    * else                                      — None (recency+importance only).

    Tuning: LOOM_EMBED_MODEL (default ``baai/bge-m3``), OPENROUTER_API_KEY (or
    LOOM_OPENROUTER_API_KEY), LOOM_OPENROUTER_HOST.
    """
    choice = os.environ.get("LOOM_EMBEDDER", "").strip().lower()
    if choice == "none":
        return None
    if choice == "fake":
        return FakeEmbeddingProvider()
    key = (os.environ.get("LOOM_OPENROUTER_API_KEY")
           or os.environ.get("OPENROUTER_API_KEY"))
    if choice == "openrouter" or (not choice and key):
        return OpenRouterEmbeddingProvider(
            model=os.environ.get("LOOM_EMBED_MODEL", "baai/bge-m3"),
            host=os.environ.get("LOOM_OPENROUTER_HOST") or None,
            api_key=key or None)
    return None
