"""The LLM abstraction. The whole engine depends only on ``LLMProvider`` —
which concrete model answers is a swappable detail.

Providers that ship here:
  * FakeProvider            — offline, deterministic, zero deps or API key.
  * OpenAICompatibleProvider — any server exposing /v1/chat/completions
                               (Ollama, vLLM, SGLang, TGI, OpenAI, OpenRouter).
  * OllamaProvider          — local inference via Ollama (a thin subclass).
  * AnthropicProvider       — real Claude, imported lazily.

Design note: OpenAI-compatible is the portable waist. Ollama for dev now,
vLLM/SGLang/OpenRouter for scale later, differ only by base_url + model name — no
client change. So we deliberately talk the OpenAI chat schema, not vendor extensions.

HTTP goes through ``httpx`` (a core dependency; see docs/DEPENDENCIES.md) — native
async and connection pooling, a real latency win on remote backends where each call
would otherwise pay a fresh TLS handshake. It is imported lazily, so the offline
``FakeProvider`` path never needs it.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, system: str, messages: list[dict],
                       schema: dict | None = None,
                       temperature: float | None = None) -> str: ...


class ProviderError(RuntimeError):
    """A provider call failed (network, HTTP error, or exhausted retries)."""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks (Qwen3 and similar)."""
    return _THINK_RE.sub("", text).strip()


# FakeProvider move detection: whole-word cues that invite departure, and the
# "Exits you can take: …" line the engine puts in the scene, so the fake picks a
# real exit rather than guessing one the handler would reject.
_MOVE_RE = re.compile(r"\b(go|leave|away|follow|flee|wander|"
                      r"north|south|east|west|up|down)\b", re.IGNORECASE)
_EXITS_RE = re.compile(r"Exits you can take:\s*(.+)")


def _perceived_exits(system: str) -> list[str]:
    m = _EXITS_RE.search(system)
    if not m:
        return []
    return [d.strip() for d in m.group(1).split(",") if d.strip()]


class FakeProvider:
    """A stand-in that fabricates persona-flavoured replies with no network.

    Good enough to prove the whole spine end-to-end and to keep tests
    deterministic. It does not reason — swap in a real provider for a real
    mind. The point is that nothing else in the engine can tell the difference.
    """
    name = "fake"

    async def complete(self, system: str, messages: list[dict],
                       schema: dict | None = None,
                       temperature: float | None = None) -> str:
        # ``schema`` (the constrained-decoding grammar) and ``temperature`` are
        # both ignored: the fake has no model to constrain or to sample, and the
        # offline suite must behave identically with or without them. Structured
        # mode is still keyed off the prompt.
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
            speech = f'{who} inclines their head. "Well met. Few wander this far."'
        elif "?" in utterance:
            speech = f'{who} considers the question. "That is not a thing I answer lightly."'
        elif any(b in u for b in ("bye", "farewell", "leave", "goodbye")):
            speech = f'{who} raises a hand. "Go carefully, then."'
        else:
            speech = (f'{who} listens, then answers slowly. '
                      f'"There is more to that than you know."')
        # Plain-text mode: no action schema was requested, reply as prose.
        if '"speech"' not in system:
            return speech
        # Structured mode: emit the JSON turn envelope. Attach a deterministic
        # emote when the prompt offers one and the utterance invites action —
        # enough to exercise the action seam offline and in tests.
        actions: list[dict] = []
        if "emote" in system and any(t in u for t in
                                     ("dance", "bow", "nod", "gesture", "wave")):
            actions.append({"name": "emote",
                            "args": {"text": "regards you with a slow, deliberate air"}})
        # Move when the action is offered, the utterance invites departure, and
        # the perceived surroundings list a real exit — enough to drive move's
        # two-room broadcast offline and in tests.
        if "move" in system and _MOVE_RE.search(u):
            exits = _perceived_exits(system)
            if exits:
                actions.append({"name": "move", "args": {"direction": exits[0]}})
        return json.dumps({"speech": speech, "actions": actions})


