"""Loop candidates (rules, high recall) and the LLM windows around them.

Only messages in the loop window (last 16 days) can be candidates; earlier ones are context only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern, Set

from . import config
from . import lexicons as lx
from .ingest import Data
from .util import tokens

MEDIA_ONLY = re.compile(r"^\s*(?:\[(?:IMG|DOC|VOICE)\]\s*)?(?:\[?(?:photo|image|video|sticker|gif|audio|document)\]?|"
                        r"<media omitted>|this message was deleted)?\s*$", re.IGNORECASE)
NOUN_STOP = {"this", "that", "with", "have", "will", "from", "your", "what", "when", "there", "they", "them", "then",
             "also", "just", "please", "pls", "okay", "thanks", "thank", "sure", "done", "today", "tomorrow", "should",
             "could", "would", "about", "need", "want", "know", "send", "call", "tell", "here", "where", "which",
             "been", "were", "only", "some", "more", "very", "much", "good", "fine", "kar", "karo", "hai", "hain"}


@dataclass
class Window:
    wid: str
    chat_id: str
    msg_ids: List[str]  # in chat order
    candidates: List[str]  # candidate msg ids inside it
    reasons: Dict[str, List[str]] = field(default_factory=dict)


def is_media_only(m: dict) -> bool:
    body = m["text"].replace("[IMG] ", "").replace("[DOC] ", "").replace("[VOICE] ", "")
    return bool(MEDIA_ONLY.match(body))


def reasons_for(m: dict, me_pattern: Optional[Pattern[str]] = None) -> List[str]:
    """Why a message could raise a loop. Empty = not a candidate."""
    text = m["text"]
    body = text.replace("[IMG] ", "").replace("[DOC] ", "").replace("[VOICE] ", "")
    if MEDIA_ONLY.match(body) or len(body) < 2:
        return []
    r = []
    if me_pattern is not None and m["sender_id"] != "ME" and me_pattern.search(body):
        r.append("addressed")  # someone uses the user's name: likely talking to them
    if lx.REQUEST.search(body) or lx.REQUEST_INDIC.search(body):
        r.append("request")
    if lx.IMPERATIVE.search(body):
        r.append("imperative")
    if "?" in body or lx.QUESTION_INDIC.search(body):
        r.append("question")
    if lx.COMMIT.search(body) or lx.COMMIT_INDIC.search(body):
        r.append("commit")
    if lx.QUOTE.search(body):
        r.append("quote")
    if lx.TASK_LIST.search(m.get("body") or ""):
        r.append("tasks")
    if lx.TIME_REF.search(body):
        r.append("time")
    if lx.MONEY.search(body):
        r.append("money")
    # Time or money alone is weak; keep it only with another cue or in a short, direct message.
    strong = [x for x in r if x not in ("time", "money")]
    if not strong and not (r and len(body) <= 80):
        return []
    return r


def find(data: Data) -> Dict[str, List[str]]:
    """msg id -> reasons, for messages inside the loop window."""
    out: Dict[str, List[str]] = {}
    for m in data.messages:
        if m["ts"] < data.loop_from:
            continue
        r = reasons_for(m, data.me_pattern if config.LOOPS_USE_ME_NAME else None)
        if r:
            out[m["id"]] = r
    return out


def _nouns(text: str) -> Set[str]:
    return {t for t in tokens(text) if len(t) >= 4 and not t.isdigit() and t not in NOUN_STOP}


def windows(data: Data, cands: Dict[str, List[str]]) -> List[Window]:
    """Group nearby candidates per chat: context before, forward slice after, plus later same-topic messages."""
    out: List[Window] = []
    n = 0
    for cid, msgs in sorted(data.by_chat.items()):
        idx = [i for i, m in enumerate(msgs) if m["id"] in cands]
        if not idx:
            continue
        groups: List[List[int]] = [[idx[0]]]
        capped: Set[int] = set()  # group k was cut only by the candidate cap: k+1 continues the same stretch
        for i in idx[1:]:
            # Join while the next candidate is close and the window stays bounded.
            near = i - groups[-1][-1] <= config.WINDOW_JOIN_GAP and i - groups[-1][0] <= config.WINDOW_MAX_SPAN
            if near and len(groups[-1]) < config.WINDOW_MAX_CANDIDATES:
                groups[-1].append(i)
            else:
                if near:
                    capped.add(len(groups) - 1)
                groups.append([i])
        for k, g in enumerate(groups):
            # Pieces of one busy stretch share less context; the stretch's last piece keeps the full forward slice.
            before = config.WINDOW_SPLIT_BEFORE if (k - 1) in capped else config.WINDOW_BEFORE
            after = config.WINDOW_SPLIT_AFTER if k in capped else config.WINDOW_AFTER
            lo = max(0, g[0] - before)
            hi = min(len(msgs), g[-1] + 1 + after)
            ids = [m["id"] for m in msgs[lo:hi]]
            # Later messages on the same topic (closures often come days later).
            topic = set().union(*(_nouns(msgs[i]["text"]) for i in g))
            extra = []
            if topic:
                for m in msgs[hi:]:
                    if len(_nouns(m["text"]) & topic) >= 1:
                        extra.append(m["id"])
                        if len(extra) >= config.WINDOW_TOPIC_EXTRA:
                            break
            n += 1
            out.append(Window(
                wid=f"w{n}",
                chat_id=cid,
                msg_ids=ids + extra,
                candidates=[msgs[i]["id"] for i in g],
                reasons={msgs[i]["id"]: cands[msgs[i]["id"]] for i in g},
            ))
    return out
