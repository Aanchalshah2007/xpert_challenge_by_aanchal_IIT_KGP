"""Help = both agree (ownership rule AND 2x probability rule), with the reason a loop is left. No network."""
import unittest
from unittest import mock

from coldstart import config, decide
from tests.test_loops_decide import T0, _raw


class BothAgree(unittest.TestCase):
    def test_someone_elses_move_is_left_with_that_reason(self):
        h = decide.help_for(_raw(owner="P1", owner_is_me=False), T0)
        self.assertEqual((h["action"], h["reason"]), ("leave", "someone else's to handle"))

    def test_both_rules_agree_on_assist(self):
        ok = _raw(next="Draft the reply", p_assist=0.8, p_remind=0.1, p_leave=0.1)
        self.assertEqual(decide.help_for(ok, T0)["action"], "assist")

    def test_disagreement_on_a_sensitive_loop_is_too_personal(self):
        l = _raw(needs_me_only=True, sensitive=True, p_assist=0.1, p_remind=0.3, p_leave=0.6)
        self.assertEqual(decide.ownership_help(l, T0)["action"], "remind")  # the ownership rule would remind
        h = decide.help_for(l, T0)
        self.assertEqual((h["action"], h["reason"]), ("leave", "too personal"))

    def test_disagreement_otherwise_means_a_reminder_would_annoy(self):
        l = _raw(needs_me_only=True, p_assist=0.1, p_remind=0.3, p_leave=0.6)
        h = decide.help_for(l, T0)
        self.assertEqual((h["action"], h["reason"]), ("leave", "a reminder would likely annoy"))

    def test_agreement_on_remind(self):
        l = _raw(needs_me_only=True, p_assist=0.0, p_remind=0.8, p_leave=0.2)
        self.assertEqual(decide.help_for(l, T0)["action"], "remind")

    def test_ownership_only_flag(self):
        l = _raw(needs_me_only=True, p_assist=0.1, p_remind=0.3, p_leave=0.6)
        with mock.patch.object(config, "HELP_RULE", "ownership"):
            self.assertEqual(decide.help_for(l, T0)["action"], "remind")


if __name__ == "__main__":
    unittest.main()
