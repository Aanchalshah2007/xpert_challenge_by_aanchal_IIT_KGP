#!/usr/bin/env python3
"""Build data/dev_small/: ~20-25 whole chats from data/dev for an end-to-end integration run. Zero calls.

    python3 eval/make_dev_small.py [--src data/dev] [--dst data/dev_small]

Chats are picked by shape, never by name: a busy 1:1 chat, a few mid-sized 1:1 chats, several groups below
the three busiest, the quietest chats, and the chats with the most image and document messages.
Chats are copied whole (messages, quotes); seed circles are kept for people still present.
No answers/ or labels/ are copied: this set is for integration only, not scoring.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
MAX_MEDIA_CHAT = 150  # media picks come from chats at most this long


def pick(chats: List[dict], msgs: List[dict]) -> List[str]:
    n = Counter(m["chat_id"] for m in msgs)
    img = Counter(m["chat_id"] for m in msgs if m["kind"] == "image_caption")
    doc = Counter(m["chat_id"] for m in msgs if m["kind"] == "document")
    ones = sorted((c["chat_id"] for c in chats if not c["is_group"] and n[c["chat_id"]]), key=lambda c: (-n[c], c))
    groups = sorted((c["chat_id"] for c in chats if c["is_group"] and n[c["chat_id"]]), key=lambda c: (-n[c], c))
    every = sorted((c["chat_id"] for c in chats if n[c["chat_id"]]), key=lambda c: (n[c], c))
    chosen: List[str] = []

    def add(ids):
        for c in ids:
            if c not in chosen:
                chosen.append(c)

    # Sized for one free-tier day of a small model (~200K tokens): the very largest chats are left out.
    add(ones[1:2])  # a busy 1:1 (the second busiest)
    add(ones[len(ones) // 4: len(ones) // 4 + 5])  # mid-sized 1:1
    add(groups[3:8])  # several groups, below the three busiest
    add(every[:6])  # quiet chats
    add([c for c, _ in doc.most_common() if n[c] <= MAX_MEDIA_CHAT][:3])  # chats with documents
    add([c for c, _ in img.most_common() if n[c] <= MAX_MEDIA_CHAT][:3])  # chats with images
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--dst", default=str(ROOT / "data" / "dev_small"))
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    chats = [json.loads(l) for l in (src / "chats.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    msgs = [json.loads(l) for l in (src / "messages.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    keep = set(pick(chats, msgs))
    k_chats = [c for c in chats if c["chat_id"] in keep]
    k_msgs = [m for m in msgs if m["chat_id"] in keep]
    ids = {m["id"] for m in k_msgs}
    for m in k_msgs:
        if m.get("quoted_id") and m["quoted_id"] not in ids:
            m["quoted_id"] = None
    people = {p for c in k_chats for p in c["participants"]}
    seed = json.loads((src / "seed.json").read_text(encoding="utf-8"))
    if isinstance(seed.get("circles"), dict):
        seed["circles"] = {p: v for p, v in seed["circles"].items() if p in people}
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    (dst / "chats.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in k_chats) + "\n", encoding="utf-8")
    (dst / "messages.jsonl").write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in k_msgs) + "\n", encoding="utf-8")
    (dst / "seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = Counter(m["kind"] for m in k_msgs)
    n = Counter(m["chat_id"] for m in k_msgs)
    print(f"{dst}: {len(k_chats)} chats ({sum(c['is_group'] for c in k_chats)} groups), {len(k_msgs)} messages, "
          f"kinds {dict(kinds)}, sizes {sorted(n.values(), reverse=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
