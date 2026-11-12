#!/usr/bin/env python3
"""Split-half check of each adopted change (zero calls, replays saved replies). Report only.

    python3 eval/split_half.py final rep2 rep3

Dev is split at the midpoint of its time span (week 1 / week 2). For each adopted change, "with" = the frozen
config, "without" = the frozen config with only that change undone:
  source shift     config.SHIFT_SOURCES off (replay)
  ownership help   the earlier 2x-ratio help rule (git HEAD decide.help_for, copied below)
  f2               config.HELP_FOLLOWUP_THEIRS off
  truncation split config.TRUNCATION_SPLITS = 0: a cut reply keeps its repaired part (the re-asks of the old path
                   were never made in these runs, so "without" is repair-only)
Per half: loops found (F1 /14 on key loops sourced in that half, our loops sourced in that half), closed right (/14),
help (/10, labels whose source is in that half). Δ = with − without, paired bootstrap over items, runs pooled.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Callable, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from ablate_decide import per_label_help  # noqa: E402
from analyse_status_help import pairs  # noqa: E402
from coldstart import config, decide as dm  # noqa: E402

HELP_RATIO = 2.0


def old_help_for(l: dict, now_ts: int, src_by_me: bool = False) -> dict:
    """The help rule before the organisers' ownership guidance (git HEAD), verbatim apart from the signature."""
    gates = dm.assist_gates(l)
    if not gates and l["p_assist"] > HELP_RATIO * l["p_leave"]:
        return {"action": "assist", "next_step": l["next"], "needs_yes": True}
    only_user = l["owner"] == "ME" and l["needs_me_only"]
    if only_user and l["p_remind"] > HELP_RATIO * l["p_leave"]:
        return {"action": "remind", "remind_at": dm.remind_at(l, now_ts)}
    return {"action": "leave", "reason": "little value in stepping in"}


def halves(out: dict, ans: dict, messages: list, labels: list, mid: float) -> Dict[str, Dict[str, list]]:
    ts = {m["id"]: m["ts"] for m in messages}
    res = {}
    for h, keep in (("week 1", lambda t: t < mid), ("week 2", lambda t: t >= mid)):
        sub_out = dict(out, open_loops=[l for l in out["open_loops"] if keep(ts[l["source_msg"]])])
        sub_ans = dict(ans, open_loops=[a for a in ans["open_loops"] if keep(min(ts[s] for s in a["source_msgs"]))])
        ps, key = pairs(sub_out["open_loops"], sub_ans, messages)
        f, c = [0] * len(key), [0] * len(key)
        for l, j in ps:
            f[j] = 1
            c[j] = 1 if l["status"] == key[j]["status"] else (-1 if key[j]["status"] == "open" else 0)
        lab = [x for x in labels if keep(ts[x["source_msg"]])]
        res[h] = {"found": f, "closed": c, "pred": [len(sub_out["open_loops"])],
                  "help": per_label_help(out, lab, ans) if lab else []}
    return res


def pts(it: dict, comp: str, idx: List[int]) -> float:
    if comp == "found":
        n = len(idx)
        return 14 * 2 * sum(it["found"][i] for i in idx) / (it["pred"][0] + n) if n else 0.0
    if comp == "closed":
        return 14 * max(0.0, sum(it["closed"][i] for i in idx) / len(idx)) if idx else 0.0
    return 10 * max(0.0, sum(it["help"][i] for i in idx) / len(idx)) if idx else 0.0


def main(argv: List[str]) -> int:
    runs = argv[1:]
    _, messages, _, ans = check.load_input(str(rp.DEV))
    labels = json.loads((rp.DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    tss = sorted(m["ts"] for m in messages)
    mid = (tss[0] + tss[-1]) / 2
    changes: Dict[str, dict] = {
        "source shift": {"comps": ["found", "closed", "help"]},
        "ownership help rule": {"comps": ["help"]},
        "f2 (followup = their move)": {"comps": ["help"]},
        "truncation split": {"comps": ["found", "closed", "help"]},
    }
    data: Dict[str, Dict[str, list]] = {c: {"with": [], "without": []} for c in changes}
    for run in runs:
        base_out = json.loads((ROOT / "eval" / "runs" / f"out_{run}.json").read_text(encoding="utf-8"))
        raw = rp.replay_raw(f".llm_cache/{run}", legacy_reask=False)
        with_out = rp.with_loops(base_out, rp.decide(raw))
        w = halves(with_out, ans, messages, labels, mid)
        config.SHIFT_SOURCES = False
        no_shift = rp.with_loops(base_out, rp.decide(rp.replay_raw(f".llm_cache/{run}", legacy_reask=False)))
        config.SHIFT_SOURCES = True
        no_owner = rp.with_loops(base_out, rp.decide(raw, help_fn=old_help_for))
        config.HELP_FOLLOWUP_THEIRS = False
        no_f2 = rp.with_loops(base_out, rp.decide(raw))
        config.HELP_FOLLOWUP_THEIRS = True
        config.TRUNCATION_SPLITS = 0
        no_split = rp.with_loops(base_out, rp.decide(rp.replay_raw(f".llm_cache/{run}", legacy_reask=False)))
        config.TRUNCATION_SPLITS = 2
        for c, o in (("source shift", no_shift), ("ownership help rule", no_owner),
                     ("f2 (followup = their move)", no_f2), ("truncation split", no_split)):
            data[c]["with"].append(w)
            data[c]["without"].append(halves(o, ans, messages, labels, mid))
    rng = random.Random(4)
    B = 3000
    print(f"{len(runs)} runs ({', '.join(runs)}); split at the dev midpoint. Δ = with − without (points); "
          "95% paired bootstrap CI over items, runs pooled.")
    print(f"{'change':<28}{'component':<10}{'week 1 Δ [CI]':>28}{'week 2 Δ [CI]':>28}  flag")
    saved = []
    for c, spec in changes.items():
        for comp in spec["comps"]:
            cells, signs = [], []
            for h in ("week 1", "week 2"):
                W = [r[h] for r in data[c]["with"]]
                O = [r[h] for r in data[c]["without"]]
                n = len(W[0][comp])
                full = list(range(n))
                d = sum(pts(a, comp, full) - pts(b, comp, full) for a, b in zip(W, O)) / len(W)
                bs = []
                for _ in range(B):
                    idx = [rng.randrange(n) for _ in range(n)] if n else []
                    bs.append(sum(pts(a, comp, idx) - pts(b, comp, idx) for a, b in zip(W, O)) / len(W))
                bs.sort()
                lo, hi = bs[int(.025 * B)], bs[int(.975 * B)]
                cells.append(f"{d:+.2f} [{lo:+.2f}, {hi:+.2f}] n={n}")
                signs.append((d, lo, hi))
            flag = ""
            if any(hi < 0 for _, _, hi in signs) and any(lo > 0 or d > 0 for d, lo, _ in signs):
                flag = "HELPS IN ONE HALF, CLEARLY HURTS IN THE OTHER"
            elif any(hi < 0 for _, _, hi in signs):
                flag = "clearly hurts in a half"
            print(f"{c:<28}{comp:<10}{cells[0]:>28}{cells[1]:>28}  {flag}")
            saved.append({"change": c, "component": comp, "halves": [list(x) for x in signs], "flag": flag})
    (ROOT / "eval" / "runs" / "split_half.json").write_text(json.dumps({"runs": runs, "rows": saved}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
