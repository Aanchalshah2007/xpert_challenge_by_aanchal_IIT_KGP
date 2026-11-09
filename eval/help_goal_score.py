#!/usr/bin/env python3
"""Score help actions and goals against hand labels in <data>/labels/, with check.py's rules.

    python3 eval/help_goal_score.py data/dev out.json

labels/help.json  : [{"source_msg": "M...", "help": "assist|remind|leave", "why": "..."}]
labels/goals.json : [{"title": ..., "area": ..., "type": ..., "horizon": ..., "evidence": ["M...", ...]}]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402

HELP_POINTS, GOAL_POINTS = check.POINTS["help"], check.POINTS["goals"]


def _load(path: Path) -> Optional[list]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # Placeholder files (e.g. "..." titles) are not labels.
    return [x for x in data if x.get("title", "") != "..."] if isinstance(data, list) else None


def score_help(out: dict, labels: List[dict], answers: Optional[dict]) -> float:
    """Each labelled loop: right action +1, assist/remind where label says leave −1. Found via accepted sources."""
    accepted: Dict[str, set] = {}
    for a in (answers or {}).get("open_loops", []):
        for s in a["source_msgs"]:
            accepted[s] = set(a["source_msgs"])
    ours = {l["source_msg"]: l for l in out.get("open_loops", [])}
    total = 0
    for lab in labels:
        srcs = accepted.get(lab["source_msg"], {lab["source_msg"]})
        mine = next((ours[s] for s in srcs if s in ours), None)
        action = (mine.get("help") or {}).get("action") if mine and mine.get("status") == "open" else None
        if action == lab["help"]:
            total += 1
        elif lab["help"] == "leave" and action in ("assist", "remind"):
            total -= 1
    return max(0.0, total / len(labels)) if labels else 0.0


def score_goals(out: dict, labels: List[dict], seed: dict) -> float:
    used, hit = set(), 0
    seeded_titles = {g.get("title") for g in seed.get("goals", [])}
    seeded = [g for g in labels if g.get("title") in seeded_titles]
    goals_ans = [g for g in labels if g.get("title") not in seeded_titles]
    goals_out = [g for g in out["goals"]
                 if not any(g.get("area") == a["area"] and set(g.get("evidence", [])) & set(a["evidence"]) for a in seeded)]
    for g in goals_out:
        for j, a in enumerate(goals_ans):
            if j not in used and g.get("area") == a["area"] and set(g.get("evidence", [])) & set(a["evidence"]):
                used.add(j)
                hit += 1
                break
    return check.f1(hit, len(goals_out), len(goals_ans))


def run(data_dir: str, out_path: str) -> Dict[str, Optional[float]]:
    folder = Path(data_dir)
    _, _, seed, answers = check.load_input(folder)
    out = json.loads(Path(out_path).read_text(encoding="utf-8"))
    help_labels = _load(folder / "labels" / "help.json")
    goal_labels = _load(folder / "labels" / "goals.json")
    return {
        "help": score_help(out, help_labels, answers) if help_labels else None,
        "goals": score_goals(out, goal_labels, seed) if goal_labels else None,
        "n_help_labels": len(help_labels or []),
        "n_goal_labels": len(goal_labels or []),
    }


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    s = run(argv[1], argv[2])
    for key, pts in (("help", HELP_POINTS), ("goals", GOAL_POINTS)):
        n = s[f"n_{key}_labels"]
        if s[key] is None:
            print(f"{key:<8} no labels")
        else:
            print(f"{key:<8}{s[key]:>6.2f}{s[key] * pts:>6.1f} / {pts}   ({n} hand labels)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
