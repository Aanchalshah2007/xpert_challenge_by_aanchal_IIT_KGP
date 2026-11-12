#!/usr/bin/env python3
"""Requests and tokens used per model in the current provider day, from usage.jsonl.

    python3 eval/quota_ledger.py [--limit MODEL=RPD ...]

Gemini free-tier days reset at 00:00 America/Los_Angeles. Failed attempts are logged and counted too.
Only records written since llm.py logs "ts" are counted (older ones have no time).

Groq (models whose calls logged x-ratelimit headers): the daily limits refill continuously over 24 h
(after 1 request x-ratelimit-reset-requests is 86.4 s = 24 h / 1,000 RPD; after 10, 14m24s), not at a fixed hour.
Shown per model: requests/tokens in the last 24 h (conservative: what a fixed 24 h window would count),
a refill estimate (each unit returns at limit/24h), and the latest headers.

    python3 eval/quota_ledger.py --tpd openai/gpt-oss-120b=200000 --tpd openai/gpt-oss-20b=200000
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
    ap.add_argument("--tpd", action="append", default=[], help="MODEL=tokens per day, for the refill estimate")
    args = ap.parse_args()
    limits = dict(x.split("=", 1) for x in args.limit)
    tpd = {k: int(v) for k, v in (x.split("=", 1) for x in args.tpd)}
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
    rolling(rows, now.timestamp(), tpd)
    if undated:
        print(f"  ({undated} older records have no timestamp and are not counted)")
    return 0


def _refill_left(events, now: float, cap: int) -> float:
    """Bucket of `cap` refilling at cap/24h; events = [(ts, amount)] in time order."""
    level, t = float(cap), None
    for ts, amount in events:
        if t is not None:
            level = min(cap, level + cap * (ts - t) / 86400)
        level -= amount
        t = ts
    if t is not None:
        level = min(cap, level + cap * (now - t) / 86400)
    return level


def rolling(rows, now: float, tpd) -> None:
    models = sorted({r["model"] for r in rows if r.get("ratelimit")} | set(tpd))
    if not models:
        return
    print("\nlast 24 h (refilling daily limits, e.g. Groq):")
    for model in models:
        rs = sorted((r for r in rows if r.get("model") == model and "ts" in r), key=lambda r: r["ts"])
        day = [r for r in rs if r["ts"] >= now - 86400]
        tok = [(r["ts"], (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)) for r in rs]
        used = sum(a for ts, a in tok if ts >= now - 86400)
        line = (f"  {model}: {len(day)} requests ({sum(r.get('error') is not None for r in day)} failed), "
                f"{used:,} tokens in the last 24 h")
        if model in tpd:
            line += (f" | left: >= {tpd[model] - used:,} (24 h window), ~{_refill_left(tok, now, tpd[model]):,.0f} "
                     f"(refill estimate) of {tpd[model]:,}")
        print(line)
        last = next((r for r in reversed(rs) if r.get("ratelimit")), None)
        if last:
            h = last["ratelimit"]
            age = round((now - last["ts"]) / 60)
            print(f"    latest headers ({age} min ago): requests left {h.get('x-ratelimit-remaining-requests')}/"
                  f"{h.get('x-ratelimit-limit-requests')} (all back in {h.get('x-ratelimit-reset-requests')}), "
                  f"tokens/min left {h.get('x-ratelimit-remaining-tokens')}/{h.get('x-ratelimit-limit-tokens')}")
        facts = [r["limit"] for r in day if r.get("limit")]
        if facts:
            print(f"    latest 429 says: {facts[-1]}")


if __name__ == "__main__":
    raise SystemExit(main())
