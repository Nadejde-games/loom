"""The LLM abstraction. The whole engine depends only on ``LLMProvider`` —
which concrete model answers is a swappable detail.

Providers that ship here:
  * FakeProvider            — offline, deterministic, zero deps or API key.
  * OpenAICompatibleProvider — any server exposing /v1/chat/completions
                               (Ollama, vLLM, SGLang, TGI, OpenAI). Stdlib-only.
  * OllamaProvider          — local inference via Ollama (a thin subclass).
  * AnthropicProvider       — real Claude, imported lazily.

Design note: OpenAI-compatible is the portable waist. Ollama for dev now,
vLLM/SGLang for scale later, differ only by base_url + model name — no client
change. So we deliberately talk the OpenAI chat schema, not vendor extensions.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, system: str, messages: list[dict]) -> str: ...


class ProviderError(RuntimeError):
    """A provider call failed (network, HTTP error, or exhausted retries)."""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks (Qwen3 and similar)."""
    return _THINK_RE.sub("", text).strip()


class FakeProvider:
    """A stand-in that fabricates persona-flavoured replies with no network.

    Good enough to prove the whole spine end-to-end and to keep tests
    deterministic. It does not reason — swap in a real provider for a real
    mind. The point is that nothing else in the engine can tell the difference.
    """
    name = "fake"

    async def complete(self, system: str, messages: list[dict]) -> str:
        utterance = ""
        for m in messages:
            if m.get("role") == "user":
                utterance = str(m.get("content", ""))
        # Recover the character's display name from the system prompt.
        who = "The figure"
        for line in system.splitlines():
            if line.startswith("You are "):
                who = line[len("You are "):].split(",")[0].split(".")[0].strip() or who
                break
        u = utterance.lower()
        if any(g in u for g in ("hello", "hi ", "hi.", "greet", "hey", "well met")):
            return f'{who} inclines their head. "Well met. Few wander this far."'
        if "?" in utterance:
            return f'{who} considers the question. "That is not a thing I answer lightly."'
        if any(b in u for b in ("bye", "farewell", "leave", "goodbye")):
            return f'{who} raises a hand. "Go carefully, then."'
        return (f'{who} listens, then answers slowly. '
                f'"There is more to that than you know."')


class OpenAICompatibleProvider:
    """Client for any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Dependency-free: the blocking POST runs in a worker thread so the call
    stays awaitable without freezing the event loop. Transient failures
    (429/503/529, connection resets) are retried with linear backoff.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 60.0, max_tokens: int = 400,
                 temperature: float = 0.8, extra_body: dict | None = None,
                 system_suffix: str = "", strip_think: bool = True,
                 retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra_body = extra_body or {}
        self.system_suffix = system_suffix
        self.strip_think = strip_think
        self.retries = max(1, retries)
        self.name = f"openai-compat:{model}"

    async def complete(self, system: str, messages: list[dict]) -> str:
        if self.system_suffix:
            system = f"{system}\n\n{self.system_suffix}"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
            **self.extra_body,
        }
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._post, payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc
        if self.strip_think:
            text = _strip_think(text)
        return text.strip()

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last: Exception | None = None
        for attempt in range(self.retries):
            req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503, 529) and attempt < self.retries - 1:
                    last = exc
                    time.sleep(0.5 * (attempt + 1))
                    continue
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                raise ProviderError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(f"cannot reach {self.url}: {exc}") from exc
        raise ProviderError(f"request failed after {self.retries} tries: {last}")


class OllamaProvider(OpenAICompatibleProvider):
    """Local inference via Ollama's OpenAI-compatible endpoint.

    Same class shape serves vLLM/SGLang later — point a plain
    OpenAICompatibleProvider at their ``/v1`` instead.

    Thinking models (Qwen3.x) route chain-of-thought into a separate
    ``reasoning`` field and count it against the token budget — so left on,
    they burn the whole budget deliberating and return empty ``content``. We
    therefore default ``think=False``, sending ``reasoning_effort: "none"`` so
    the spoken reply lands in ``content`` and turns stay fast. Pass
    ``think=True`` to allow deliberation (e.g. the game-master planning).
    """

    def __init__(self, model: str = "qwen3.5:35b-a3b",
                 host: str = "http://localhost:11434",
                 think: bool = False, timeout: float = 120.0, **kw):
        extra = dict(kw.pop("extra_body", None) or {})
        if not think:
            extra.setdefault("reasoning_effort", "none")
        super().__init__(base_url=host.rstrip("/") + "/v1", model=model,
                         api_key="ollama", timeout=timeout, extra_body=extra, **kw)
        self.host = host
        self.name = f"ollama:{model}"


class AnthropicProvider:
    """Real Claude. Requires the ``anthropic`` package and an API key."""

    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None,
                 max_tokens: int = 400):
        import anthropic  # lazy: only imported if actually instantiated
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"

    async def complete(self, system: str, messages: list[dict]) -> str:
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()


def get_default_provider() -> LLMProvider:
    """Resolve a provider from the environment (explicit, no network probing):

    * LOOM_PROVIDER = fake | ollama | anthropic   — force a choice.
    * else LOOM_OLLAMA_MODEL set                   — use local Ollama.
    * else ANTHROPIC_API_KEY set                   — use Claude.
    * else                                         — FakeProvider.

    Ollama tuning: LOOM_OLLAMA_MODEL (default qwen3.5:35b-a3b), LOOM_OLLAMA_HOST.
    """
    choice = os.environ.get("LOOM_PROVIDER", "").strip().lower()
    if choice == "fake":
        return FakeProvider()
    if choice == "ollama" or (not choice and os.environ.get("LOOM_OLLAMA_MODEL")):
        return OllamaProvider(
            model=os.environ.get("LOOM_OLLAMA_MODEL", "qwen3.5:35b-a3b"),
            host=os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434"),
        )
    if choice == "anthropic" or (not choice and os.environ.get("ANTHROPIC_API_KEY")):
        try:
            return AnthropicProvider()
        except Exception as exc:
            print(f"[loom] anthropic unavailable ({exc!r}); using FakeProvider")
    return FakeProvider()
