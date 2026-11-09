"""LLM wrapper tests with a mocked chat function. Run: python3 -m unittest discover tests"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

from coldstart import llm_client
from coldstart.llm_client import LLM, parse_json


class FakeChat:
    """Stands in for llm.chat: scripted replies, logs usage like the real one."""

    def __init__(self, replies, prompt_tokens=10, completion_tokens=5):
        self.replies = list(replies)
        self.seen = []
        self.lock = threading.Lock()
        self.pt, self.ct = prompt_tokens, completion_tokens

    def __call__(self, messages, max_tokens=None, response_format=None, timeout=None):
        with self.lock:
            self.seen.append({"messages": messages, "response_format": response_format})
            reply = self.replies.pop(0) if self.replies else '{"ok": true}'
        if isinstance(reply, Exception):
            raise reply
        with open("usage.jsonl", "a") as f:
            f.write(json.dumps({"prompt_tokens": self.pt, "completion_tokens": self.ct}) + "\n")
        return reply


class InCwd(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()


class ParseJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(parse_json('```json\n{"a": [1, 2]}\n```'), {"a": [1, 2]})

    def test_prose_and_trailing_comma(self):
        self.assertEqual(parse_json('Sure! Here it is: {"a": [1, 2,], "b": None,} hope that helps'), {"a": [1, 2], "b": None})

    def test_smart_quotes_and_bools(self):
        self.assertEqual(parse_json("{“ok”: True}"), {"ok": True})

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_json("no json here")
        with self.assertRaises(ValueError):
            parse_json(None)


class Client(InCwd):
    def test_reask_on_bad_json(self):
        fake = FakeChat(["not json", '{"fixed": 1}'])
        c = LLM(chat_fn=fake, backoff=0)
        self.assertEqual(c.ask_json("q"), {"fixed": 1})
        self.assertEqual(len(fake.seen), 2)
        self.assertIn("not valid JSON", fake.seen[1]["messages"][-1]["content"])

    def test_gives_up_after_one_reask(self):
        c = LLM(chat_fn=FakeChat(["bad", "still bad"]), backoff=0)
        self.assertIsNone(c.ask_json("q"))

    def test_retries_transient_errors(self):
        fake = FakeChat([TimeoutError("slow"), ConnectionError("reset"), '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=0)
        self.assertEqual(c.ask_json("q"), {"a": 1})
        self.assertEqual(len(fake.seen), 3)

    def test_exhausted_retries_return_none(self):
        c = LLM(chat_fn=FakeChat([TimeoutError()] * 5), retries=2, backoff=0)
        self.assertIsNone(c.ask_json("q"))
        self.assertEqual(c.failures.get("default"), 1)

    def test_json_mode_dropped_on_400(self):
        err = urllib.error.HTTPError("u", 400, "response_format unsupported", {}, None)
        self.addCleanup(err.close)
        fake = FakeChat([err, '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=0)
        self.assertEqual(c.ask_json("q"), {"a": 1})
        self.assertEqual(fake.seen[0]["response_format"], {"type": "json_object"})
        self.assertIsNone(fake.seen[1]["response_format"])

    def test_auth_error_not_retried(self):
        err = urllib.error.HTTPError("u", 401, "bad key", {}, None)
        self.addCleanup(err.close)
        fake = FakeChat([err, '{"a": 1}'])
        self.assertIsNone(LLM(chat_fn=fake, backoff=0).ask_json("q"))
        self.assertEqual(len(fake.seen), 1)

    def test_map_keeps_order_and_tallies_stage(self):
        fake = FakeChat([], prompt_tokens=7, completion_tokens=3)
        fake.replies = []
        c = LLM(chat_fn=lambda m, **k: fake(m, **k) and json.dumps({"echo": m[-1]["content"]}), concurrency=4)
        with c.stage("loops"):
            out = c.map_json([f"p{i}" for i in range(10)], system="sys")
        self.assertEqual([o["echo"] for o in out], [f"p{i}" for i in range(10)])
        self.assertEqual(c.tokens["loops"], {"prompt": 70, "completion": 30, "calls": 10})
        self.assertEqual(c.summary(1000)["tokens_per_1k_msgs"], 100.0)

    def test_cache(self):
        os.environ["LLM_MODEL"] = "m"
        fake = FakeChat(['{"n": 1}', '{"n": 2}'])
        c = LLM(chat_fn=fake, cache_dir="cache")
        self.assertEqual(c.ask_json("same"), {"n": 1})
        self.assertEqual(c.ask_json("same"), {"n": 1})
        self.assertEqual(len(fake.seen), 1)
        self.assertTrue(any(Path("cache").iterdir()))

    def test_available(self):
        saved = {k: os.environ.pop(k, None) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
        try:
            self.assertFalse(llm_client.available())
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
