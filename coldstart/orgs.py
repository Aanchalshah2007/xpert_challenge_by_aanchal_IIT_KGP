"""Organisations, rules only: seed org names + Home + group clustering by name tokens and member overlap.

Few orgs on purpose: the org score divides by max(#ours, #theirs).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Set

from . import lexicons as lx
from .profile import Profile
from .util import acronym, tokens

OVERLAP_JOIN = 0.5  # share of a group's members already in an org's groups
MIN_ANCHOR_LEN = 3


@dataclass
class Orgs:
    orgs: List[dict]  # output-shaped organisation records
    group_org: Dict[str, str]  # group chat id -> org id
    kind: Dict[str, str]  # org id -> seed | home | family | social
    keys: Dict[str, Set[str]] = field(default_factory=dict)  # seed org id -> name keys

    def ids_of(self, kind: str) -> List[str]:
        return [o for o, k in self.kind.items() if k == kind]


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


def _name_keys(name: str) -> Set[str]:
    keys = {t for t in tokens(name) if len(t) >= MIN_ANCHOR_LEN and t not in lx.NAME_STOPWORDS}
    if len(_squash(name)) >= MIN_ANCHOR_LEN:
        keys.add(_squash(name))
    acr = acronym(name)
    if len(acr) >= 2:
        keys.add(acr)
    return keys


def _matches(chat_name: str, keys: Set[str]) -> bool:
    toks = set(tokens(chat_name))
    squashed = _squash(chat_name)
    return bool(toks & keys) or any(len(k) >= 5 and k in squashed for k in keys)


def build(profile: Profile, seed: dict) -> Orgs:
    groups = {cid: cp for cid, cp in profile.chats.items() if cp.is_group}
    orgs: List[dict] = []
    kind: Dict[str, str] = {}
    keys: Dict[str, Set[str]] = {}
    for i, name in enumerate(seed.get("organisations") or [], 1):
        oid = f"org{i}"
        orgs.append({"id": oid, "name": name, "type": "company", "side": "work", "role": "owner"})
        kind[oid] = "seed"
        keys[oid] = _name_keys(name)
    # A key claimed by two seed orgs identifies neither.
    counts = Counter(k for ks in keys.values() for k in ks)
    keys = {o: {k for k in ks if counts[k] == 1} for o, ks in keys.items()}

    group_org: Dict[str, str] = {}

    # 1. Group names that carry a seed org's name, acronym or squashed name.
    for cid, cp in groups.items():
        hits = [o for o, ks in keys.items() if _matches(cp.name, ks)]
        if len(hits) == 1:
            group_org[cid] = hits[0]

    # 2. One round of anchor propagation: distinctive tokens that co-occur with a seed key in a matched
    #    group name and recur in other group names pull those groups in too.
    token_df = Counter(t for cp in groups.values() for t in set(cp.name_tokens))
    extra: Dict[str, Set[str]] = {o: set() for o in keys}
    for cid, oid in group_org.items():
        for t in groups[cid].name_tokens:
            if len(t) >= MIN_ANCHOR_LEN and t not in lx.NAME_STOPWORDS and token_df[t] >= 2 and not t.isdigit():
                extra[oid].add(t)
    claimed = Counter(t for ts in extra.values() for t in ts)
    for cid, cp in groups.items():
        if cid in group_org:
            continue
        hits = {o for o, ts in extra.items() for t in cp.name_tokens if t in ts and claimed[t] == 1}
        if len(hits) == 1:
            group_org[cid] = hits.pop()

    def add(oid: str, name: str, type_: str, side: str, role: str, k: str) -> str:
        if oid not in kind:
            orgs.append({"id": oid, "name": name, "type": type_, "side": side, "role": role})
            kind[oid] = k
        return oid

    add("home", "Home", "residence", "home", "owner", "home")

    # 3. Family groups by name, then home groups by name / flat-number hints.
    for cid, cp in groups.items():
        if cid in group_org:
            continue
        if lx.FAMILY_GROUP.search(cp.name):
            group_org[cid] = add("family", "Family", "other", "home", "member", "family")
    for cid, cp in groups.items():
        if cid not in group_org and (lx.HOME_GROUP.search(cp.name) or lx.ADDRESS_HINT.search(cp.name)):
            group_org[cid] = "home"
    for cid, cp in groups.items():
        if cid not in group_org and _overlap(cp.members, _members(groups, group_org, "family")) >= OVERLAP_JOIN:
            group_org[cid] = "family"

    # 4. Member overlap with seed-org clusters.
    for cid, cp in sorted(groups.items()):
        if cid in group_org:
            continue
        best, best_ov = None, 0.0
        for oid in keys:
            ov = _overlap(cp.members, _members(groups, group_org, oid))
            if ov > best_ov:
                best, best_ov = oid, ov
        if best and best_ov >= OVERLAP_JOIN:
            group_org[cid] = best

    # 5. Everything else: one social sink, so it doesn't dilute the real orgs.
    for cid in groups:
        if cid not in group_org:
            group_org[cid] = add("social", "Friends & communities", "other", "home", "member", "social")
    return Orgs(orgs=orgs, group_org=group_org, kind=kind, keys=keys)


def _members(groups: Dict[str, object], group_org: Dict[str, str], oid: str) -> Set[str]:
    out: Set[str] = set()
    for cid, o in group_org.items():
        if o == oid:
            out |= groups[cid].members  # type: ignore[attr-defined]
    return out


def _overlap(members: Set[str], pool: Set[str]) -> float:
    return len(members & pool) / len(members) if members else 0.0
