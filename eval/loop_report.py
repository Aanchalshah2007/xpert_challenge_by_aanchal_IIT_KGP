#!/usr/bin/env python3
"""Loop diagnostics against the key: precision/recall/F1, status confusion, error buckets with examples.

    python3 eval/loop_report.py data/dev eval/runs/out.json [--examples 3]

Buckets
  misses:  missed trigger   - key source is not a rule candidate (the LLM never had it marked)
           wrong source     - we have a loop in that chat whose source is near the key source, but not it
           not extracted    - candidate in a window, but the LLM produced no loop for it
  false positives:
           duplicate        - matches a key loop already matched by another of our loops (same task twice)
           wrong source     - near (±10 msgs, same chat) a key source we did not match
           not a real loop  - nothing in the key near it
  status:  wrong status     - matched, status differs (and whether it cost a point)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import candidates, ingest  # noqa: E402

NEAR = 10  # messages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("out")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args()
    data = ingest.load(args.data)
    _, messages, _, ans = check.load_input(args.data)
    out = json.loads(Path(args.out).read_text(encoding="utf-8"))
    cands = candidates.find(data)
    pos = {m["id"]: (m["chat_id"], i) for cid, ms in data.by_chat.items() for i, m in enumerate(ms)}

    since = ans.get("loops_scored_from")
    key = [a for a in ans["open_loops"] if since is None or min(data.by_id[s]["ts"] for s in a["source_msgs"]) >= since]
    ours = [l for l in out["open_loops"] if since is None or data.by_id[l["source_msg"]]["ts"] >= since]

    # Same greedy matching as check.py.
    pairs, used, matched_ours = [], set(), set()
    for li, l in enumerate(ours):
        for j, a in enumerate(key):
            if j not in used and l["source_msg"] in a["source_msgs"]:
                used.add(j)
                matched_ours.add(li)
                pairs.append((l, a))
                break
    p = len(pairs) / len(ours) if ours else 0.0
    r = len(pairs) / len(key) if key else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    print(f"loops: ours {len(ours)} | key {len(key)} | matched {len(pairs)} | P {p:.2f} R {r:.2f} F1 {f:.2f}")

    conf = Counter((a["status"], l["status"]) for l, a in pairs)
    print("\nstatus confusion on matched (key -> ours):")
    for (k, o), n in sorted(conf.items()):
        cost = " (costs 1)" if k == "open" and o != "open" else ""
        print(f"  {k:>7} -> {o:<7} {n}{cost}")
    print("our status mix:", dict(Counter(l["status"] for l in ours)),
          "| help mix:", dict(Counter((l.get("help") or {}).get("action") for l in ours if l["status"] == "open")))

    def near(a_id: str, b_id: str) -> bool:
        (ca, ia), (cb, ib) = pos[a_id], pos[b_id]
        return ca == cb and abs(ia - ib) <= NEAR

    def show(mid: str) -> str:
        m = data.by_id[mid]
        who = "ME" if m["sender_id"] == "ME" else m["sender_id"]
        return f"{mid} {m['chat_id']} {who}: {m['text'][:90]!r}"

    buckets: dict = {k: [] for k in ("missed trigger", "wrong source (miss)", "not extracted",
                                     "duplicate", "wrong source (fp)", "not a real loop", "wrong status")}
    matched_key = {id(a) for _, a in pairs}
    for a in key:
        if id(a) in matched_key:
            continue
        src = min(a["source_msgs"], key=lambda s: data.by_id[s]["ts"])
        nearby = [l for l in ours if near(l["source_msg"], src)]
        if not any(s in cands for s in a["source_msgs"]):
            buckets["missed trigger"].append(f"{show(src)} | key: {a['title']}")
        elif nearby:
            buckets["wrong source (miss)"].append(f"{show(src)} | ours chose {nearby[0]['source_msg']} | key: {a['title']}")
        else:
            buckets["not extracted"].append(f"{show(src)} | key: {a['title']}")
    for li, l in enumerate(ours):
        if li in matched_ours:
            continue
        hit_any = [a for a in key if l["source_msg"] in a["source_msgs"]]
        if hit_any:
            buckets["duplicate"].append(f"{show(l['source_msg'])} | ours: {l['title']}")
        elif any(near(l["source_msg"], s) for a in key for s in a["source_msgs"]):
            buckets["wrong source (fp)"].append(f"{show(l['source_msg'])} | ours: {l['title']}")
        else:
            buckets["not a real loop"].append(f"{show(l['source_msg'])} | ours: {l['title']}")
    for l, a in pairs:
        if l["status"] != a["status"]:
            cost = "costs 1" if a["status"] == "open" else "costs 0"
            buckets["wrong status"].append(f"{show(l['source_msg'])} | key {a['status']} ours {l['status']} ({cost}) | {a['title']}")

    print("\nerror buckets:")
    for name, items in buckets.items():
        print(f"  {name}: {len(items)}")
        for x in items[: args.examples]:
            print(f"      - {x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
