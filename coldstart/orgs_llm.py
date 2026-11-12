"""Organisations with ONE LLM call (ablation "orgs_llm"): the model regroups the group chats into organisations,
starting from the rules' clusters (orgs.build). Any failure keeps the rules' result.

The card lists the seed organisation names, then every group chat with its size, activity, the rules' cluster and its
strongest member overlaps with other groups. The reply names each organisation once with its groups.
Seed organisations keep their ids (people links point at them) and Home stays "home".
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from . import lexicons as lx
from .llm_client import LLM
from .orgs import Orgs
from .profile import Profile
from .util import log

MAX_ORGS = 8
OVERLAP_SHOW = 0.3  # show a group pair when this share of the smaller group's members is shared
TYPES = {"company", "residence", "vacation home", "workspace", "society", "school", "other"}
ROLES = {"owner", "partner", "helper", "resident", "parent", "member", "other"}

SYSTEM = """You group a person's WhatsApp group chats into the organisations of their life. The person is "ME".
An organisation is a company ME owns or works with (a client, vendor or partner business), ME's residence or
housing society, a school, a club or community, or ME's family. Rules:
- FEW organisations: merge groups that belong to the same organisation (shared name parts, shared members, a team
  plus its sub-teams). At most 8 organisations, including Home.
- Keep every seed organisation (the person named them) and keep its id S1, S2, ...; keep Home as "home".
- Groups of friends, alumni, hobbies and casual communities with no clear organisation go together in ONE
  organisation named "Friends & communities" (type other, side home, role member).
- Every group goes to exactly one organisation.
- type: company | residence | vacation home | workspace | society | school | other. side: work | home.
  role (ME's role): owner | partner | helper | resident | parent | member | other.
Reply with JSON only:
{"orgs":[{"id":"S1","name":"...","type":"company","side":"work","role":"owner","groups":["G1","G4"]},
 {"id":"home","name":"Home","type":"residence","side":"home","role":"owner","groups":["G2"]},
 {"id":"new1","name":"...","type":"school","side":"home","role":"parent","groups":["G7"]}]}"""


def card(profile: Profile, seed: dict, rules: Orgs) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """Prompt text, local group id -> chat id, local seed id -> org id."""
    groups = sorted((cid for cid, cp in profile.chats.items() if cp.is_group),
                    key=lambda c: -profile.chats[c].n_msgs)
    gid = {cid: f"G{i}" for i, cid in enumerate(groups, 1)}
    seed_ids = rules.ids_of("seed")
    sid = {oid: f"S{i}" for i, oid in enumerate(seed_ids, 1)}
    names = {o["id"]: o["name"] for o in rules.orgs}
    cluster = {oid: sid.get(oid, "home" if oid == "home" else names.get(oid, oid)) for oid in names}
    lines = ["Seed organisations:"] + [f"{sid[o]} {names[o]}" for o in seed_ids] + ["", "Group chats (id | name | members | "
             "messages | ME's share | rules' guess):"]
    for cid in groups:
        cp = profile.chats[cid]
        lines.append(f"{gid[cid]} | {cp.name} | {len(cp.members)} | {cp.n_msgs} | {round(100 * cp.me_share)}% | "
                     f"{cluster.get(rules.group_org.get(cid, ''), '-')}")
    pairs = []
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            ma, mb = profile.chats[a].members, profile.chats[b].members
            small = min(len(ma), len(mb))
            if small >= 2 and len(ma & mb) / small >= OVERLAP_SHOW:
                pairs.append((len(ma & mb) / small, gid[a], gid[b]))
    pairs.sort(reverse=True)
    if pairs:
        lines += ["", "Shared members (share of the smaller group):"]
        lines += [f"{a}-{b} {round(100 * s)}%" for s, a, b in pairs[:60]]
    return "\n".join(lines), {v: k for k, v in gid.items()}, {v: k for k, v in sid.items()}


def parse(reply: object, gmap: Dict[str, str], smap: Dict[str, str], rules: Orgs) -> Optional[Orgs]:
    if not isinstance(reply, dict) or not isinstance(reply.get("orgs"), list):
        return None
    orgs: List[dict] = []
    kind: Dict[str, str] = {}
    group_org: Dict[str, str] = {}
    seen_ids: Set[str] = set()
    n_new = 0
    for r in reply["orgs"][:MAX_ORGS]:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        if rid in smap:
            oid, k = smap[rid], "seed"
        elif rid == "home":
            oid, k = "home", "home"
        elif lx.FAMILY_GROUP.search(str(r.get("name") or "")) and "family" not in seen_ids:
            oid, k = "family", "family"  # people.py reads family membership from this id
        else:
            n_new += 1
            oid, k = f"llm{n_new}", "social"
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        base = next((o for o in rules.orgs if o["id"] == oid), {})
        name = str(r.get("name") or base.get("name") or "Organisation").strip()[:80]
        orgs.append({"id": oid, "name": name,
                     "type": r.get("type") if r.get("type") in TYPES else base.get("type", "other"),
                     "side": r.get("side") if r.get("side") in ("work", "home") else base.get("side", "home"),
                     "role": r.get("role") if r.get("role") in ROLES else base.get("role", "member")})
        kind[oid] = k
        for g in r.get("groups") or []:
            cid = gmap.get(str(g))
            if cid and cid not in group_org:
                group_org[cid] = oid
    for oid, k in (("home", "home"),) + tuple((o, "seed") for o in rules.ids_of("seed")):
        if oid not in kind:  # Home and seed orgs always exist
            base = next(o for o in rules.orgs if o["id"] == oid)
            orgs.append(dict(base))
            kind[oid] = k
    for cid, oid in rules.group_org.items():  # groups the model left out keep the rules' org (re-added if needed)
        if cid not in group_org:
            if oid not in kind:
                orgs.append(dict(next(o for o in rules.orgs if o["id"] == oid)))
                kind[oid] = rules.kind.get(oid, "social")
            group_org[cid] = oid
    if not group_org:
        return None
    return Orgs(orgs=orgs, group_org=group_org, kind=kind, keys=rules.keys)


def refine(profile: Profile, seed: dict, rules: Orgs, client: LLM) -> Orgs:
    prompt, gmap, smap = card(profile, seed, rules)
    with client.stage("orgs"):
        reply = client.ask_json(prompt, system=SYSTEM, max_tokens=3000)
    out = parse(reply, gmap, smap, rules)
    if out is None:
        log("orgs: LLM reply unusable, keeping the rules' organisations")
        return rules
    log(f"orgs: LLM regrouped {len(gmap)} groups into {len(out.orgs)} organisations (rules: {len(rules.orgs)})")
    return out
