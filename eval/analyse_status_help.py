#!/usr/bin/env python3
"""Zero-call status + help analysis of a paid run, from its saved replies (eval/replay.py).

    python3 eval/analyse_status_help.py .llm_cache/run1-frozen eval/runs/out_run1.json

Status: confusion on matched loops (check.py pairing), p_done calibration, closed-right under done-thresholds
p_done > r * p_open (r = 2.0 / 1.5 / 1.0), with a paired bootstrap over key loops.
Help: per hand label, found or not, our action vs the label, and which decide.help_for branch produced it.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from coldstart import decide as dm  # noqa: E402


def status_at(r: float) -> Callable[[dict], str]:
    def f(l: dict) -> str:
        if l["p_done"] >= l["p_dropped"] and l["p_done"] > r * l["p_open"]:
            return "done"
        if l["p_dropped"] > l["p_done"] and l["p_dropped"] > r * l["p_open"]:
            return "dropped"
        return "open"
    return f


def pairs(out_loops: List[dict], ans: dict, messages: List[dict]) -> Tuple[List[Tuple[dict, int]], List[dict]]:
    """check.py's pairing: (our loop, key index), plus the key loops in the scored window."""
    since = ans.get("loops_scored_from")
    key = ans["open_loops"]
    if since is not None:
        ts = {m["id"]: m["ts"] for m in messages}
        out_loops = [l for l in out_loops if ts.get(l.get("source_msg"), 0) >= since]
        key = [a for a in key if min(ts.get(x, 0) for x in a["source_msgs"]) >= since]
    used, ps = set(), []
    for l in out_loops:
        for j, a in enumerate(key):
            if j not in used and l.get("source_msg") in a["source_msgs"]:
                used.add(j)
                ps.append((l, j))
                break
    return ps, key


def per_key_closed(ps: List[Tuple[dict, int]], key: List[dict], status_of: Callable[[dict], str]) -> List[int]:
    """Contribution of each key loop to closed-right: +1 right, -1 open called closed, 0 otherwise / unmatched."""
    c = [0] * len(key)
    for l, j in ps:
        st, ks = status_of(l), key[j]["status"]
        c[j] = 1 if st == ks else (-1 if ks == "open" else 0)
    return c


def boot_diff(a: List[int], b: List[int], n: int = 10000, seed: int = 7) -> Tuple[float, float, float]:
    """Paired bootstrap over key loops of (sum b - sum a) / N, in closed-right points (x14)."""
    rng, N = random.Random(seed), len(a)
    d = [y - x for x, y in zip(a, b)]
    pts = check.POINTS["loops_closed"]
    stats = sorted(sum(d[rng.randrange(N)] for _ in range(N)) / N * pts for _ in range(n))
    return sum(d) / N * pts, stats[int(0.025 * n)], stats[int(0.975 * n)]


