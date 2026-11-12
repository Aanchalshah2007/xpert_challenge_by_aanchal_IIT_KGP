#!/usr/bin/env python3
"""Paid-run spend ledger (eval/runs/spend.jsonl): summary, reconciliation with OpenRouter, key usage.

    python3 eval/spend.py summary [--run RUN]     # zero calls: spend per run / stage / provider from the ledger
    python3 eval/spend.py key                     # GET /key: the key's own usage and limit (USD); the key is never printed
    python3 eval/spend.py reconcile --run RUN     # GET /generation per call: provider, total_cost, native reasoning tokens

Needs LLM_BASE_URL / LLM_API_KEY for `key` and `reconcile` (source .env.openrouter first). Spend figures here are
OpenRouter's own cost data; nothing is computed as tokens x price.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from coldstart.spend import DEFAULT_CAP_USD, HARD_CAP_USD, read  # noqa: E402

LEDGER = ROOT / "eval" / "runs" / "spend.jsonl"
GENERATIONS = ROOT / "eval" / "runs" / "spend_generations.jsonl"


def _get(path: str) -> dict:
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"],
                                                       "User-Agent": "coldstart-spend/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def key_usage() -> dict:
    d = _get("/key").get("data") or {}
    return {k: d.get(k) for k in ("usage", "usage_daily", "limit", "limit_remaining")}


def summarise(rows: list) -> dict:
    out = {"calls": 0, "failed": 0, "cost": 0.0, "cost_missing": 0, "prompt": 0, "completion": 0, "reasoning": 0,
           "cached": 0, "by_stage": defaultdict(float), "by_provider": defaultdict(int)}
    for r in rows:
        if r.get("error"):
            out["failed"] += 1
            continue
        out["calls"] += 1
        if r.get("cost") is None:
            out["cost_missing"] += 1
        out["cost"] += r.get("cost") or 0.0
        out["prompt"] += r.get("prompt_tokens") or 0
        out["completion"] += r.get("completion_tokens") or 0
        out["reasoning"] += r.get("reasoning_tokens") or 0
        out["cached"] += r.get("cached_tokens") or 0
        out["by_stage"][r.get("stage")] += r.get("cost") or 0.0
        out["by_provider"][r.get("provider") or "?"] += 1
    return out


def cmd_summary(run: str = None) -> None:
    rows = read(LEDGER)
    runs = sorted({r.get("run") for r in rows}, key=lambda x: min(r["ts"] for r in rows if r.get("run") == x))
    total = sum(r.get("cost") or 0.0 for r in rows)
    print(f"ledger total ${total:.4f}  (experiment cap ${DEFAULT_CAP_USD:.2f}, hard cap ${HARD_CAP_USD:.2f})")
    for name in runs:
        if run and name != run:
            continue
        s = summarise([r for r in rows if r.get("run") == name])
        print(f"  {name}: {s['calls']} calls, {s['failed']} failed, ${s['cost']:.5f}"
              f"{' (' + str(s['cost_missing']) + ' without cost)' if s['cost_missing'] else ''}; tokens in "
              f"{s['prompt']:,} (cached {s['cached']:,}), out {s['completion']:,} (reasoning {s['reasoning']:,})")
        print(f"     by stage: {dict((k, round(v, 5)) for k, v in s['by_stage'].items())}")
        print(f"     by provider (calls): {dict(s['by_provider'])}")


def cmd_reconcile(run: str) -> None:
    rows = [r for r in read(LEDGER) if r.get("run") == run and r.get("id") and not r.get("error")]
    done = {g.get("id") for g in read(GENERATIONS)}
    n = 0
    with open(GENERATIONS, "a", encoding="utf-8") as f:
        for r in rows:
            if r["id"] in done:
                continue
            try:
                g = _get(f"/generation?id={r['id']}").get("data") or {}
            except Exception as e:  # not yet available or transient
                print(f"  {r['id']}: {type(e).__name__} {getattr(e, 'code', '')}")
                time.sleep(1)
                continue
            keep = {k: g.get(k) for k in ("id", "provider_name", "total_cost", "tokens_prompt", "tokens_completion",
                                          "native_tokens_prompt", "native_tokens_completion", "native_tokens_reasoning",
                                          "native_tokens_cached", "finish_reason", "latency", "generation_time")}
            keep["run"] = run
            f.write(json.dumps(keep) + "\n")
            n += 1
    gens = [g for g in read(GENERATIONS) if g.get("run") == run]
    ledger_cost = sum(r.get("cost") or 0.0 for r in rows)
    gen_cost = sum(g.get("total_cost") or 0.0 for g in gens)
    provs = defaultdict(int)
    for g in gens:
        provs[g.get("provider_name")] += 1
    print(f"reconciled {n} new; {len(gens)}/{len(rows)} calls have generation data")
    print(f"  ledger usage.cost ${ledger_cost:.5f} vs generation total_cost ${gen_cost:.5f}")
    print(f"  native reasoning tokens {sum(g.get('native_tokens_reasoning') or 0 for g in gens):,}")
    print(f"  providers: {dict(provs)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "key", "reconcile"])
    ap.add_argument("--run")
    a = ap.parse_args()
    if a.cmd == "summary":
        cmd_summary(a.run)
    elif a.cmd == "key":
        print(json.dumps(key_usage()))
    else:
        if not a.run:
            ap.error("--run is required")
        cmd_reconcile(a.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
