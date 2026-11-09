"""People circles + links, rules only: seed pinned, kin lexicon, group membership, 1:1 cue counts."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import lexicons as lx
from .orgs import Orgs
from .profile import ChatProfile, PersonProfile, Profile

KIN_PRIORITY = ["spouse", "parent", "child", "sibling", "in-law", "relative"]
# "Brand: Company - topic", "A x B", "A <> B": a deal group between two organisations.
DEAL_NAME = re.compile(r":|<>|\s[x×|]\s|\s-\s")
SPOUSE_RATIO = 2.0  # busiest 1:1 must be this many times the next one
CORE_MAX_MEMBERS = 10  # bigger work groups are communities, not teams (unless named like a team)
LINK_MIN_1TO1 = 5  # a 1:1 this busy, plus a team group, is enough to say they run the org


def _is_deal(cp: ChatProfile) -> bool:
    return bool(DEAL_NAME.search(cp.name)) and len(cp.members) <= 6


def _is_team(cp: ChatProfile) -> bool:
    return not _is_deal(cp) and (len(cp.members) <= CORE_MAX_MEMBERS or bool(lx.WORK_GROUP.search(cp.name)))


def _kin(p: PersonProfile) -> Optional[str]:
    for kin in KIN_PRIORITY:
        if p.kin_hits.get(kin):
            return kin
    return None


def _spouse(profile: Profile, family_people: set) -> Optional[str]:
    """The busiest 1:1, when it dwarfs the rest and the person shares family/home groups with ME."""
    ranked = sorted((p for p in profile.people.values() if p.one2one), key=lambda p: -p.n_one2one)
    if len(ranked) < 2 or ranked[0].n_one2one < SPOUSE_RATIO * ranked[1].n_one2one:
        return None
    return ranked[0].pid if ranked[0].pid in family_people else None


def build(profile: Profile, orgs: Orgs, seed: dict) -> List[dict]:
    pinned: Dict[str, str] = dict(seed.get("circles") or {})
    chats = profile.chats
    seed_orgs = set(orgs.ids_of("seed"))
    in_kind: Dict[str, Dict[str, List[str]]] = {}
    for pid, p in profile.people.items():
        by: Dict[str, List[str]] = {}
        for cid in p.groups:
            oid = orgs.group_org.get(cid)
            if oid:
                by.setdefault(oid, []).append(cid)
        in_kind[pid] = by

    family_people = {pid for pid, by in in_kind.items() if "family" in by or "home" in by}
    spouse = _spouse(profile, family_people)

    out = []
    for pid, p in sorted(profile.people.items()):
        by = in_kind[pid]
        links: List[dict] = []
        work = {o: cids for o, cids in by.items() if o in seed_orgs}
        core = {o: [c for c in cids if _is_team(chats[c])] for o, cids in work.items()}
        core = {o: cids for o, cids in core.items() if cids}
        kin = _kin(p)
        if pid in pinned:
            circle = pinned[pid]
        elif pid == spouse:
            circle = "spouse"
        elif kin and (p.one2one or "family" in by):
            circle = kin if kin != "spouse" else "relative"
        elif "family" in by:
            circle = "relative"
        elif core:
            circle = "colleague"
        elif work and all(_is_deal(chats[c]) for cids in work.values() for c in cids):
            circle = "client"
        elif "home" in by:
            circle = "staff"
        else:
            circle = _from_cues(p)

        # Links only where we are fairly sure (wrong links dilute the org score).
        if circle in ("colleague", "partner"):
            for oid, cids in sorted(core.items()):
                if len(cids) >= 3 or (p.one2one and p.n_one2one >= LINK_MIN_1TO1):
                    links.append({"organisation": oid, "link": "runs"})
        elif circle == "staff" and "home" in by and p.one2one:
            links.append({"organisation": "home", "link": "supports"})

        rec: dict = {"id": pid, "circle": circle}
        if links:
            rec["links"] = links
        out.append(rec)
    return out


def _from_cues(p: PersonProfile) -> str:
    cues = p.cue_hits
    best = max(("staff", "vendor", "client"), key=lambda k: cues.get(k, 0))
    if cues.get(best, 0) >= 2 and cues[best] > cues.get("friend", 0):
        return best
    return "friend"
