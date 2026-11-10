#!/usr/bin/env python3
"""Requests and tokens used per model in the current provider day, from usage.jsonl.

    python3 eval/quota_ledger.py [--limit MODEL=RPD ...]

Gemini free-tier days reset at 00:00 America/Los_Angeles. Only successful calls are logged by llm.py,
and only records written since llm.py logs "ts" are counted (older ones have no time).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

USAGE = Path("usage.jsonl")


def pacific_midnight(now: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        la = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no tz database: PST (good from early Nov to mid Mar)
        la = now.astimezone(timezone(timedelta(hours=-8)))
    return la.replace(hour=0, minute=0, second=0, microsecond=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", action="append", default=[], help="MODEL=RPD, e.g. gemini-3.5-flash=20")
    args = ap.parse_args()
    limits = dict(x.split("=", 1) for x in args.limit)
    now = datetime.now(timezone.utc)
    start = pacific_midnight(now)
    rows = [json.loads(l) for l in USAGE.read_text().splitlines() if l.strip()] if USAGE.exists() else []
    undated = sum("ts" not in r for r in rows)
    per = defaultdict(lambda: {"requests": 0, "failed": 0, "prompt": 0, "completion": 0, "reasoning": 0})
    for r in rows:
        if "ts" in r and r["ts"] >= start.timestamp():
            p = per[r["model"]]
            p["requests"] += 1  # attempts: failed ones count against free-tier quotas too
            if r.get("error") is not None:
                p["failed"] += 1
                continue
            p["prompt"] += r.get("prompt_tokens") or 0
            p["completion"] += r.get("completion_tokens") or 0
            if r.get("reasoning_tokens") is not None:
                p["reasoning"] += r["reasoning_tokens"]
            elif r.get("total_tokens") is not None:  # Gemini reports thoughts only inside total_tokens
                p["reasoning"] += max(0, r["total_tokens"] - (r.get("prompt_tokens") or 0) - (r.get("completion_tokens") or 0))
    print(f"provider day since {start.isoformat()} (Pacific midnight); now {now.astimezone().isoformat(timespec='minutes')}")
    if not per:
        print("  no logged requests today")
    for model, p in sorted(per.items()):
        left = f" | left {int(limits[model]) - p['requests']} of {limits[model]}" if model in limits else ""
        print(f"  {model}: {p['requests']} requests ({p['failed']} failed), tokens in {p['prompt']} out {p['completion']} "
              f"(reasoning {p['reasoning']}){left}")
    if undated:
        print(f"  ({undated} older records have no timestamp and are not counted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
