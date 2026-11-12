#!/usr/bin/env python3
"""Blind help hold-out: a label sheet of open loops the 25 hand labels don't cover, and its scorer. Zero calls.

    python3 eval/holdout.py make      # writes data/dev/labels/holdout_sheet.md (+ holdout_ids.json); no actions shown
    python3 eval/holdout.py score [out.json]   # system actions vs the hold-out labels (default: the final run)

Selection is blind to the system's help actions: open loops of the final run whose source is not among (or accepted
together with) the 25 labelled sources, at most one per chat, drawn with a fixed seed. Scoring follows check.py's
help rule: right action +1, assist/remind where the label says leave −1, otherwise 0; reported out of 10.
"""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402

DEV = ROOT / "data" / "dev"
LABELS = DEV / "labels"
SHEET = LABELS / "holdout_sheet.md"
IDS = LABELS / "holdout_ids.json"
OUT = ROOT / "eval" / "runs" / "out_final.json"
N, SEED, CONTEXT = 10, 23, 3
IST = timezone(timedelta(hours=5, minutes=30))


def make() -> int:
    chats, messages, seed, ans = check.load_input(str(DEV))
    out = json.loads(OUT.read_text(encoding="utf-8"))
    labelled = {x["source_msg"] for x in json.loads((LABELS / "help.json").read_text(encoding="utf-8"))}
    accepted = {s: set(a["source_msgs"]) for a in ans["open_loops"] for s in a["source_msgs"]}
    excluded = set(labelled) | {s for l in labelled for s in accepted.get(l, set())}
    pool = [l for l in out["open_loops"] if l["status"] == "open" and l["source_msg"] not in excluded]
    rng = random.Random(SEED)
    rng.shuffle(pool)
    picked, chats_used = [], set()
    for l in pool:
        if l["chat_id"] not in chats_used:
            picked.append(l)
            chats_used.add(l["chat_id"])
        if len(picked) == N:
            break
    by_chat = {}
    for m in sorted(messages, key=lambda m: (m["chat_id"], m["ts"])):
        by_chat.setdefault(m["chat_id"], []).append(m)
    cmeta = {c["chat_id"]: c for c in chats}
    lines = ["# Blind help hold-out (10 open loops, not among the 25 labelled)", "",
             "For each loop, write the help action you want (assist / remind / leave) and a short why, as for the 25 "
             "labels. The system's action is not shown. `>>` marks the source message. Fill in the `help:` and `why:` "
             "lines only.", ""]
    for i, l in enumerate(picked, 1):
        msgs = by_chat[l["chat_id"]]
        k = next(j for j, m in enumerate(msgs) if m["id"] == l["source_msg"])
        c = cmeta[l["chat_id"]]
        lines += [f"## H{i} — {l['title']}", "",
                  f"Chat: {c.get('name') or l['chat_id']} ({'group' if c.get('is_group') else '1:1'}); "
                  f"loop type: {l['type']}; due: {l.get('due') or '-'}", ""]
        for m in msgs[max(0, k - CONTEXT): k + CONTEXT + 1]:
            when = datetime.fromtimestamp(m["ts"], IST).strftime("%a %d %b %H:%M")
            who = "ME" if m["sender_id"] == "ME" else (m.get("sender_name") or m["sender_id"])
            text = " ".join((m.get("body") or "").split())
            lines.append(f"{'>>' if m['id'] == l['source_msg'] else '  '} {when} {who}: {text}")
        lines += ["", "help: ", "why: ", ""]
    LABELS.mkdir(parents=True, exist_ok=True)
    SHEET.write_text("\n".join(lines), encoding="utf-8")
    IDS.write_text(json.dumps([{"id": f"H{i}", "source_msg": l["source_msg"]} for i, l in enumerate(picked, 1)],
                              indent=1), encoding="utf-8")
    print(f"wrote {SHEET.relative_to(ROOT)} ({len(picked)} loops from {len(chats_used)} chats; "
          f"{sum(1 for l in picked if cmeta[l['chat_id']].get('is_group'))} in groups); pool was {len(pool)} open loops")
    return 0


def score(out_path: Path = OUT) -> int:
    ids = json.loads(IDS.read_text(encoding="utf-8"))
    text = SHEET.read_text(encoding="utf-8")
    blocks = re.split(r"^## (H\d+) ", text, flags=re.M)
    labels = {}
    for j in range(1, len(blocks), 2):
        m = re.search(r"^help:\s*(assist|remind|leave)\b", blocks[j + 1], re.M | re.I)
        if m:
            labels[blocks[j]] = m.group(1).lower()
    out = {l["source_msg"]: l for l in json.loads(Path(out_path).read_text(encoding="utf-8"))["open_loops"]}
    tot, rows = 0, []
    for x in ids:
        lab = labels.get(x["id"])
        if lab is None:
            rows.append(f"{x['id']}: not labelled")
            continue
        act = (out[x["source_msg"]].get("help") or {}).get("action")
        v = 1 if act == lab else (-1 if lab == "leave" and act in ("assist", "remind") else 0)
        tot += v
        rows.append(f"{x['id']}: label {lab:<7} system {act:<7} -> {v:+d}")
    n = sum(1 for x in ids if x["id"] in labels)
    print("\n".join(rows))
    if n:
        print(f"hold-out help: {max(0, tot) / n * 10:.1f} / 10 on {n} blind labels "
              f"(right {sum('-> +1' in r for r in rows)}, −1 {sum('-> -1' in r for r in rows)})")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(make() if a == ["make"] else score(Path(a[1]) if len(a) > 1 else OUT) if a[:1] == ["score"]
             else print(__doc__) or 2)
