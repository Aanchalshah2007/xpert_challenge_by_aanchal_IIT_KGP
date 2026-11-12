"""Spend ledger + hard cap (dev-only paid runs). Run: python3 -m unittest discover tests"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coldstart import spend
from coldstart.llm_client import LLM


class PaidChat:
    """Stands in for llm.chat with an OpenRouter-style usage record (cost in USD)."""

    def __init__(self, cost=0.001, fail=False):
        self.cost, self.fail, self.n = cost, fail, 0

    def __call__(self, messages, max_tokens=None, response_format=None, timeout=None, meta=None, extra=None):
        self.n += 1
        if self.fail:
            raise TimeoutError("slow")
        if meta is not None:
            meta.update({"model": "m", "provider": "P", "id": f"gen-{self.n}", "prompt_tokens": 100,
                         "completion_tokens": 50, "reasoning_tokens": 20, "cost": self.cost})
        return '{"ok": true}'


class Ledger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.getcwd()
        os.chdir(self._tmp.name)
        self.path = Path(self._tmp.name) / "spend.jsonl"
        self.env = mock.patch.dict(os.environ, {"COLDSTART_SPEND_LEDGER": str(self.path)}, clear=False)
        self.env.start()
        for k in ("COLDSTART_SPEND_CAP", "COLDSTART_RUN_BUDGET", "COLDSTART_LLM_CACHE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        os.chdir(self._old)
        self._tmp.cleanup()

    def test_off_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(LLM(chat_fn=PaidChat()).ledger)

    def test_records_real_cost_and_provider(self):
        c = LLM(chat_fn=PaidChat(cost=0.0012), backoff=0)
        self.assertEqual(c.ask_json("q"), {"ok": True})
        rows = spend.read(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["cost"], rows[0]["provider"], rows[0]["id"], rows[0]["reasoning_tokens"]),
                         (0.0012, "P", "gen-1", 20))

    def test_cap_blocks_before_sending(self):
        self.path.write_text(json.dumps({"run": "old", "cost": 2.999}) + "\n")
        fake = PaidChat()
        c = LLM(chat_fn=fake, backoff=0)
        self.assertIsNone(c.ask_json("q" * 1000, max_tokens=2000))  # worst case would pass $3.00
        self.assertEqual(fake.n, 0)
        self.assertIn("spend cap", c.exhausted)

    def test_cap_env_cannot_exceed_hard_cap(self):
        with mock.patch.dict(os.environ, {"COLDSTART_SPEND_CAP": "50"}):
            self.assertEqual(spend.Ledger(str(self.path)).cap, spend.HARD_CAP_USD)

    def test_run_budget(self):
        with mock.patch.dict(os.environ, {"COLDSTART_RUN_BUDGET": "0.0001"}):
            fake = PaidChat()
            c = LLM(chat_fn=fake, backoff=0)
            self.assertIsNone(c.ask_json("q", max_tokens=2000))
            self.assertEqual(fake.n, 0)

    def test_failed_attempt_logged_without_cost(self):
        c = LLM(chat_fn=PaidChat(fail=True), backoff=0, retries=1)
        self.assertIsNone(c.ask_json("q"))
        rows = spend.read(self.path)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["error"] and r["cost"] is None for r in rows))
        self.assertEqual(sum(spend._charged(r) for r in rows), 0.0)

    def test_missing_cost_counts_reserve_against_cap(self):
        c = LLM(chat_fn=PaidChat(cost=None), backoff=0)
        c.ask_json("q", max_tokens=100)
        r = spend.read(self.path)[0]
        self.assertIsNone(r["cost"])
        self.assertGreater(r["cost_missing_reserved"], 0)


class CachePerHost(unittest.TestCase):
    def test_env_cache_gets_host_subfolder(self):
        with mock.patch.dict(os.environ, {"COLDSTART_LLM_CACHE": "/tmp/c", "LLM_BASE_URL": "https://openrouter.ai/api/v1"}):
            os.environ.pop("COLDSTART_SPEND_LEDGER", None)
            self.assertEqual(LLM(chat_fn=PaidChat()).cache_dir, Path("/tmp/c/openrouter.ai"))

    def test_explicit_cache_dir_unchanged(self):
        self.assertEqual(LLM(chat_fn=PaidChat(), cache_dir="/tmp/x").cache_dir, Path("/tmp/x"))


if __name__ == "__main__":
    unittest.main()