class OpenAICompatibleProvider:
    """Client for any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Async and pooled via httpx: a shared ``AsyncClient`` per provider keeps
    connections alive across turns (no fresh TLS handshake each call). Transient
    failures (429/503/529, connection resets) are retried with linear backoff.
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
        self._client = None   # lazy httpx.AsyncClient, reused for connection pooling
        # Whether to STREAM the reply when an inference reporter is watching, so a live
        # token count can be surfaced. On for slow local backends (Ollama/vLLM), where the
        # per-second feedback matters; off for the hosted OpenRouter tier, which is fast and
        # only wants the end tally (its non-streamed response carries usage). Never streams
        # when no reporter is installed — the offline/plain path is untouched.
        self.stream_progress = False

    def _schema_payload(self, schema: dict) -> dict:
        """Payload fields that constrain generation to ``schema``, in the OpenAI
        standard form (``response_format`` / ``json_schema``, ``strict``).

        The one place per-backend constraint dialects are translated — the same
        role ``extra_body`` plays for ``reasoning_effort`` / ``num_ctx``. A vLLM
        that wants ``guided_json`` or an Ollama that needs the native ``format``
        field overrides only this method; ``complete`` and the registry are
        untouched, and the JSON Schema stays the portable form between them.
        """
        return {"response_format": {
            "type": "json_schema",
            "json_schema": {"name": "turn", "schema": schema, "strict": True},
        }}

    async def complete(self, system: str, messages: list[dict],
                       schema: dict | None = None,
                       temperature: float | None = None) -> str:
        if self.system_suffix:
            system = f"{system}\n\n{self.system_suffix}"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "max_tokens": self.max_tokens,
            # A per-call override (e.g. the director's low-temp act-gate) wins over
            # the provider's configured default; None means "use the default".
            "temperature": self.temperature if temperature is None else temperature,
            "stream": False,
            **self.extra_body,
        }
        if schema is not None:
            payload.update(self._schema_payload(schema))

        from . import telemetry
        reporter = telemetry.get_reporter()
        if reporter is None:
            # The plain path — unchanged, and what the offline suite exercises.
            return self._clean(self._content(await self._post(payload)))

        # A reporter is watching: announce the call, keep a live elapsed/token heartbeat,
        # and finalise with a tally. Slow local backends stream so tokens climb in view;
        # the hosted tier stays non-streamed and reports its token count from usage.
        call = telemetry.new_call(self.model)
        reporter.start(call)
        heartbeat = asyncio.create_task(self._heartbeat(call, reporter))
        try:
            if self.stream_progress:
                text = await self._post_streamed(payload, call)
            else:
                data = await self._post(payload)
                usage = (data or {}).get("usage") or {}
                if usage.get("completion_tokens"):
                    call.tokens = int(usage["completion_tokens"])
                text = self._content(data)
            call.ok = True
            return self._clean(text)
        except BaseException:
            call.ok = False
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            call.stop(ok=call.ok)
            reporter.finish(call)

    def _content(self, data: dict) -> str:
        """The message content out of a chat-completions response, or a clear error."""
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc

    def _clean(self, text: str) -> str:
        """Post-process reply text: drop <think> blocks (when configured) and trim."""
        if self.strip_think:
            text = _strip_think(text)
        return text.strip()

    @staticmethod
    def _stream_delta(line: str):
        """Parse one SSE ``data:`` line into ``(piece, usage)`` — ``piece`` is the content
        fragment (``"[DONE]"`` for the terminator, ``None`` for a keepalive/parse miss),
        ``usage`` the token accounting when a final chunk carries it. Pure, so the stream
        parse is unit-tested without a socket."""
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None, None
        payload = line[5:].strip()
        if payload == "[DONE]":
            return "[DONE]", None
        try:
            obj = json.loads(payload)
        except ValueError:
            return None, None
        piece = None
        for ch in obj.get("choices") or []:
            frag = (ch.get("delta") or {}).get("content")
            if frag:
                piece = (piece or "") + frag
        return piece, obj.get("usage")

    async def _post_streamed(self, payload: dict, call) -> str:
        """Stream the reply, counting content deltas into ``call.tokens`` as they arrive so
        a watcher sees the token count climb, and returning the accumulated text. Retries
        only apply to opening the stream (``_post`` owns the non-streamed retry loop); a
        mid-stream failure raises, which the engine's validate/retry already tolerates."""
        import httpx
        body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = self._get_client()
        await self._pace_wait()
        parts: list[str] = []
        usage: dict = {}
        try:
            async with client.stream("POST", self.url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                    self._pace_note(self._is_retryable(resp.status_code, detail))
                    raise ProviderError(f"HTTP {resp.status_code} from {self.url}: {detail}")
                self._pace_note(False)
                async for line in resp.aiter_lines():
                    piece, u = self._stream_delta(line)
                    if u:
                        usage = u
                    if piece == "[DONE]":
                        break
                    if piece:
                        parts.append(piece)
                        call.tokens += 1        # one content delta ≈ one token (live proxy)
        except httpx.HTTPError as exc:
            raise ProviderError(f"cannot reach {self.url}: {exc}") from exc
        if usage.get("completion_tokens"):
            call.tokens = int(usage["completion_tokens"])   # exact count if the tail gave it
        return "".join(parts)

    async def _heartbeat(self, call, reporter, period: float = 1.0, after: float = 1.0) -> None:
        """Emit a ``progress`` tick every ``period`` seconds once the call has run longer
        than ``after`` — so fast calls stay quiet (start + finish only) and a slow one shows
        a live, ticking elapsed/token line. Cancelled when the call finishes."""
        try:
            await asyncio.sleep(after)
            while True:
                try:
                    reporter.progress(call)
                except Exception:
                    pass            # a reporter must never break the inference path
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            return

    def _get_client(self):
        """The shared ``httpx.AsyncClient`` for this provider, created on first use
        so its connection pool is reused across calls (keep-alive — no fresh TLS
        handshake per turn) and the offline ``FakeProvider`` never imports httpx."""
        if self._client is None:
            import httpx   # lazy: only when this provider actually reaches a server
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _is_retryable(self, status: int, body: str) -> bool:
        """Whether an HTTP error status should be retried with backoff — the standard
        transient statuses. A subclass widens this when a backend disguises a
        rate-limit as another status (see ``OpenRouterProvider``)."""
        return status in (429, 503, 529)

    async def _pace_wait(self) -> None:
        """Hook: await any client-side pacing before a request. No-op by default;
        a rate-limited backend (see ``OpenRouterProvider``) overrides it to space
        bursts out and stay under a server rate limit."""
        return

    def _pace_note(self, throttled: bool) -> None:
        """Hook: record a request's outcome (throttled or clean) so adaptive pacing
        can tighten or relax. No-op by default."""
        return

    async def _post(self, payload: dict) -> dict:
        """POST the chat request and return the decoded JSON. Async and pooled via
        httpx; paces bursts (``_pace_wait``), retries the transient statuses
        (``_is_retryable``) and transport errors with linear backoff, then raises
        ``ProviderError``. The overridable seam the capturing test doubles replace to
        assert the wire payload without a network."""
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = self._get_client()
        last: object = None
        for attempt in range(self.retries):
            await self._pace_wait()
            try:
                resp = await client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:       # transport: connect/read/timeout
                last = exc
                if attempt < self.retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(f"cannot reach {self.url}: {exc}") from exc
            if resp.status_code >= 400:
                body = resp.text
                if self._is_retryable(resp.status_code, body) and attempt < self.retries - 1:
                    self._pace_note(True)        # throttled -> pace the next calls out
                    last = f"HTTP {resp.status_code}"
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(
                    f"HTTP {resp.status_code} from {self.url}: {body[:200]}")
            self._pace_note(False)               # clean -> relax the pacing
            return resp.json()
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
        # Local + slow: stream when a reporter watches, so the token count ticks live.
        self.stream_progress = True


class VLLMProvider(OpenAICompatibleProvider):
    """Local inference via a vLLM OpenAI-compatible server (``/v1``).

    The sibling of ``OllamaProvider`` for the other local backend the swappable
    design always anticipated — same OpenAI waist, only the quirks differ:

    * **Thinking off by default.** Qwen3.x defaults to reasoning, and with no
      ``--reasoning-parser`` on the server the chain-of-thought lands *inline in
      ``content``* and (on a small token budget) crowds out the actual answer —
      the same failure the Ollama provider guards against. We send
      ``reasoning_effort: "none"`` (verified to suppress it on this build; the
      Qwen template's ``chat_template_kwargs: {"enable_thinking": false}`` is an
      equivalent lever). Pass ``think=True`` to allow deliberation.
    * **Constrained decoding is standard.** vLLM honors the OpenAI
      ``response_format: {json_schema, strict}`` the base ``_schema_payload``
      already emits (verified end-to-end on the turn envelope), so — unlike some
      backends — it needs no override here; the action seam's grammar guarantee
      carries over unchanged.
    * **Auth optional.** A vLLM started without ``--api-key`` needs no token; pass
      one (or ``LOOM_VLLM_API_KEY``) only if the server requires it.

    The default ``timeout`` is generous because a shared/loaded server (vLLM may
    be serving another workload) can be slow to first token; the engine runs
    replies off the loop, so latency never stalls the world.
    """

    def __init__(self, model: str = "qwen-local",
                 host: str = "http://localhost:8000",
                 think: bool = False, api_key: str | None = None,
                 timeout: float = 180.0, **kw):
        extra = dict(kw.pop("extra_body", None) or {})
        if not think:
            extra.setdefault("reasoning_effort", "none")
        super().__init__(base_url=host.rstrip("/") + "/v1", model=model,
                         api_key=api_key, timeout=timeout, extra_body=extra, **kw)
        self.host = host
        self.name = f"vllm:{model}"
        # Local + slow: stream when a reporter watches, so the token count ticks live.
        self.stream_progress = True


class OpenRouterProvider(OpenAICompatibleProvider):
    """Hosted inference via OpenRouter's OpenAI-compatible endpoint (``/v1``).

    The remote sibling of ``VLLMProvider`` on the same swappable waist — the game
    runs identically whether a model answers from the local vLLM box or from a
    hosted provider behind OpenRouter; only the ``base_url``, the auth, and a couple
    of quirks differ. Lets the game reach models the local GPUs cannot hold (and
    serves as a zero-ops fallback when the workstation is busy).

    * **Model is an OpenRouter slug**, e.g. ``qwen/qwen3.6-35b-a3b`` (our NPC tier)
      or ``qwen/qwen3.6-27b`` (the denser game-master tier) — not a local tag.
    * **Thinking off by default.** Qwen3.x reasons by default and, on a small token
      budget, the chain-of-thought crowds out the answer — the same failure the
      local providers guard. OpenRouter's portable lever is ``reasoning:{enabled:
      false}`` (verified live to yield ``reasoning_tokens: 0`` and a clean envelope);
      ``think=True`` re-enables deliberation (e.g. a planning game-master).
    * **Constrained decoding is standard.** OpenRouter forwards the OpenAI
      ``response_format: {json_schema, strict}`` the base ``_schema_payload`` already
      emits (verified end-to-end on the turn envelope), so the action seam's grammar
      guarantee carries over unchanged — no override needed.
    * **Auth required.** A Bearer key (``OPENROUTER_API_KEY``); unlike a keyless
      local vLLM, a remote call must authenticate.
    """

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "qwen/qwen3.6-35b-a3b",
                 api_key: str | None = None, host: str | None = None,
                 think: bool = False, timeout: float = 120.0, retries: int = 5, **kw):
        extra = dict(kw.pop("extra_body", None) or {})
        if not think:
            extra.setdefault("reasoning", {"enabled": False})
        # retries defaults higher than the base (5 vs 3): OpenRouter throttles bursts,
        # and _is_retryable rides them out (see below), so a few extra backoff steps
        # turn a burst-heavy run — like the behavioral harness — from failing to slow.
        super().__init__(base_url=(host or self.BASE_URL).rstrip("/"), model=model,
                         api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
                         timeout=timeout, retries=retries, extra_body=extra, **kw)
        self.name = f"openrouter:{model}"

    def _is_retryable(self, status: int, body: str) -> bool:
        """OpenRouter surfaces a burst rate-limit as a **401** with a cookie-auth
        message ("No cookie auth credentials" / "No user or org id"), not a 429 — so
        the base rule would give up on what is really throttling. Retry it: a
        genuinely bad key fails on the *first* call, before any burst, so widening to
        this specific 401 only ever rides out a rate-limit, never masks real auth."""
        if super()._is_retryable(status, body):
            return True
        low = body.lower()
        return status == 401 and ("cookie" in low or "no user or org" in low)

    # Adaptive per-model pacing. OpenRouter rate-limits *bursts* per model (worse on
    # the pricier ones), which a purely reactive retry can't ride when the whole run
    # is bursty (the behavioral harness). This spaces requests out only once a
    # throttle is actually seen, and decays back to zero when they come clean — so
    # normal play (calls already seconds apart) pays nothing, while a burst is paced
    # under the limit instead of failing. Class-level and keyed by model, so all
    # instances answering as one model share the same pacing state.
    _PACE_MAX = 4.0     # seconds: ceiling on the inter-request interval
    _pace: dict = {}    # model -> {"interval": float, "next_ok": float (monotonic)}

    def _pace_state(self) -> dict:
        return OpenRouterProvider._pace.setdefault(
            self.model, {"interval": 0.0, "next_ok": 0.0})

    async def _pace_wait(self) -> None:
        st = self._pace_state()
        if st["interval"] <= 0:
            return                               # not throttling — no delay
        now = time.monotonic()
        if now < st["next_ok"]:
            await asyncio.sleep(st["next_ok"] - now)
        st["next_ok"] = time.monotonic() + st["interval"]

    def _pace_note(self, throttled: bool) -> None:
        st = self._pace_state()
        if throttled:
            # Back off: double the interval (from a 0.5s floor), capped, and hold the
            # next slot out so the immediately-following calls are already spaced.
            st["interval"] = min(self._PACE_MAX, (st["interval"] or 0.5) * 2)
            st["next_ok"] = time.monotonic() + st["interval"]
        elif st["interval"] > 0:
            st["interval"] *= 0.7                # clean call — relax toward zero
            if st["interval"] < 0.05:
                st["interval"] = 0.0


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

    async def complete(self, system: str, messages: list[dict],
                       schema: dict | None = None,
                       temperature: float | None = None) -> str:
        # ``schema`` is accepted for interface parity but not applied here:
        # Claude's structured-output path is tool-use, not ``response_format``.
        # Until that graduates, the mind's validate-and-retry stays this
        # provider's shape guarantee — defense-in-depth, never dropped silently.
        extra = {} if temperature is None else {"temperature": temperature}
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            **extra,
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()


