"""Goals: keyword-filtered ME messages -> ONE LLM call -> at most 8 goals with verified evidence ids."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .ingest import Data
from .llm_client import LLM
from .candidates import NOUN_STOP
from .util import log, tokens

IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM = (Path(__file__).parent / "prompts" / "goals_system.txt").read_text(encoding="utf-8")
MAX_CANDIDATES = 60
MAX_GOALS = 8
MIN_EVIDENCE, MAX_EVIDENCE = 1, 5  # the prompt asks for 2-5; gpt-oss often cites one, which is still verified evidence
RELATED_MAX = 4  # extra evidence: ME's own candidate messages clearly about the same goal...
RELATED_MIN_SHARED = 2  # ...sharing at least this many content words with the goal's title and cited messages
AREAS = {"self", "spouse", "kids", "parents", "work", "friends", "experiences", "finances", "health", "home"}
TYPES = {"habit", "milestone", "experience"}
HORIZONS = {"3mo", "1yr", "3yr", "5yr"}

STRONG = re.compile(
    r"(?<![\w])(goals?|target|plan(?:ning|ned|s)?|aim|want to|wanna|hope to|dream|this year|next year|by (?:dec|jan|"
    r"march|june|end of)\w*|in \d+ (?:months|years)|resolution|will start|start(?:ing)? to|habit|every (?:day|morning|"
    r"week)|marathon|gym|workout|weight|diet|yoga|meditat\w*|learn\w*|course|launch\w*|raise|fundrais\w*|hir(?:e|ing)|"
    r"scale|revenue|profit|arr|mrr|invest\w*|savings?|retire\w*|renovat\w*|trip|travel|vacation|holiday|admission|"
    r"build(?:ing)?|grow(?:th)?|expand\w*|quarter|obsessed|focus(?:ing)? on|priority|long term)(?![\w])",
    re.IGNORECASE,
)
WEAK = re.compile(r"(?<![\w])(should|need to|going to|will|next|soon|kids?|school|health|money|team|product|"
                  r"business|company|startup|family)(?![\w])", re.IGNORECASE)
PLAN_SHAPE = re.compile(r"(?<![\w])(i|we|i'm|i am|we're|we are|my|our|let's|lets)(?![\w])", re.IGNORECASE)
URL = re.compile(r"https?://\S+")


def candidates(data: Data) -> List[dict]:
    """ME's own text messages that sound like plans or aims, best first, forwards de-duplicated."""
    scored: List[Tuple[float, dict]] = []
    for m in data.messages:
        if m["sender_id"] != "ME" or m.get("kind") != "text":
            continue
        body = URL.sub("", m["text"]).strip()
        if len(body) < 25:
            continue
        strong = {h.lower() for h in STRONG.findall(body)}
        if not strong:
            continue
        weak = {h.lower() for h in WEAK.findall(body)}
        score = 2 * len(strong) + len(weak) + (1 if PLAN_SHAPE.search(body) else 0) + min(len(body), 300) / 300
        scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, m in scored:
        key = m["text"].lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
        if len(out) >= MAX_CANDIDATES:
            break
    return sorted(out, key=lambda m: m["ts"])


def build_prompt(data: Data, msgs: List[dict]) -> Tuple[str, Dict[str, str]]:
    local: Dict[str, str] = {}
    lines = []
    for k, m in enumerate(msgs, 1):
        local[f"g{k}"] = m["id"]
        c = data.chats[m["chat_id"]]
        where = f'group "{c.get("name") or ""}"' if c["is_group"] else "1:1 with a contact"
        when = datetime.fromtimestamp(m["ts"], IST).strftime("%a %d %b")
        lines.append(f"g{k} {when} | {where} | {m['text'][:400]}")
    return "\n".join(lines), local


def _words(text: str) -> set:
    return {t for t in tokens(text) if len(t) >= 4 and not t.isdigit() and t not in NOUN_STOP}


def related(goal_title: str, ev: List[str], pool: List[str], data: Data) -> List[str]:
    """Up to RELATED_MAX of ME's other candidate messages that share >= RELATED_MIN_SHARED content words with the
    goal (title + cited messages). Deterministic; ids come from the input, so they always exist."""
    words = _words(goal_title).union(*(_words(data.by_id[m]["text"]) for m in ev))
    scored = []
    for mid in pool:
        if mid in ev or data.by_id[mid]["sender_id"] != "ME":
            continue
        n = len(_words(data.by_id[mid]["text"]) & words)
        if n >= RELATED_MIN_SHARED:
            scored.append((-n, data.by_id[mid]["ts"], mid))
    return [mid for _, _, mid in sorted(scored)[:RELATED_MAX]]


def parse(reply: object, local: Dict[str, str], data: Data) -> List[dict]:
    if not isinstance(reply, dict) or not isinstance(reply.get("goals"), list):
        return []
    out = []
    for g in reply["goals"]:
        if not isinstance(g, dict):
            continue
        ev = []
        for x in g.get("evidence") or []:
            mid = local.get(str(x))
            if mid and mid not in ev and data.by_id[mid]["sender_id"] == "ME":  # verified: real, ME's own
                ev.append(mid)
        area, typ, hor = str(g.get("area", "")).lower(), str(g.get("type", "")).lower(), str(g.get("horizon", "")).lower()
        if len(ev) < MIN_EVIDENCE or area not in AREAS or typ not in TYPES or hor not in HORIZONS:
            continue
        title = str(g.get("title") or "").strip()[:120] or "Goal"
        ev = ev[:MAX_EVIDENCE]
        ev += related(title, ev, list(local.values()), data)[: MAX_EVIDENCE - len(ev)]
        out.append({"title": title, "area": area, "type": typ, "horizon": hor, "evidence": ev})
        if len(out) >= MAX_GOALS:
            break
    return out


def extract(data: Data, client: LLM) -> List[dict]:
    msgs = candidates(data)
    if not msgs:
        return []
    prompt, local = build_prompt(data, msgs)
    with client.stage("goals"):
        reply = client.ask_json(prompt, system=SYSTEM)
    goals = parse(reply, local, data)
    log(f"goals: {len(msgs)} candidate messages -> {len(goals)} goals")
    return goals
