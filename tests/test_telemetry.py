"""Inference telemetry (offline).

The reporter seam is inert unless installed, tags calls from the context label, and reports
token counts. The SSE delta parser is pure and covered directly. No network: the provider's
``_post`` is stubbed to a canned response.
"""
import unittest

from loom.ai import (InferenceReporter, OpenAICompatibleProvider, get_default_provider,
                     inference_label, set_reporter)
from loom.ai.telemetry import InferenceCall, new_call


class _CapReporter(InferenceReporter):
    def __init__(self):
        self.events = []

    def start(self, c):
        self.events.append(("start", c.label, c.tokens))

    def progress(self, c):
        self.events.append(("progress", c.tokens))

    def finish(self, c):
        self.events.append(("finish", c.label, c.tokens, c.ok))


class _Canned(OpenAICompatibleProvider):
    """A provider whose HTTP POST is replaced by a canned chat-completions response."""

    def __init__(self, content="hi", usage=None, boom=False):
        super().__init__(base_url="http://example/v1", model="m")
        self._resp = {"choices": [{"message": {"content": content}}]}
        if usage:
            self._resp["usage"] = usage
        self._boom = boom

    async def _post(self, payload):
        if self._boom:
            from loom.ai import ProviderError
            raise ProviderError("boom")
        return self._resp


class TelemetrySeamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_reporter(None)

    def tearDown(self):
        set_reporter(None)

    async def test_no_reporter_is_the_plain_path(self):
        # With nothing installed, complete() returns content and touches no telemetry.
        self.assertEqual(await _Canned("hello").complete("sys", []), "hello")

    async def test_reporter_gets_start_then_finish(self):
        cap = _CapReporter()
        set_reporter(cap)
        out = await _Canned("hello", usage={"completion_tokens": 12}).complete("sys", [])
        self.assertEqual(out, "hello")
        kinds = [e[0] for e in cap.events]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "finish")
        finish = cap.events[-1]
        self.assertEqual(finish[2], 12)         # token count from usage
        self.assertTrue(finish[3])              # ok

    async def test_label_from_context(self):
        cap = _CapReporter()
        set_reporter(cap)
        tok = inference_label.set("Wren the Wayfinder")
        try:
            await _Canned("x").complete("sys", [])
        finally:
            inference_label.reset(tok)
        self.assertEqual(cap.events[-1][1], "Wren the Wayfinder")

    async def test_failure_reports_not_ok_and_reraises(self):
        cap = _CapReporter()
        set_reporter(cap)
        from loom.ai import ProviderError
        with self.assertRaises(ProviderError):
            await _Canned(boom=True).complete("sys", [])
        self.assertEqual(cap.events[-1][0], "finish")
        self.assertFalse(cap.events[-1][3])     # ok is False


class CallRecordTests(unittest.TestCase):
    def test_label_defaults_to_model(self):
        self.assertEqual(new_call("qwen3.6:27b").label, "qwen3.6:27b")

    def test_tok_s_zero_until_tokens(self):
        c = InferenceCall(id=1, label="l", model="m", started=0.0)
        self.assertEqual(c.tok_s, 0.0)

    def test_stop_freezes_elapsed(self):
        c = InferenceCall(id=1, label="l", model="m", started=0.0)
        c.stop(ok=True)
        first = c.elapsed
        self.assertTrue(c.finished)
        self.assertEqual(first, c.elapsed)      # frozen after stop


class StreamDeltaParseTests(unittest.TestCase):
    def _d(self, line):
        return OpenAICompatibleProvider._stream_delta(line)

    def test_content(self):
        self.assertEqual(self._d('data: {"choices":[{"delta":{"content":"He"}}]}'),
                         ("He", None))

    def test_done(self):
        self.assertEqual(self._d("data: [DONE]"), ("[DONE]", None))

    def test_usage_tail(self):
        piece, usage = self._d('data: {"choices":[],"usage":{"completion_tokens":5}}')
        self.assertIsNone(piece)
        self.assertEqual(usage["completion_tokens"], 5)

    def test_keepalive_and_garbage(self):
        self.assertEqual(self._d(""), (None, None))
        self.assertEqual(self._d(": ping"), (None, None))
        self.assertEqual(self._d("data: not-json"), (None, None))


class _FakeStreamResp:
    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"error detail"


class _FakeClient:
    def __init__(self, lines, status=200):
        self._lines, self._status = lines, status

    def stream(self, method, url, json=None, headers=None):
        return _FakeStreamResp(self._lines, self._status)


class StreamingPathTests(unittest.IsolatedAsyncioTestCase):
    """The streamed provider path (local backends): tokens climb per delta, text accumulates,
    the final usage sets the exact count. Driven by a fake httpx client — no socket."""

    def setUp(self):
        set_reporter(None)

    def tearDown(self):
        set_reporter(None)

    async def test_streaming_accumulates_text_and_counts_tokens(self):
        from loom.ai import OllamaProvider
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{"content":" there"}}]}',
            'data: {"choices":[],"usage":{"completion_tokens":3}}',
            'data: [DONE]',
        ]
        p = OllamaProvider(model="m", host="http://x")
        self.assertTrue(p.stream_progress)
        p._get_client = lambda: _FakeClient(lines)
        cap = _CapReporter()
        set_reporter(cap)
        out = await p.complete("sys", [])
        self.assertEqual(out, "Hello there")
        finish = cap.events[-1]
        self.assertEqual(finish[0], "finish")
        self.assertEqual(finish[2], 3)          # exact token count from the usage tail

    async def test_streaming_http_error_raises(self):
        from loom.ai import OllamaProvider, ProviderError
        p = OllamaProvider(model="m", host="http://x")
        p._get_client = lambda: _FakeClient([], status=500)
        set_reporter(_CapReporter())
        with self.assertRaises(ProviderError):
            await p.complete("sys", [])


if __name__ == "__main__":
    unittest.main()
