#!/usr/bin/env python3
"""Zero-call precision sweep on saved replies: loop-confidence cutoff and stricter merging of near-duplicates.

    python3 eval/precision_sweep.py rep2 rep3 run1-frozen abl_a1:burst ...

Per setting and run: loops found (/14), closed right (/14), help (/10) with the current decide rules.
Reports the mean, Δ vs the frozen setting with a two-level bootstrap, and in how many runs the total improved
(adoption rule: better in EVERY run).
Settings:
  conf>=t       drop loops the model is less than t sure about (frozen: 0.5)
  jac=j         merge same-chat loops whose titles share >= j of their words (frozen: 0.5)
  near=Nm       also merge same-chat, same-owner loops whose sources are within N minutes (keeps the earliest)
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from ablate_decide import _set_ablate, per_label_help  # noqa: E402
from analyse_status_help import pairs  # noqa: E402
from coldstart import config, loops  # noqa: E402

SETTINGS: List[Tuple[str, float, float, int]] = [  # name, conf cutoff, title jaccard, near-merge minutes
    ("frozen conf>=0.5 jac=0.5", 0.5, 0.5, 0),
    ("conf>=0.6", 0.6, 0.5, 0),
    ("conf>=0.7", 0.7, 0.5, 0),
    ("conf>=0.75", 0.75, 0.5, 0),
    ("conf>=0.8", 0.8, 0.5, 0),
    ("conf>=0.85", 0.85, 0.5, 0),
    ("conf>=0.9", 0.9, 0.5, 0),
    ("jac=0.35", 0.5, 0.35, 0),
    ("jac=0.25", 0.5, 0.25, 0),
    ("near=30m", 0.5, 0.5, 30),
    ("near=120m", 0.5, 0.5, 120),
]


def near_merge(raw: List[dict], minutes: int) -> List[dict]:
    if not minutes:
        return raw
    d = rp.data()
    kept: List[dict] = []
    for l in sorted(raw, key=lambda l: d.by_id[l["source_msg"]]["ts"]):
        ts = d.by_id[l["source_msg"]]["ts"]
        if any(k["chat_id"] == l["chat_id"] and k["owner"] == l["owner"]
               and 0 <= ts - d.by_id[k["source_msg"]]["ts"] <= minutes * 60 for k in kept):
            continue
        kept.append(l)
    return kept


def points(out: dict, ans: dict, messages: list, labels: list) -> Dict[str, object]:
    ps, key = pairs(out["open_loops"], ans, messages)
    found = [0] * len(key)
    closed = [0] * len(key)
    for l, j in ps:
        found[j] = 1
        closed[j] = 1 if l["status"] == key[j]["status"] else (-1 if key[j]["status"] == "open" else 0)
    return {"found": found, "closed": closed, "pred": len(out["open_loops"]), "help": per_label_help(out, labels, ans)}


def score(it: Dict[str, object], kidx: List[int], hidx: List[int]) -> float:
    n = len(kidx)
    f1 = 2 * sum(it["found"][i] for i in kidx) / (it["pred"] + n)  # type: ignore[index,operator]
    c = max(0.0, sum(it["closed"][i] for i in kidx) / n)  # type: ignore[index]
    h = max(0.0, sum(it["help"][i] for i in hidx) / len(hidx))  # type: ignore[index]
    return 14 * f1 + 14 * c + 10 * h


def main(argv: List[str]) -> int:
    _, messages, _, ans = check.load_input(str(rp.DEV))
    labels = json.loads((rp.DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    res: Dict[str, Dict[str, Dict[str, object]]] = {s[0]: {} for s in SETTINGS}
    n_loops: Dict[str, List[int]] = {s[0]: [] for s in SETTINGS}
    for spec in argv[1:]:
        run, _, flags = spec.partition(":")
        base_out = json.loads((ROOT / "eval" / "runs" / f"out_{run if run != 'run1-frozen' else 'run1'}.json").read_text())
        _set_ablate(flags)
        for name, conf, jac, near in SETTINGS:
            config.MIN_CONFIDENCE, loops.MERGE_TITLE_JACCARD = conf, jac
            raw = near_merge(rp.replay_raw(f".llm_cache/{run}"), near)
            out = rp.with_loops(base_out, rp.decide(raw))
            res[name][run] = points(out, ans, messages, labels)
            n_loops[name].append(len(raw))
        config.MIN_CONFIDENCE, loops.MERGE_TITLE_JACCARD = 0.5, 0.5
        _set_ablate("")
    runs = [s.partition(":")[0] for s in argv[1:]]
    nk, nh = len(ans["open_loops"]), len(labels)
    fk, fh = list(range(nk)), list(range(nh))
    base = SETTINGS[0][0]
    rng = random.Random(3)
    B = 3000
    draws = []
    for _ in range(B):
        kidx = [rng.randrange(nk) for _ in range(nk)]
        hidx = [rng.randrange(nh) for _ in range(nh)]
        rsel = [runs[rng.randrange(len(runs))] for _ in runs]  # same run draw for every setting: paired runs
        draws.append((kidx, hidx, rsel))
    print(f"{len(runs)} runs; points = found/14 + closed/14 + help/10 (mean over runs); Δ vs '{base}', paired")
    print(f"{'setting':<26}{'loops':>7}{'found':>7}{'closed':>8}{'help':>6}{'sum':>7}{'Δ [95% CI]':>24}{'runs better':>13}{'worse':>7}")
    for name, *_ in SETTINGS:
        per = {r: res[name][r] for r in runs}
        comp = [sum(f(per[r]) for r in runs) / len(runs) for f in (
            lambda it: 14 * 2 * sum(it["found"]) / (it["pred"] + nk),
            lambda it: 14 * max(0.0, sum(it["closed"]) / nk),
            lambda it: 10 * max(0.0, sum(it["help"]) / nh))]
        deltas = [score(per[r], fk, fh) - score(res[base][r], fk, fh) for r in runs]
        boots = sorted(sum(score(res[name][r], k, h) - score(res[base][r], k, h) for r in rs) / len(rs)
                       for k, h, rs in draws)
        d = sum(deltas) / len(deltas)
        ci = f"{d:+.2f} [{boots[int(.025 * B)]:+.2f}, {boots[int(.975 * B)]:+.2f}]"
        better = sum(x > 1e-9 for x in deltas)
        worse = sum(x < -1e-9 for x in deltas)
        print(f"{name:<26}{sum(n_loops[name]) / len(runs):>7.0f}{comp[0]:>7.2f}{comp[1]:>8.2f}{comp[2]:>6.2f}"
              f"{sum(comp):>7.2f}{ci:>24}{f'{better}/{len(runs)}':>13}{worse:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
