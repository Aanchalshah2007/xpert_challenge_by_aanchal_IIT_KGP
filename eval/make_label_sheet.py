#!/usr/bin/env python3
"""Write a hand-labelling sheet for help actions and goals: <data>/labels/to_label.md

    python3 eval/make_label_sheet.py data/dev [--goals 20]

Help: every OPEN loop in the dev key, with ±3 messages of context and a blank help field.
Goals: the user's (ME's) own messages that sound like plans or aims (keyword search, no LLM).
Fill it in, then run eval/labels_from_sheet.py to produce labels/help.json and labels/goals.json.
Lives in eval/ because it reads the answer key; the pipeline never does.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
CONTEXT = 3
STRONG_CUES = re.compile(
    r"(?<![\w])(goals?|target|plan(?:ning|ned)?|aim|want to|wanna|hope to|dream|this year|next year|by (?:dec\w*|jan\w*|"
    r"march|june|end of \w+)|in \d+ (?:months|years)|resolution|will start|start(?:ing)? to|habit|every (?:day|morning|week)|"
    r"marathon|gym|workout|weight|diet|yoga|meditat\w*|learn\w*|course|launch|raise|fundrais\w*|hire|hiring|scale|"
    r"revenue|profit|arr|mrr|invest\w*|savings?|retire\w*|renovat\w*|trip|travel|vacation|holiday|admission|"
    r"anniversary|family time|build(?:ing)?|grow(?:th)?|expand\w*|quarter|q[1-4])(?![\w])",
    re.IGNORECASE,
)
WEAK_CUES = re.compile(
    r"(?<![\w])(should|need to|going to|will|next|soon|weekend|kids?|son|daughter|school|parents|wife|house|farm|"
    r"health|run|book|write|money)(?![\w])",
    re.IGNORECASE,
)
PLAN_SHAPE = re.compile(r"(?<![\w])(i|we|i'm|i am|we're|we are|my|our|let's|lets)(?![\w])", re.IGNORECASE)


def _when(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%a %d %b %H:%M")


def _line(m: dict, names: Dict[str, str], mark: bool = False) -> str:
    who = "ME" if m["sender_id"] == "ME" else (names.get(m["sender_id"]) or m["sender_id"])
    body = " ".join((m.get("body") or "").split())[:300]
    arrow = ">>" if mark else "  "
    return f"    {arrow} {m['id']} {_when(m['ts'])} {who}: {body}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--goals", type=int, default=20)
    args = ap.parse_args()
    chats, msgs, _, answers = check.load_input(args.data)
    chat_name = {c["chat_id"]: c.get("name") or c["chat_id"] for c in chats}
    names = {m["sender_id"]: m.get("sender_name") or "" for m in msgs}
    by_chat: Dict[str, List[dict]] = {}
    for m in sorted(msgs, key=lambda m: (m["ts"], m["id"])):
        by_chat.setdefault(m["chat_id"], []).append(m)
    pos = {m["id"]: (m["chat_id"], i) for cid, ms in by_chat.items() for i, m in enumerate(ms)}
    by_id = {m["id"]: m for m in msgs}

    out: List[str] = [
        "# Labelling sheet (gitignored, contains data)",
        "",
        "Fill the blank fields, then: `python3 eval/labels_from_sheet.py " + args.data + "`",
        "",
        "## Help (open loops from the dev key)",
        "",
        "help: one of `assist` (I can take the next step for the user (ME)), `remind` (only the user (ME) can act; nudge them),",
        "`leave` (stay out: private, sensitive, or nothing useful to do). `why`: a few words. Blank = skipped.",
        "",
    ]
    open_loops = [a for a in (answers or {}).get("open_loops", []) if a["status"] == "open"]
    for n, a in enumerate(open_loops, 1):
        src = min(a["source_msgs"], key=lambda x: by_id[x]["ts"])
        cid, i = pos[src]
        ctx = by_chat[cid][max(0, i - CONTEXT): i + CONTEXT + 1]
        out += [
            f"### H{n:02d}",
            f"- source_msg: {src}",
            f"- chat: {chat_name[cid]} ({cid})",
            f"- title: {a['title']}",
            f"- owner: {a['owner']} | type: {a['type']} | due: {a.get('due')}",
            "- context:",
            *[_line(m, names, m["id"] == src) for m in ctx],
            "- help: ",
            "- why: ",
            "",
        ]

    out += [
        "## Goals (the user's (ME's) own messages that sound like plans or aims)",
        "",
        "keep: `y` / `n`. area: self, spouse, kids, parents, work, friends, experiences, finances, health, home.",
        "type: habit, milestone, experience. horizon: 3mo, 1yr, 3yr, 5yr.",
        "group: same short tag on several candidates = one goal with all their ids as evidence (optional).",
        "title: required on the first candidate of each kept goal/group.",
        "",
    ]
    cands = []
    for m in msgs:
        if m["sender_id"] != "ME" or m.get("kind") != "text":
            continue
        body = " ".join((m.get("body") or "").split())
        strong = {h.lower() for h in STRONG_CUES.findall(body)}
        weak = {h.lower() for h in WEAK_CUES.findall(body)}
        if not strong or len(body) < 25 or "http" in body:
            continue
        score = 2 * len(strong) + len(weak) + (1 if PLAN_SHAPE.search(body) else 0) + min(len(body), 300) / 300
        cands.append((score, m, body))
    cands.sort(key=lambda x: -x[0])
    seen_chats: Dict[str, int] = {}
    picked = []
    seen_bodies = set()
    for score, m, body in cands:  # at most 3 per chat, no repeated forwards, for variety
        if seen_chats.get(m["chat_id"], 0) >= 3 or body.lower() in seen_bodies:
            continue
        seen_bodies.add(body.lower())
        seen_chats[m["chat_id"]] = seen_chats.get(m["chat_id"], 0) + 1
        picked.append((m, body))
        if len(picked) >= args.goals:
            break
    for n, (m, body) in enumerate(sorted(picked, key=lambda x: x[0]["ts"]), 1):
        out += [
            f"### G{n:02d}",
            f"- msg: {m['id']}",
            f"- chat: {chat_name[m['chat_id']]} ({m['chat_id']}) | {_when(m['ts'])}",
            f"- text: {body[:500]}",
            "- keep: ",
            "- area: ",
            "- type: ",
            "- horizon: ",
            "- group: ",
            "- title: ",
            "",
        ]
    dst = Path(args.data) / "labels" / "to_label.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {dst}: {len(open_loops)} open loops, {len(picked)} goal candidates (of {len(cands)} keyword hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
