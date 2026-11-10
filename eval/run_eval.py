#!/usr/bin/env python3
"""Run the pipeline, score it with check.py (+ hand labels), and summarise tokens per stage.

    python3 eval/run_eval.py [data_dir] [--out out.json] [--tag note]

Keeps a history in eval/runs/history.jsonl (gitignored) and prints the delta against the previous run.
Token counts come from the usage.jsonl lines this run appended.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import check  # noqa: E402
import help_goal_score  # noqa: E402

HISTORY = ROOT / "eval" / "runs" / "history.jsonl"
STATS = ROOT / "eval" / "runs" / "stats.json"
USAGE = Path("usage.jsonl")  # llm.py writes it in the working directory


def _usage_from(offset: int) -> Dict[str, float]:
    tot = {"calls": 0, "failed_attempts": 0, "prompt": 0, "completion": 0}
    if USAGE.exists():
        with open(USAGE, "rb") as f:
            f.seek(offset)
            for line in f.read().decode("utf-8", "replace").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("error") is not None:
                    tot["failed_attempts"] += 1
                    continue
                tot["calls"] += 1
                tot["prompt"] += r.get("prompt_tokens") or 0
                tot["completion"] += r.get("completion_tokens") or 0
    return tot


def _last() -> Optional[dict]:
    if not HISTORY.exists():
        return None
    lines = [l for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "runs" / "out.json"))
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    offset = USAGE.stat().st_size if USAGE.exists() else 0
    t0 = time.time()
    if STATS.exists():
        STATS.unlink()
    env = dict(os.environ, COLDSTART_STATS=str(STATS))
    r = subprocess.run([str(ROOT / "run.sh"), args.data, args.out], capture_output=True, text=True, env=env)
    seconds = round(time.time() - t0, 1)
    sys.stderr.write(r.stderr[-3000:])
    if r.returncode != 0:
        print(f"run.sh exited {r.returncode}")
        return 1

    chats, messages, seed, answers = check.load_input(args.data)
    out = json.loads(Path(args.out).read_text(encoding="utf-8"))
    errs = check.validate(out, chats, messages)
    if errs:
        print("INVALID OUTPUT:", errs[:5])
        return 1
    scores = check.score(out, answers, seed, messages) if answers else {}
    extra = help_goal_score.run(args.data, args.out)
    for k in ("help", "goals"):
        if scores.get(k) is None and extra[k] is not None:
            scores[k] = extra[k]  # hand labels fill what the key leaves unlabelled
    usage = _usage_from(offset)
    tokens = usage["prompt"] + usage["completion"]
    rec = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag,
        "data": args.data,
        "seconds": seconds,
        "scores": scores,
        "points": round(sum((v or 0) * check.POINTS[k] for k, v in scores.items()), 2),
        "possible": sum(check.POINTS[k] for k, v in scores.items() if v is not None),
        "usage": usage,
        "tokens_per_1k_msgs": round(1000 * tokens / len(messages), 1) if messages else None,
        "model": os.environ.get("LLM_MODEL"),
        "stages": json.loads(STATS.read_text())["stages"] if STATS.exists() else {},
    }
    prev = _last()

    print(f"\n{'Part':<16}{'Score':>7}{'Points':>9}{'Δ pts':>8}")
    for k, pts in check.POINTS.items():
        v = scores.get(k)
        if v is None:
            print(f"{k:<16}{'—':>7}")
            continue
        d = ""
        if prev and prev["scores"].get(k) is not None:
            d = f"{(v - prev['scores'][k]) * pts:+.1f}"
        src = " (hand labels)" if k in ("help", "goals") and extra[k] is not None else ""
        print(f"{k:<16}{v:>7.2f}{v * pts:>6.1f}/{pts:<3}{d:>7}{src}")
    dp = f"  Δ {rec['points'] - prev['points']:+.1f}" if prev else ""
    print(f"{'total':<16}{'':>7}{rec['points']:>6.1f}/{rec['possible']}{dp}")
    dt = ""
    if prev:
        ptok = prev["usage"]["prompt"] + prev["usage"]["completion"]
        dt = f"  Δ {tokens - ptok:+d}"
    print(f"\nruntime {seconds}s | LLM calls {usage['calls']} | tokens {usage['prompt']}+{usage['completion']}={tokens}{dt}"
          f" | {rec['tokens_per_1k_msgs']} tokens / 1k msgs")

    for name, t in rec["stages"].items():
        print(f"  stage {name:<8} calls {t['calls']:>4} | tokens {t['prompt']}+{t['completion']} | "
              f"failed prompts {t.get('failed_prompts', 0)} | models {','.join(t.get('models', []))}")
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