def status_report(raw: List[dict]) -> None:
    chats, messages, seed, ans = check.load_input(str(rp.DEV))
    by_src = {l["source_msg"]: l for l in raw}  # raw (merged) loops carry the probabilities
    ps, key = pairs(rp.decide(raw), ans, messages)
    ps_raw = [(by_src[l["source_msg"]], j) for l, j in ps]
    print(f"key loops in scored window: {len(key)} ({Counter(a['status'] for a in key)}); matched {len(ps)}")
    cm = Counter((key[j]["status"], l["status"]) for l, j in ps)
    sts = ["open", "done", "dropped"]
    print("\nconfusion on matched loops (rows: key, cols: ours at r=2.0)")
    print(f"{'':>9}" + "".join(f"{s:>9}" for s in sts))
    for k in sts:
        print(f"{k:>9}" + "".join(f"{cm[(k, s)]:>9}" for s in sts))
    wrong_done = sum(v for (k, s), v in cm.items() if k == "open" and s != "open")
    open_on_done = sum(v for (k, s), v in cm.items() if k != "open" and s == "open")
    print(f"wrong done/dropped on open loops: {wrong_done} (-1 each); open on done loops: {open_on_done} (0 each)")

    print("\ncalibration: p_done (normalised over open/done/dropped) vs key status, matched loops")
    bins = [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9), (.9, 1.01)]
    for lo, hi in bins:
        sel = [key[j]["status"] for l, j in ps_raw if lo <= l["p_done"] < hi]
        if sel:
            print(f"  p_done {lo:.1f}-{min(hi, 1):.1f}: n={len(sel):>3}  key done {sel.count('done'):>3}"
                  f"  open {sel.count('open'):>3}  dropped {sel.count('dropped'):>2}  -> done rate {sel.count('done') / len(sel):.2f}")
    print("\nall our loops in the window: p_done distribution",
          Counter(round(l["p_done"], 1) for l in raw))

    base = per_key_closed(ps_raw, key, status_at(2.0))
    print(f"\nthreshold sweep (done if p_done > r * p_open), closed-right points of {check.POINTS['loops_closed']}:")
    for r in (2.0, 1.5, 1.0, 0.5):
        c = per_key_closed(ps_raw, key, status_at(r))
        st = Counter((key[j]["status"], status_at(r)(l)) for l, j in ps_raw)
        d, lo, hi = boot_diff(base, c)
        print(f"  r={r:<4} closed {sum(c) / len(key) * 14:5.2f}  right {sum(x == 1 for x in c):>3}  wrong-done "
              f"{sum(x == -1 for x in c):>2}  open-on-done {sum(v for (k, s), v in st.items() if k != 'open' and s == 'open'):>3}"
              f"  vs r=2.0: {d:+.2f} [95% CI {lo:+.2f}, {hi:+.2f}]")


def help_branch(l: dict, now: int, src_by_me: bool) -> str:
    waiting = l["type"] == "followup" or (src_by_me and l["type"] == "question")
    if l["owner"] != "ME" or (src_by_me and waiting):
        why = "owner not ME" if l["owner"] != "ME" else "user's own question/followup"
        return f"other's move ({why}){' + overdue' if waiting and dm.overdue(l, now) else ''}"
    g = dm.assist_gates(l)
    return "user's move, all gates pass" if not g else f"user's move, gates fail: {', '.join(g)}"


def help_report(raw: List[dict]) -> None:
    d = rp.data()
    chats, messages, seed, ans = check.load_input(str(rp.DEV))
    labels = json.loads((rp.DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    accepted: Dict[str, set] = {}
    for a in ans["open_loops"]:
        for s in a["source_msgs"]:
            accepted[s] = set(a["source_msgs"])
    out = {l["source_msg"]: l for l in rp.decide(raw)}
    raws = {l["source_msg"]: l for l in raw}
    rows, tally = [], Counter()
    for i, lab in enumerate(labels, 1):
        srcs = accepted.get(lab["source_msg"], {lab["source_msg"]})
        src = next((s for s in sorted(srcs) if s in out), None)
        if src is None:
            res, act, branch = "not found", None, "-"
        else:
            l, r = out[src], raws[src]
            act = (l.get("help") or {}).get("action") if l["status"] == "open" else None
            m = d.by_id[src]
            branch = help_branch(r, d.now, m["sender_id"] == "ME") if l["status"] == "open" else f"status {l['status']}"
            res = ("right" if act == lab["help"] else "-1" if lab["help"] == "leave" and act in ("assist", "remind")
                   else "0")
        tally[res] += 1
        feats = ""
        if src:
            r = raws[src]
            feats = (f"type={r['type']} owner={'ME' if r['owner'] == 'ME' else 'other'} by_me={d.by_id[src]['sender_id'] == 'ME'} "
                     f"due={'yes' if r['due'] else 'no'} gates_fail={len(dm.assist_gates(r))}")
        rows.append((i, lab["help"], act or "-", res, branch, feats))
    print(f"\nhelp vs {len(labels)} hand labels: {dict(tally)}  -> score {max(0, tally['right'] - tally['-1']) / len(labels) * 10:.1f}/10")
    print(f"{'#':>3} {'label':<7}{'ours':<7}{'result':<10}{'rule that decided':<52}features")
    for i, lab, act, res, br, f in rows:
        print(f"{i:>3} {lab:<7}{act:<7}{res:<10}{br[:51]:<52}{f}")


def main(argv: List[str]) -> int:
    raw = rp.replay_raw(argv[1])
    status_report(raw)
    help_report(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
