#!/usr/bin/env python3
"""Build the fixed loop evaluation slice eval/runs/slice_v1.json. Zero calls.

    python3 eval/make_slice.py [--negatives 30] [--seed 11] [--write]

Units are the current pipeline's candidate windows (the windows v2 sends to the LLM):
- positives: every window that contains a source of a dev-key loop (open and done);
- hard negatives: windows with no key source where a cached v1 answer (Gemini small, Groq 120b/20b) proposed
  a loop, i.e. a v1 false positive;
- negatives: seeded random windows with rule candidates and no key source (never empty windows).
The slice is stored as window message ids, so it stays fixed even if windowing changes later; scoring is on
the union of its messages. Prints counts, key coverage and token estimates (v2 on the slice, the v1 calls a
same-slice v1+shift comparison still needs, and the old pinned sample for reference).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import batch_compare as bc  # noqa: E402
import check  # noqa: E402
import offline_rules as orl  # noqa: E402
from coldstart import candidates, config, decide, ingest, loops  # noqa: E402
from coldstart.llm_client import parse_json  # noqa: E402

OUT = ROOT / "eval" / "runs" / "slice_v1.json"
CHAR_BUDGET = 7000  # Groq set-up of the v1 comparison (8K tokens-per-minute)
TOK_PER_CHAR = 0.333  # measured for gpt-oss (eval/runs/token_ratios.json)
OUT_PER_CALL = 453  # measured completion tokens per Groq gpt-oss-120b call, reasoning included
V1_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")


def estimate(data, wins, system: str) -> Dict[str, int]:
    """Calls and tokens for the Groq set-up: windows split to the char budget, packed per call."""
    pieces = [p for w in wins for p in bc.split(data, w, CHAR_BUDGET)]
    groups = loops._batches(data, pieces, CHAR_BUDGET, 10_000)
    chars = sum(len(loops.build_prompts(data, g, char_budget=10**9, per_call=10**6)[0][0]) + len(system) for g in groups)
    return {"calls": len(groups), "in": round(chars * TOK_PER_CHAR), "out": OUT_PER_CALL * len(groups),
            "total": round(chars * TOK_PER_CHAR) + OUT_PER_CALL * len(groups)}


def v1_proposals() -> Dict[str, Set[str]]:
    """Source msg -> set-ups that proposed it, from cached v1 answers (after shift, merge, decide)."""
    data = ingest.load(str(ROOT / "data" / "dev"))
    bc.v1_settings(data)
    wbi, wins, sp, sm = bc.sample(data, "gemini-3.5-flash", orl.CACHE)
    cands = candidates.find(data)
    setups = {"gemini": ([parse_json((orl.CACHE / f"{bc.cache_key('gemini-3.5-flash', p)}.txt").read_text(encoding="utf-8"))
                          for p in sp], sm, wbi)}
    for m in V1_MODELS:
        setups[m.split("/")[1]] = orl.groq_replies(data, wins, m)
    out: Dict[str, Set[str]] = {}
    for name, (replies, maps, w) in setups.items():
        raw = []
        for r, bmap in zip(replies, maps):
            if r is not None:
                raw += loops.parse_reply(bc.v1_reply(r), bmap, data, w)
        raw = loops.media_closure(loops.shift_sources(raw, data, cands), data)
        for l in decide.decide(loops.merge(raw, data), data):
            out.setdefault(l["source_msg"], set()).add(name)
    bc.v2_settings()  # undo v1_settings for the rest of this run
    return out


def v1_uncached(data_v1, scope: Set[str]) -> Dict[str, int]:
    """v1 Groq calls a same-slice v1+shift comparison needs: v1 pieces touching the slice that are not cached."""
    wins = [w for w in candidates.windows(data_v1, candidates.find(data_v1)) if set(w.candidates) & scope]
    pieces = [p for w in wins for p in bc.split(data_v1, w, CHAR_BUDGET)]
    groups = loops._batches(data_v1, pieces, CHAR_BUDGET, 10_000)
    need, chars = 0, 0
    for g in groups:
        prompt = loops.build_prompts(data_v1, g, char_budget=10**9, per_call=10**6)[0][0]
        messages = [{"role": "system", "content": bc.SYSTEM_V1}, {"role": "user", "content": prompt}]
        parts = ["openai/gpt-oss-120b", messages, orl.GROQ["max_tokens"], True, {"reasoning_effort": orl.GROQ["effort"]}]
        if not (orl.CACHE / f"{hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()}.txt").exists():
            need += 1
            chars += len(prompt) + len(bc.SYSTEM_V1)
    return {"calls": len(groups), "uncached_calls": need,
            "uncached_tokens": round(chars * TOK_PER_CHAR) + OUT_PER_CALL * need}


def main() -> int:
    bc.v2_settings()  # the slice's windows are v2 windows
    ap = argparse.ArgumentParser()
    ap.add_argument("--negatives", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--write", action="store_true", help=f"save to {OUT.relative_to(ROOT)}")
    args = ap.parse_args()

    proposed = v1_proposals()
    data = ingest.load(str(ROOT / "data" / "dev"))
    wins = candidates.windows(data, candidates.find(data))
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    key = ans["open_loops"]
    srcs = {s for a in key for s in a["source_msgs"]}

    pos = [w for w in wins if set(w.msg_ids) & srcs]
    rest = [w for w in wins if not set(w.msg_ids) & srcs and w.candidates]
    hard = [w for w in rest if set(w.msg_ids) & set(proposed)]
    pool = sorted((w for w in rest if w not in hard), key=lambda w: w.wid)
    neg = random.Random(args.seed).sample(pool, min(args.negatives, len(pool)))
    chosen = {"positive": pos, "hard_negative": hard, "negative": neg}

    scope = {m for ws in chosen.values() for w in ws for m in w.msg_ids}
    covered = [a for a in key if any(s in scope for s in a["source_msgs"])]
    print(f"windows: {len(wins)} total | positive {len(pos)} | hard negative {len(hard)} | "
          f"negative {len(neg)} (seed {args.seed}, from {len(pool)})")
    print(f"messages in slice: {len(scope)} | key loops covered: {len(covered)}/{len(key)} "
          f"({dict(Counter(a['status'] for a in covered))})")

    # Hard negatives that several v1 set-ups agreed on may be real loops the noisy key missed.
    flags = sorted((m, sorted(by)) for m, by in proposed.items() if len(by) >= 2 and m not in srcs and m in scope)
    print(f"possible key misses (proposed by >=2 v1 set-ups, not in key): {len(flags)}")
    for m, by in flags:
        msg = data.by_id[m]
        print(f"  {m} {msg['chat_id']} {msg['sender_id']}: {' '.join(msg['text'].split())[:90]!r} <- {', '.join(by)}")

    v2 = estimate(data, [w for ws in chosen.values() for w in ws], bc.SYSTEM_V2)
    data_v1 = ingest.load(str(ROOT / "data" / "dev"))
    bc.v1_settings(data_v1)
    v1 = v1_uncached(data_v1, scope)
    bc.v2_settings()
    old = json.loads((ROOT / "eval" / "runs" / "sample_v1.json").read_text())
    old_scope = set(old["all47"])
    old_wins = [w for w in wins if set(w.candidates) & old_scope]
    old_est = estimate(data, old_wins, bc.SYSTEM_V2)
    print(f"\nestimate, Groq gpt-oss-120b, {CHAR_BUDGET}-char calls ({TOK_PER_CHAR} tok/char, {OUT_PER_CALL} out/call):")
    print(f"  v2 on slice:            {v2['calls']} calls, ~{v2['in']:,} in + ~{v2['out']:,} out = ~{v2['total']:,} tokens")
    print(f"  v1 for same-slice comparison: {v1['calls']} calls, {v1['uncached_calls']} not cached, "
          f"~{v1['uncached_tokens']:,} tokens to fill")
    print(f"  v2 on old pinned sample: {old_est['calls']} calls, ~{old_est['total']:,} tokens "
          f"({len(old_wins)} windows, for reference)")

    if args.write:
        OUT.write_text(json.dumps({
            "seed": args.seed, "negatives_requested": args.negatives,
            "windows": {k: [{"wid": w.wid, "chat_id": w.chat_id, "msg_ids": w.msg_ids, "candidates": w.candidates}
                            for w in ws] for k, ws in chosen.items()},
            "scope": sorted(scope), "key_loops_covered": len(covered),
            "possible_key_misses": [m for m, _ in flags], "estimate_v2": v2, "estimate_v1_fill": v1,
        }, indent=1))
        print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
