"""Regression tests for loop patterns found in the zero-call analysis. Synthetic chats, made-up names, no LLM."""
import contextlib
import json
import re
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



@contextlib.contextmanager
def _settings(**kw):
    """Temporarily set config values (the v2 options stay testable while v1 is the default)."""
    old = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(config, k, v)


class Bursts(unittest.TestCase):
    def test_busy_chat_is_split_into_windows_with_few_candidates(self):
        asks = ["Pls pay 2000", "Pls book the tickets", "Can you call the plumber?", "Pls order the lamp",
                "Send me the link", "Pls get milk", "Can we take it to the farm?", "Need to discuss school",
                "Pls fix the fan", "Pls renew the insurance", "Can u check the car?", "Pls pay 4200 for this"]
        d = _data([(f"M{i:02d}", "C1", "P1", i, a) for i, a in enumerate(asks, 1)])
        with _settings(WINDOW_MAX_CANDIDATES=8):  # v2 option; v1 (the default) has no cap
            wins = candidates.windows(d, candidates.find(d))
        self.assertGreaterEqual(len(wins), 2)
        self.assertTrue(all(len(w.candidates) <= 8 for w in wins))
        covered = {c for w in wins for c in w.candidates}
        self.assertEqual(covered, {f"M{i:02d}" for i in range(1, len(asks) + 1)})  # nothing lost by splitting

    def test_prompt_uses_needs_me_only_and_no_name_field(self):
        self.assertIn('"needs_me_only"', loops.SYSTEM)
        self.assertEqual(re.findall(r"needs_\w+_only", loops.SYSTEM), ["needs_me_only"] * 2)


class NameAddressed(unittest.TestCase):
    def test_runtime_name_tags_group_message_and_reaches_prompt(self):
        chats = [{"chat_id": "G1", "name": "Ops", "is_group": True, "participants": ["ME", "P1", "P2"]}]
        d = _data([("M1", "G1", "P1", 0, "Zorvik, we need the signed copy to raise the order"),
                   ("M2", "G1", "P2", 1, "talmane bhai any news"),
                   ("M3", "G1", "P2", 2, "nice weather"),
                   ("M4", "G1", "ME", 3, "on it")], chats=chats)  # the name is read from ME's own messages
        self.assertEqual(d.me_names, ["Zorvik Talmane", "Zorvik"])
        with _settings(LOOPS_USE_ME_NAME=True):  # v2 option
            c = candidates.find(d)
            self.assertIn("addressed", c["M1"])
            self.assertNotIn("M3", c)
            prompts, _ = loops.build_prompts(d, candidates.windows(d, c))
            self.assertIn("Zorvik Talmane", prompts[0])
        c = candidates.find(d)  # v1 (default): the name is neither a cue nor in the prompt
        self.assertNotIn("addressed", c.get("M1", []))
        prompts, _ = loops.build_prompts(d, candidates.windows(d, c))
        self.assertNotIn("addressed as", prompts[0] if prompts else "")

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

    def test_delegation_is_the_other_persons_move(self):
        # f2 (changelog 22): a followup waits on someone else, whatever the owner field says: leave, remind if overdue.
        d = _data([("M1", "C1", "ME", 0, "please ask them about the shortlist")])
        l = _raw("M1", type="followup", needs_me_only=True, p_remind=0.7, p_leave=0.2, p_assist=0.1)
        self.assertEqual(decide.ownership_help(l, d.now)["action"], "leave")
        overdue = dict(l, due="2000-01-01")
        self.assertEqual(decide.ownership_help(overdue, d.now)["action"], "remind")


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


class CompactPromptFormat(unittest.TestCase):
    def test_compact_lines_keep_id_date_time_sender_and_text(self):
        d = _data([("M1", "C1", "P1", 0, "Pls  pay\nthe 2000"), ("M2", "C1", "ME", 1, "ok"),
                   ("M3", "C1", "P1", 24 * 60, "Did you pay?")])
        wins = candidates.windows(d, candidates.find(d))
        with _settings(PROMPT_COMPACT=False):
            old, old_maps = loops.build_prompts(d, wins)
        with _settings(PROMPT_COMPACT=True):
            new, new_maps = loops.build_prompts(d, wins)
        self.assertEqual(old_maps, new_maps)  # same local ids
        lines = new[0].split("\n")
        self.assertEqual(sum(l.startswith("-- ") for l in lines), 2)  # one date line per day
        self.assertTrue(any(l.startswith("*m1 ") and l.endswith("P1: Pls pay the 2000") for l in lines))
        self.assertIn("Pls pay the 2000", new[0])
        self.assertLess(len(new[0]), len(old[0]))


class CirclesAndGoalsParsing(unittest.TestCase):
    def test_generic_family_answer_is_kept_for_people_py(self):
        from coldstart import circles
        got = circles.parse({"people": [{"id": "P1", "circle": "family", "conf": 0.9},
                                        {"id": "P2", "circle": "boss", "conf": 0.9},
                                        {"id": "P9", "circle": "friend", "conf": 0.9}]}, {"P1", "P2"})
        self.assertEqual(got, {"P1": ("family", 0.9)})  # unknown circle and unknown id dropped

    def test_goal_with_one_verified_own_message_is_kept(self):
        from coldstart import goals
        d = _data([("M1", "C1", "ME", 0, "Planning to run a marathon by March, training every morning"),
                   ("M2", "C1", "P1", 1, "Want to run a marathon too")])
        reply = {"goals": [{"title": "Run a marathon", "area": "health", "type": "milestone", "horizon": "3mo",
                            "evidence": ["g1", "g2"]}]}
        got = goals.parse(reply, {"g1": "M1", "g2": "M2"}, d)
        self.assertEqual(got[0]["evidence"], ["M1"])  # the other person's message is not ME's evidence
