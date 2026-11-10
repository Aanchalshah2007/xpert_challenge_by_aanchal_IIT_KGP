#!/usr/bin/env python3
"""Measure deterministic post-LLM steps on cached answers. Zero calls.

    python3 eval/offline_rules.py [--scope big|all]

Replays the cached replies of each set-up in eval/batch_compare.py (Gemini small batches, Groq gpt-oss-120b/20b),
then toggles config.SHIFT_SOURCES / config.MEDIA_CLOSURE and scores each combination against the key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import batch_compare as bc  # noqa: E402
import check  # noqa: E402
from coldstart import candidates, config, decide, ingest, loops  # noqa: E402
from coldstart.llm_client import parse_json  # noqa: E402

CACHE = Path(".llm_cache")
GROQ = {"max_tokens": 2000, "effort": "low", "budget": 7000}


def groq_replies(data, wins, model: str):
    """Rebuild run_groq's pieces and prompts; return (replies, maps, wins_by_id) from cache."""
    pieces = [p for w in wins for p in bc.split(data, w, GROQ["budget"])]
    groups = loops._batches(data, pieces, GROQ["budget"], 10_000)
    replies, maps = [], []
    for g in groups:
        prompts, bmaps = loops.build_prompts(data, g, char_budget=10**9, per_call=10**6)
        messages = [{"role": "system", "content": bc.SYSTEM_V1}, {"role": "user", "content": prompts[0]}]
        parts = [model, messages, GROQ["max_tokens"], True, {"reasoning_effort": GROQ["effort"]}]
        f = CACHE / f"{hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()}.txt"
        try:
            replies.append(parse_json(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            replies.append(None)
        maps.append(bmaps[0])
    return replies, maps, {p.wid: p for p in pieces}


def score(data, raw: List[dict], key, scope, cands):
    raw = loops.media_closure(loops.shift_sources(raw, data, cands), data)
    ours = [l for l in decide.decide(loops.merge(raw, data), data) if l["source_msg"] in scope]
    key = [a for a in key if any(s in scope for s in a["source_msgs"])]
    used, pairs = set(), []
    for l in ours:
        for j, a in enumerate(key):
            if j not in used and l["source_msg"] in a["source_msgs"]:
                used.add(j)
                pairs.append((l, a))
                break
    n = len(pairs)
    p, r = (n / len(ours) if ours else 0), (n / len(key) if key else 0)
    return {"proposed": len(ours), "matched": n, "key": len(key), "F1": 2 * p * r / (p + r) if p + r else 0.0,
            "status_right": sum(l["status"] == a["status"] for l, a in pairs),
            "wrong_done": sum(a["status"] == "open" and l["status"] != "open" for l, a in pairs),
            "done": sum(l["status"] == "done" for l in ours), "sources": {l["source_msg"] for l in ours}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["big", "all"], default="big")
    args = ap.parse_args()
    data = ingest.load(str(ROOT / "data" / "dev"))
    bc.v1_settings(data)
    wins_by_id, wins, sprompts, smaps = bc.sample(data, "gemini-3.5-flash", CACHE)
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    cands = candidates.find(data)
    scope = bc.big_scope(data, wins, sprompts, CACHE) if args.scope == "big" else {m for w in wins for m in w.msg_ids}
    acc = {s for a in ans["open_loops"] for s in a["source_msgs"]}

    setups = {}
    small = [parse_json((CACHE / f"{bc.cache_key('gemini-3.5-flash', p)}.txt").read_text(encoding="utf-8")) for p in sprompts]
    setups["gemini small"] = (small, smaps, wins_by_id)
    for m in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
        setups["groq " + m.split("/")[1]] = groq_replies(data, wins, m)

    combos = [("baseline", False, False), ("+shift", True, False), ("+media", False, True), ("+both", True, True)]
    print(f"scope: {len(scope)} messages | key loops: "
          f"{sum(any(s in scope for s in a['source_msgs']) for a in ans['open_loops'])}\n")
    print(f"{'set-up':<20}{'rules':<10}{'proposed':>9}{'matched':>8}{'F1':>7}{'status ok':>10}{'wrong done':>11}{'done':>6}")
    for name, (replies, maps, wbi) in setups.items():
        raw = []
        for reply, bmap in zip(replies, maps):
            if reply is not None:
                raw += loops.parse_reply(bc.v1_reply(reply), bmap, data, wbi)
        base_sources = None
        for label, shift, media in combos:
            config.SHIFT_SOURCES, config.MEDIA_CLOSURE = shift, media
            s = score(data, raw, ans["open_loops"], scope, cands)
            print(f"{name:<20}{label:<10}{s['proposed']:>9}{s['matched']:>8}{s['F1']:>7.2f}{s['status_right']:>10}"
                  f"{s['wrong_done']:>11}{s['done']:>6}")
            if base_sources is None:
                base_sources = s["sources"]
            elif shift:
                moved_in, moved_out = s["sources"] - base_sources, base_sources - s["sources"]
                if moved_in or moved_out:
                    print(f"{'':<30}moved: +{sorted(moved_in)} -{sorted(moved_out)} "
                          f"(new ones accepted by key: {sorted(moved_in & acc)}, lost: {sorted(moved_out & acc)})")
        print()
    config.SHIFT_SOURCES = config.MEDIA_CLOSURE = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
