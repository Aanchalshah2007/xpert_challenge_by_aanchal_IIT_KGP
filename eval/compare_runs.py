#!/usr/bin/env python3
"""Compare arms of full-dev runs on the loop components, with a two-level paired bootstrap. Zero calls.

    python3 eval/compare_runs.py base=rep2,rep3 a=abl_a1,abl_a2 b=abl_b1,abl_b2 ...

Each name is eval/runs/out_<name>.json. Per run: loops found (F1, x14), closed right (x14), help on the hand labels
(x10), and the data total from check.py. Bootstrap: resample runs within each arm (run-to-run noise: providers,
sampling) and resample key loops / help labels (the same indices for every arm, so items are paired).
Δ is each arm minus the first arm, in points; CI is the 2.5-97.5 percentile.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import check  # noqa: E402
import help_goal_score  # noqa: E402
from ablate_decide import per_label_help  # noqa: E402
from analyse_status_help import pairs  # noqa: E402

RUNS = ROOT / "eval" / "runs"
DEV = ROOT / "data" / "dev"


def items(name: str, ans: dict, messages: list, labels: list, seed: dict) -> Dict[str, object]:
    out = json.loads((RUNS / f"out_{name}.json").read_text(encoding="utf-8"))
    ps, key = pairs(out["open_loops"], ans, messages)
    since = ans.get("loops_scored_from")
    pred = len(out["open_loops"]) if since is None else len(
        [l for l in out["open_loops"] if {m["id"]: m["ts"] for m in messages}.get(l["source_msg"], 0) >= since])
    found = [0] * len(key)
    closed = [0] * len(key)
    for l, j in ps:
        found[j] = 1
        closed[j] = 1 if l["status"] == key[j]["status"] else (-1 if key[j]["status"] == "open" else 0)
    s = check.score(out, ans, seed, messages)
    total = sum((s.get(k) or 0) * v for k, v in check.POINTS.items() if k not in ("help", "goals") and s.get(k) is not None)
    hg = help_goal_score.run_obj(str(DEV), out)
    return {"found": found, "closed": closed, "pred": pred, "help": per_label_help(out, labels, ans),
            "total": total, "goals": (hg["goals"] or 0) * check.POINTS["goals"]}


def pts(r: Dict[str, object], kidx: List[int], hidx: List[int]) -> Tuple[float, float, float]:
    found, closed, helps = r["found"], r["closed"], r["help"]  # type: ignore[assignment]
    n = len(kidx)
    m = sum(found[i] for i in kidx)  # type: ignore[index]
    f1 = 2 * m / (r["pred"] + n) if (r["pred"] + n) else 0.0  # type: ignore[operator]
    c = max(0.0, sum(closed[i] for i in kidx) / n)  # type: ignore[index]
    h = max(0.0, sum(helps[i] for i in hidx) / len(hidx))  # type: ignore[index]
    return f1 * check.POINTS["loops_found"], c * check.POINTS["loops_closed"], h * check.POINTS["help"]


def main(argv: List[str]) -> int:
    arms = [(a.split("=")[0], a.split("=")[1].split(",")) for a in argv[1:]]
    chats, messages, seed, ans = check.load_input(str(DEV))
    labels = json.loads((DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    data = {n: items(n, ans, messages, labels, seed) for _, names in arms for n in names}
    nk = len(next(iter(data.values()))["found"])  # type: ignore[arg-type]
    nh = len(labels)
    full_k, full_h = list(range(nk)), list(range(nh))
    rng = random.Random(5)
    B = 4000
    boots = {a: [] for a, _ in arms}
    for _ in range(B):
        kidx = [rng.randrange(nk) for _ in range(nk)]
        hidx = [rng.randrange(nh) for _ in range(nh)]
        for a, names in arms:
            pick = [names[rng.randrange(len(names))] for _ in names]
            vals = [pts(data[n], kidx, hidx) for n in pick]
            boots[a].append(tuple(sum(v[i] for v in vals) / len(vals) for i in range(3)))
    base = arms[0][0]
    print(f"arms vs '{base}' ({nk} key loops, {nh} help labels, {B} bootstrap draws). Points: found/14, closed/14, help/10.")
    print(f"{'arm':<10}{'runs':>5}{'found':>7}{'closed':>8}{'help':>6}{'loops+help':>11}{'Δ loops+help [95% CI]':>28}"
          f"{'Δ found':>9}{'Δ closed':>9}{'Δ help':>8}{'data total':>12}{'goals':>7}")
    for a, names in arms:
        p = [pts(data[n], full_k, full_h) for n in names]
        mean = [sum(x[i] for x in p) / len(p) for i in range(3)]
        diffs = sorted(sum(x) - sum(y) for x, y in zip(boots[a], boots[base]))
        d = sum(mean) - sum(sum(pts(data[n], full_k, full_h)) for n in arms[0][1]) / len(arms[0][1])
        comp = [sum(x[i] for x in p) / len(p) - sum(pts(data[n], full_k, full_h)[i] for n in arms[0][1]) / len(arms[0][1])
                for i in range(3)]
        tot = sum(data[n]["total"] for n in names) / len(names)  # type: ignore[misc]
        gl = sum(data[n]["goals"] for n in names) / len(names)  # type: ignore[misc]
        ci = f"{d:+.2f} [{diffs[int(.025 * B)]:+.2f}, {diffs[int(.975 * B)]:+.2f}]"
        print(f"{a:<10}{len(names):>5}{mean[0]:>7.2f}{mean[1]:>8.2f}{mean[2]:>6.2f}{sum(mean):>11.2f}{ci:>28}"
              f"{comp[0]:>+9.2f}{comp[1]:>+9.2f}{comp[2]:>+8.2f}{tot:>12.2f}{gl:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
