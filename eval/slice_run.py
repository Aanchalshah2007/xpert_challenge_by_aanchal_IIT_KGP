#!/usr/bin/env python3
"""Run one loop-prompt version on the fixed slice (eval/runs/slice_v1.json) and score it there.

    set -a; source <(tr -d '\\r' < .env.groq); set +a
    COLDSTART_LLM_CACHE=.llm_cache python3 eval/slice_run.py --prompt v2 [--dry-run] [--stop-tokens 120000]
    COLDSTART_LLM_CACHE=.llm_cache python3 eval/slice_run.py --prompt v1 [--dry-run]

v2 = archived v2 prompt (eval/prompt_archive) + v2 windows (the slice's stored windows) + compact lossless format.
v1 = archived v1 prompt + v1 windows/format. Pieces already answered in the earlier Groq v1 calls are reused per
window (those answers came from calls with other neighbouring windows); only the rest is asked.
Both: Groq set-up of the v1 comparison (7,000-char pieces, max_tokens 2000, reasoning effort low), then
source shift + media closure + merge + decide, scored on the slice's messages (key loops with a source there).
--dry-run makes no requests: it prints planned calls, how many are cached, and the token estimate.
A run stops early if most replies come back near-empty (a sign of a broken prompt or model), or at --stop-tokens.
Results go to eval/runs/slice_result_<prompt>.json (never slice_v1.json, the slice itself). Re-running resumes from the cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import batch_compare as bc  # noqa: E402
import check  # noqa: E402
import offline_rules  # noqa: E402
import offline_rules as orl  # noqa: E402
from coldstart import candidates, config, ingest, llm_client, loops  # noqa: E402
from coldstart.candidates import Window  # noqa: E402
from coldstart.llm_client import LLM, parse_json  # noqa: E402

SLICE = ROOT / "eval" / "runs" / "slice_v1.json"
BUDGET, MAX_TOKENS = 7000, 2000
TOK_PER_CHAR, OUT_PER_CALL = 0.333, 453
EMPTY_TOKENS = 30  # a reply this short is "no loops" with next to no reasoning
EMPTY_CHECK_AFTER, EMPTY_SHARE = 6, 0.75


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", choices=["v1", "v2"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop-tokens", type=int, default=120_000)
    ap.add_argument("--cache-only", action="store_true", help="no requests: uncached calls count as failed")
    ap.add_argument("--dump-raw", help="write the parsed raw loops (before shift/merge/decide) to this JSON file")
    args = ap.parse_args()
    bc.v2_settings()
    sl = json.loads(SLICE.read_text())
    scope = set(sl["scope"])
    data = ingest.load(str(ROOT / "data" / "dev"))
    cands = candidates.find(data)
    if args.prompt == "v1":
        bc.v1_settings(data)
        system = bc.SYSTEM_V1
        wins = [w for w in candidates.windows(data, candidates.find(data)) if set(w.candidates) & scope]
    else:
        system = bc.SYSTEM_V2
        wins = [Window(wid=w["wid"], chat_id=w["chat_id"], msg_ids=w["msg_ids"], candidates=w["candidates"],
                       reasons={c: cands[c] for c in w["candidates"] if c in cands})
                for ws in sl["windows"].values() for w in ws]
    pieces = [p for w in wins for p in bc.split(data, w, BUDGET)]
    wins_by_id = {p.wid: p for p in pieces}
    reused: List[Any] = []  # (cached v1 reply, map limited to the slice pieces it already answered)
    if args.prompt == "v1":
        # Reuse cached v1 answers window by window: a piece answered in an earlier v1 call (same piece, same
        # messages) is not asked again, even though that call carried different neighbouring windows.
        old_sample = bc.sample(data, "gemini-3.5-flash", orl.CACHE)[1]  # the windows of the earlier v1 runs
        old_r, old_m, old_w = orl.groq_replies(data, old_sample, "openai/gpt-oss-120b")
        done = set()
        for reply, bmap in zip(old_r, old_m):
            if reply is None:
                continue
            sub = {wl: (wid, loc) for wl, (wid, loc) in bmap.items()
                   if wid in wins_by_id and old_w[wid].msg_ids == wins_by_id[wid].msg_ids}
            if sub:
                reused.append((bc.v1_reply(reply), sub))
                done.update(wid for wid, _ in sub.values())
        pieces = [p for p in pieces if p.wid not in done]
        print(f"v1: {len(done)} pieces answered in cached v1 calls, reused per window", flush=True)
    groups = loops._batches(data, pieces, BUDGET, 10_000)
    model = os.environ.get("LLM_MODEL", "")
    extra = {"reasoning_effort": "low"} if args.prompt == "v1" else llm_client.provider_extra()
    cache = Path(os.environ.get("COLDSTART_LLM_CACHE") or ".llm_cache")

    def key(prompt: str) -> Path:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        parts = [model, messages, MAX_TOKENS, True] + ([extra] if extra else [])
        return cache / f"{hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()}.txt"

    built = [loops.build_prompts(data, g, char_budget=10**9, per_call=10**6) for g in groups]
    todo = [i for i, (p, _) in enumerate(built) if not key(p[0]).exists()]
    est_in = round(sum(len(built[i][0][0]) + len(system) for i in todo) * TOK_PER_CHAR)
    print(f"{args.prompt} | model {model} | extra {extra} | {len(wins)} windows -> {len(pieces)} pieces -> "
          f"{len(groups)} calls, {len(groups) - len(todo)} cached, {len(todo)} to make: ~{est_in:,} in + "
          f"~{OUT_PER_CALL * len(todo):,} out tokens", flush=True)
    if args.dry_run:
        return 0
    if args.cache_only:
        os.environ.pop("LLM_API_KEY", None)  # belt and braces: nothing can be sent

    client = LLM(concurrency=1, timeout=300, cache_dir=str(cache), extra=extra)
    offset = bc.USAGE.stat().st_size if bc.USAGE.exists() else 0
    replies: List[Any] = []
    maps: List[Any] = []
    stopped, made = "", []

    def spent() -> int:
        return sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in bc.usage_rows(offset))

    t0 = time.time()
    with client.stage(f"slice-{args.prompt}"):
        for i, (prompts, bmaps) in enumerate(built):
            if spent() >= args.stop_tokens:
                stopped = f"stopped at the {args.stop_tokens:,}-token line after {i} of {len(groups)} calls"
                break
            if args.cache_only and i in todo:
                replies.append(None)
                maps.append(bmaps[0])
                continue
            before = bc.USAGE.stat().st_size if bc.USAGE.exists() else 0
            messages = [{"role": "system", "content": system}, {"role": "user", "content": prompts[0]}]
            try:
                text = client.complete(messages, max_tokens=MAX_TOKENS)
                reply = bc.v1_reply(parse_json(text))
            except (RuntimeError, ValueError) as e:
                print(f"  call {i + 1} failed: {str(e)[:160]}", flush=True)
                reply = None
            rows = [r for r in bc.usage_rows(before) if r.get("error") is None]
            if rows:
                made.append(rows[-1].get("completion_tokens") or 0)
                if rows[-1].get("finish_reason") == "length":
                    print(f"  call {i + 1} truncated", flush=True)
            replies.append(reply)
            maps.append(bmaps[0])
            if client.exhausted:
                stopped = f"stopped after {i + 1} of {len(groups)} calls: {client.exhausted}"
                break
            if len(made) >= EMPTY_CHECK_AFTER and sum(t <= EMPTY_TOKENS for t in made) >= EMPTY_SHARE * len(made):
                stopped = f"stopped: {sum(t <= EMPTY_TOKENS for t in made)} of {len(made)} replies near-empty"
                break
            if (i + 1) % 5 == 0:
                print(f"  {i + 1}/{len(groups)} calls, {spent():,} tokens this run", flush=True)
    wall = round(time.time() - t0, 1)

    raw: List[dict] = []
    for reply, bmap in reused + list(zip(replies, maps)):
        if reply is not None:
            raw += loops.parse_reply(reply, bmap, data, wins_by_id)
    if args.dump_raw:
        Path(args.dump_raw).write_text(json.dumps({"raw": raw, "scope": sorted(scope)}, indent=0))
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    s = offline_rules.score(data, raw, ans["open_loops"], scope, candidates.find(data))
    s.pop("sources")
    usage = bc.tokens(bc.usage_rows(offset))
    out: Dict[str, Any] = {"prompt": args.prompt, "model": model, "extra": extra, "calls_planned": len(groups),
                           "reused_cached_calls": len(reused),
                           "answered": len(replies), "failed_prompts": sum(r is None for r in replies),
                           "near_empty_replies": sum(t <= EMPTY_TOKENS for t in made), "wall_s": wall,
                           "stopped": stopped, "usage": usage, "score": s}
    if not args.cache_only:
        (ROOT / "eval" / "runs" / f"slice_result_{args.prompt}.json").write_text(json.dumps(out, indent=1))
    print(f"\nslice: {len(scope)} messages, {s['key']} key loops | proposed {s['proposed']} matched {s['matched']} "
          f"F1 {s['F1']:.3f} | status right {s['status_right']} wrong done {s['wrong_done']}")
    print(f"answered {len(replies)}/{len(groups)} | failed prompts {out['failed_prompts']} | near-empty "
          f"{out['near_empty_replies']} | tokens in {usage['in']:,} out {usage['out']:,} (reasoning {usage['reasoning']}) | "
          f"failed attempts {usage['failed_attempts']} | wall {wall}s" + (f"\n{stopped}" if stopped else ""))
    return 0 if not stopped else 3


if __name__ == "__main__":
    sys.exit(main())
