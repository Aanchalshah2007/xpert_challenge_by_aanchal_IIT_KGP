"""Deterministic per-chat and per-person features. No LLM."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import lexicons as lx
from .ingest import Data
from .util import tokens

CUE_SETS = {
    "staff": lx.STAFF_CUES,
    "vendor": lx.VENDOR_CUES,
    "client": lx.CLIENT_CUES,
    "colleague": lx.COLLEAGUE_CUES,
    "friend": lx.FRIEND_CUES,
}


@dataclass
class ChatProfile:
    chat_id: str
    name: str
    is_group: bool
    members: Set[str]  # participants without ME
    n_msgs: int
    me_share: float
    name_tokens: List[str]


@dataclass
class PersonProfile:
    pid: str
    name: str
    one2one: Optional[str]  # chat id of the 1:1 chat with ME
    n_one2one: int  # messages in that chat
    n_sent: int  # messages this person sent anywhere
    groups: List[str]  # group chat ids they are in
    kin_hits: Counter = field(default_factory=Counter)  # kin term -> hits (chat name + ME's 1:1 messages)
    cue_hits: Counter = field(default_factory=Counter)  # cue set -> hits in their 1:1


@dataclass
class Profile:
    chats: Dict[str, ChatProfile]
    people: Dict[str, PersonProfile]


def _kin_hits(texts: List[str]) -> Counter:
    hits: Counter = Counter()
    for t in texts:
        for kin, pat in lx.KIN.items():
            if pat.search(t):
                hits[kin] += 1
    return hits


def build(data: Data) -> Profile:
    chats: Dict[str, ChatProfile] = {}
    for cid, c in data.chats.items():
        msgs = data.by_chat[cid]
        n = len(msgs)
        chats[cid] = ChatProfile(
            chat_id=cid,
            name=c.get("name") or "",
            is_group=bool(c["is_group"]),
            members=set(c["participants"]) - {"ME"},
            n_msgs=n,
            me_share=(sum(m["sender_id"] == "ME" for m in msgs) / n) if n else 0.0,
            name_tokens=tokens(c.get("name") or ""),
        )

    sent = Counter(m["sender_id"] for m in data.messages)
    groups_of: Dict[str, List[str]] = defaultdict(list)
    one2one: Dict[str, str] = {}
    for cid, cp in chats.items():
        if cp.is_group:
            for p in cp.members:
                groups_of[p].append(cid)
        else:
            other = data.other(cid)
            if other:
                # Several 1:1 chats with one person are rare; keep the busiest.
                if other not in one2one or cp.n_msgs > chats[one2one[other]].n_msgs:
                    one2one[other] = cid

    people: Dict[str, PersonProfile] = {}
    for pid in data.people:
        cid = one2one.get(pid)
        kin: Counter = Counter()
        cues: Counter = Counter()
        if cid:
            msgs = data.by_chat[cid]
            # Kin terms: how ME addresses them, plus the saved contact name.
            kin = _kin_hits([chats[cid].name] + [m["text"] for m in msgs if m["sender_id"] == "ME"])
            for m in msgs:
                for cue, pat in CUE_SETS.items():
                    if pat.search(m["text"]):
                        cues[cue] += 1
        people[pid] = PersonProfile(
            pid=pid,
            name=data.names.get(pid, ""),
            one2one=cid,
            n_one2one=chats[cid].n_msgs if cid else 0,
            n_sent=sent.get(pid, 0),
            groups=sorted(groups_of.get(pid, [])),
            kin_hits=kin,
            cue_hits=cues,
        )
    return Profile(chats=chats, people=people)
