"""Turn raw loops (with LLM probabilities) into scored decisions, using the asymmetric costs in check.py.

status: done/dropped only if p > STATUS_RATIO * p_open (a false "done" costs 1, a false "open" costs 0).
help (open loops only): assist only if every safety gate passes and p_assist > HELP_RATIO * p_leave;
remind if only the user can act and p_remind > HELP_RATIO * p_leave; otherwise leave, with a reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import config
from .ingest import Data

IST = timezone(timedelta(hours=5, minutes=30))


def status(l: dict) -> str:
    if l["p_done"] >= l["p_dropped"] and l["p_done"] > config.STATUS_RATIO * l["p_open"]:
        return "done"
    if l["p_dropped"] > l["p_done"] and l["p_dropped"] > config.STATUS_RATIO * l["p_open"]:
        return "dropped"
    return "open"


def assist_gates(l: dict) -> List[str]:
    """Reasons assist is unsafe. Empty = every gate passes."""
    fails = []
    if l["owner"] != "ME" or not l["owner_is_me"]:
        fails.append("someone else owns it")
    if l["private"]:
        fails.append("private matter")
    if not l["reversible"]:
        fails.append("not easily undone")
    if l["sensitive"]:
        fails.append("sensitive")
    if l["needs_me_only"]:
        fails.append("only the user can do it")
    if not l.get("next"):
        fails.append("no concrete next step")
    return fails


def remind_at(l: dict, now_ts: int) -> str:
    """Due date at REMIND_HOUR IST if it's still ahead; otherwise the next morning at REMIND_HOUR."""
    now = datetime.fromtimestamp(now_ts, IST)
    at: Optional[datetime] = None
    if l.get("due"):
        d = datetime.strptime(l["due"], "%Y-%m-%d").replace(hour=config.REMIND_HOUR, tzinfo=IST)
        if d > now:
            at = d
    if at is None:
        at = (now + timedelta(days=1)).replace(hour=config.REMIND_HOUR, minute=0, second=0, microsecond=0)
    return at.isoformat()


def help_for(l: dict, now_ts: int) -> dict:
    gates = assist_gates(l)
    if not gates and l["p_assist"] > config.HELP_RATIO * l["p_leave"]:
        return {"action": "assist", "next_step": l["next"], "needs_yes": True}
    only_user = l["owner"] == "ME" and l["needs_me_only"]
    if only_user and l["p_remind"] > config.HELP_RATIO * l["p_leave"]:
        return {"action": "remind", "remind_at": remind_at(l, now_ts)}
    reason = "; ".join(g for g in gates if g != "no concrete next step") or "little value in stepping in"
    return {"action": "leave", "reason": reason}


def decide(raw: List[dict], data: Data) -> List[dict]:
    """Raw loops -> output-shaped open_loops."""
    out = []
    for l in raw:
        st = status(l)
        rec = {
            "title": l["title"],
            "type": l["type"],
            "owner": l["owner"],
            "chat_id": l["chat_id"],
            "source_msg": l["source_msg"],
            "due": l["due"],
            "status": st,
            "closed_by": l["close"] if st != "open" else None,
        }
        if st == "open":
            rec["help"] = help_for(l, data.now)
        out.append(rec)
    return out
