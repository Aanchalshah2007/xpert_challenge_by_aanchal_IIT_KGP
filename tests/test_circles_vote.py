"""Circles majority vote (consensus ablation). No network."""
import unittest

from coldstart import circles


class Vote(unittest.TestCase):
    def test_majority_wins_with_mean_confidence(self):
        out = circles.vote([{"P1": ("friend", 0.6)}, {"P1": ("colleague", 0.9)}, {"P1": ("friend", 0.8)}])
        self.assertEqual(out["P1"][0], "friend")
        self.assertAlmostEqual(out["P1"][1], 0.7)

    def test_no_majority_keeps_first_sample(self):
        out = circles.vote([{"P1": ("friend", 0.6)}, {"P1": ("colleague", 0.9)}, {"P1": ("vendor", 0.8)}])
        self.assertEqual(out["P1"], ("friend", 0.6))

    def test_person_missing_from_some_samples(self):
        out = circles.vote([{}, {"P2": ("staff", 0.7)}, {}])
        self.assertEqual(out["P2"], ("staff", 0.7))


if __name__ == "__main__":
    unittest.main()
