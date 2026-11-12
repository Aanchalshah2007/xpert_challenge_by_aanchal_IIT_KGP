"""Circles from 1:1 chat cards: one short card per contact, batched LLM calls, verified ids. Seed circles stay pinned.

A card holds what a person would look at: the saved contact name, how much they talk, shared group names,
kin words the user uses with them, and a few messages (cue-bearing ones first, the rest spread over the chat).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config
from .ingest import Data
from .llm_client import LLM
from .profile import CUE_SETS, Profile
from . import lexicons as lx
from .util import log

SYSTEM = (Path(__file__).parent / "prompts" / "circles_system.txt").read_text(encoding="utf-8")
CIRCLES = {"spouse", "child", "parent", "in-law", "sibling", "relative", "friend", "colleague", "client", "vendor",
           "partner", "staff"}
FAMILY = "family"  # a generic family answer: people.py keeps the rules' kin circle, else "relative"
KIN_CIRCLES = {"spouse", "child", "parent", "in-law", "sibling", "relative"}
CARD_MSGS = 10  # messages shown per card
CUE_MSGS = 6  # of which at most this many are picked for kin/role cues
MSG_CHARS = 160
MAX_GROUPS = 4


def _pick(msgs: List[dict]) -> List[dict]:
    """Cue-bearing messages first, then evenly spread ones, shown in chat order."""
    texts = [m for m in msgs if m["text"].strip()]
    cue = [m for m in texts if any(p.search(m["text"]) for p in list(CUE_SETS.values()) + list(lx.KIN.values()))]
    chosen = cue[:: max(1, len(cue) // CUE_MSGS)][:CUE_MSGS]
    rest = [m for m in texts if m not in chosen]
    need = CARD_MSGS - len(chosen)
    if rest and need > 0:
        step = len(rest) / need
        chosen += [rest[int(i * step)] for i in range(min(need, len(rest)))]
    return sorted(chosen, key=lambda m: m["ts"])


def cards(data: Data, profile: Profile, pinned: Dict[str, str]) -> List[Tuple[str, str]]:
    """(person id, card text) for every unpinned person with a 1:1 chat that has messages."""
    out = []
    for pid, p in sorted(profile.people.items()):
        if pid in pinned or not p.one2one or not p.n_one2one:
            continue
        cp = profile.chats[p.one2one]
        lines = [f'## {pid} "{p.name}" | 1:1: {p.n_one2one} msgs, ME wrote {round(100 * cp.me_share)}%']
        groups = sorted(p.groups, key=lambda c: profile.chats[c].n_msgs, reverse=True)[:MAX_GROUPS]
        if groups:
            lines.append("groups together: " + "; ".join(
                f'"{profile.chats[c].name}" ({len(profile.chats[c].members)} members)' for c in groups)
                + (f" (+{len(p.groups) - MAX_GROUPS} more)" if len(p.groups) > MAX_GROUPS else ""))
        if p.kin_hits:
            lines.append("kin words: " + ", ".join(sorted(p.kin_hits)))
        for m in _pick(data.by_chat[p.one2one]):
            text = " ".join(m["text"].split())
            text = text[:MSG_CHARS] + "…" if len(text) > MSG_CHARS else text
            lines.append(f"{'ME' if m['sender_id'] == 'ME' else pid}: {text}")
        out.append((pid, "\n".join(lines)))
    return out


def _batches(items: List[Tuple[str, str]], budget: int) -> List[List[Tuple[str, str]]]:
    out: List[List[Tuple[str, str]]] = [[]]
    size = 0
    for it in items:
        if out[-1] and size + len(it[1]) > budget:
            out.append([])
            size = 0
        out[-1].append(it)
        size += len(it[1]) + 1
    return [b for b in out if b]


def parse(reply: object, ids: set) -> Dict[str, Tuple[str, float]]:
    out: Dict[str, Tuple[str, float]] = {}
    if not isinstance(reply, dict) or not isinstance(reply.get("people"), list):
        return out
    for r in reply["people"]:
        if not isinstance(r, dict):
            continue
        pid, circle = str(r.get("id", "")), str(r.get("circle", "")).lower()
        if pid in ids and (circle in CIRCLES or circle == FAMILY):
            try:
                conf = min(1.0, max(0.0, float(r.get("conf", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            out[pid] = (circle, conf)
    return out


def prompts(data: Data, profile: Profile, pinned: Dict[str, str],
            budget: Optional[int] = None) -> List[Tuple[str, set]]:
    items = cards(data, profile, pinned)
    return [("\n\n".join(c for _, c in b), {pid for pid, _ in b})
            for b in _batches(items, budget or config.CIRCLES_CHAR_BUDGET)]


def vote(samples: List[Dict[str, Tuple[str, float]]]) -> Dict[str, Tuple[str, float]]:
    """Majority circle per person over independent samples; no majority: the first sample that labelled them.
    Confidence = mean confidence of the samples that gave the winning circle."""
    out: Dict[str, Tuple[str, float]] = {}
    for pid in {p for s in samples for p in s}:
        votes = [s[pid] for s in samples if pid in s]
        circles_ = [c for c, _ in votes]
        best = max(circles_, key=lambda c: (circles_.count(c), -circles_.index(c)))
        if circles_.count(best) < 2:
            best = circles_[0]
        confs = [conf for c, conf in votes if c == best]
        out[pid] = (best, sum(confs) / len(confs))
    return out


def extract_consensus(data: Data, profile: Profile, pinned: Dict[str, str], client: LLM,
                      n: Optional[int] = None) -> Dict[str, Tuple[str, float]]:
    """config.CIRCLES_SAMPLES independent passes (identical prompts), majority vote per person (changelog 24)."""
    n = n or config.CIRCLES_SAMPLES
    samples = [extract(data, profile, pinned, client, sample=k) for k in range(n)]
    return vote(samples) if n > 1 else samples[0]


def extract(data: Data, profile: Profile, pinned: Dict[str, str], client: LLM,
            budget: Optional[int] = None, sample: int = 0) -> Dict[str, Tuple[str, float]]:
    ps = prompts(data, profile, pinned, budget)
    if not ps:
        return {}
    with client.stage("circles"):
        replies = client.map_json([p for p, _ in ps], system=SYSTEM, max_tokens=None, sample=sample)
    out: Dict[str, Tuple[str, float]] = {}
    for (_, ids), reply in zip(ps, replies):
        out.update(parse(reply, ids))
    # Cards the model skipped get one more call (just those cards).
    cards_by_id = dict(cards(data, profile, pinned))
    missing = [pid for _, ids in ps for pid in sorted(ids) if pid not in out]
    if missing:
        retry = _batches([(pid, cards_by_id[pid]) for pid in missing], budget or config.CIRCLES_CHAR_BUDGET)
        with client.stage("circles"):
            again = client.map_json(["\n\n".join(c for _, c in b) for b in retry], system=SYSTEM,
                                    max_tokens=None, sample=sample)
        for b, reply in zip(retry, again):
            out.update(parse(reply, {pid for pid, _ in b}))
    log(f"circles: {sum(len(i) for _, i in ps)} cards in {len(ps)} calls (+{len(missing)} re-asked) -> {len(out)} labelled")
    return out
