"""Candidate cues in Devanagari and Bengali script (synthetic sentences; no data). No network."""
import unittest

from coldstart import candidates


def reasons(text: str):
    return candidates.reasons_for({"text": text, "body": text, "sender_id": "P1"})


class Multilingual(unittest.TestCase):
    def test_hindi_request_commit_question(self):
        self.assertIn("request", reasons("कृपया बिल भेज दो"))
        self.assertIn("commit", reasons("मैं कल भेज दूंगा"))
        self.assertIn("question", reasons("आप कब आएंगे"))  # no question mark

    def test_bengali_request_commit_question(self):
        self.assertIn("request", reasons("দয়া করে টাকা পাঠাও"))
        self.assertIn("commit", reasons("আমি কাল পাঠাবো"))
        self.assertIn("question", reasons("তুমি কখন আসবে"))

    def test_no_match_inside_a_longer_word(self):
        # "कब" (when) must not match inside a longer word just because the next character is a vowel sign
        self.assertNotIn("question", reasons("मैंने कबूल किया"))
        self.assertEqual(reasons("नमस्ते, शुभ प्रभात"), [])  # a greeting is not a candidate

    def test_latin_unchanged(self):
        self.assertIn("request", reasons("pls send the invoice"))
        self.assertEqual(reasons("good morning"), [])


if __name__ == "__main__":
    unittest.main()
