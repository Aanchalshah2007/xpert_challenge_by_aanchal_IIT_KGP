#!/usr/bin/env python3
"""Resumable full-dev LLM run on Groq's free tier: fills the disk cache for every LLM stage of the pipeline.

    set -a; source <(tr -d '\\r' < .env.groq); set +a
    COLDSTART_LLM_CACHE=.llm_cache python3 eval/full_run.py data/dev --plan    # estimate only, no calls
    COLDSTART_LLM_CACHE=.llm_cache python3 eval/full_run.py data/dev           # run (resumes from the cache)

It runs the pipeline's own stage functions (circles, goals, loops) with the frozen config, so the prompts and
cache keys are exactly those ./run.sh will send; afterwards ./run.sh with the same cache makes no new calls.
Calls go one at a time through a pacer that keeps under Groq's per-minute token limit and waits for the daily
token bucket (refilling continuously over 24 h) to hold the next call plus a reserve. 429s are honoured (retry
hints); a quota refusal pauses the run and it resumes as quota refills. Stops for good if more than 20% of call
attempts fail for non-quota reasons (errors, empty replies, unparseable JSON). Refuses to run against any host
but api.groq.com (free tier only). Progress: eval/runs/full_run_progress.log every 15 minutes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import llm  # noqa: E402
import quota_ledger as ql  # noqa: E402
from coldstart import candidates, circles, config, goals, ingest, llm_client, loops, profile  # noqa: E402

MODEL = "openai/gpt-oss-120b"
TPD, TPM = 200_000, 8_000  # Groq free tier for this model
TPM_USE = 0.85  # pace to this share of the per-minute limit
RESERVE = 8_000  # daily tokens left untouched
TOK_PER_CHAR = 0.333
EST_OUT = {"loops": 620, "circles": 1500, "goals": 1500}  # measured per call (loops: v1 slice run)
REASONING_SHARE = 0.34  # of output tokens (v1 slice run: 4,017 of 11,782)
FAIL_SHARE, FAIL_MIN = 0.20, 10
PAUSE_S = 15 * 60
LOG_EVERY_S = 15 * 60
PROGRESS = ROOT / "eval" / "runs" / "full_run_progress.log"
USAGE = Path("usage.jsonl")
IST = timezone(timedelta(hours=5, minutes=30))


def ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%a %d %b %H:%M IST")


def usage_rows(model: str) -> List[dict]:
    if not USAGE.exists():
        return []
    rows = []
    for line in USAGE.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("model") == model and "ts" in r:
            rows.append(r)
    return rows


def _tok(r: dict) -> int:
    return (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)


def daily_left(now: Optional[float] = None) -> float:
    rows = sorted(usage_rows(MODEL), key=lambda r: r["ts"])
    return ql._refill_left([(r["ts"], _tok(r)) for r in rows], now or time.time(), TPD)


def latest_headers() -> Tuple[Optional[dict], Optional[float]]:
    last = next((r for r in reversed(usage_rows(MODEL)) if r.get("ratelimit")), None)
    return (last["ratelimit"], last["ts"]) if last else (None, None)


# ---------------------------------------------------------------- plan (no calls)

def first_pass(data, prof) -> List[Tuple[str, List[dict], Optional[int]]]:
    """(stage, messages, max_tokens) of every first-pass call the pipeline makes, in pipeline order."""
    out = []
    pinned = dict(data.seed.get("circles") or {})
    for p, _ in circles.prompts(data, prof, pinned):
        out.append(("circles", [{"role": "system", "content": circles.SYSTEM}, {"role": "user", "content": p}], None))
    msgs = goals.candidates(data)
    if msgs:
        p, _ = goals.build_prompt(data, msgs)
        out.append(("goals", [{"role": "system", "content": goals.SYSTEM}, {"role": "user", "content": p}], None))
    wins = [w for win in candidates.windows(data, candidates.find(data)) for w in loops.split(data, win)]
    for p in loops.build_prompts(data, wins)[0]:
        out.append(("loops", [{"role": "system", "content": loops.SYSTEM}, {"role": "user", "content": p}],
                    config.LOOPS_MAX_TOKENS))
    return out


def cache_key(messages: List[dict], max_tokens: Optional[int], extra: Optional[dict]) -> str:
    parts: List[Any] = [os.environ.get("LLM_MODEL"), messages, max_tokens, True]  # same as LLM.complete
    if extra:
        parts.append(extra)
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def estimate(calls, cache: Path, extra) -> Dict[str, Any]:
    todo = [c for c in calls if not (cache / f"{cache_key(c[1], c[2], extra)}.txt").exists()]
    t_in = sum(round(sum(len(m["content"]) for m in c[1]) * TOK_PER_CHAR) for c in todo)
    t_out = sum(EST_OUT[c[0]] for c in todo)
    by_stage: Dict[str, List[int]] = {}
    for c in calls:
        by_stage.setdefault(c[0], [0, 0])[0] += 1
    for c in todo:
        by_stage[c[0]][1] += 1
    return {"calls": len(calls), "todo": len(todo), "in": t_in, "out": t_out,
            "reasoning": round(t_out * REASONING_SHARE), "by_stage": by_stage}


def eta(est: Dict[str, Any], now: float) -> float:
    """Finish time: the per-minute pace or the daily bucket refill, whichever is slower."""
    need = est["in"] + est["out"]
    if not need:
        return now
    per_minute = need / (TPM * TPM_USE) * 60
    short = need + RESERVE - max(0.0, daily_left(now))
    refill = max(0.0, short) / (TPD / 86400)
    return now + max(per_minute, refill) + 25 * est["todo"]  # ~25 s latency per call


# ---------------------------------------------------------------- paced, counted chat

class Pacer:
    def __init__(self) -> None:
        self.recent: List[Tuple[float, int]] = []
        self.lock = threading.Lock()
        self.ok = self.quota = self.errors = self.empty = 0
        self.tokens = 0
        self.stop: Optional[str] = None

    def _wait_for(self, est: int) -> None:
        while True:
            now = time.time()
            self.recent = [(t, n) for t, n in self.recent if t > now - 60]
            minute_ok = sum(n for _, n in self.recent) + est <= TPM * TPM_USE or not self.recent
            day = daily_left(now)
            if minute_ok and day - est >= RESERVE:
                return
            if day - est < RESERVE:
                wait = min(PAUSE_S, (est + RESERVE - day) / (TPD / 86400) + 5)
                note(f"daily bucket ~{day:,.0f} tokens < next call ~{est:,} + reserve {RESERVE:,}; "
                     f"pausing {wait / 60:.0f} min")
            else:
                wait = 61 - (now - self.recent[0][0])
            time.sleep(max(1.0, wait))

    def chat(self, messages, **kw) -> str:
        if self.stop:
            raise RuntimeError(f"harness stopped: {self.stop}")
        est = round(sum(len(m["content"]) for m in messages) * TOK_PER_CHAR) + (kw.get("max_tokens") or 1500)
        self._wait_for(est)
        before = USAGE.stat().st_size if USAGE.exists() else 0
        try:
            text = llm.chat(messages, **kw)
        except Exception as e:
            code = getattr(e, "code", None)
            with self.lock:
                if code == 429:
                    self.quota += 1
                else:
                    self.errors += 1
            self._record(before)
            self._check()
            raise
        with self.lock:
            if text is None or not str(text).strip():
                self.empty += 1
            else:
                self.ok += 1
        self._record(before)
        self._check()
        return text

    def _record(self, before: int) -> None:
        n = 0
        if USAGE.exists():
            with open(USAGE, encoding="utf-8") as f:
                f.seek(before)
                for line in f:
                    try:
                        n += _tok(json.loads(line))
                    except ValueError:
                        pass
        with self.lock:
            self.recent.append((time.time(), max(n, 1)))
            self.tokens += n

    def failed_share(self, json_failures: int = 0) -> Tuple[int, int]:
        bad = self.errors + self.empty + json_failures
        return bad, self.ok + self.errors + self.empty

    def _check(self, json_failures: int = 0) -> None:
        bad, total = self.failed_share(json_failures)
        if total >= FAIL_MIN and bad > FAIL_SHARE * total and not self.stop:
            self.stop = (f"{bad} of {total} call attempts failed for non-quota reasons "
                         f"(errors {self.errors}, empty {self.empty}, bad JSON {json_failures})")


def note(msg: str) -> None:
    line = f"[{ist(time.time())}] {msg}"
    print(line, flush=True)


def progress(calls, cache: Path, extra, pacer: Pacer, t0: float, stop: threading.Event) -> None:
    while not stop.is_set():
        est = estimate(calls, cache, extra)
        h, hts = latest_headers()
        line = (f"[{ist(time.time())}] calls done {est['calls'] - est['todo']}/{est['calls']} "
                f"(remaining {est['todo']}: {est['by_stage']}) | tokens this run {pacer.tokens:,} | "
                f"attempts ok {pacer.ok}, 429 {pacer.quota}, errors {pacer.errors}, empty {pacer.empty} | "
                f"daily bucket ~{daily_left():,.0f} | ETA {ist(eta(est, time.time()))}"
                + (f" | headers: requests left {h.get('x-ratelimit-remaining-requests')}" if h else ""))
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)
        stop.wait(LOG_EVERY_S)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir")
    ap.add_argument("--plan", action="store_true", help="print the estimate and quota, make no calls")
    ap.add_argument("--max-passes", type=int, default=12)
    args = ap.parse_args()
    host = (urlparse(os.environ.get("LLM_BASE_URL", "")).hostname or "").lower()
    if host != "api.groq.com" or os.environ.get("LLM_MODEL") != MODEL or not os.environ.get("LLM_API_KEY"):
        print(f"refusing: needs LLM_BASE_URL on api.groq.com (free tier), LLM_MODEL={MODEL} and a key "
              f"(got host {host or 'unset'}, model {os.environ.get('LLM_MODEL')})")
        return 2
    cache = Path(os.environ.get("COLDSTART_LLM_CACHE") or "")
    if not str(cache) or str(cache) == ".":
        print("refusing: set COLDSTART_LLM_CACHE so every reply is saved")
        return 2
    extra = llm_client.provider_extra()
    data = ingest.load(args.input_dir)
    prof = profile.build(data)
    calls = first_pass(data, prof)
    est = estimate(calls, cache, extra)
    now = time.time()
    h, hts = latest_headers()
    print(f"plan: {est['calls']} first-pass calls {est['by_stage']} ([total, to make]); {est['todo']} to make: "
          f"~{est['in']:,} in + ~{est['out']:,} out (~{est['reasoning']:,} of it reasoning) tokens")
    print(f"quota now: daily-token bucket ~{daily_left(now):,.0f} of {TPD:,} (refill estimate from usage.jsonl; "
          f"Groq headers do not report the daily token limit)")
    if h:
        print(f"  latest Groq headers ({round((now - hts) / 60)} min ago): requests left "
              f"{h.get('x-ratelimit-remaining-requests')}/{h.get('x-ratelimit-limit-requests')}, tokens/min left "
              f"{h.get('x-ratelimit-remaining-tokens')}/{h.get('x-ratelimit-limit-tokens')}")
    print(f"projected finish: {ist(eta(est, now))} (plus JSON re-asks and circles re-asks, not in the plan)")
    if args.plan:
        return 0

    pacer = Pacer()
    stop_log = threading.Event()
    t0 = time.time()
    threading.Thread(target=progress, args=(calls, cache, extra, pacer, t0, stop_log), daemon=True).start()
    try:
        for n in range(1, args.max_passes + 1):
            client = llm_client.LLM(concurrency=1, timeout=config.LLM_TIMEOUT_S, cache_dir=str(cache),
                                    chat_fn=pacer.chat, extra=extra)
            note(f"pass {n}: {estimate(calls, cache, extra)['todo']} first-pass calls left")
            pinned = dict(data.seed.get("circles") or {})
            labels = circles.extract(data, prof, pinned, client)
            g = goals.extract(data, client)
            raw = loops.extract(data, client)
            pacer._check(sum(client.json_failures.values()))
            if pacer.stop:
                note(f"STOPPED for good: {pacer.stop}")
                return 3
            left = estimate(calls, cache, extra)["todo"]
            given_up = sum(client.failures.values())
            note(f"pass {n} done: circles {len(labels)} labelled, goals {len(g)}, loops {len(raw)} | "
                 f"{left} first-pass calls left, {given_up} prompts given up this pass"
                 + (f" | quota: {client.exhausted}" if client.exhausted else ""))
            if left == 0 and given_up == 0:
                note(f"cache complete: {pacer.tokens:,} tokens this run, {pacer.ok} ok attempts, "
                     f"{pacer.quota} 429s, {pacer.errors} errors, {pacer.empty} empty")
                return 0
            if client.exhausted:
                note(f"quota refused; pausing {PAUSE_S // 60} min, then resuming from the cache")
                time.sleep(PAUSE_S)
        note(f"gave up after {args.max_passes} passes with calls still failing")
        return 4
    finally:
        stop_log.set()
        est = estimate(calls, cache, extra)
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(f"[{ist(time.time())}] exit: calls done {est['calls'] - est['todo']}/{est['calls']}, "
                    f"tokens this run {pacer.tokens:,}\n")


if __name__ == "__main__":
    sys.exit(main())
