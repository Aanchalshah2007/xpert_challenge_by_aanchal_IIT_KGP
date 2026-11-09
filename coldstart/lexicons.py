"""Generic multilingual (English / Hinglish / Bengali-English) patterns. No names from any dataset."""
from __future__ import annotations

import re
from typing import Dict, Pattern


def _words(*ws: str) -> Pattern[str]:
    return re.compile(r"(?<![\w])(?:" + "|".join(ws) + r")(?![\w])", re.IGNORECASE)


# Address / kinship terms. Used on chat names and on ME's messages inside a 1:1 chat.
KIN: Dict[str, Pattern[str]] = {
    "spouse": _words("wife", "wifey", "hubby", "husband", "my love", "jaan", "jaanu", "babe", "baby"),
    "parent": _words("mom", "mum", "mummy", "mommy", "mother", "maa", "amma", "ammi", "papa", "pappa", "dad", "daddy",
                     "father", "pitaji", "mataji", "baba"),
    "sibling": _words("bro", "brother", "sis", "sister", "bhai", "bhaiya", "bhaiyya", "didi", "behen"),
    "in-law": _words("in[- ]?law", "sasur", "saas", "sasu ?maa", "jiju", "jijaji", "bhabhi", "saala", "saali", "devar",
                     "nanad", "jamai", "bahu"),
    "relative": _words("mama(?:ji)?", "mami(?:ji)?", "masi", "mausi", "mashi", "chacha(?:ji)?", "chachi", "kaka", "kaki",
                       "bua", "fufa", "phupha", "nana(?:ji)?", "nani", "dadi", "dadu", "thakuma", "cousin", "uncle",
                       "aunty", "auntie", "aunt", "nephew", "niece", "pishi", "jethu", "jethi", "boudi"),
    "child": _words("beta", "beti", "son", "daughter", "my boy", "my girl"),
}

# Group-chat names that point to family / friends / home.
FAMILY_GROUP = _words("family", "fam", "families", "siblings", "sisters", "brothers", "bros?", "sis", "cousins?",
                      "parivar", "parivaar", "khandaan", "relatives", "in[- ]?laws", "husbands", "wives")
HOME_GROUP = _words("home", "house", "flat", "kitchen", "carpool", "society", "apartments?", "residents?", "building",
                    "tower", "maid", "driver", "cook", "staff", "ghar", "bari", "housekeeping")
# Flat / block numbers like "7A", "5b", "B-1204": a residential hint for a group name.
ADDRESS_HINT = re.compile(r"(?<![\w])(?:\d{1,4}[A-Za-z]|[A-Za-z]-?\d{2,4})(?![\w])")
VACATION_GROUP = _words("farm ?house", "villa", "cottage", "beach ?house", "holiday home", "bungalow", "estate")
WORK_GROUP = _words("ops", "operations", "team", "office", "finance", "sales", "marketing", "growth", "product",
                    "tech", "hr", "accounts", "leadership", "founders?", "board", "pvt", "ltd", "llp", "inc",
                    "client", "campaign", "ads?")

# 1:1 message cues (counted per chat, mostly on the other person's messages).
STAFF_CUES = _words("sir", "saab", "sahab", "madam", "ma'?am", "memsaab", "salary", "chhutti", "chutti", "leave today",
                    "petrol", "diesel", "gaadi", "gadi", "car wash", "groceries", "sabzi", "ration", "pickup", "drop")
VENDOR_CUES = _words("invoice", "quotation", "quote", "estimate", "gst", "bill", "advance", "delivery", "dispatch",
                     "order", "rate", "rates", "per sq ?ft", "sqft", "material", "installation", "fees?", "payment",
                     "trading", "enterprises?", "traders?", "services")
CLIENT_CUES = _words("campaign", "report", "dashboard", "roas", "cac", "ads?", "proposal", "deliverables?", "onboarding",
                     "pilot", "integration", "demo", "contract", "renewal", "attribution", "targeting")
COLLEAGUE_CUES = _words("standup", "sprint", "deploy", "pr", "jira", "deck", "ticket", "office", "team", "hiring",
                        "interview", "release", "bug", "client", "meeting")
FRIEND_CUES = _words("bro", "dude", "yaar", "party", "drinks", "dinner", "match", "game", "trip", "weekend", "bday",
                     "birthday", "catch up", "haha+", "lol")

# Words that never identify an organisation by themselves (used when clustering group names).
NAME_STOPWORDS = {
    "the", "and", "of", "for", "to", "in", "on", "at", "a", "an", "x", "with", "by", "is", "my", "our", "we", "group",
    "groups", "chat", "team", "official", "new", "old", "all", "only", "updates", "update", "connect", "circle",
    "community", "club", "forum", "network", "members", "1", "2", "3", "4", "5", "c1", "c2", "where", "heart", "love",
}

# Request / commitment cues (Phase 2 loop candidates).
REQUEST = _words("pls", "plz", "please", "can you", "could you", "would you", "will you", "possible to", "kindly",
                 "need you to", "let me know", "lmk", "send", "share", "confirm", "pay", "transfer", "decide",
                 "kar do", "kar dena", "bhej do", "bhejo", "batao", "bata do")
COMMIT = _words("will do", "i'?ll", "i will", "let me", "on it", "will send", "will share", "will check", "will call",
                "kar dunga", "kar denge", "bhej dunga", "bhejta", "dekhta", "reverting", "will revert", "tomorrow")
