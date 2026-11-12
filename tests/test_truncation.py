"""A loops reply cut at max_tokens is split and retried, not re-asked or silently repaired. No network."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from coldstart import ingest, loops
from coldstart.llm_client import LLM

T0 = 1_788_000_000


def _two_chat_data(tmp: str) -> ingest.Data:
    chats = [{"chat_id": c, "name": c, "is_group": False, "participants": ["ME", p]} for c, p in (("C1", "P1"), ("C2", "P2"))]
    rows = [("M1", "C1", "P1", T0, "Pls send the invoice for August"),
            ("M2", "C2", "P2", T0 + 30, "Can you pay the electrician today?")]
    msgs = [{"id": i, "chat_id": c, "sender_id": s, "sender_name": "x", "ts": t, "kind": "text", "body": b,
             "quoted_id": None} for i, c, s, t, b in rows]
    d = Path(tmp)
    (d / "chats.jsonl").write_text("\n".join(json.dumps(c) for c in chats))
    (d / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs))
    (d / "seed.json").write_text("{}")
    return ingest.load(tmp)


class CutChat:
    """Cuts any reply to a prompt holding more than one window; answers single windows properly."""

    def __init__(self, always_cut=False):
        self.calls, self.always_cut = [], always_cut

    def __call__(self, messages, max_tokens=None, response_format=None, timeout=None, meta=None, **_):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        cands = [l.split()[0].lstrip("*") for l in prompt.splitlines() if l.startswith("*m")]
        loop = {"w": "W1", "src": cands[0], "owner": "ME", "type": "commitment", "title": "Do it", "p_open": 0.9,
                "p_done": 0.05, "p_dropped": 0.05, "conf": 0.9}
        if self.always_cut or "## W2" in prompt:
            if meta is not None:
                meta["finish_reason"] = "length"
            return json.dumps({"loops": [loop]})[:-3]  # cut mid-reply
        if meta is not None:
            meta["finish_reason"] = "stop"
        return json.dumps({"loops": [loop]})


class Truncation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = _two_chat_data(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_cut_batch_is_split_and_both_halves_answered(self):
        fake = CutChat()
        out = loops.extract(self.data, LLM(chat_fn=fake, backoff=0))
        self.assertEqual(sorted(l["source_msg"] for l in out), ["M1", "M2"])
        self.assertEqual(len(fake.calls), 3)  # the cut batch, then one call per half; no re-ask

    def test_unsplittable_cut_keeps_repaired_part_and_stops(self):
        fake = CutChat(always_cut=True)
        out = loops.extract(self.data, LLM(chat_fn=fake, backoff=0))
        self.assertEqual(len(fake.calls), 3)  # batch, two halves (single short windows can't be cut further)
        self.assertEqual(out, [])  # the cut replies' JSON didn't survive repair: nothing invented

    def test_cache_hit_without_finish_reason_detects_cut_text(self):
        c = LLM(chat_fn=CutChat(), cache_dir=self.tmp.name + "/cache")
        c.cache_dir.mkdir()
        reply, cut = c.ask_json_ex("## W1\n*m1 x P1: hi\n## W2\n*m2 x P2: yo")
        self.assertTrue(cut)
        c2 = LLM(chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")), cache_dir=str(c.cache_dir))
        for f in c.cache_dir.glob("*.meta.json"):
            f.unlink()  # an old cache without the finish sidecar
        reply, cut = c2.ask_json_ex("## W1\n*m1 x P1: hi\n## W2\n*m2 x P2: yo")
        self.assertTrue(cut)


if __name__ == "__main__":
    unittest.main()
