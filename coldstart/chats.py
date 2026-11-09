"""Chat ties: every chat exactly once. Groups -> organisation, 1:1 -> the other person."""
from __future__ import annotations

from typing import List

from .ingest import Data
from .orgs import Orgs


def build(data: Data, orgs: Orgs) -> List[dict]:
    ties = []
    for cid, c in sorted(data.chats.items()):
        other = data.other(cid)
        if c["is_group"] or other is None:
            ties.append({"chat_id": cid, "organisation": orgs.group_org.get(cid, "home")})
        else:
            ties.append({"chat_id": cid, "person": other})
    return ties
