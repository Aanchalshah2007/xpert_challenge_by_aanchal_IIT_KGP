"""Regression tests for loop patterns found in the zero-call analysis. Synthetic chats, made-up names, no LLM."""
import json
import tempfile
import unittest
from pathlib import Path

from coldstart import candidates, config, decide, ingest, loops

T0 = 1_788_000_000
MIN = 60


def _data(rows, chats=None, me_name="Zorvik Talmane"):
    """rows: (id, chat_id, sender_id, minutes after T0, body[, kind])."""
    chats = chats or [{"chat_id": "C1", "name": "x", "is_group": False, "participants": ["ME", "P1"]}]
    msgs = []
    for r in rows:
        mid, cid, sid, minutes, body = r[:5]
        kind = r[5] if len(r) > 5 else "text"
        msgs.append({"id": mid, "chat_id": cid, "sender_id": sid, "ts": T0 + minutes * MIN, "kind": kind, "body": body,
                     "sender_name": me_name if sid == "ME" else "Pel Orrin", "quoted_id": None})
    tmp = tempfile.mkdtemp()
    d = Path(tmp)
    (d / "chats.jsonl").write_text("\n".join(json.dumps(c) for c in chats))
    (d / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs))
    (d / "seed.json").write_text("{}")
    return ingest.load(tmp)


def _raw(src, chat="C1", **kw):
    base = {"title": "t", "type": "commitment", "owner": "ME", "chat_id": chat, "source_msg": src, "due": None,
            "p_open": 0.8, "p_done": 0.2, "p_dropped": 0.0, "close": None, "owner_is_me": True, "private": False,
            "reversible": True, "sensitive": False, "needs_me_only": False, "p_assist": 0.1, "p_remind": 0.2,
            "p_leave": 0.7, "next": None, "conf": 0.9}
    base.update(kw)
    return base


class Bursts(unittest.TestCase):
    def test_busy_chat_is_split_into_windows_with_few_candidates(self):
        asks = ["Pls pay 2000", "Pls book the tickets", "Can you call the plumber?", "Pls order the lamp",
                "Send me the link", "Pls get milk", "Can we take it to the farm?", "Need to discuss school",
                "Pls fix the fan", "Pls renew the insurance", "Can u check the car?", "Pls pay 3500 on this"]
        d = _data([(f"M{i:02d}", "C1", "P1", i, a) for i, a in enumerate(asks, 1)])
        wins = candidates.windows(d, candidates.find(d))
        self.assertGreaterEqual(len(wins), 2)
        self.assertTrue(all(len(w.candidates) <= config.WINDOW_MAX_CANDIDATES for w in wins))
        covered = {c for w in wins for c in w.candidates}
        self.assertEqual(covered, {f"M{i:02d}" for i in range(1, len(asks) + 1)})  # nothing lost by splitting

    def test_prompt_asks_for_one_loop_per_request(self):
        self.assertIn("own loop", loops.SYSTEM)


class NameAddressed(unittest.TestCase):
    def test_runtime_name_tags_group_message_and_reaches_prompt(self):
        chats = [{"chat_id": "G1", "name": "Ops", "is_group": True, "participants": ["ME", "P1", "P2"]}]
        d = _data([("M1", "G1", "P1", 0, "Zorvik, we need the signed copy to raise the order"),
                   ("M2", "G1", "P2", 1, "talmane bhai any news"),
                   ("M3", "G1", "P2", 2, "nice weather"),
                   ("M4", "G1", "ME", 3, "on it")], chats=chats)  # the name is read from ME's own messages
        self.assertEqual(d.me_names, ["Zorvik Talmane", "Zorvik"])
        c = candidates.find(d)
        self.assertIn("addressed", c["M1"])
        self.assertNotIn("M3", c)
        prompts, _ = loops.build_prompts(d, candidates.windows(d, c))
        self.assertIn("Zorvik Talmane", prompts[0])

    def test_generic_or_blank_name_is_not_injected(self):
        for generic in ("You", "", "  ", "Me"):
            d = _data([("M1", "C1", "P1", 0, "You need to see this"), ("M2", "C1", "ME", 1, "ok")], me_name=generic)
            self.assertEqual(d.me_names, [])
            self.assertNotIn("addressed", candidates.find(d).get("M1", []))
            prompts, _ = loops.build_prompts(d, candidates.windows(d, candidates.find(d)))
            self.assertNotIn("addressed as", prompts[0] if prompts else "")


