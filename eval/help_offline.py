#!/usr/bin/env python3
"""Score help decisions on cached loop replies against data/dev/labels/help.json. Zero calls.

    python3 eval/help_offline.py RAW.json [RAW2.json ...]

RAW files come from `eval/slice_run.py --cache-only --dump-raw RAW.json`. Raw loops from all files are pooled
(shift + media closure + merge, as in the pipeline), then decided with the current coldstart.decide and with the
old ratio rule kept here for comparison. Scored like check.py's help: per labelled loop, right action +1,
assist/remind where the label says leave -1; a labelled loop we did not find (or did not call open) scores 0.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import candidates, config, decide, ingest, loops  # noqa: E402


def old_help(l: dict) -> str:
    """The decide.py rule before changelog 13: act only if p_action > 2 * p_leave."""
    if not decide.assist_gates(l) and l["p_assist"] > 2.0 * l["p_leave"]:
        return "assist"
    if l["owner"] == "ME" and l["needs_me_only"] and l["p_remind"] > 2.0 * l["p_leave"]:
        return "remind"
    return "leave"


def main(paths) -> int:
    data = ingest.load(str(ROOT / "data" / "dev"))
    config.LOOPS_USE_ME_NAME = False
    cands = candidates.find(data)
    raw = [l for p in paths for l in json.loads(Path(p).read_text())["raw"]]
    raw = loops.merge(loops.media_closure(loops.shift_sources(raw, data, cands), data), data)
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    accepted = {s: set(a["source_msgs"]) for a in ans["open_loops"] for s in a["source_msgs"]}
    labels = json.loads((ROOT / "data" / "dev" / "labels" / "help.json").read_text())
    by_src = {l["source_msg"]: l for l in raw}
    rows = []
    for lab in labels:
        srcs = accepted.get(lab["source_msg"], {lab["source_msg"]})
        l = next((by_src[s] for s in sorted(srcs) if s in by_src), None)
        if l is None or decide.status(l) != "open":
            rows.append((lab, None, None, None))
            continue
        new = decide.decide([l], data)[0]["help"]["action"]
        rows.append((lab, l, old_help(l), new))

    def pts(i: int) -> int:
        return sum((1 if r[i] == r[0]["help"] else -1 if r[0]["help"] == "leave" and r[i] in ("assist", "remind")
                    else 0) for r in rows)

    found = [r for r in rows if r[1] is not None]
    print(f"{len(labels)} help labels; {len(found)} found open in the cached replies ({len(paths)} raw file(s))")
    for name, i in (("old (2x leave)", 2), ("new (ownership)", 3)):
        right = sum(r[i] == r[0]["help"] for r in found)
        wrong_act = sum(r[0]["help"] == "leave" and r[i] in ("assist", "remind") for r in found)
        print(f"  {name:<16} right {right}/{len(found)} | acted where label says leave {wrong_act} | "
              f"points {pts(i)} -> score {max(0, pts(i)) / len(labels):.2f} x {check.POINTS['help']} = "
              f"{max(0, pts(i)) / len(labels) * check.POINTS['help']:.1f}")
    print("  label -> old/new:", dict(Counter((r[0]["help"], r[2], r[3]) for r in found)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
