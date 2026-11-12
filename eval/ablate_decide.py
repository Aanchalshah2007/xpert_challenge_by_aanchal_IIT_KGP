#!/usr/bin/env python3
"""Zero-call ablations of decide.py rules (status threshold, help rules) on saved replies, paired per label / key loop.

    python3 eval/ablate_decide.py RUN_CACHE:OUT_JSON [RUN_CACHE:OUT_JSON ...]

For each run: replay raw loops once, apply each variant, score help (25 hand labels) and closed-right (key loops).
Paired bootstrap over labels / key loops, pooled across runs (each run's items resampled together).
Also counts how many of all our open loops change action (the unlabelled effect).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from analyse_status_help import pairs, per_key_closed, status_at  # noqa: E402
from coldstart import decide as dm  # noqa: E402


def help_f(drop_private: bool = False, followup_is_theirs: bool = False) -> Callable[..., dict]:
    """decide.help_for with the candidate rule changes (f1: no private gate; f2: a followup is someone else's move)."""
    def gates(l: dict) -> List[str]:
        g = dm.assist_gates(l)
        return [x for x in g if not (drop_private and x == "private matter")]

    def f(l: dict, now_ts: int, src_by_me: bool = False) -> dict:
        waiting = l["type"] == "followup" or (src_by_me and l["type"] == "question")
        theirs = l["owner"] != "ME" or (src_by_me and waiting) or (followup_is_theirs and l["type"] == "followup")
        if theirs:
            if waiting and dm.overdue(l, now_ts):
                return {"action": "remind", "remind_at": dm.remind_at(l, now_ts)}
            return {"action": "leave", "reason": "someone else's move"}
        if not gates(l):
            return {"action": "assist", "next_step": l["next"], "needs_yes": True}
        return {"action": "remind", "remind_at": dm.remind_at(l, now_ts)}
    return f


VARIANTS: Dict[str, Tuple[Optional[float], Optional[Callable[..., dict]]]] = {
    "frozen (r=2.0, current help)": (None, None),
    "e: r=1.5": (1.5, None),
    "e: r=1.0": (1.0, None),
    "f1: no private gate": (None, help_f(drop_private=True)),
    "f2: followup = their move": (None, help_f(followup_is_theirs=True)),
    "f1+f2": (None, help_f(drop_private=True, followup_is_theirs=True)),
}


def per_label_help(out: dict, labels: List[dict], ans: dict) -> List[int]:
    accepted: Dict[str, set] = {}
    for a in ans["open_loops"]:
        for s in a["source_msgs"]:
            accepted[s] = set(a["source_msgs"])
    ours = {l["source_msg"]: l for l in out["open_loops"]}
    res = []
    for lab in labels:
        srcs = accepted.get(lab["source_msg"], {lab["source_msg"]})
        mine = next((ours[s] for s in sorted(srcs) if s in ours), None)
        act = (mine.get("help") or {}).get("action") if mine and mine.get("status") == "open" else None
        res.append(1 if act == lab["help"] else (-1 if lab["help"] == "leave" and act in ("assist", "remind") else 0))
    return res


def boot(base: List[List[int]], var: List[List[int]], scale: float, n: int = 10000, seed: int = 11) -> Tuple[float, float, float]:
    """Mean over runs of (sum var - sum base)/N * scale; paired bootstrap resampling items within each run."""
    rng = random.Random(seed)
    diffs = [[v - b for b, v in zip(br, vr)] for br, vr in zip(base, var)]
    point = sum(sum(d) / len(d) for d in diffs) / len(diffs) * scale
    stats = []
    for _ in range(n):
        tot = 0.0
        for d in diffs:
            N = len(d)
            tot += sum(d[rng.randrange(N)] for _ in range(N)) / N
        stats.append(tot / len(diffs) * scale)
    stats.sort()
    return point, stats[int(.025 * n)], stats[int(.975 * n)]


def _set_ablate(flags: str) -> None:
    """Rebuild config and the loop prompt for the flags a run was made with (its cache keys depend on them).
    Decide-rule flags are cleared so the variants below start from the frozen rules."""
    import importlib
    import os
    from coldstart import candidates, config, loops
    os.environ["COLDSTART_ABLATE"] = ",".join(f for f in flags.split(",") if f and not f.startswith(("help_", "status_")))
    importlib.reload(config)
    importlib.reload(candidates)
    importlib.reload(loops)


def main(argv: List[str]) -> int:
    runs = [(a.split(":") + [""])[:3] for a in argv[1:]]  # cache:out[:ablate flags the run was made with]
    chats, messages, seed, ans = check.load_input(str(rp.DEV))
    labels = json.loads((rp.DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    help_items: Dict[str, List[List[int]]] = {k: [] for k in VARIANTS}
    closed_items: Dict[str, List[List[int]]] = {k: [] for k in VARIANTS}
    changed: Dict[str, List[str]] = {k: [] for k in VARIANTS}
    for cache, base_path, flags in runs:
        base_out = json.loads(Path(base_path).read_text(encoding="utf-8"))
        _set_ablate(flags)
        raw = rp.replay_raw(cache)
        _set_ablate("")
        by_src = {l["source_msg"]: l for l in raw}
        ref = None
        for name, (r, hf) in VARIANTS.items():
            out = rp.with_loops(base_out, rp.decide(raw, status_fn=status_at(r) if r else None, help_fn=hf))
            help_items[name].append(per_label_help(out, labels, ans))
            ps, key = pairs(out["open_loops"], ans, messages)
            closed_items[name].append(per_key_closed([(by_src[l["source_msg"]], j) for l, j in ps], key,
                                                     status_at(r or 2.0)))
            acts = [(l.get("help") or {}).get("action") for l in out["open_loops"]]
            ref = ref or acts
            n_open = sum(a is not None for a in acts)
            changed[name].append(f"{sum(a != b for a, b in zip(acts, ref))}/{n_open}")
    base = "frozen (r=2.0, current help)"
    hp, cp = check.POINTS["help"], check.POINTS["loops_closed"]
    print(f"{len(runs)} run(s); help on {len(labels)} labels (points of {hp}); closed-right on key loops (of {cp})")
    print(f"{'variant':<30}{'help':>7}{'Δ help [95% CI]':>26}{'closed':>8}{'Δ closed [95% CI]':>26}  actions changed (all open loops)")
    for name in VARIANTS:
        h = sum(max(0, sum(x) / len(x)) for x in help_items[name]) / len(runs) * hp
        c = sum(max(0, sum(x) / len(x)) for x in closed_items[name]) / len(runs) * cp
        dh, hlo, hhi = boot(help_items[base], help_items[name], hp)
        dc, clo, chi = boot(closed_items[base], closed_items[name], cp)
        print(f"{name:<30}{h:>7.2f}{f'{dh:+.2f} [{hlo:+.2f}, {hhi:+.2f}]':>26}{c:>8.2f}{f'{dc:+.2f} [{clo:+.2f}, {chi:+.2f}]':>26}  {', '.join(changed[name])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
