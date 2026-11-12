#!/usr/bin/env python3
"""Run the CURRENT loop prompt and window settings on the pinned v1 sample, and score like offline_rules.

    set -a; source <(tr -d '\\r' < .env.openrouter); set +a
    COLDSTART_LLM_CACHE=.llm_cache python3 eval/sample_run.py --tag v2 [--char-budget 7000] [--stop-tokens 150000]

Windows: built with today's settings; kept if they hold a candidate inside eval/runs/sample_v1.json.
Scored on the same message scopes as the v1 comparison (41-window "big41" and 47-window "all47").
Results go to eval/runs/sample_<tag>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import batch_compare as bc  # noqa: E402
import check  # noqa: E402
import offline_rules  # noqa: E402
from coldstart import candidates, ingest, llm_client, loops  # noqa: E402
from coldstart.candidates import Window  # noqa: E402
from coldstart.llm_client import LLM, parse_json  # noqa: E402

# v1 + source shift + media closure, gpt-oss-120b (Groq), replayed with eval/offline_rules.py on cached answers.
V1_SHIFT_120B = {"big41": {"proposed": 52, "matched": 6, "key": 19, "F1": 0.171, "status_right": 4, "wrong_done": 0},
                 "all47": {"proposed": 55, "matched": 7, "key": 24, "F1": 0.177, "status_right": 5, "wrong_done": 0}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--char-budget", type=int, default=7000)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--stop-tokens", type=int, default=150_000)
    ap.add_argument("--concurrency", type=int, default=1)
    args = ap.parse_args()
    data = ingest.load(str(ROOT / "data" / "dev"))
    sample = json.loads((ROOT / "eval" / "runs" / "sample_v1.json").read_text())
    scopes = {"big41": set(sample["big41"]), "all47": set(sample["all47"])}
    cands = candidates.find(data)
    wins = [w for w in candidates.windows(data, cands) if set(w.candidates) & scopes["all47"]]
    pieces = [p for w in wins for p in bc.split(data, w, args.char_budget)]
    groups = loops._batches(data, pieces, args.char_budget, 10_000)
    wins_by_id = {p.wid: p for p in pieces}
    client = LLM(concurrency=args.concurrency, timeout=300, cache_dir=os.environ.get("COLDSTART_LLM_CACHE") or ".llm_cache",
                 extra=llm_client.provider_extra())
    model = os.environ.get("LLM_MODEL", "")
    print(f"model {model} | extra {client.extra} | {len(wins)} windows -> {len(pieces)} pieces -> {len(groups)} calls",
          flush=True)

    offset = bc.USAGE.stat().st_size if bc.USAGE.exists() else 0
    replies: List[Any] = []
    maps: List[Any] = []
    truncations = 0
    stopped = ""

    def spent() -> int:
        return sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in bc.usage_rows(offset))

    def one(group: List[Window], allow_split: bool) -> None:
        nonlocal truncations
        prompts, bmaps = loops.build_prompts(data, group, char_budget=10**9, per_call=10**6)
        before = bc.USAGE.stat().st_size if bc.USAGE.exists() else 0
        messages = [{"role": "system", "content": loops.SYSTEM}, {"role": "user", "content": prompts[0]}]
        try:
            text = client.complete(messages, max_tokens=args.max_tokens)
        except RuntimeError as e:
            print(f"  call failed: {str(e)[:160]}", flush=True)
            replies.append(None)
            maps.append(bmaps[0])
            return
        rows = bc.usage_rows(before)
        cut = bool(rows) and rows[-1].get("finish_reason") == "length"
        try:
            reply = parse_json(text)
        except ValueError:
            reply, cut = None, True
        if cut:
            truncations += 1
            halves = ([group[: len(group) // 2], group[len(group) // 2:]] if len(group) > 1
                      else [[h] for h in bc.halve(group[0])])
            if allow_split and len(halves) > 1:
                for h in halves:
                    wins_by_id.update({w.wid: w for w in h})
                    one(h, allow_split=False)
                return
        replies.append(reply)
        maps.append(bmaps[0])

    t0 = time.time()
    with client.stage(f"sample-{args.tag}"):
        for i, g in enumerate(groups, 1):
            if spent() >= args.stop_tokens:
                stopped = f"stopped at the {args.stop_tokens}-token line after {i - 1} of {len(groups)} calls"
                break
            one(g, allow_split=True)
            if client.exhausted:
                stopped = f"stopped after {i} of {len(groups)} calls: {client.exhausted}"
                break
            if i % 5 == 0:
                print(f"  {i}/{len(groups)} calls, {spent():,} tokens", flush=True)
    wall = round(time.time() - t0, 1)

    raw = []
    for reply, bmap in zip(replies, maps):
        if reply is not None:
            raw += loops.parse_reply(reply, bmap, data, wins_by_id)
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    usage = bc.tokens(bc.usage_rows(offset))
    out = {"tag": args.tag, "model": model, "extra": client.extra, "calls_planned": len(groups), "wall_s": wall,
           "truncations": truncations, "failed_prompts": sum(r is None for r in replies), "stopped": stopped,
           "usage": usage, "scores": {}}
    print(f"\n{'scope':<7}{'':<12}{'proposed':>9}{'matched':>8}{'key':>5}{'F1':>7}{'status ok':>10}{'wrong done':>11}")
    for name, scope in scopes.items():
        s = offline_rules.score(data, raw, ans["open_loops"], scope, cands)
        s.pop("sources")
        out["scores"][name] = s
        ref = V1_SHIFT_120B[name]
        for label, r in (("v1+shift", ref), (args.tag, s)):
            print(f"{name:<7}{label:<12}{r['proposed']:>9}{r['matched']:>8}{r['key']:>5}{r['F1']:>7.2f}"
                  f"{r['status_right']:>10}{r['wrong_done']:>11}")
    print(f"\ncalls ok {usage['logged_calls']} | failed attempts {usage['failed_attempts']} | failed prompts "
          f"{out['failed_prompts']} | truncations {truncations} | tokens in {usage['in']:,} out {usage['out']:,} "
          f"(reasoning {usage['reasoning']}) | wall {wall}s" + (f"\n{stopped}" if stopped else ""))
    (ROOT / "eval" / "runs" / f"sample_{args.tag}.json").write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
