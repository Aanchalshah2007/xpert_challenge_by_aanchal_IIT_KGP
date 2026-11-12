#!/usr/bin/env python3
"""Candidate in-text names for a human decision (zero calls; redacts nothing).

    python3 scripts/term_review.py      # writes data/raw_logs/term_review.md (gitignored): terms + counts, no context

A candidate is a word (3+ letters) that occurs in dev message BODIES, is capitalised there at least once or is not an
ordinary word, and also occurs in ai-logs/. Excluded: ordinary English words (vocabulary built offline from the
Python standard library's text and the system manual pages, since no word list is installed), words used in our
own code and docs, a list of public brands, names the leak check already knows, and allow-listed tokens.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import sysconfig
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import name_match  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw_logs" / "term_review.md"
WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
BRANDS = {  # well-known public brands and services (generic list, not taken from the data)
    "whatsapp", "google", "gmail", "youtube", "android", "iphone", "ipad", "apple", "microsoft", "windows", "excel",
    "outlook", "linkedin", "facebook", "instagram", "twitter", "amazon", "flipkart", "netflix", "spotify", "uber",
    "zomato", "swiggy", "paytm", "phonepe", "gpay", "razorpay", "airtel", "jio", "vodafone", "hdfc", "icici", "sbi",
    "axis", "kotak", "visa", "mastercard", "zoom", "slack", "notion", "canva", "figma", "github", "openai", "chatgpt",
    "claude", "gemini", "groq", "openrouter", "dropbox", "telegram", "skype", "teams", "ola", "rapido", "makemytrip",
    "irctc", "indigo", "vistara", "airbnb", "booking", "oyo", "dunzo", "bigbasket", "blinkit", "zepto", "myntra",
    "nykaa", "ikea", "samsung", "oneplus", "xiaomi", "dell", "lenovo", "intel", "nvidia", "tesla", "toyota",
    "honda", "hyundai", "maruti", "tata", "mahindra", "reliance", "infosys", "wipro", "starbucks", "mcdonalds",
    "dominos", "pdf", "covid", "upi", "aadhaar", "pan", "gst", "neft", "rtgs", "imps",
}


def _vocab() -> set:
    """Ordinary words: Python stdlib text + man pages + our repo's code and docs (lower-case)."""
    v: set = set()
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for f in stdlib.rglob("*.py"):
        if "site-packages" in f.parts or "test" in f.parts:
            continue
        try:
            v.update(w.lower() for w in WORD.findall(f.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass
    for f in Path("/usr/share/man").rglob("*.gz"):
        try:
            v.update(w.lower() for w in WORD.findall(gzip.open(f).read().decode("utf-8", "ignore")))
        except (OSError, EOFError):
            pass
    for f in [*(ROOT / "coldstart").rglob("*"), *(ROOT / "eval").rglob("*.py"), *(ROOT / "scripts").rglob("*.py"),
              *(ROOT / "docs").rglob("*.md"), ROOT / "README.md", ROOT / "check.py", ROOT / "schema.json"]:
        if f.is_file() and f.suffix in (".py", ".txt", ".md", ".json"):
            v.update(w.lower() for w in WORD.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return v


def main() -> int:
    vocab = _vocab()
    known = set(name_match.needles(ROOT / "data" / "dev")) | name_match.allow()
    body_n, cap_n = Counter(), Counter()
    for line in (ROOT / "data" / "dev" / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        body = json.loads(line).get("body") or ""
        for w in WORD.findall(body):
            k = w.lower()
            body_n[k] += 1
            if w[0].isupper():
                cap_n[k] += 1
    logs = {f.name: f.read_text(encoding="utf-8", errors="replace") for f in sorted((ROOT / "ai-logs").glob("*.md"))
            if f.name != "README.md"}
    log_n = {n: Counter(w.lower() for w in WORD.findall(t)) for n, t in logs.items()}
    rows = []
    for k, n in body_n.items():
        if k in vocab or k in BRANDS or k in known:
            continue
        per = {f: c[k] for f, c in log_n.items() if c[k]}
        if not per:
            continue
        rows.append((sum(per.values()), k, n, cap_n[k], per))
    rows.sort(key=lambda r: (-r[0], r[1]))
    md = ["# Term review (candidates only; nothing redacted)", "",
          f"{len(rows)} words occur in dev message bodies, are capitalised there or not ordinary words, and also occur in "
          "ai-logs/. Excluded: ordinary English (stdlib + man-page vocabulary), our code's words, public brands, names "
          "the leak check already knows. Counts only. Mark the ones to redact and I will add them as a rule.", "",
          "| term | in ai-logs (total) | per file | in dev bodies | capitalised in bodies |", "|---|---|---|---|---|"]
    for tot, k, n, cap, per in rows:
        md.append(f"| {k} | {tot} | {', '.join(f'{f} {c}' for f, c in per.items())} | {n} | {cap} |")
    OUT.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"vocabulary {len(vocab):,} words; {len(rows)} candidate terms written to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
