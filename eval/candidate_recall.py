#!/usr/bin/env python3
"""Candidate recall against the key's loop sources: the ceiling for loop finding. No LLM.

    python3 eval/candidate_recall.py [data_dir] [--misses]

strict  = the key's source message is itself a candidate
window  = the source is inside some LLM window (the LLM could still pick it)
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import candidates, ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--misses", action="store_true")
    args = ap.parse_args()
    data = ingest.load(args.data)
    _, _, _, answers = check.load_input(args.data)
    cands = candidates.find(data)
    wins = candidates.windows(data, cands)
    in_window = {m for w in wins for m in w.msg_ids}

    loops = [a for a in answers["open_loops"] if min(data.by_id[s]["ts"] for s in a["source_msgs"]) >= data.loop_from]
    strict = sum(any(s in cands for s in a["source_msgs"]) for a in loops)
    window = sum(any(s in in_window for s in a["source_msgs"]) for a in loops)
    uniq_src = {a["source_msgs"][0] for a in loops}
    n_win_msgs = sum(len(w.msg_ids) for w in wins)
    in_scope = sum(1 for m in data.messages if m["ts"] >= data.loop_from)

    print(f"messages in loop window: {in_scope} | candidates: {len(cands)} ({len(cands) / max(1, in_scope):.0%})")
    print(f"windows: {len(wins)} | messages sent to LLM (with overlap): {n_win_msgs} | "
          f"LLM calls at {candidates.config.WINDOWS_PER_CALL}/call: {-(-len(wins) // candidates.config.WINDOWS_PER_CALL)}")
    print(f"key loops: {len(loops)} ({len(uniq_src)} distinct sources)")
    print(f"recall strict: {strict}/{len(loops)} = {strict / len(loops):.0%} | "
          f"distinct sources: {sum(s in cands for s in uniq_src)}/{len(uniq_src)}")
    print(f"recall window: {window}/{len(loops)} = {window / len(loops):.0%}")
    print("reasons on hit sources:", Counter(r for s in uniq_src if s in cands for r in cands[s]).most_common())
    if args.misses:
        print("\nmissed sources (strict):")
        for s in sorted(uniq_src - set(cands)):
            m = data.by_id[s]
            print(f"  {s} {m['chat_id']:5} {'ME' if m['sender_id'] == 'ME' else 'OT'} "
                  f"{'in-window' if s in in_window else 'OUTSIDE  '} {m['text'][:90]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
