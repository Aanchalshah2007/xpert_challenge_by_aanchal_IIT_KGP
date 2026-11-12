"""Loops (id mapping, merge) and decide (thresholds, gates) with a mocked LLM. No network."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from coldstart import candidates, config, decide, ingest, loops
from coldstart.llm_client import LLM

DAY = 86400
T0 = 1_788_000_000


def _data(tmp: str) -> ingest.Data:
    chats = [{"chat_id": "C1", "name": "A", "is_group": False, "participants": ["ME", "P1"]}]
    rows = [
        ("M1", "P1", T0, "Pls send the invoice for August"),
        ("M2", "ME", T0 + 60, "will send tomorrow"),
        ("M3", "P1", T0 + DAY, "Can you also send the invoice? reminder"),
        ("M4", "ME", T0 + 2 * DAY, "sent"),
        ("M5", "P1", T0 + 3 * DAY, "thanks"),
    ]
    msgs = [{"id": i, "chat_id": "C1", "sender_id": s, "sender_name": "x", "ts": t, "kind": "text", "body": b,
             "quoted_id": None} for i, s, t, b in rows]
    d = Path(tmp)
    (d / "chats.jsonl").write_text("\n".join(json.dumps(c) for c in chats))
    (d / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs))
    (d / "seed.json").write_text("{}")
    return ingest.load(tmp)


def _raw(**kw):
    base = {"title": "Send invoice", "type": "commitment", "owner": "ME", "chat_id": "C1", "source_msg": "M1",
            "due": None, "p_open": 0.6, "p_done": 0.4, "p_dropped": 0.0, "close": None, "owner_is_me": True,
            "private": False, "reversible": True, "sensitive": False, "needs_me_only": False,
            "p_assist": 0.1, "p_remind": 0.2, "p_leave": 0.7, "next": None, "conf": 0.9}
    base.update(kw)
    return base


class Loops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = _data(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_candidates_and_window(self):
        c = candidates.find(self.data)
        self.assertIn("M1", c)
        self.assertIn("M2", c)
        w = candidates.windows(self.data, c)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0].msg_ids[:5], ["M1", "M2", "M3", "M4", "M5"])

    def test_extract_maps_ids_and_merges_repeat_to_earliest(self):
        def fake_chat(messages, **_):
            prompt = messages[-1]["content"]
            local = {line.split()[0].lstrip("*"): line for line in prompt.splitlines() if line.lstrip("* ").startswith("m")}
            first = next(k for k, v in local.items() if "Pls send the invoice" in v)
            repeat = next(k for k, v in local.items() if "also send the invoice" in v)
            closer = next(k for k, v in local.items() if v.rstrip().endswith("ME: sent"))
            loop = {"w": "W1", "owner": "ME", "type": "commitment", "title": "Send August invoice", "p_open": 0.1,
                    "p_done": 0.9, "p_dropped": 0, "close": closer, "conf": 0.9}
            return json.dumps({"loops": [dict(loop, src=repeat), dict(loop, src=first),
                                         dict(loop, src="m999"), dict(loop, w="W9", src=first)]})
        out = loops.extract(self.data, LLM(chat_fn=fake_chat))
        self.assertEqual([l["source_msg"] for l in out], ["M1"])  # earliest kept, bad ids dropped
        self.assertEqual(out[0]["close"], "M4")
        d = decide.decide(out, self.data)[0]
        self.assertEqual((d["status"], d["closed_by"]), ("done", "M4"))
        self.assertNotIn("help", d)

    def test_failed_llm_gives_no_loops(self):
        def broken(messages, **_):
            raise TimeoutError()
        self.assertEqual(loops.extract(self.data, LLM(chat_fn=broken, retries=0, backoff=0)), [])

    def test_low_confidence_dropped(self):
        self.assertEqual(loops.merge([_raw(conf=config.MIN_CONFIDENCE - 0.1)], self.data), [])


class Decide(unittest.TestCase):
    def test_status_needs_twice_open(self):
        self.assertEqual(decide.status(_raw(p_open=0.34, p_done=0.66)), "open")  # 0.66 < 0.68
        self.assertEqual(decide.status(_raw(p_open=0.3, p_done=0.7)), "done")
        self.assertEqual(decide.status(_raw(p_open=0.2, p_done=0.1, p_dropped=0.7)), "dropped")

    def test_someone_elses_move_is_leave_unless_overdue_followup(self):
        self.assertEqual(decide.ownership_help(_raw(owner="P1", owner_is_me=False), T0)["action"], "leave")
        f = _raw(owner="P1", owner_is_me=False, type="followup")
        self.assertEqual(decide.ownership_help(f, T0)["action"], "leave")  # no due date: never overdue
        self.assertEqual(decide.ownership_help(dict(f, due="2000-01-01"), T0)["action"], "remind")  # past due
        self.assertEqual(decide.ownership_help(dict(f, due="2030-01-02"), T0)["action"], "leave")  # not yet due

    def test_question_the_user_sent_waits_on_the_other_person(self):
        q = _raw(type="question", p_assist=0.7, next="Draft it")  # model said owner ME
        self.assertEqual(decide.ownership_help(q, T0, src_by_me=True)["action"], "leave")
        self.assertEqual(decide.ownership_help(dict(q, due="2000-01-01"), T0, src_by_me=True)["action"], "remind")
        self.assertEqual(decide.ownership_help(q, T0, src_by_me=False)["action"], "assist")  # asked of the user

    def test_assist_needs_every_gate(self):
        ok = _raw(p_assist=0.1, p_leave=0.8, next="Draft the reply")  # gates, not ratios
        self.assertEqual(decide.ownership_help(ok, T0)["action"], "assist")
        for bad in ({"sensitive": True}, {"private": True}, {"reversible": False}, {"owner": "P1"}, {"next": None}):
            self.assertNotEqual(decide.ownership_help(dict(ok, **bad), T0)["action"], "assist", bad)

    def test_users_own_move_is_remind_when_assist_is_unsafe(self):
        r = _raw(needs_me_only=True, p_remind=0.1, p_leave=0.8, due="2030-01-02")  # probabilities don't gate it
        h = decide.ownership_help(r, T0)
        self.assertEqual(h["action"], "remind")
        self.assertEqual(h["remind_at"], "2030-01-02T10:00:00+05:30")
        self.assertEqual(decide.ownership_help(dict(r, needs_me_only=False, next="Draft it"), T0)["action"], "assist")

    def test_remind_without_due_is_next_morning(self):
        h = decide.ownership_help(_raw(needs_me_only=True, p_remind=0.8, p_leave=0.2), T0)
        self.assertTrue(h["remind_at"].endswith("T10:00:00+05:30"))


if __name__ == "__main__":
    unittest.main()
