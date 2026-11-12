"""The ONE dataset-term matcher shared by scripts/leak_check.py and scripts/redact_logs.py.

Anything leak_check flags, redact_logs replaces: both call `needles()` for the terms and `finditer()` for the matches.

Terms (from every data folder given):
  - chat titles (groups and 1:1), full sender names, seed organisation names: matched as token sequences,
    case-insensitive, with any run of whitespace / punctuation / underscores between tokens
    ("Jane Doe", "jane_doe", "JANE.DOE", "Jane  Doe" all match); like the old substring check, no word boundary
    is required at the ends;
  - every name token (>= MIN_TOKEN letters) of sender names, 1:1 chat names and seed organisation names, i.e. first
    and last names: matched as whole words, case-insensitive;
  - numbers of 5+ digits from message bodies: matched as whole digit runs (not inside a longer number such as a
    timestamp).
scripts/leak_allow.txt lists generic words that happen to be names; they are excluded everywhere (one token per line).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
MIN_TOKEN = 4
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)  # letters or digits
NUMBER = re.compile(r"\d{5,}")
SEP = r"[\W_]+"

# one-word chat titles / org names of 6+ letters match anywhere, even inside a longer word (as the old check did)
SUBSTRING_SOURCES = ("chat title", "1:1 chat name", "seed organisation")

# category -> replacement token used by the redactor
TOKEN_FOR = {"chat title": "[CHAT]", "1:1 chat name": "[NAME]", "sender name": "[NAME]",
             "seed organisation": "[NAME]", "number in a message": "[NUMBER]"}


def _jsonl(path: Path) -> List[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def allow() -> set:
    p = ROOT / "scripts" / "leak_allow.txt"
    if not p.exists():
        return set()
    return {l.strip().lower() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def _key(text: str) -> str:
    """Normalised form of a term or a match: lowercase tokens joined by one space."""
    return " ".join(t.lower() for t in TOKEN.findall(text))


def needles(data_dir: Path) -> Dict[str, str]:
    """normalised term -> where it came from (category + id)."""
    out: Dict[str, str] = {}
    chats = _jsonl(data_dir / "chats.jsonl")
    msgs = _jsonl(data_dir / "messages.jsonl")
    seed = json.loads((data_dir / "seed.json").read_text(encoding="utf-8")) if (data_dir / "seed.json").exists() else {}

    def add_phrase(text: str, src: str, min_len: int) -> None:
        k = _key(text)
        if len(k.replace(" ", "")) >= min_len:
            out.setdefault(k, src)

    def add_tokens(text: str, src: str) -> None:
        for w in WORD.findall(text or ""):
            if len(w) >= MIN_TOKEN:
                out.setdefault(w.lower(), src)

    for c in chats:
        title = c.get("name") or ""
        if c.get("is_group"):
            add_phrase(title, f"chat title {c['chat_id']}", 3)
        else:  # 1:1 chat names are person names
            add_phrase(title, f"1:1 chat name {c['chat_id']}", 3)
            add_tokens(title, f"1:1 chat name {c['chat_id']}")
    for m in msgs:
        name = m.get("sender_name") or ""
        src = f"sender name ({m['sender_id']})"
        add_phrase(name, src, 3)
        add_tokens(name, src)
    for name in seed.get("organisations") or []:
        add_phrase(name, "seed organisation", 3)
        add_tokens(name, "seed organisation")
    for m in msgs:
        for n in NUMBER.findall(m.get("body") or ""):
            out.setdefault(n, "number in a message")
    al = allow()
    return {k: v for k, v in out.items() if k not in al}


def compile_matcher(needle_map: Dict[str, str]) -> re.Pattern:
    """One case-insensitive regex for all terms, longest first."""
    parts = []
    for k in sorted(needle_map, key=len, reverse=True):
        toks = k.split(" ")
        if len(toks) == 1 and k.isdigit():
            parts.append(r"(?<!\d)" + re.escape(k) + r"(?!\d)")
        elif len(toks) == 1 and WORD.fullmatch(k) and not (len(k) >= 6 and needle_map[k].startswith(SUBSTRING_SOURCES)):
            parts.append(r"(?<![^\W\d_])" + re.escape(k) + r"(?![^\W\d_])")
        else:  # phrase: any separator between tokens; no end boundary (as the old substring check)
            parts.append(SEP.join(re.escape(t) for t in toks))
    return re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile(r"(?!x)x")


def finditer(text: str, needle_map: Dict[str, str], matcher: re.Pattern) -> Iterator[Tuple[int, int, str, str]]:
    """(start, end, normalised term, source) for every dataset term in the text."""
    for m in matcher.finditer(text):
        k = _key(m.group(0))
        src = needle_map.get(k)
        if src is None:  # a phrase matched with extra separators: normalise the same way
            src = needle_map.get(" ".join(k.split()), "dataset term")
        yield m.start(), m.end(), k, src


def category(src: str) -> str:
    for c in TOKEN_FOR:
        if src.startswith(c):
            return c
    return "dataset term"
