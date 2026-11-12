"""Turn raw loops (with LLM probabilities) into scored decisions, using the asymmetric costs in check.py.

status: done/dropped only if p > STATUS_RATIO * p_open (a false "done" costs 1, a false "open" costs 0).
help (open loops only) = "both agree": act only when two rules give the same action, else leave with a reason
("someone else's to handle" / "too personal" / "a reminder would likely annoy"). Rule 1, ownership_help, asks whose
move it is (organisers' guidance); rule 2, ratio_help, acts only if P(action) > HELP_RATIO * P(leave). Rule 1:
- someone else's move (they owe it / it is their own plan): leave, unless the user is waiting on it (followup)
  and it is overdue -> remind;
- the user's move (a promise, a decision, an ask to the user): remind, or assist if the assistant can move it
  forward itself (every safety gate passes and there is a concrete next step).
A question or followup the user sent waits on the other person, so it counts as their move.
overdue: the due date (stated or clearly implied) is before today, IST; a loop with no due date is never overdue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import config
from .ingest import Data

IST = timezone(timedelta(hours=5, minutes=30))
LEAVE_THEIRS = "someone else's to handle"
LEAVE_PERSONAL = "too personal"
LEAVE_ANNOY = "a reminder would likely annoy"


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
    if l["private"] and config.HELP_PRIVATE_GATE:
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


def overdue(l: dict, now_ts: int) -> bool:
    """Its due date (stated or clearly implied, resolved by the model) is before today, IST. No due date: not overdue."""
    try:
        return bool(l.get("due")) and (
            datetime.strptime(l["due"], "%Y-%m-%d").date() < datetime.fromtimestamp(now_ts, IST).date())
    except ValueError:
        return False


def ownership_help(l: dict, now_ts: int, src_by_me: bool = False) -> dict:
    """The organisers' "whose move is it" rule."""
    # A question or followup the user sent is waiting on the other person's answer: their move.
    waiting = l["type"] == "followup" or (src_by_me and l["type"] == "question")
    theirs = l["owner"] != "ME" or (src_by_me and waiting) or (config.HELP_FOLLOWUP_THEIRS and l["type"] == "followup")
    if theirs:  # someone else's move
        if waiting and overdue(l, now_ts):
            return {"action": "remind", "remind_at": remind_at(l, now_ts)}
        return {"action": "leave", "reason": LEAVE_THEIRS}
    if not assist_gates(l):  # the assistant can move it forward itself (safe, with a concrete next step)
        return {"action": "assist", "next_step": l["next"], "needs_yes": True}
    return {"action": "remind", "remind_at": remind_at(l, now_ts)}


def ratio_help(l: dict, now_ts: int) -> dict:
    """The earlier probability rule: act only if P(action) > HELP_RATIO * P(leave) (assist also needs every gate)."""
    if not assist_gates(l) and l["p_assist"] > config.HELP_RATIO * l["p_leave"]:
        return {"action": "assist", "next_step": l["next"], "needs_yes": True}
    if l["owner"] == "ME" and l["needs_me_only"] and l["p_remind"] > config.HELP_RATIO * l["p_leave"]:
        return {"action": "remind", "remind_at": remind_at(l, now_ts)}
    return {"action": "leave", "reason": "little value in stepping in"}


def help_for(l: dict, now_ts: int, src_by_me: bool = False) -> dict:
    """Both agree (changelog 36): act only when the ownership rule and the probability rule give the same action;
    otherwise leave, saying why: someone else's to handle / too personal / a reminder would likely annoy."""
    own = ownership_help(l, now_ts, src_by_me)
    if config.HELP_RULE == "ownership" or own["action"] == "leave":
        return own
    if ratio_help(l, now_ts)["action"] == own["action"]:
        return own
    return {"action": "leave", "reason": LEAVE_PERSONAL if l["sensitive"] else LEAVE_ANNOY}


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
            src = data.by_id.get(l["source_msg"])
            rec["help"] = help_for(l, data.now, bool(src) and src["sender_id"] == "ME")
        out.append(rec)
    return out
