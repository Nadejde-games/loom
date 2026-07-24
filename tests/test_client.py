"""The reference terminal client's .env loader (offline).

The client reads LOOM_HOST/LOOM_PORT so `make play` reaches the server on the port the
setup wizard configured. This locks that seam: a repo-root .env is parsed, a real env var
still wins, and a missing file is a no-op.
"""
import os
import tempfile
import unittest

from client.terminal import _load_env


class LoadEnvTests(unittest.TestCase):
    _KEYS = ("LOOM_PORT", "LOOM_HOST")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write_env(self, root, body):
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as f:
            f.write(body)

    def test_reads_port_and_host(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_env(root, "# config\nLOOM_PORT=4123\nLOOM_HOST=0.0.0.0\n")
            _load_env(root)
            self.assertEqual(os.environ["LOOM_PORT"], "4123")
            self.assertEqual(os.environ["LOOM_HOST"], "0.0.0.0")

    def test_real_env_var_wins(self):
        os.environ["LOOM_PORT"] = "9999"
        with tempfile.TemporaryDirectory() as root:
            self._write_env(root, "LOOM_PORT=4123\n")
            _load_env(root)
            self.assertEqual(os.environ["LOOM_PORT"], "9999")   # setdefault: existing wins

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as root:
            _load_env(root)                                     # no .env — must not raise
            self.assertNotIn("LOOM_PORT", os.environ)


if __name__ == "__main__":
    unittest.main()
