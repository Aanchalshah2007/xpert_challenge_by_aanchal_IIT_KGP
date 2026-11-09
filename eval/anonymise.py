#!/usr/bin/env python3
"""Name-swapped copy of a data folder, to test that the pipeline doesn't lean on names.

    python3 eval/anonymise.py data/dev data/dev_anon [--seed 7]

Every word of every person name (sender names, 1:1 chat names) and of the seed organisation names is
replaced by a made-up word, consistently, in message bodies, sender names, chat titles, seed.json and
the answer key. Acronyms of seed organisations ("BlueSkyLabs" -> "BSL") follow their new names.
Ids, timestamps and structure are unchanged, so check.py scores the copy with the same key.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from coldstart.util import acronym  # noqa: E402

WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
ONSETS = ["b", "d", "f", "g", "k", "l", "m", "n", "p", "r", "s", "t", "v", "z", "sh", "ch", "tr", "pr", "kh"]
VOWELS = ["a", "e", "i", "o", "u", "aa", "ee", "ai"]


def _read(path: Path) -> List[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write(path: Path, rows: List[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _generic_words() -> set:
    allow = ROOT / "scripts" / "leak_allow.txt"
    words = {"the", "and", "for", "with", "from", "mr", "mrs", "dr", "sir", "madam", "ji", "office", "home"}
    if allow.exists():
        words |= {l.strip().lower() for l in allow.read_text().splitlines() if l.strip() and not l.startswith("#")}
    return words


class Swapper:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.map: Dict[str, str] = {}
        self.used: set = set()

    def fake(self, word: str) -> str:
        key = word.lower()
        if key not in self.map:
            n, tries = max(1, min(3, len(word) // 3)), 0
            while True:
                new = "".join(self.rng.choice(ONSETS) + self.rng.choice(VOWELS) for _ in range(n))
                if new not in self.used and len(new) >= 3:
                    break
                tries += 1
                if tries % 50 == 0:
                    n += 1  # short names run out of combinations; go one syllable longer
            self.used.add(new)
            self.map[key] = new
        return self.map[key]

    def regex(self) -> re.Pattern:
        keys = sorted(self.map, key=len, reverse=True)
        return re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.IGNORECASE)

    def apply(self, text: str, pat: re.Pattern) -> str:
        def sub(m: re.Match) -> str:
            old, new = m.group(0), self.map[m.group(0).lower()]
            if old.isupper() and len(old) > 1:
                return new.upper()
            if old[0].isupper():
                return new[0].upper() + new[1:]
            return new
        return pat.sub(sub, text or "") if self.map else text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    chats, msgs = _read(src / "chats.jsonl"), _read(src / "messages.jsonl")
    seed = json.loads((src / "seed.json").read_text(encoding="utf-8"))
    generic = _generic_words()
    sw = Swapper(args.seed)

    # Seed orgs first: camel parts mapped, acronym follows the new name.
    org_rename: Dict[str, str] = {}
    for name in seed.get("organisations") or []:
        new_words = []
        for w in re.findall(r"[^\W_]+", name):
            parts = CAMEL.findall(w) or [w]
            new_words.append("".join(sw.fake(p).capitalize() for p in parts))
        new = " ".join(new_words)
        org_rename[name] = new.upper() if name.isupper() else new
        sw.map[name.lower()] = org_rename[name].lower()
        old_acr, new_acr = acronym(name), acronym(new)
        if len(old_acr) >= 2 and old_acr not in generic:
            sw.map[old_acr] = new_acr

    names = [m.get("sender_name") or "" for m in msgs] + [c.get("name") or "" for c in chats if not c.get("is_group")]
    for n in names:
        for w in WORD.findall(n):
            if w.lower() not in generic:
                sw.fake(w)
    pat = sw.regex()

    dst.mkdir(parents=True, exist_ok=True)
    for c in chats:
        c["name"] = sw.apply(c.get("name"), pat)
    for m in msgs:
        m["body"] = sw.apply(m.get("body"), pat)
        m["sender_name"] = sw.apply(m.get("sender_name"), pat)
    seed["organisations"] = [org_rename.get(o, o) for o in seed.get("organisations") or []]
    _write(dst / "chats.jsonl", chats)
    _write(dst / "messages.jsonl", msgs)
    (dst / "seed.json").write_text(json.dumps(seed, indent=1, ensure_ascii=False), encoding="utf-8")

    for sub in ("answers", "labels"):
        if (src / sub).exists():
            shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)
    ans_path = dst / "answers" / "answers.json"
    if ans_path.exists():
        ans = json.loads(ans_path.read_text(encoding="utf-8"))
        for o in ans.get("organisations", []):
            o["name"] = sw.apply(o["name"], pat)
        for l in ans.get("open_loops", []):
            l["title"] = sw.apply(l.get("title"), pat)
        ans_path.write_text(json.dumps(ans, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"anonymised {len(msgs)} messages, {len(sw.map)} words swapped -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