class Delegations(unittest.TestCase):
    def test_users_own_delegation_is_a_candidate(self):
        d = _data([("M1", "C1", "ME", 0, "FBC coffee. please ask."), ("M2", "C1", "ME", 1, "pls check with him")])
        c = candidates.find(d)
        self.assertIn("M1", c)
        self.assertIn("M2", c)

    def test_delegation_owned_by_me_can_be_reminded(self):
        d = _data([("M1", "C1", "ME", 0, "please ask them about the shortlist")])
        self.assertIn("owned by ME", loops.SYSTEM)  # the prompt rule for the user's own delegations
        l = _raw("M1", type="followup", needs_me_only=True, p_remind=0.7, p_leave=0.2, p_assist=0.1)
        self.assertEqual(decide.help_for(l, d.now)["action"], "remind")


class ContextThenAsk(unittest.TestCase):
    def test_source_moves_from_context_line_to_the_ask(self):
        d = _data([("M1", "C1", "P1", 0, "Kid's party on 27th sept"), ("M2", "C1", "P1", 0, "Invite to be made"),
                   ("M3", "C1", "P1", 1, "Pls keep a cool font")])
        out = loops.shift_sources([_raw("M1")], d, candidates.find(d))
        self.assertEqual(out[0]["source_msg"], "M2")

    def test_concrete_source_is_not_moved(self):
        d = _data([("M1", "C1", "P1", 0, "Pls book the hall for 27th"), ("M2", "C1", "P1", 0, "Pls send the invite")])
        self.assertEqual(loops.shift_sources([_raw("M1")], d, candidates.find(d))[0]["source_msg"], "M1")

    def test_no_shift_across_senders_or_long_gaps(self):
        d = _data([("M1", "C1", "P1", 0, "Party on 27th"), ("M2", "C1", "ME", 0, "Pls make the invite"),
                   ("M3", "C1", "P1", 30, "Invite to be made")])
        self.assertEqual(loops.shift_sources([_raw("M1")], d, candidates.find(d))[0]["source_msg"], "M1")


class MediaClosure(unittest.TestCase):
    def test_users_media_after_payment_request_is_closure_evidence(self):
        d = _data([("M1", "C1", "P1", 0, "Pls pay 4500"), ("M2", "C1", "ME", 5, "[photo]", "image_caption")])
        l = loops.media_closure([_raw("M1", p_open=0.3, p_done=0.7)], d)[0]
        self.assertEqual(l["close"], "M2")
        self.assertGreater(l["p_done"], 0.7)
        self.assertEqual(decide.status(l), "done")

    def test_evidence_still_needs_twice_open(self):
        d = _data([("M1", "C1", "P1", 0, "Pls pay 4500"), ("M2", "C1", "ME", 5, "[photo]", "image_caption")])
        l = loops.media_closure([_raw("M1", p_open=0.9, p_done=0.1)], d)[0]
        self.assertEqual(decide.status(l), "open")  # boost alone can't overturn a confident "open"

    def test_their_media_or_non_payment_is_not_evidence(self):
        d = _data([("M1", "C1", "P1", 0, "Pls pay 4500"), ("M2", "C1", "P1", 1, "[photo]", "image_caption"),
                   ("M3", "C1", "P1", 2, "Pls book Bhopal"), ("M4", "C1", "ME", 3, "[photo]", "image_caption")])
        out = loops.media_closure([_raw("M1"), _raw("M3")], d)
        self.assertEqual([l["close"] for l in out], [None, None])  # M2 is theirs; M3 isn't a payment


if __name__ == "__main__":
    unittest.main()
