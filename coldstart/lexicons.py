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

# Loop candidates (high recall; the LLM decides what is real).
REQUEST = _words("pls", "plz", "pl", "please", "kindly", "can (?:you|u|we)", "could (?:you|u|we)", "would (?:you|u)",
                 "will (?:you|u)", "possible to", "need (?:you|u) to", "need to", "i need", "we need", "let me know",
                 "lmk", "let'?s", "lets", "shall (?:we|i)", "should (?:i|we)", "do you", "did you", "have you", "any update",
                 "reminder", "remind", "required", "asap", "urgent", "waiting", "follow ?up", "help",
                 "kar do", "kar dena", "karo", "karna", "karwa", "bhej do", "bhejo", "batao", "bata do", "dena", "dedo",
                 "chahiye", "lena", "le lo", "dekh lo", "karte hai", "karenge", "go ahead", "(?:you|u|we) can")
# Quotes, price lists and numbered task lists: things someone now has to act on.
QUOTE = _words("quotation", "quote", "estimate", "invoice", "proforma", "pi", "po", "per (?:kg|piece|pc|unit|sq ?ft|month)",
               "gst", "advance", "balance due")
TASK_LIST = re.compile(r"(?:^|\n|\s)(?:1[.)]|[-•*]\s).{3,}(?:\n|\s)(?:2[.)]|[-•*]\s)", re.DOTALL)
# Imperative verbs at the start of a message: "Pay 3500", "Order one more", "Send your address".
IMPERATIVE = re.compile(
    r"^(?:\W*(?:hi|hey|hello|ok|okay|and|also|so|then|sir|ma'?am|bhai|bhaiya|didi|ji)\W+)*"
    r"(?:pay|send|share|order|buy|get|book|call|text|message|ask|check|confirm|decide|fix|arrange|bring|transfer|"
    r"forward|sign|submit|approve|review|update|invite|schedule|reschedule|cancel|renew|collect|drop|pick|tell|"
    r"inform|find|figure|discuss|meet|reply|revert|clean|repair|replace|return|deliver)\b",
    re.IGNORECASE,
)
COMMIT = _words("will do", "i'?ll", "ill", "i will", "we'?ll", "we will", "let me", "on it", "will \\w+", "shall \\w+",
                "going to", "gonna", "reverting", "will revert", "get back", "noted", "done by", "by (?:mon|tues|wednes|"
                "thurs|fri|satur|sun)day", "kar dunga", "kar dungi", "kar denge", "bhej dunga", "bhej dungi", "bhejta",
                "bhejti", "dekhta", "dekhti", "karta hu", "karti hu", "dunga", "dungi")
# Payment requests (with MONEY below): media the user sends right after one is closure evidence.
PAY = _words("pay", "paid", "payment", "transfer", "upi", "gpay", "paytm", "phonepe", "neft", "imps", "reimburse",
             "fees?", "dues?", "bhej do paise", "paise")
# Dates, times and money: deadlines, meetings and payments.
TIME_REF = _words("today", "tonight", "tomorrow", "tmrw", "tmr", "kal", "aaj", "parso", "this week", "next week",
                  "weekend", "(?:mon|tues|wednes|thurs|fri|satur|sun)day", "mon|tue|wed|thu|fri|sat|sun",
                  "\\d{1,2}(?::\\d{2})? ?(?:am|pm)", "\\d{1,2}(?:st|nd|rd|th)?\\s(?:jan|feb|mar|apr|may|jun|jul|aug|sep|"
                  "oct|nov|dec)\\w*", "(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\\w*\\s\\d{1,2}", "eod", "deadline",
                  "due", "before", "by")
MONEY = re.compile(r"(?:₹|rs\.?|inr)\s?\d|\d[\d,]{2,}\s?(?:/-|rs|inr|k|lakh|lakhs|cr)?\b", re.IGNORECASE)


# ---- Devanagari (Hindi) and Bengali script: generic request / commitment / question words (no names).
# Boundaries must cover the scripts' vowel signs, which \w does not match.
_INDIC = "\u0900-\u09FF"


def _indic(*ws: str) -> Pattern[str]:
    return re.compile(r"(?<![\w" + _INDIC + r"])(?:" + "|".join(ws) + r")(?![\w" + _INDIC + r"])")


REQUEST_INDIC = _indic(
    # Hindi: please, send, do, give, tell me, pay, bring, needed
    "कृपया", "प्लीज़", "प्लीज", "भेजो", "भेज दो", "भेज दीजिए", "भेजिए", "भेजना", "करो", "कर दो", "कर दीजिए", "कीजिए",
    "करिए", "करना", "दे दो", "दीजिए", "देना", "बताओ", "बता दो", "बताइए", "बताना", "बता देना", "भुगतान करें",
    "भुगतान कर दो", "पेमेंट कर दो", "ले आओ", "लाओ", "चाहिए",
    # Bengali: please, send, do, give, tell me, pay, needed
    "দয়া করে", "অনুগ্রহ করে", "প্লিজ", "পাঠাও", "পাঠান", "পাঠিয়ে দাও", "পাঠিয়ে দিন", "করো", "করুন", "করে দাও",
    "করে দিন", "দাও", "দিয়ে দিন", "বলো", "বলুন", "জানাও", "জানান", "টাকা দাও", "টাকা পাঠাও", "পেমেন্ট করুন", "লাগবে",
)
COMMIT_INDIC = _indic(
    # Hindi: I will do / send / give / tell, it will be done
    "करूंगा", "करूँगा", "करूंगी", "करूँगी", "कर दूंगा", "कर दूँगा", "कर दूंगी", "कर दूँगी", "भेज दूंगा", "भेज दूँगा",
    "भेज दूंगी", "भेज दूँगी", "दे दूंगा", "दे दूँगा", "बता दूंगा", "बता दूँगा", "हो जाएगा", "करता हूं", "करता हूँ",
    "करती हूं", "करती हूँ",
    # Bengali: I will do / send / give / let you know, it will be done
    "করব", "করবো", "করে দেব", "করে দেবো", "পাঠাব", "পাঠাবো", "পাঠিয়ে দেব", "পাঠিয়ে দেবো", "দেব", "দেবো", "জানাব",
    "জানাবো", "হয়ে যাবে",
)
QUESTION_INDIC = _indic(
    # Hindi: what / when / where / how / why / who / how much
    "क्या", "कब", "कहाँ", "कहां", "कैसे", "क्यों", "कौन", "कितना", "कितने", "कितनी",
    # Bengali: what / when / where / how / why / who / how much
    "কি", "কী", "কখন", "কবে", "কোথায়", "কেমন", "কেন", "কে", "কত",
)
