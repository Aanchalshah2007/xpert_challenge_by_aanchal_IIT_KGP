#!/usr/bin/env python3
"""Fail if any person name, chat title, seed org name or long number from the data appears in the pipeline.

    python3 scripts/leak_check.py [data_dir ...]      (default: data/dev)

Scans coldstart/ (code + prompts), run.sh and llm.py. Exit 1 on any hit.
Generic words that happen to be names (e.g. a person called "Home") go in scripts/leak_allow.txt,
one per line, only after checking they are not data-specific.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
SCAN = [ROOT / "coldstart", ROOT / "run.sh", ROOT / "llm.py"]
SCAN_EXT = {".py", ".txt", ".sh", ".json", ".md", ".cfg", ".toml", ".yaml", ".yml"}
MIN_TOKEN = 4
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
NUMBER = re.compile(r"\d{5,}")


def _jsonl(path: Path) -> List[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _allow() -> Set[str]:
    p = ROOT / "scripts" / "leak_allow.txt"
    if not p.exists():
        return set()
    return {l.strip().lower() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def needles(data_dir: Path) -> Dict[str, str]:
    """needle (lowercase) -> where it came from."""
    out: Dict[str, str] = {}
    chats = _jsonl(data_dir / "chats.jsonl")
    msgs = _jsonl(data_dir / "messages.jsonl")
    seed = json.loads((data_dir / "seed.json").read_text(encoding="utf-8")) if (data_dir / "seed.json").exists() else {}
    for c in chats:
        title = " ".join((c.get("name") or "").split()).strip()
        if len(title) >= 6:
            out[title.lower()] = f"chat title {c['chat_id']}"
        if not c.get("is_group"):  # 1:1 chat names are person names
            for w in WORD.findall(title):
                if len(w) >= MIN_TOKEN:
                    out[w.lower()] = f"1:1 chat name {c['chat_id']}"
    for m in msgs:
        for w in WORD.findall(m.get("sender_name") or ""):
            if len(w) >= MIN_TOKEN:
                out[w.lower()] = f"sender name ({m['sender_id']})"
    for name in seed.get("organisations") or []:
        out[name.lower()] = "seed organisation"
        for w in WORD.findall(name):
            if len(w) >= MIN_TOKEN:
                out[w.lower()] = "seed organisation word"
    for m in msgs:
        for n in NUMBER.findall(m.get("body") or ""):
            out[n] = "number in a message"
    allow = _allow()
    return {k: v for k, v in out.items() if k not in allow}


def scan(needle_map: Dict[str, str]) -> List[str]:
    hits = []
    word_needles = {k for k in needle_map if WORD.fullmatch(k)}
    phrase_needles = [k for k in needle_map if k not in word_needles]
    files = []
    for root in SCAN:
        files += [root] if root.is_file() else [p for p in root.rglob("*") if p.suffix in SCAN_EXT and "__pycache__" not in p.parts]
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            for w in set(re.findall(r"[^\W\d_]+", low)) & word_needles:
                hits.append(f"{f.relative_to(ROOT)}:{i}: '{w}' ({needle_map[w]})")
            for p in phrase_needles:
                if p in low:
                    hits.append(f"{f.relative_to(ROOT)}:{i}: '{p}' ({needle_map[p]})")
    return hits


def main(argv: List[str]) -> int:
    dirs = [Path(a) for a in argv[1:]] or [ROOT / "data" / "dev"]
    needle_map: Dict[str, str] = {}
    for d in dirs:
        if not (d / "chats.jsonl").exists():
            print(f"leak_check: no data at {d}, nothing to check against")
            continue
        needle_map.update(needles(d))
    hits = scan(needle_map)
    if hits:
        print(f"leak_check: FAIL, {len(hits)} hit(s):")
        for h in hits:
            print("  " + h)
        return 1
    print(f"leak_check: OK ({len(needle_map)} names/titles/numbers checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
