"""Build the output object, drop dangling references, validate with check.validate()."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check  # noqa: E402  (only validate() is used; nothing here reads answers)

KEYS = ("people", "organisations", "chats", "goals", "open_loops")


def skeleton(chats: List[dict]) -> dict:
    """Valid with no messages at all: everyone a friend, every chat tied, groups to Home."""
    people = sorted({p for c in chats for p in c.get("participants", [])} - {"ME"})
    ties = []
    for c in chats:
        rest = [p for p in c.get("participants", []) if p != "ME"]
        if c.get("is_group") or len(rest) != 1:
            ties.append({"chat_id": c["chat_id"], "organisation": "home"})
        else:
            ties.append({"chat_id": c["chat_id"], "person": rest[0]})
    return {
        "people": [{"id": p, "circle": "friend"} for p in people],
        "organisations": [{"id": "home", "name": "Home", "type": "residence", "side": "home", "role": "owner"}],
        "chats": ties,
        "goals": [],
        "open_loops": [],
    }


def clean(out: dict, msg_ids: set) -> dict:
    """Drop references that would fail validation instead of failing the whole output."""
    org_ids = {o["id"] for o in out["organisations"]}
    people_ids = {p["id"] for p in out["people"]}
    for p in out["people"]:
        if "links" in p:
            p["links"] = [l for l in p["links"] if l.get("organisation") in org_ids]
            if not p["links"]:
                del p["links"]
        if "looks_after" in p:
            p["looks_after"] = [x for x in p["looks_after"] if x in people_ids and x != p["id"]]
            if not p["looks_after"]:
                del p["looks_after"]
    goals = []
    for g in out["goals"]:
        g["evidence"] = [m for m in g.get("evidence", []) if m in msg_ids]
        if g["evidence"]:
            goals.append(g)
    out["goals"] = goals[:10]
    out["open_loops"] = [l for l in out["open_loops"] if l.get("source_msg") in msg_ids]
    return out


def build(parts: Dict[str, list], msg_ids: set) -> dict:
    return clean({k: list(parts.get(k) or []) for k in KEYS}, msg_ids)


def validate(out: dict, chats: List[dict], messages: List[dict]) -> List[str]:
    return check.validate(out, chats, messages)
