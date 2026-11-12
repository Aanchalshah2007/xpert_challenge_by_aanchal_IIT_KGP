#!/usr/bin/env python3
"""The pre-registered medium-vs-low decision rule (fixed by the user before the medium replicates ran). Zero calls.

    python3 eval/decide_medium.py

Adopt 120b medium + max_tokens 6,000 only if ALL hold:
 1. mean loops+help over the 3 medium runs beats the 2 low runs' mean by >= +1.0
 2. no medium run is more than 1.5 below the low mean
 3. truncated or failed replies < 2% of calls (medium runs)
 4. projected hidden-set wall time at concurrency 8 < 15 min
 5. projected hidden cost <= $0.015 per 1K at the listed price, 6.5K messages in the last 16 days, 17K messages
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from compare_runs import items, pts  # noqa: E402
from coldstart.spend import read  # noqa: E402

LOW = ["rep2_cur", "rep3_cur"]  # frozen low runs, help re-decided with the adopted f2 rule
MEDIUM = ["med6k_c", "med_r2", "med_r3"]
MED_LEDGER_RUNS = ["med6k_1", "med6k_2", "med6k_c", "med_r2", "med_r3"]  # every medium/6,000 call made
FRESH_CONC8 = ["med_r2", "med_r3"]  # complete medium runs at concurrency 8 (med6k_c ran at 3, half cached)
PRICE_IN, PRICE_OUT = 0.15e-6, 0.60e-6  # listed, openai/gpt-oss-120b, 2026-11-11
DEV_WINDOW_MSGS = 4502  # all dev messages fall in the 16-day loop window
HIDDEN_MSGS, HIDDEN_WINDOW = 17000, 6500


def stage_times(log: Path) -> dict:
    t = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[coldstart\s+([\d.]+)s\] (?:llm\[(\w+)\]:|(final):)", line)
        if m:
            t[m.group(2) or m.group(3)] = float(m.group(1))
    return t


def main() -> int:
    dev = ROOT / "data" / "dev"
    chats, messages, seed, ans = check.load_input(str(dev))
    labels = json.loads((dev / "labels" / "help.json").read_text(encoding="utf-8"))
    full_k, full_h = list(range(len(ans["open_loops"]))), list(range(len(labels)))
    score = {n: sum(pts(items(n, ans, messages, labels, seed), full_k, full_h)) for n in LOW + MEDIUM}
    low_mean = sum(score[n] for n in LOW) / len(LOW)
    med_mean = sum(score[n] for n in MEDIUM) / len(MEDIUM)
    c1 = med_mean - low_mean >= 1.0
    c2 = all(score[n] >= low_mean - 1.5 for n in MEDIUM)

    rows = [r for r in read(ROOT / "eval" / "runs" / "spend.jsonl") if r.get("run") in MED_LEDGER_RUNS]
    bad = [r for r in rows if r.get("error") or r.get("finish_reason") != "stop"]
    c3 = len(bad) / len(rows) < 0.02

    proj = []
    for n in FRESH_CONC8:
        t = stage_times(ROOT / "eval" / "runs" / f"{n}.log")
        loops_s = t["loops"] - t["goals"]
        proj.append(t["goals"] + loops_s * HIDDEN_WINDOW / DEV_WINDOW_MSGS)
    c4 = max(proj) < 15 * 60

    costs = []
    for n in FRESH_CONC8:
        rr = [r for r in read(ROOT / "eval" / "runs" / "spend.jsonl") if r.get("run") == n and not r.get("error")]
        loops = sum(r["prompt_tokens"] * PRICE_IN + r["completion_tokens"] * PRICE_OUT for r in rr if r["stage"] == "loops")
        other = sum(r["prompt_tokens"] * PRICE_IN + r["completion_tokens"] * PRICE_OUT for r in rr if r["stage"] != "loops")
        costs.append((loops / DEV_WINDOW_MSGS * HIDDEN_WINDOW + 2 * other) / (HIDDEN_MSGS / 1000))  # people x2
    c5 = max(costs) <= 0.015

    print(f"loops+help: low {[round(score[n], 2) for n in LOW]} mean {low_mean:.2f}; "
          f"medium {[round(score[n], 2) for n in MEDIUM]} mean {med_mean:.2f}")
    print(f"1. medium mean - low mean = {med_mean - low_mean:+.2f} (need >= +1.00): {'PASS' if c1 else 'FAIL'}")
    print(f"2. worst medium run {min(score[n] for n in MEDIUM):.2f} vs floor {low_mean - 1.5:.2f}: {'PASS' if c2 else 'FAIL'}")
    print(f"3. truncated/failed {len(bad)}/{len(rows)} = {100 * len(bad) / len(rows):.1f}% (need < 2%): {'PASS' if c3 else 'FAIL'}")
    print(f"4. projected hidden wall time {max(proj) / 60:.1f} min (runs: {[round(p / 60, 1) for p in proj]}; need < 15): "
          f"{'PASS' if c4 else 'FAIL'}")
    print(f"5. projected hidden $/1K at listed price {max(costs):.4f} (runs: {[round(c, 4) for c in costs]}; circles+goals x2; "
          f"need <= 0.015): {'PASS' if c5 else 'FAIL'}")
    ok = c1 and c2 and c3 and c4 and c5
    print("DECISION:", "adopt 120b medium + max_tokens 6,000" if ok else "freeze low")
    return 0


if __name__ == "__main__":
    sys.exit(main())
