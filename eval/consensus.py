#!/usr/bin/env python3
"""Zero-call test of 3-call consensus for goals and circles, using fresh paid runs as independent samples.

    python3 eval/consensus.py run1 rep2 rep3 abl_a1 ...

Circles and goals prompts don't depend on the loop ablation flags, so every fresh run is an independent sample of
those two calls. For every triple of runs: goals kept if they appear in >= 2 of the 3 (same area and shared evidence
or >= 2 shared title words; evidence merged); circles by majority vote per person (no majority: the first run's
answer). Compares mean and spread (SD) of the scores of single runs vs consensus triples.
"""
from __future__ import annotations

import itertools
import json
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import check  # noqa: E402
import help_goal_score  # noqa: E402
from coldstart.util import tokens  # noqa: E402

RUNS = ROOT / "eval" / "runs"
DEV = ROOT / "data" / "dev"


def same_goal(a: dict, b: dict) -> bool:
    if a.get("area") != b.get("area"):
        return False
    if set(a.get("evidence") or []) & set(b.get("evidence") or []):
        return True
    return len(set(tokens(a.get("title", ""))) & set(tokens(b.get("title", "")))) >= 2


def goals_consensus(runs: List[List[dict]]) -> List[dict]:
    out: List[dict] = []
    used = [set() for _ in runs]
    for i, gs in enumerate(runs):
        for gi, g in enumerate(gs):
            if gi in used[i]:
                continue
            support, ev = 1, list(g.get("evidence") or [])
            for j in range(i + 1, len(runs)):
                hit = next((k for k, h in enumerate(runs[j]) if k not in used[j] and same_goal(g, h)), None)
                if hit is not None:
                    used[j].add(hit)
                    support += 1
                    ev += [e for e in runs[j][hit].get("evidence") or [] if e not in ev]
            if support >= 2:
                out.append(dict(g, evidence=ev))
    return out[:10]


def circles_consensus(runs: List[List[dict]]) -> List[dict]:
    by = [{p["id"]: p for p in r} for r in runs]
    out = []
    for pid, p in by[0].items():
        votes = [b[pid].get("circle") for b in by if pid in b]
        best = max(set(votes), key=votes.count)
        out.append(dict(p, circle=best if votes.count(best) >= 2 else p.get("circle")))
    return out


def main(argv: List[str]) -> int:
    names = argv[1:]
    chats, messages, seed, ans = check.load_input(str(DEV))
    outs = {n: json.loads((RUNS / f"out_{n}.json").read_text(encoding="utf-8")) for n in names}
    labels = json.loads((DEV / "labels" / "goals.json").read_text(encoding="utf-8"))

    def goal_pts(goals: List[dict]) -> float:
        return help_goal_score.score_goals({"goals": goals}, labels, seed) * check.POINTS["goals"]

    def circ_pts(people: List[dict]) -> float:
        base = outs[names[0]]
        return (check.score(dict(base, people=people), ans, seed, messages)["circles"] or 0) * check.POINTS["circles"]

    single_g = [goal_pts(o["goals"]) for o in outs.values()]
    single_c = [circ_pts(o["people"]) for o in outs.values()]
    tri = list(itertools.combinations(names, 3))
    cons_g = [goal_pts(goals_consensus([outs[n]["goals"] for n in t])) for t in tri]
    cons_c = [circ_pts(circles_consensus([outs[n]["people"] for n in t])) for t in tri]
    n_goals = [len(goals_consensus([outs[n]["goals"] for n in t])) for t in tri]
    flips = []  # people whose circle differs between single runs: instability
    ids = [p["id"] for p in outs[names[0]]["people"]]
    for pid in ids:
        vals = {next(p["circle"] for p in o["people"] if p["id"] == pid) for o in outs.values()}
        flips.append(len(vals) > 1)
    print(f"{len(names)} single runs, {len(tri)} consensus triples")
    print(f"goals   /8 : single mean {st.mean(single_g):.2f} SD {st.pstdev(single_g):.2f} (n goals "
          f"{st.mean(len(o['goals']) for o in outs.values()):.1f})  |  consensus mean {st.mean(cons_g):.2f} "
          f"SD {st.pstdev(cons_g):.2f} (n goals {st.mean(n_goals):.1f})")
    print(f"circles /12: single mean {st.mean(single_c):.2f} SD {st.pstdev(single_c):.2f}  |  consensus mean "
          f"{st.mean(cons_c):.2f} SD {st.pstdev(cons_c):.2f};  people whose circle varies across single runs: "
          f"{sum(flips)}/{len(ids)}")
    print("single goal points per run:", [round(x, 2) for x in single_g])
    print("single circle points per run:", [round(x, 2) for x in single_c])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