def get_default_provider() -> LLMProvider:
    """Resolve a provider from the environment (explicit, no network probing):

    * LOOM_PROVIDER = fake | ollama | vllm | openrouter | anthropic — force a choice.
    * else LOOM_VLLM_MODEL set                           — use local vLLM.
    * else LOOM_OPENROUTER_MODEL set                     — use hosted OpenRouter.
    * else LOOM_OLLAMA_MODEL set                         — use local Ollama.
    * else ANTHROPIC_API_KEY set                         — use Claude.
    * else                                               — FakeProvider.

    vLLM tuning:  LOOM_VLLM_MODEL (default qwen-local), LOOM_VLLM_HOST
                  (default http://localhost:8000), LOOM_VLLM_API_KEY (optional).
    OpenRouter tuning: LOOM_OPENROUTER_MODEL (default qwen/qwen3.6-35b-a3b),
                  OPENROUTER_API_KEY (or LOOM_OPENROUTER_API_KEY), LOOM_OPENROUTER_HOST.
    Ollama tuning: LOOM_OLLAMA_MODEL (default qwen3.5:35b-a3b), LOOM_OLLAMA_HOST.
    """
    choice = os.environ.get("LOOM_PROVIDER", "").strip().lower()
    if choice == "fake":
        return FakeProvider()
    if choice == "vllm" or (not choice and os.environ.get("LOOM_VLLM_MODEL")):
        return VLLMProvider(
            model=os.environ.get("LOOM_VLLM_MODEL", "qwen-local"),
            host=os.environ.get("LOOM_VLLM_HOST", "http://localhost:8000"),
            api_key=os.environ.get("LOOM_VLLM_API_KEY") or None,
        )
    if choice == "openrouter" or (not choice and os.environ.get("LOOM_OPENROUTER_MODEL")):
        return OpenRouterProvider(
            model=os.environ.get("LOOM_OPENROUTER_MODEL", "qwen/qwen3.6-35b-a3b"),
            host=os.environ.get("LOOM_OPENROUTER_HOST") or None,
            api_key=(os.environ.get("LOOM_OPENROUTER_API_KEY")
                     or os.environ.get("OPENROUTER_API_KEY") or None),
        )
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
