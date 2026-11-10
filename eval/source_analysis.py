#!/usr/bin/env python3
"""Zero-call analysis of loop misses on cached LLM answers (default small batches).

    LLM_MODEL=gemini-3.5-flash python3 eval/source_analysis.py [--scope big|all] [--show-between 8]

scope big = the windows covered by the cached big-batch calls (the compared sample), all = every cached window.
For each key loop in scope: exact / earlier / later / none, and for "none" a bucket:
  missed trigger  - key source is not a rule candidate (not marked in the prompt)
  dropped         - the LLM returned it, but confidence filter or merge removed it
  elsewhere       - the LLM returned a loop in that window, but more than NEAR messages away
  LLM rejected    - marked candidate in a window the LLM saw; it returned nothing near it
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import batch_compare as bc  # noqa: E402
import check  # noqa: E402
from coldstart import candidates, config, decide, ingest, loops  # noqa: E402
from coldstart.llm_client import parse_json  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
NEAR = bc.NEAR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--scope", choices=["big", "all"], default="big")
    ap.add_argument("--show-between", type=int, default=8)
    args = ap.parse_args()
    model = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
    cache = Path(os.environ.get("COLDSTART_LLM_CACHE") or ".llm_cache")
    data = ingest.load(args.data)
    bc.v1_settings(data)
    wins_by_id, wins, sprompts, smaps = bc.sample(data, model, cache)
    cands = candidates.find(data)
    _, _, _, ans = check.load_input(args.data)
    pos = {m["id"]: (cid, i) for cid, ms in data.by_chat.items() for i, m in enumerate(ms)}

    if args.scope == "big":
        budget = int(sum(len(p) for p in sprompts) / 3) + 2000
        bprompts, bmaps = loops.build_prompts(data, wins, char_budget=budget, per_call=10_000)
        ok = {wid for p, bm in zip(bprompts, bmaps) if (cache / f"{bc.cache_key(model, p)}.txt").exists()
              for wid, _ in bm.values()}
        scoped = [w for w in wins if w.wid in ok]
    else:
        scoped = wins
    scope = {m for w in scoped for m in w.msg_ids}
    marked = {m for w in scoped for m in w.reasons}

    raw = []
    for p, bmap in zip(sprompts, smaps):
        reply = parse_json((cache / f"{bc.cache_key(model, p)}.txt").read_text(encoding="utf-8"))
        raw += loops.parse_reply(bc.v1_reply(reply), bmap, data, wins_by_id)
    final = [l for l in decide.decide(loops.merge(raw, data), data) if l["source_msg"] in scope]
    key = [a for a in ans["open_loops"] if any(s in scope for s in a["source_msgs"])]
    print(f"scope: {len(scoped)} windows, {len(scope)} messages | key loops {len(key)} | raw LLM loops "
          f"{sum(l['source_msg'] in scope for l in raw)} | final {len(final)} (conf >= {config.MIN_CONFIDENCE})\n")

    def show(mid: str) -> str:
        m = data.by_id[mid]
        who = "ME" if m["sender_id"] == "ME" else "THEM"
        when = datetime.fromtimestamp(m["ts"], IST).strftime("%d %b %H:%M")
        tags = ",".join(cands.get(mid, [])) or "-"
        return f"{mid} [{when}] {who} ({tags}): {m['text'][:150]!r}"

    buckets: dict = {}
    for a in key:
        src = min(a["source_msgs"], key=lambda x: data.by_id[x]["ts"])
        cs, i = pos[src]
        mine = [l for l in final if l["source_msg"] in a["source_msgs"]]
        near = sorted((pos[l["source_msg"]][1] - i, l) for l in final
                      if pos[l["source_msg"]][0] == cs and abs(pos[l["source_msg"]][1] - i) <= NEAR)
        if mine:
            kind = "exact"
        elif near:
            kind = "earlier" if min(near, key=lambda x: abs(x[0]))[0] < 0 else "later"
        elif src not in marked:
            kind = "none: missed trigger"
        elif any(l["source_msg"] == src or (pos[l["source_msg"]][0] == cs and abs(pos[l["source_msg"]][1] - i) <= NEAR)
                 for l in raw):
            kind = "none: dropped (conf/merge)"
        elif any(pos[l["source_msg"]][0] == cs for l in raw if l["source_msg"] in scope):
            kind = "none: elsewhere in chat"
        else:
            kind = "none: LLM rejected"
        buckets.setdefault(kind, []).append(a)
        print(f"== [{kind}] key ({a['status']}, {a['type']}, owner {a['owner'] if a['owner'] == 'ME' else 'THEM'}): {a['title']}")
        print(f"   key src : {show(src)}")
        if kind in ("earlier", "later"):
            d, l = min(near, key=lambda x: abs(x[0]))
            print(f"   ours    : {show(l['source_msg'])}  (offset {d:+d} msgs) | our title: {l['title']}")
            lo, hi = sorted((pos[l["source_msg"]][1], i))
            between = data.by_chat[cs][lo + 1: hi]
            for m in between[: args.show_between]:
                print(f"      ...  {show(m['id'])}")
            if len(between) > args.show_between:
                print(f"      ...  (+{len(between) - args.show_between} more)")
        elif kind.startswith("none"):
            rl = [l for l in raw if pos[l["source_msg"]][0] == cs and abs(pos[l["source_msg"]][1] - i) <= 3 * NEAR]
            for l in rl[:3]:
                print(f"   raw LLM : {show(l['source_msg'])} conf {l['conf']:.2f} | {l['title']}")
        print()
    print("summary:", {k: len(v) for k, v in sorted(buckets.items())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
