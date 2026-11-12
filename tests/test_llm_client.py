"""LLM wrapper tests with a mocked chat function. Run: python3 -m unittest discover tests"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock
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

    def test_one_json_400_does_not_switch_json_mode_off_for_later_calls(self):
        err = urllib.error.HTTPError("u", 400, "json_validate_failed", {}, None)
        self.addCleanup(err.close)
        fake = FakeChat([err, '{"a": 1}', '{"b": 2}'])
        c = LLM(chat_fn=fake, backoff=0, concurrency=1)
        self.assertEqual(c.ask_json("q1"), {"a": 1})
        self.assertEqual(c.ask_json("q2"), {"b": 2})
        self.assertIsNone(fake.seen[1]["response_format"])  # that call retried without JSON mode
        self.assertEqual(fake.seen[2]["response_format"], {"type": "json_object"})  # the next call has it again

    def test_repeated_json_400s_switch_json_mode_off(self):
        errs = [urllib.error.HTTPError("u", 400, "response_format unsupported", {}, None) for _ in range(3)]
        for e in errs:
            self.addCleanup(e.close)
        fake = FakeChat([errs[0], '{"a": 1}', errs[1], '{"a": 1}', errs[2], '{"a": 1}', '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=0, concurrency=1)
        for q in ("q1", "q2", "q3", "q4"):
            self.assertEqual(c.ask_json(q), {"a": 1})
        self.assertIsNone(fake.seen[-1]["response_format"])  # after 3 in a row it stays off, and says so in the log

    def test_auth_error_not_retried(self):
        err = urllib.error.HTTPError("u", 401, "bad key", {}, None)
        self.addCleanup(err.close)
        fake = FakeChat([err, '{"a": 1}'])
        self.assertIsNone(LLM(chat_fn=fake, backoff=0).ask_json("q"))
        self.assertEqual(len(fake.seen), 1)

    def test_forbidden_stops_the_whole_run(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        self.addCleanup(err.close)
        fake = FakeChat([err] + ['{"a": 1}'] * 5)
        c = LLM(chat_fn=fake, backoff=0)
        self.assertEqual(c.map_json(["q1", "q2", "q3"]), [None, None, None])
        self.assertEqual(len(fake.seen), 1)  # one attempt, then the breaker refuses the rest
        self.assertIn("403", c.exhausted)

    def _http(self, code):
        err = urllib.error.HTTPError("u", code, "x", {}, None)
        self.addCleanup(err.close)
        return err

    def test_overload_codes_are_retried(self):
        for code in (503, 429, 500):
            fake = FakeChat([self._http(code), self._http(code), '{"a": 1}'])
            self.assertEqual(LLM(chat_fn=fake, backoff=0).ask_json("q"), {"a": 1}, code)
            self.assertEqual(len(fake.seen), 3)

    def test_five_tries_then_fallback_and_failure_counted(self):
        fake = FakeChat([self._http(503)] * 10)
        c = LLM(chat_fn=fake, backoff=0)
        with c.stage("loops"):
            self.assertIsNone(c.ask_json("q"))
        self.assertEqual(len(fake.seen), 5)
        self.assertEqual(c.tokens["loops"]["failed_prompts"], 1)
        self.assertEqual(c.summary(10)["failures"], {"loops": 1})

    def _quota(self, body):
        import io
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(body.encode()))
        self.addCleanup(err.close)
        return err

    def test_daily_quota_trips_breaker_for_whole_run(self):
        daily = "Quota exceeded ... requests per day limit exceeded on Free Tier. Please retry in 17h31m41.6s."
        fake = FakeChat([self._quota(daily)] + ['{"a": 1}'] * 5)
        c = LLM(chat_fn=fake, backoff=0)
        self.assertIsNone(c.ask_json("q1"))
        self.assertIsNone(c.ask_json("q2"))  # no call made: breaker is open
        self.assertEqual(len(fake.seen), 1)
        self.assertIn("429", c.exhausted)
        self.assertEqual(c.summary(1)["failures"], {"default": 2})

    def test_short_retry_hint_is_honoured(self):
        fake = FakeChat([self._quota("Rate limit. Please retry in 0.01s."), '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=100)  # would sleep 200 s if the hint were ignored
        self.assertEqual(c.ask_json("q"), {"a": 1})
        self.assertIsNone(c.exhausted)

    def test_groq_try_again_hint_and_retry_after_header(self):
        fake = FakeChat([self._quota("Rate limit reached on tokens per minute (TPM). Please try again in 0.01s."),
                         '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=100)
        self.assertEqual(c.ask_json("q"), {"a": 1})
        import io
        err = urllib.error.HTTPError("u", 429, "x", {"retry-after": "0.01"}, io.BytesIO(b"slow down"))
        self.addCleanup(err.close)
        fake = FakeChat([err, '{"b": 2}'])
        self.assertEqual(LLM(chat_fn=fake, backoff=100).ask_json("q"), {"b": 2})

    def test_groq_daily_limit_trips_breaker(self):
        fake = FakeChat([self._quota("Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 9m.")])
        c = LLM(chat_fn=fake, backoff=0)
        self.assertIsNone(c.ask_json("q"))
        self.assertIsNotNone(c.exhausted)

    def test_extra_fields_passed_and_cached_separately(self):
        os.environ["LLM_MODEL"] = "m"
        seen = []

        def chat(messages, extra=None, **_):
            seen.append(extra)
            return '{"n": %d}' % len(seen)
        plain = LLM(chat_fn=lambda m, **k: '{"n": 0}', cache_dir="cache")
        self.assertEqual(plain.ask_json("same"), {"n": 0})
        low = LLM(chat_fn=chat, cache_dir="cache", extra={"reasoning_effort": "low"})
        self.assertEqual(low.ask_json("same"), {"n": 1})  # not served from the plain entry
        self.assertEqual(seen, [{"reasoning_effort": "low"}])

    def test_not_found_not_retried_and_stops_the_run(self):
        fake = FakeChat([self._http(404), '{"a": 1}', '{"a": 1}'])
        c = LLM(chat_fn=fake, backoff=0)
        self.assertIsNone(c.ask_json("q"))
        self.assertIsNone(c.ask_json("q2"))  # model unavailable: no second attempt
        self.assertEqual(len(fake.seen), 1)

    def test_repeated_overload_halves_concurrency_to_floor(self):
        c = LLM(chat_fn=FakeChat([self._http(503)] * 20), backoff=0, concurrency=8)
        for _ in range(4):
            c.ask_json("q")  # 5 overloads each
        self.assertEqual(c._limit, 2)  # 8 -> 4 -> 2, never below the floor

    def test_backoff_doubles_with_jitter(self):
        c = LLM(chat_fn=FakeChat([]), backoff=2.0)
        waits = []
        with mock.patch.object(llm_client.time, "sleep", side_effect=waits.append):
            for attempt in range(4):
                c._wait(attempt)
        for w, base in zip(waits, (2, 4, 8, 16)):
            self.assertTrue(0.75 * base <= w <= 1.25 * base, (w, base))

    def test_concurrency_limit_is_respected(self):
        active, peak, lock = [0], [0], threading.Lock()

        def slow(messages, **_):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return '{"ok": 1}'
        c = LLM(chat_fn=slow, concurrency=8)
        c._limit = 2
        c.map_json([f"p{i}" for i in range(8)])
        self.assertLessEqual(peak[0], 2)

    def test_map_keeps_order_and_tallies_stage(self):
        fake = FakeChat([], prompt_tokens=7, completion_tokens=3)
        fake.replies = []
        c = LLM(chat_fn=lambda m, **k: fake(m, **k) and json.dumps({"echo": m[-1]["content"]}), concurrency=4)
        with c.stage("loops"):
            out = c.map_json([f"p{i}" for i in range(10)], system="sys")
        self.assertEqual([o["echo"] for o in out], [f"p{i}" for i in range(10)])
        self.assertEqual({k: c.tokens["loops"][k] for k in ("prompt", "completion", "calls", "failed_prompts")},
                         {"prompt": 70, "completion": 30, "calls": 10, "failed_prompts": 0})
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
