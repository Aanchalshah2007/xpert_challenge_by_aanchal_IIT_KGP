#!/usr/bin/env python3
"""Final analysis (zero calls): writes eval/runs/analysis.md and eval/runs/pareto.svg from saved outputs + ledger.

    python3 eval/analysis.py

Never prints or writes names: only ids-free categories and counts.
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import check  # noqa: E402
import help_goal_score  # noqa: E402
from ablate_decide import per_label_help  # noqa: E402
from analyse_status_help import pairs  # noqa: E402
from coldstart.spend import read  # noqa: E402

RUNS = ROOT / "eval" / "runs"
DEV = ROOT / "data" / "dev"
P_IN, P_OUT = 0.15e-6, 0.60e-6  # listed price, openai/gpt-oss-120b (OpenRouter models API, 2026-11-11 22:38 IST)
P20_IN, P20_OUT = 0.03e-6, 0.13e-6  # listed price, openai/gpt-oss-20b, same source and date
N_DEV = 4502
B = 4000
LOW = {"run 1 (frozen + re-ask)": "run1_cur", "rep2": "rep2_cur", "rep3": "rep3_cur", "final": "final_bothagree"}
ARMS = {  # arm -> (output names, ledger runs per output, price)
    "120b low (frozen)": (["run1_cur", "rep2_cur", "rep3_cur", "final_bothagree"], [["run1-frozen-b"], ["rep2"], ["rep3"], ["final"]], (P_IN, P_OUT)),
    "120b medium, 6k cap": (["med6k_c", "med_r2", "med_r3"], [["med6k_1", "med6k_2", "med6k_c"], ["med_r2"], ["med_r3"]], (P_IN, P_OUT)),
    "20b low": (["oss20_1", "oss20_2"], [["oss20_1"], ["oss20_2"]], (P20_IN, P20_OUT)),
}


def load(name: str) -> dict:
    return json.loads((RUNS / f"out_{name}.json").read_text(encoding="utf-8"))


def cost_of(runs: List[str], price: Tuple[float, float]) -> Tuple[float, float, int, int, Dict[str, float]]:
    """(actual $, listed $, input, output tokens, listed $ by stage) of one complete run; for an assembled run the
    union of distinct prompts across its partial runs."""
    seen, rows = set(), []
    for r in read(RUNS / "spend.jsonl"):
        if r.get("run") in runs and not r.get("error"):
            k = r.get("prompt_sha") or r.get("id")
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
    tin = sum(r["prompt_tokens"] for r in rows)
    tout = sum(r["completion_tokens"] for r in rows)
    by = defaultdict(float)
    for r in rows:
        by[r["stage"]] += r["prompt_tokens"] * price[0] + r["completion_tokens"] * price[1]
    return sum(r["cost"] or 0 for r in rows), tin * price[0] + tout * price[1], tin, tout, dict(by)


def quality(out: dict, ctx) -> Dict[str, float]:
    chats, messages, seed, ans, labels = ctx
    s = check.score(out, ans, seed, messages)
    hg = help_goal_score.run_obj(str(DEV), out)
    s["help"], s["goals"] = hg["help"], hg["goals"]
    return {k: (s.get(k) or 0.0) * v for k, v in check.POINTS.items()}


def boot_components(out: dict, ctx) -> Dict[str, Tuple[float, float, float]]:
    """Item-level bootstrap per component (the run is fixed; items resampled)."""
    chats, messages, seed, ans, labels = ctx
    rng = random.Random(1)
    res = {}
    seeded = set((seed.get("circles") or {}).keys())
    circ = {p["id"]: p.get("circle") for p in out["people"]}
    ci = [int(circ.get(p["id"]) == p["circle"]) for p in ans["people"] if p["id"] not in seeded]
    orgs, n_ours, n_key = check.match_orgs(out, ans)
    org_items = [orgs.get(o["id"], (None, 0.0))[1] for o in ans["organisations"]]
    tie = {c["chat_id"]: c for c in out["chats"]}
    ch = []
    for c in ans["chats"]:
        mine = tie.get(c["chat_id"], {})
        ch.append(int(mine.get("person") == c["person"]) if "person" in c else
                  int(mine.get("organisation") is not None and orgs.get(c["organisation"], (None,))[0] == mine.get("organisation")))
    ps, key = pairs(out["open_loops"], ans, messages)
    found = [0] * len(key)
    closed = [0] * len(key)
    for l, j in ps:
        found[j] = 1
        closed[j] = 1 if l["status"] == key[j]["status"] else (-1 if key[j]["status"] == "open" else 0)
    pred = len(out["open_loops"])
    hl = per_label_help(out, labels, ans)
    specs = {
        "circles": (ci, lambda xs: 12 * sum(xs) / len(xs)),
        "organisations": (org_items, lambda xs: 8 * sum(xs) / max(n_ours, len(xs))),
        "chats": (ch, lambda xs: 8 * sum(xs) / len(xs)),
        "loops found": (found, lambda xs: 14 * 2 * sum(xs) / (pred + len(xs))),
        "loops closed right": (closed, lambda xs: 14 * max(0.0, sum(xs) / len(xs))),
        "help (25 hand labels)": (hl, lambda xs: 10 * max(0.0, sum(xs) / len(xs))),
    }
    for name, (xs, f) in specs.items():
        bs = sorted(f([xs[rng.randrange(len(xs))] for _ in xs]) for _ in range(B))
        res[name] = (f(xs), bs[int(.025 * B)], bs[int(.975 * B)])
    return res


def points_formula(q: float, cost_per_1k: float) -> float:
    lo, hi = 0.0095, 0.152
    f = 1.0 if cost_per_1k <= lo else 0.0 if cost_per_1k >= hi else math.log(hi / cost_per_1k) / math.log(hi / lo)
    return q + 12 * f * q / sum(check.POINTS.values())


def hidden_cost(listed_by_stage: Dict[str, float], window: int, people_scale: float = 2.0) -> float:
    loops = listed_by_stage.get("loops", 0.0) / N_DEV * window
    other = sum(v for k, v in listed_by_stage.items() if k != "loops") * people_scale
    return (loops + other) / 17.0


def error_profile(out: dict, ctx) -> List[str]:
    chats, messages, seed, ans, labels = ctx
    by_id = {m["id"]: m for m in messages}
    is_group = {c["chat_id"]: c.get("is_group") for c in chats}
    ps, key = pairs(out["open_loops"], ans, messages)
    matched = {j for _, j in ps}
    lines = ["| key loops by kind | n | found | found % |", "|---|---|---|---|"]
    kinds = Counter()
    hit = Counter()
    for j, a in enumerate(key):
        src = min(a["source_msgs"], key=lambda i: by_id[i]["ts"])
        k = f"{a['type']}, raised by {'the user' if by_id[src]['sender_id'] == 'ME' else 'the other person'}, " \
            f"{'group' if is_group.get(a['chat_id']) else '1:1'}"
        kinds[k] += 1
        hit[k] += j in matched
    for k, n in kinds.most_common():
        lines.append(f"| {k} | {n} | {hit[k]} | {100 * hit[k] // n}% |")
    acc = {s for a in key for s in a["source_msgs"]}
    fp = Counter((l["type"], "group" if is_group.get(l["chat_id"]) else "1:1") for l in out["open_loops"]
                 if l["source_msg"] not in acc)
    lines += ["", f"Our loops not in the key: {sum(fp.values())} of {len(out['open_loops'])}. By type: "
              + ", ".join(f"{t} ({g}) {n}" for (t, g), n in fp.most_common()) + "."]
    st_cm = Counter((key[j]["status"], l["status"]) for l, j in ps)
    lines += ["", "Status on matched loops (key → ours): " + ", ".join(f"{a}→{b} {n}" for (a, b), n in sorted(st_cm.items())) + "."]
    seeded = set((seed.get("circles") or {}).keys())
    circ = {p["id"]: p.get("circle") for p in out["people"]}
    cc, cr = Counter(), Counter()
    for p in ans["people"]:
        if p["id"] in seeded:
            continue
        cc[p["circle"]] += 1
        cr[p["circle"]] += circ.get(p["id"]) == p["circle"]
    lines += ["", "| circle (key) | n | right | % |", "|---|---|---|---|"]
    lines += [f"| {c} | {n} | {cr[c]} | {100 * cr[c] // n}% |" for c, n in cc.most_common()]
    return lines


def svg_pareto(arms: Dict[str, dict], path: Path) -> None:
    W, H, L, R, T, Bm = 680, 400, 70, 170, 30, 50
    xs = [p["listed"] for a in arms.values() for p in a["runs"]]
    ys = [p["q"] for a in arms.values() for p in a["runs"]]
    x0, x1 = math.log10(min(xs) * 0.8), math.log10(max(xs) * 1.25)
    y0, y1 = math.floor(min(ys) - 1), math.ceil(max(ys) + 1)
    X = lambda v: L + (math.log10(v) - x0) / (x1 - x0) * (W - L - R)  # noqa: E731
    Y = lambda v: T + (y1 - v) / (y1 - y0) * (H - T - Bm)  # noqa: E731
    colors = ["#2a78d6", "#eb6834", "#1baf7a"]  # reference categorical slots 1-3, fixed order
    marks = ["circle", "square", "triangle"]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="system-ui, sans-serif" font-size="12">',
         '<title>Cost-quality frontier on dev: listed $ per 1K messages vs quality points</title>',
         f'<rect width="{W}" height="{H}" fill="#fcfcfb"/>']
    for v in range(int(y0), int(y1) + 1, 2):
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e4e3df" stroke-width="1"/>'
                 f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#52514e">{v}</text>')
    for v in (0.003, 0.005, 0.01, 0.02, 0.03):
        if x0 <= math.log10(v) <= x1:
            o.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{H - Bm}" stroke="#e4e3df" stroke-width="1"/>'
                     f'<text x="{X(v):.1f}" y="{H - Bm + 16}" text-anchor="middle" fill="#52514e">${v}</text>')
    if x0 <= math.log10(0.0095) <= x1:
        o.append(f'<line x1="{X(0.0095):.1f}" x2="{X(0.0095):.1f}" y1="{T}" y2="{H - Bm}" stroke="#52514e" '
                 f'stroke-dasharray="4 4" stroke-width="1"/><text x="{X(0.0095) + 4:.1f}" y="{T + 12}" fill="#52514e">'
                 f'$0.0095 full-points line (scored on hidden cost)</text>')
    o.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" text-anchor="middle" fill="#0b0b0b">dev $ per 1K messages at '
             f'the listed price (log scale; dev = worst case per message)</text>')
    o.append(f'<text transform="translate(16,{(T + H - Bm) / 2}) rotate(-90)" text-anchor="middle" fill="#0b0b0b">'
             f'quality points (of 74)</text>')
    for i, (name, a) in enumerate(arms.items()):
        c, m = colors[i], marks[i]
        for p in a["runs"]:
            x, y = X(p["listed"]), Y(p["q"])
            tip = f'<title>{name} · {p["name"]}: {p["q"]:.1f} points, ${p["listed"]:.4f}/1K</title>'
            if m == "circle":
                o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{c}" stroke="#fcfcfb" stroke-width="2">{tip}</circle>')
            elif m == "square":
                o.append(f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10" rx="2" fill="{c}" stroke="#fcfcfb" '
                         f'stroke-width="2">{tip}</rect>')
            else:
                o.append(f'<path d="M{x:.1f},{y - 6:.1f} L{x + 6:.1f},{y + 5:.1f} L{x - 6:.1f},{y + 5:.1f} Z" fill="{c}" '
                         f'stroke="#fcfcfb" stroke-width="2">{tip}</path>')
        mx, my = X(a["mean_listed"]), Y(a["mean_q"])
        o.append(f'<line x1="{mx - 9:.1f}" x2="{mx + 9:.1f}" y1="{my:.1f}" y2="{my:.1f}" stroke="#0b0b0b" stroke-width="2"/>')
        ly = T + 18 + i * 22
        o.append(f'<text x="{W - R + 14}" y="{ly + 4}" fill="#0b0b0b">{name}</text>')
        if m == "circle":
            o.append(f'<circle cx="{W - R + 6}" cy="{ly}" r="5" fill="{c}"/>')
        elif m == "square":
            o.append(f'<rect x="{W - R + 1}" y="{ly - 5}" width="10" height="10" rx="2" fill="{c}"/>')
        else:
            o.append(f'<path d="M{W - R + 6},{ly - 6} L{W - R + 12},{ly + 5} L{W - R},{ly + 5} Z" fill="{c}"/>')
    o.append(f'<text x="{W - R + 2}" y="{T + 18 + 3 * 22 + 4}" fill="#52514e">— arm mean</text>')
    o.append("</svg>")
    path.write_text("\n".join(o), encoding="utf-8")


def holdout_result() -> str:
    """Score of the blind hold-out if the sheet has been filled in, else 'pending'."""
    import subprocess
    sheet = DEV / "labels" / "holdout_sheet.md"
    if not sheet.exists() or not any(l.strip().lower().startswith("help:") and l.strip()[5:].strip()
                                      for l in sheet.read_text(encoding="utf-8").splitlines()):
        return "pending (10 blind labels requested; sheet not filled in yet)"
    r = subprocess.run([sys.executable, str(ROOT / "eval" / "holdout.py"), "score", str(RUNS / "out_final_bothagree.json")],
                       capture_output=True, text=True)
    return (r.stdout.strip().splitlines() or ["no result"])[-1]


def overfitting_section(data_tots: List[float], anon_total: float) -> List[str]:
    md = ["## 8. Overfitting checks", ""]
    sh = RUNS / "split_half.json"
    rows = json.loads(sh.read_text())["rows"] if sh.exists() else []
    hurts = [r for r in rows if r["flag"]]
    md += [f"- **Name swap:** every person and organisation name replaced, fresh run: {anon_total:.1f}/56 vs "
           f"{data_tots[-1]:.1f} on the original names; the frozen config ranges {min(data_tots):.1f}–{max(data_tots):.1f} "
           "over 4 runs, so the drop is within the noise floor (loops lost most: found 2.8 vs 3.4).",
           "- **Split-half** (dev cut at the midpoint of its 13.7 days; each adopted change undone alone; 3 runs, "
           "zero calls, `eval/split_half.py`): " + ("no change helps in one half and clearly hurts in the other; "
                                                     "every Δ is ≥ 0 in both halves." if rows and not hurts else
                                                     f"flags: {[r['change'] + ' ' + r['component'] for r in hurts]}."
                                                     if rows else "not run."), ""]
    if rows:
        md += ["| change | component | week 1 Δ [95% CI] | week 2 Δ [95% CI] |", "|---|---|---|---|"]
        for r in rows:
            (d1, l1, h1), (d2, l2, h2) = r["halves"]
            md.append(f"| {r['change']} | {r['component']} | {d1:+.2f} [{l1:+.2f}, {h1:+.2f}] | {d2:+.2f} [{l2:+.2f}, {h2:+.2f}] |")
        md += ["", "Week 1 holds only 8 of the 25 help labels, so its help cells are thin. The truncation split is "
               "compared with repair-only (the old re-ask calls were never made in these runs).", ""]
    md += [f"- **Blind help hold-out** (10 open loops outside the 25 labels, chosen without looking at the actions; "
           "**labels suggested by a separate AI from the sheet's context only, blind to the system's actions**, not by "
           f"the user). Frozen help rule (both agree): {holdout_result()}. Before the choice, the ownership rule scored "
           "0.0 (it acted on 6 loops the labels leave) and the 2x rule 3.0. **The hold-out was then used to choose the "
           "help rule** (a rule the user fixed in advance: highest pooled score over all 35 labels, ties within 0.5 to "
           "the more conservative rule): pooled both agree 2.9, 2x 2.6, ownership 1.4; 25 labels 3.2 / 2.4 / 3.2; "
           "hold-out 2.0 / 3.0 / 0.0. **So the hold-out is no longer an independent test** of the frozen config "
           "(changelog 34, 36). Label mixes differ (hold-out 6/10 leave vs 7/25).",
           "- **Variants tried vs adopted (this session):** 22 quality variants tried — 4 prompt/structure ablations "
           "(bursts, delegations, runtime name, organisations call), 2 status thresholds, 2 help rules, 10 precision "
           "settings, 2 consensus set-ups, reasoning medium, gpt-oss-20b, both-agree help; **3 adopted** (f2, truncation "
           "split, both-agree help), plus the provider-routing fields (reliability) and Devanagari/Bengali cue words "
           "(coverage; 2 new dev candidates, not tuned). Earlier sessions chose the v1 prompt "
           "over v2 on a pre-registered slice rule and added the source shift and the ownership help rule.",
           "- **Optimistic dev numbers:** help (the ownership rule's refinements and f2 were chosen on the same 25 "
           "labels, and the help rule itself was chosen using all 35 labels including the hold-out, so no help number "
           "here is independent); goals (3 labels; the candidate "
           "filter and the label sheet used similar keywords); organisations and chats (rules written while looking at "
           "dev's group names and structure); circles slightly (confidence cutoff 0.6 picked on dev). **Possibly "
           "pessimistic:** loop precision (the generated key misses asks that several model set-ups agree on).", ""]
    return md


def main() -> int:
    chats, messages, seed, ans = check.load_input(str(DEV))
    labels = json.loads((DEV / "labels" / "help.json").read_text(encoding="utf-8"))
    ctx = (chats, messages, seed, ans, labels)
    final = load("final_bothagree")  # the final run's replies, decided with the frozen help rule (both agree)
    md: List[str] = ["# Analysis (final frozen config, dev set)", "",
                     "Generated by `eval/analysis.py` from saved outputs and the spend ledger; zero LLM calls. "
                     "Measured = from runs on dev; projected = scaled to the hidden set (marked).", ""]

    # 1. components with bootstrap CIs + run-to-run spread
    comp = boot_components(final, ctx)
    q_final = quality(final, ctx)
    spread = {k: [quality(load(n), ctx) for n in LOW.values()] for k in ["x"]}["x"]
    md += ["## 1. Score by component (final run) with 95% bootstrap CIs (measured)", "",
           "Item CI = resampling the scored items of the final run. Run spread = the frozen config over 4 runs "
           "(run 1, rep2, rep3, final; help re-decided with the adopted rule).", "",
           "| component | points | item CI | run spread (min–max, SD) |", "|---|---|---|---|"]
    keymap = {"circles": "circles", "organisations": "organisations", "chats": "chats", "loops found": "loops_found",
              "loops closed right": "loops_closed", "help (25 hand labels)": "help"}
    for name, (v, lo, hi) in comp.items():
        vals = [s[keymap[name]] for s in spread]
        md.append(f"| {name} | {v:.2f} / {check.POINTS[keymap[name]]} | [{lo:.2f}, {hi:.2f}] | "
                  f"{min(vals):.2f}–{max(vals):.2f}, SD {st.pstdev(vals):.2f} |")
    gvals = [s["goals"] for s in spread]
    md.append(f"| goals (3 hand labels) | {q_final['goals']:.2f} / 8 | too few labels for a CI | "
              f"{min(gvals):.2f}–{max(gvals):.2f}, SD {st.pstdev(gvals):.2f} |")
    tots = [sum(s.values()) for s in spread]
    data_tots = [sum(v for k, v in s.items() if k not in ("help", "goals")) for s in spread]
    md += [f"| **data total (check.py, /56)** | **{sum(v for k, v in q_final.items() if k not in ('help', 'goals')):.2f}** | | "
           f"{min(data_tots):.2f}–{max(data_tots):.2f}, SD {st.pstdev(data_tots):.2f} |",
           f"| **quality incl. help + goals (/74)** | **{sum(q_final.values()):.2f}** | | "
           f"{min(tots):.2f}–{max(tots):.2f}, SD {st.pstdev(tots):.2f} |", "",
           "**Noise floor (a result):** identical config and prompts, fresh calls, give run-to-run swings of "
           f"{max(tots) - min(tots):.1f} quality points across 4 runs; ablation CIs span about ±3 points "
           "(two-level bootstrap). Provider routing and sampling differ per run even at temperature 0. "
           "Any single-run comparison on dev is uninformative below ~3 points.", ""]

    # 2. error profile
    md += ["## 2. Predicted hidden-set error profile by category (final run on dev, no names)", ""]
    md += error_profile(final, ctx)
    md += ["", "Prediction for the hidden set (hand-labelled, 14-day scored window): loops dominate the error. "
           "Most misses are requests from the other person in 1:1 chats the model judges not to be a task, and "
           "done loops we call open (closure not visible in the window; never a false 'done'). Precision is the "
           "weaker side on dev (most of our loops are not in the key), but the key is system-generated and many of "
           "these look like real asks, so hidden precision may be higher. Circles errors concentrate in the "
           "classes in the table above with low %. Help is scored only on found open loops, so loop recall caps it.", ""]

    # 3. decision theory
    md += ["## 3. Decision theory for the status and help thresholds (from check.py's scoring)", "",
           "**Status.** For a matched loop with probability q that it is done (and 1−q open): calling it *done* scores "
           "+1·q − 1·(1−q) = 2q − 1; calling it *open* scores 0·q + 1·(1−q) = 1 − q. Done is better iff 2q − 1 > 1 − q, "
           "i.e. q > 2/3, i.e. **P(done) > 2·P(open)** (dropped handled the same way). Calibration on dev: matched "
           "loops with p_done ≤ 0.3 are done 55% of the time (< 2/3 → open is right); p_done ≥ 0.7 were done 3/3. "
           "Thresholds 1.5 / 1.0 / 0.5 changed nothing in 9 runs because p_done is bimodal.", "",
           "**Help.** On a labelled open loop: the right action scores +1; assist/remind where the label says leave "
           "scores −1; leave where the label says act scores 0. Choosing between an action and leave, with a = P(label is "
           "that action) and 1 − a = P(label is leave): acting scores a − (1 − a) = 2a − 1, leaving scores 1 − a. Act iff "
           "2a − 1 > 1 − a, i.e. a > 2/3, i.e. **P(act) > 2·P(leave)** — the same asymmetry as status. The organisers' 'whose move is it' rule implements this structurally: someone "
           "else's move (including every followup, rule f2) → leave unless overdue; the user's move → remind, or assist "
           "when every safety gate passes. The frozen rule is **both agree**: act only when that ownership rule and the "
           "explicit probability rule (P(action) > 2·P(leave)) give the same action, otherwise leave — every change "
           "moves actions toward leave, which can never cost −1. Leave reasons on the final run: someone else's to "
           "handle 41, a reminder would likely annoy 19, too personal 1.", ""]

    # 4. paired bootstrap final vs run 1
    def items(out):
        ps, key = pairs(out["open_loops"], ans, messages)
        f, c = [0] * len(key), [0] * len(key)
        for l, j in ps:
            f[j] = 1
            c[j] = 1 if l["status"] == key[j]["status"] else (-1 if key[j]["status"] == "open" else 0)
        return f, c, len(out["open_loops"]), per_label_help(out, labels, ans)
    a_, b_ = items(load("run1")), items(final)
    rng = random.Random(9)
    nk, nh = len(a_[0]), len(a_[3])

    def tot(it, k, h):
        return 14 * 2 * sum(it[0][i] for i in k) / (it[2] + nk) + 14 * max(0, sum(it[1][i] for i in k) / nk) + \
            10 * max(0, sum(it[3][i] for i in h) / nh)
    full_k, full_h = list(range(nk)), list(range(nh))
    diffs = []
    for _ in range(B):
        k = [rng.randrange(nk) for _ in range(nk)]
        h = [rng.randrange(nh) for _ in range(nh)]
        diffs.append(tot(b_, k, h) - tot(a_, k, h))
    diffs.sort()
    d = tot(b_, full_k, full_h) - tot(a_, full_k, full_h)
    md += ["## 4. Final config vs run 1 (paired bootstrap over key loops and help labels)", "",
           f"Loops found + closed + help: run 1 {tot(a_, full_k, full_h):.2f}, final {tot(b_, full_k, full_h):.2f}, "
           f"Δ {d:+.2f} [95% CI {diffs[int(.025 * B)]:+.2f}, {diffs[int(.975 * B)]:+.2f}] (measured; items paired, "
           "single runs, so run-to-run noise is NOT in this CI; with it, see the ±3-point noise floor). Differences: "
           "truncation split instead of re-ask, help rule f2.", ""]

    # 5. generalisation
    a_chats, a_msgs, a_seed, a_ans = check.load_input(str(ROOT / "data" / "dev_anon"))
    s_anon = check.score(json.loads((RUNS / "out_final_anon.json").read_text()), a_ans, a_seed, a_msgs)
    anon_total = sum((s_anon.get(k) or 0) * v for k, v in check.POINTS.items() if k not in ("help", "goals"))
    md += ["## 5. Generalisation", "",
           "| test | result |", "|---|---|",
           f"| name swap (every person/org name replaced, fresh run, frozen config) | data total {anon_total:.2f} vs "
           f"{data_tots[-1]:.2f} on the original names (dev range of the frozen config {min(data_tots):.2f}–"
           f"{max(data_tots):.2f}): inside the noise floor |",
           "| leak check (no dev name, title or number in code, prompts, run.sh, llm.py) | OK for the pipeline |",
           "| runtime name (reading the user's display name from ME's sender_name) | tested as ablation c: Δ −0.11 "
           "[−3.33, +3.19]; not used, so the pipeline needs no name at all |",
           "| Python 3.9 (docker python:3.9-slim) | LLM off: valid, 12.5; full LLM path replayed offline "
           "(--network none): valid and byte-identical to the final run |", ""]

    # 6. cost
    arms_data = {}
    for arm, (outs, ledgers, price) in ARMS.items():
        runs = []
        for o_name, led in zip(outs, ledgers):
            actual, listed, tin, tout, by = cost_of(led, price)
            q = sum(quality(load(o_name), ctx).values())
            runs.append({"name": o_name, "actual": actual / N_DEV * 1000, "listed": listed / N_DEV * 1000, "q": q,
                         "by": by, "tin": tin, "tout": tout})
        arms_data[arm] = {"runs": runs, "mean_listed": st.mean(r["listed"] for r in runs),
                          "mean_q": st.mean(r["q"] for r in runs)}
    fa, fl, ftin, ftout, fby = cost_of(["final"], (P_IN, P_OUT))
    fact = {r["stage"]: 0.0 for r in read(RUNS / "spend.jsonl") if r.get("run") == "final"}
    for r in read(RUNS / "spend.jsonl"):
        if r.get("run") == "final" and not r.get("error"):
            fact[r["stage"]] += r["cost"] or 0
    md += ["## 6. Cost story", "",
           f"**Final run (measured):** {ftin:,} input + {ftout:,} output tokens; actual ${fa:.4f} as routed "
           f"(= ${fa / N_DEV * 1000:.4f} per 1K messages); at the listed price ($0.15 / $0.60 per M) ${fl:.4f} = "
           f"**${fl / N_DEV * 1000:.4f} per 1K** (baseline $0.038).", "",
           "| stage | actual $ (routed) | listed $ | share |", "|---|---|---|---|"]
    for k in ("loops", "circles", "goals"):
        md.append(f"| {k} | {fact.get(k, 0):.5f} | {fby.get(k, 0):.5f} | {100 * fby.get(k, 0) / fl:.0f}% |")
    md += ["", "**Hidden-set sensitivity (projected).** Loops run only on the last 16 days, so the hidden cost per 1K "
           "depends on how many of the ~17K messages fall there (circles + goals assumed 2x dev for more people):", "",
           "| messages in last 16 days | 2,000 | 4,500 | 6,500 | 8,000 | 10,000 | 17,000 (all) |", "|---|---|---|---|---|---|---|"]
    row = "| $ per 1K at listed price |"
    for w in (2000, 4500, 6500, 8000, 10000, 17000):
        row += f" {hidden_cost(fby, w):.4f} |"
    be = next(w for w in range(1000, 17001, 100) if hidden_cost(fby, w) > 0.0095)
    md += [row, "", f"Under $0.0095 (full cost points) up to ~{be - 100:,} messages in the last 16 days "
           f"({100 * (be - 100) // 17000}% of the set). **Saving from the 16-day window:** "
           f"extracting loops over all 17K messages would cost {hidden_cost(fby, 17000):.4f} vs "
           f"{hidden_cost(fby, 6500):.4f} per 1K at an even 6.5K, i.e. {hidden_cost(fby, 17000) / hidden_cost(fby, 6500):.1f}x.", ""]
    ev = read(RUNS / "spend.jsonl")
    total_spend = sum(r.get("cost") or 0 for r in ev)
    md += ["**Zero-spend toolkit.** Every paid run saves its replies (cache keyed by prompt, with finish reasons) and "
           "its ledger line per call (cost, provider, prompt hash). `eval/replay.py` rebuilds a run exactly (verified "
           "byte-identical on 5 runs); on top of it: decide-rule ablations (`ablate_decide.py`: 6 variants × 9 runs), "
           "the precision sweep (11 settings × 9 runs), consensus (84 triples), per-provider quality, the "
           "pre-registered decision (`decide_medium.py`) and this analysis. That is ~150 run-equivalents evaluated "
           f"for $0, versus ~$4 at $0.028 per fresh run. Total dev spend for the whole session: ${total_spend:.4f} "
           "(ledger; equals the key's own usage within $0.001).", ""]

    md += ["## 7. Cost-quality frontier (measured on dev; hidden projected)", "",
           "![Pareto](pareto.svg)", "",
           "| config | runs | quality /74 (mean, range) | dev $/1K listed | dev $/1K actual | projected hidden $/1K "
           "(6.5K window) | expected points (quality + cost) |", "|---|---|---|---|---|---|---|"]
    for arm, a in arms_data.items():
        qs = [r["q"] for r in a["runs"]]
        by = defaultdict(float)
        for r in a["runs"]:
            for k, v in r["by"].items():
                by[k] += v / len(a["runs"])
        hc = hidden_cost(by, 6500)
        md.append(f"| {arm} | {len(qs)} | {st.mean(qs):.2f} ({min(qs):.1f}–{max(qs):.1f}) | {a['mean_listed']:.4f} | "
                  f"{st.mean(r['actual'] for r in a['runs']):.4f} | {hc:.4f} | {points_formula(st.mean(qs), hc):.2f} |")
    md += ["", "Expected points = quality + 12 × f(cost) × quality / 74 (organisers' formula; f = 1 at ≤ $0.0095, 0 at "
           "≥ $0.152, log in between), using the projected hidden cost (6.5K window messages). Low and 20b earn full "
           "cost points there; medium ($0.0130) keeps ~89% of them. **On the point estimate medium has the most expected "
           "points**, but its edge is within the noise floor and partly comes from goals (3 hand labels, run SD ~1): on "
           "the pre-registered loops+help rule it gained +0.91 (< +1.0 required) with 3.5% truncated calls (> 2%), so "
           "the rule chose **120b low** (frozen). 20b is cheaper but lower on every loop component.", ""]
    md += overfitting_section(data_tots, anon_total)
    (RUNS / "analysis.md").write_text("\n".join(md), encoding="utf-8")
    svg_pareto(arms_data, RUNS / "pareto.svg")
    print("wrote eval/runs/analysis.md and eval/runs/pareto.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
