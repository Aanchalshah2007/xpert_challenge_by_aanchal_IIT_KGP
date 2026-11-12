#!/usr/bin/env python3
"""Write redacted copies of raw AI-session transcripts (data/raw_logs/, gitignored) into ai-logs/. Zero calls.

    python3 scripts/redact_logs.py [--src data/raw_logs] [--dst ai-logs] [--dry-run]

Replaces, in this order (counts per category are printed; nothing replaced is ever printed):
  api keys      values of *KEY* / *TOKEN* / *SECRET* variables in the local .env files; sk-/gsk_/AIza/xai- keys;
                32+ hex characters; the value after "Bearer" or "X-API-Key"; the value of any *API_KEY* / *TOKEN* /
                *SECRET* variable being set (NAME=value, "name": "value")                                   -> [API_KEY]
  excerpts      any dataset message body longer than 15 chars (also JSON-escaped); the first 40+ chars of
                long bodies (truncated prints); any run of 5+ consecutive words also found in a body, ignoring
                case, punctuation and line wraps (partial quotes); a whole 2-4 word body inside quotation marks
                (scripts/excerpt_match.py, shared with the leak check)                                      -> [dataset excerpt removed]
  urls          every URL                                                                                    -> [URL]
  emails        every e-mail address                                                                         -> [EMAIL]
  user paths    the local user's folder name in C:\\Users\\<name>\\, /mnt/c/Users/<name>/, /home/<user>/ and the
                dash-encoded -Users-<name>- form, also when an export wrapped it onto two lines               -> [USER]
  phones        +CC numbers and 10-digit mobile numbers                                                      -> [PHONE]
  passwords     the value after password / passcode / pwd / otp / pin                                        -> [SECRET]
  dataset terms chat titles, sender names, 1:1 chat names, seed organisation names, their name tokens, and
                5+ digit numbers from messages, via scripts/name_match.py: the SAME matcher the leak check uses
                (case-insensitive, any separators between name tokens)                                      -> [CHAT] / [NAME] / [NUMBER]
  person ids    person ids from the datasets (e.g. P07)                                                      -> [PERSON]
Tokens in scripts/leak_allow.txt are kept (shared with the leak check). Everything else is kept verbatim.
An existing destination file is only replaced if this script wrote it (first-line marker), never otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excerpt_match  # noqa: E402
import name_match  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARK = "<!-- redacted copy written by scripts/redact_logs.py; review before committing -->"
MIN_BODY = 16  # "longer than ~15 chars"
MIN_PREFIX = 40  # truncated prints: a body's first 40+ chars
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".log", ".html", ".csv"}

KEY_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"), re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])"),
]
AUTH_VALUE = re.compile(r"(?i)(\b(?:bearer|x-api-key)\b[\s:=\"']+)(?!\[)[A-Za-z0-9._\-]{8,}")
# NAME_API_KEY=value, "api_key": "value", SECRET: value ... (any key/token/secret variable being set)
KEY_ASSIGN = re.compile(r"(?i)(\b[A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN(?!S))[A-Z0-9_]*[\"']?\s*[=:]\s*[\"']?)(?!\[)([^\s\"',]{8,})")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE = [re.compile(r"\+\d{1,3}[\s\-]?\d{3,5}[\s\-]?\d{3,5}(?:[\s\-]?\d{2,5})?"), re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")]
PASSWORD = re.compile(r"(?i)\b(password|passwd|passcode|pwd|otp|pin)(\s*(?:\bis\b|:|=)\s*)(?!\[)(\S{3,})")
URL = re.compile(r"https?://[^\s\"'<>)\]]+")
PERSON_ID = re.compile(r"^[A-Z]\d{1,4}$")


def _jsonl(p: Path) -> List[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _env_secrets() -> List[str]:
    """Values of key/token variables in local env files (read only; never printed)."""
    out = []
    for f in ROOT.glob(".env*"):
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().rstrip("\r")
            if "=" in line and re.search(r"KEY|TOKEN|SECRET", line.split("=", 1)[0], re.I):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 12:
                    out.append(v)
    return out


def data_dirs() -> List[Path]:
    """Real data folders. Name-swapped copies (eval/anonymise.py, *_anon) hold made-up names that identify no one;
    their 3-letter inventions collide with ordinary words, so they are left out (as in the leak check's default)."""
    return [d for d in sorted((ROOT / "data").iterdir()) if (d / "messages.jsonl").exists()
            and (d / "chats.jsonl").exists() and not d.name.endswith("_anon")]


def dataset_terms() -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """Excerpts and person ids (redactor-only) + the shared name/number needles of every data folder."""
    t: Dict[str, Set[str]] = {"bodies": set(), "prefixes": set(), "ids": set()}
    needle_map: Dict[str, str] = {}
    for d in data_dirs():
        needle_map.update(name_match.needles(d))
        for m in _jsonl(d / "messages.jsonl"):
            body = m.get("body") or ""
            if len(body) >= MIN_BODY:
                t["bodies"].add(body)
                flat = " ".join(body.split())
                if len(flat) >= MIN_PREFIX:
                    t["prefixes"].add(flat[:MIN_PREFIX])
            if PERSON_ID.match(m.get("sender_id") or ""):
                t["ids"].add(m["sender_id"])
        for c in _jsonl(d / "chats.jsonl"):
            t["ids"].update(p for p in c.get("participants") or [] if PERSON_ID.match(p))
        seed = json.loads((d / "seed.json").read_text(encoding="utf-8")) if (d / "seed.json").exists() else {}
        t["ids"].update(p for p in (seed.get("circles") or {}) if PERSON_ID.match(p))
    return t, needle_map


def _replace_all(text: str, needles: List[str], token: str) -> Tuple[str, int]:
    n = 0
    for s in sorted(needles, key=len, reverse=True):
        if s and s in text:
            n += text.count(s)
            text = text.replace(s, token)
    return text, n


def redact(text: str, terms: Dict[str, Set[str]], needle_map: Dict[str, str], matcher: re.Pattern,
           secrets: List[str], body_index: "excerpt_match.BodyIndex", user_names: List[str]) -> Tuple[str, Counter]:
    c: Counter = Counter()
    text, c["api keys"] = _replace_all(text, secrets, "[API_KEY]")
    for p in KEY_PATTERNS:
        text, k = p.subn("[API_KEY]", text)
        c["api keys"] += k
    text, k = AUTH_VALUE.subn(lambda m: m.group(1) + "[API_KEY]", text)
    c["api keys"] += k
    text, k = KEY_ASSIGN.subn(lambda m: m.group(1) + "[API_KEY]", text)
    c["api keys"] += k
    bodies = list(terms["bodies"])
    escaped = [json.dumps(b, ensure_ascii=False)[1:-1] for b in bodies]
    text, k1 = _replace_all(text, bodies + [e for e in escaped if e not in terms["bodies"]], "[dataset excerpt removed]")
    text, k2 = _replace_all(text, list(terms["prefixes"]), "[dataset excerpt removed]")
    c["excerpts (full)"], c["excerpts (truncated prints)"] = k1, k2
    text, c["excerpts (5+ word overlap)"] = excerpt_match.replace_spans(
        text, excerpt_match.ngram_spans(text, body_index), "[dataset excerpt removed]")
    text, c["excerpts (quoted short message)"] = excerpt_match.replace_spans(
        text, excerpt_match.quoted_short_spans(text, body_index), "[dataset excerpt removed]")
    text, c["urls"] = URL.subn("[URL]", text)
    text, c["emails"] = EMAIL.subn("[EMAIL]", text)
    text, c["user paths"] = excerpt_match.replace_spans(text, excerpt_match.user_path_spans(text, user_names), "[USER]")
    for p in PHONE:
        text, k = p.subn("[PHONE]", text)
        c["phones"] += k
    text, c["passwords"] = PASSWORD.subn(lambda m: m.group(1) + m.group(2) + "[SECRET]", text)
    pieces, last = [], 0
    for start, end, _, src in name_match.finditer(text, needle_map, matcher):
        cat = name_match.category(src)
        pieces += [text[last:start], name_match.TOKEN_FOR.get(cat, "[NAME]")]
        last = end
        c["dataset " + cat] += 1
    text = "".join(pieces) + text[last:]
    if terms["ids"]:
        text, c["person ids"] = re.subn(r"\b(" + "|".join(sorted(terms["ids"], key=len, reverse=True)) + r")\b",
                                        "[PERSON]", text)
    return text, c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data" / "raw_logs"))
    ap.add_argument("--dst", default=str(ROOT / "ai-logs"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in TEXT_EXT] if src.exists() else []
    if not files:
        print(f"no transcripts found in {src}")
        return 0
    terms, needle_map = dataset_terms()
    matcher = name_match.compile_matcher(needle_map)
    secrets = _env_secrets()
    body_index = excerpt_match.BodyIndex.from_data(data_dirs())
    user_names = excerpt_match.user_names(ROOT)
    print(f"dataset terms: {len(terms['bodies'])} bodies, {len(needle_map)} names/titles/numbers (shared matcher), "
          f"{len(terms['ids'])} person ids; {len(secrets)} key values from env files")
    total: Counter = Counter()
    for f in files:
        out = dst / f.relative_to(src)
        if out.exists() and not out.read_text(encoding="utf-8", errors="replace").startswith(MARK):
            print(f"SKIP {out.name}: exists and was not written by this script (not overwritten)")
            continue
        text, c = redact(f.read_text(encoding="utf-8", errors="replace"), terms, needle_map, matcher, secrets,
                         body_index, user_names)
        total += c
        print(f"{f.name}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if v))
        if not a.dry_run:
            out.parent.mkdir(parents=True, exist_ok=True)
            body = text if out.suffix.lower() in (".json", ".jsonl") else MARK + "\n" + text
            out.write_text(body, encoding="utf-8")
    print("TOTAL: " + ", ".join(f"{k} {v}" for k, v in sorted(total.items()) if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
