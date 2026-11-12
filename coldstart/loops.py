"""Open loops: rule candidates -> windows -> batched LLM -> validated, merged raw loops (pre-decide).

Short local ids (W1, m12) in prompts; mapped back to real ids in code. Anything the LLM returns that
doesn't map to a real message in its window is dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import candidates, config
from . import lexicons as lx
from .candidates import Window
from .ingest import Data
from .llm_client import LLM
from .util import log, tokens

IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM = (Path(__file__).parent / "prompts" / "loops_system.txt").read_text(encoding="utf-8")
# Ablations (dev only, config.ABLATE): one sentence each, from the v2 prompt.
_V1_FOLLOWUP = "- ME asks someone else to do something and is waiting on it (owner = that person's id) -> type followup\n"
_DELEGATE = ("- ME delegates or asks someone else to do something (\"please ask\", \"pls check with ...\", \"send me ...\"): "
             "the loop is owned by ME, type followup, because ME has to make sure it happens\n")
_NO_LOOPS = '"No loops" is a normal and common answer for a window.\n'
_BURST = ("Each separate request is its own loop, even when one person sends several in a row "
          "(\"pay this\", \"book that\", \"also fix the fan\" = three loops).\n")
if "delegate" in config.ABLATE:
    assert _V1_FOLLOWUP in SYSTEM
    SYSTEM = SYSTEM.replace(_V1_FOLLOWUP, _DELEGATE)
if "burst" in config.ABLATE:
    assert _NO_LOOPS in SYSTEM
    SYSTEM = SYSTEM.replace(_NO_LOOPS, _NO_LOOPS + _BURST)
TYPES = {"commitment", "followup", "deadline", "question"}
MERGE_TITLE_JACCARD = 0.5


def _when(ts: int, year: bool = False) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%a %d %b %Y %H:%M" if year else "%a %d %b %H:%M")


def _clip(text: str) -> str:
    return text[: config.MAX_MSG_CHARS] + "…" if len(text) > config.MAX_MSG_CHARS else text


def _chat_header(data: Data, cid: str) -> str:
    c = data.chats[cid]
    if c["is_group"]:
        return f'group "{c.get("name") or cid}", {len(c["participants"]) - 1} other members'
    other = data.other(cid) or "?"
    return f"1:1 with {other} ({data.names.get(other, '')})"


def build_prompts(data: Data, wins: List[Window], char_budget: Optional[int] = None, per_call: Optional[int] = None,
                  ) -> Tuple[List[str], List[Dict[str, Tuple[str, Dict[str, str]]]]]:
    """One prompt per batch of windows. Also returns, per prompt, W-id -> (window id, local m-id -> msg id)."""
    prompts, maps = [], []
    for batch in _batches(data, wins, char_budget, per_call):
        today = f"Today is {_when(data.now, year=True)} (IST)."
        if data.me_names and config.LOOPS_USE_ME_NAME:
            alias = " or ".join(f'"{n}"' for n in data.me_names)
            lines = [today, f"ME is the user, addressed as {alias}: messages that use this name are talking to ME.",
                     "Windows:"]
        else:
            lines = [today + " Windows:"]
        bmap: Dict[str, Tuple[str, Dict[str, str]]] = {}
        k = 0
        for j, w in enumerate(batch, 1):
            wl = f"W{j}"
            local: Dict[str, str] = {}
            lines.append(f"\n## {wl}: {_chat_header(data, w.chat_id)}")
            day = None
            for mid in w.msg_ids:
                k += 1
                m = data.by_id[mid]
                local[f"m{k}"] = mid
                mark = "*" if mid in w.reasons else " "
                if config.PROMPT_COMPACT:
                    text = _clip(" ".join(m["text"].split()))
                    d, hm = _when(m["ts"]).rsplit(" ", 1)
                    if d != day:
                        lines.append(f"-- {d}")
                        day = d
                    lines.append(f"{mark}m{k} {hm} {m['sender_id']}: {text}")
                else:  # v1 format, kept byte-identical for cache replay
                    text = _clip(m["text"].replace("\n", " "))
                    lines.append(f"{mark}m{k} {_when(m['ts'])} {m['sender_id']}: {text}")
            bmap[wl] = (w.wid, local)
        prompts.append("\n".join(lines))
        maps.append(bmap)
    return prompts, maps


def split(data: Data, w: Window, budget: Optional[int] = None, tag: str = "") -> List[Window]:
    """Cut a window larger than the prompt budget into pieces that fit, each carrying SPLIT_OVERLAP messages of
    the previous one (the slice-tested set-up)."""
    budget = budget or config.PROMPT_CHAR_BUDGET
    sizes = [min(len(data.by_id[m]["text"]), config.MAX_MSG_CHARS) + 24 for m in w.msg_ids]
    if sum(sizes) <= budget:
        return [w]
    pieces: List[Window] = []
    start = 0
    while start < len(w.msg_ids):
        end, size = start, 0
        while end < len(w.msg_ids) and (end == start or size + sizes[end] <= budget):
            size += sizes[end]
            end += 1
        ids = w.msg_ids[start:end]
        pieces.append(Window(wid=f"{w.wid}.{tag}{len(pieces) + 1}", chat_id=w.chat_id, msg_ids=ids,
                             candidates=[c for c in w.candidates if c in ids],
                             reasons={c: r for c, r in w.reasons.items() if c in ids}))
        if end >= len(w.msg_ids):
            break
        start = max(end - config.SPLIT_OVERLAP, start + 1)
    return pieces


def _batches(data: Data, wins: List[Window], char_budget: Optional[int] = None,
             per_call: Optional[int] = None) -> List[List[Window]]:
    """Pack windows into calls by count and approximate prompt size; a huge window gets a call of its own."""
    budget = char_budget or config.PROMPT_CHAR_BUDGET
    per_call = per_call or config.WINDOWS_PER_CALL
    out: List[List[Window]] = [[]]
    size = 0
    for w in wins:
        wsize = sum(min(len(data.by_id[m]["text"]), config.MAX_MSG_CHARS) + 24 for m in w.msg_ids)
        if out[-1] and (len(out[-1]) >= per_call or size + wsize > budget):
            out.append([])
            size = 0
        out[-1].append(w)
        size += wsize
    return [b for b in out if b]


def _p(x: object) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, v))


def _norm3(a: float, b: float, c: float, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    s = a + b + c
    return (a / s, b / s, c / s) if s > 0 else default


def parse_reply(reply: object, bmap: Dict[str, Tuple[str, Dict[str, str]]], data: Data,
                wins_by_id: Dict[str, Window]) -> List[dict]:
    """LLM JSON -> raw loops with real ids. Drops anything that doesn't map back."""
    if not isinstance(reply, dict) or not isinstance(reply.get("loops"), list):
        return []
    out = []
    for r in reply["loops"]:
        if not isinstance(r, dict) or str(r.get("w")) not in bmap:
            continue
        wid, local = bmap[str(r["w"])]
        src = local.get(str(r.get("src")))
        if src is None:
            # The model sometimes cites an id from a neighbouring window of the same batch.
            src = next((loc.get(str(r.get("src"))) for _, loc in bmap.values() if str(r.get("src")) in loc), None)
            if src is None or data.by_id[src]["chat_id"] != wins_by_id[wid].chat_id:
                continue
        m = data.by_id[src]
        if m["ts"] < data.loop_from:
            continue  # earlier messages are context only
        chat_id = m["chat_id"]
        owner = str(r.get("owner") or "ME")
        if owner != "ME" and owner not in data.chats[chat_id]["participants"]:
            owner = "ME"
        close = local.get(str(r.get("close"))) if r.get("close") else None
        if close and data.by_id[close]["ts"] < m["ts"]:
            close = None
        p_open, p_done, p_drop = _norm3(_p(r.get("p_open")), _p(r.get("p_done")), _p(r.get("p_dropped")), (1.0, 0.0, 0.0))
        p_assist, p_remind, p_leave = _norm3(_p(r.get("p_assist")), _p(r.get("p_remind")), _p(r.get("p_leave")),
                                             (0.0, 0.0, 1.0))
        out.append({
            "title": str(r.get("title") or "").strip()[:120] or "Follow up",
            "type": r.get("type") if r.get("type") in TYPES else "commitment",
            "owner": owner,
            "chat_id": chat_id,
            "source_msg": src,
            "due": _date(r.get("due")),
            "p_open": p_open, "p_done": p_done, "p_dropped": p_drop,
            "close": close,
            "owner_is_me": bool(r.get("owner_is_me", owner == "ME")),
            "private": bool(r.get("private")),
            "reversible": bool(r.get("reversible")),
            "sensitive": bool(r.get("sensitive")),
            "needs_me_only": bool(r.get("needs_me_only")),
            "p_assist": p_assist, "p_remind": p_remind, "p_leave": p_leave,
            "next": (str(r["next"]).strip()[:200] if r.get("next") else None),
            "conf": _p(r.get("conf", 0.5)),
        })
    return out


def _date(x: object) -> Optional[str]:
    if not isinstance(x, str):
        return None
    try:
        return datetime.strptime(x.strip()[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def merge(loops: List[dict], data: Data) -> List[dict]:
    """One loop per source_msg; same-task loops in a chat merge into the one with the earliest source."""
    loops = [l for l in loops if l["conf"] >= config.MIN_CONFIDENCE]
    loops.sort(key=lambda l: (data.by_id[l["source_msg"]]["ts"], -l["conf"]))
    kept: List[dict] = []
    for l in loops:
        dup = None
        for k in kept:
            if k["chat_id"] != l["chat_id"]:
                continue
            if k["source_msg"] == l["source_msg"] or (l["close"] and l["close"] == k["close"]):
                dup = k
                break
            a, b = set(tokens(k["title"])), set(tokens(l["title"]))
            if a and b and len(a & b) / len(a | b) >= MERGE_TITLE_JACCARD:
                dup = k
                break
        if dup is None:
            kept.append(l)
        elif l["conf"] > dup["conf"] and l["source_msg"] == dup["source_msg"]:
            kept[kept.index(dup)] = l  # same source: keep the more confident reading
    return kept


STRONG_CUES = {"request", "imperative", "commit", "question", "quote", "tasks", "addressed"}
ASK_CUES = {"request", "imperative"}


def shift_sources(raw: List[dict], data: Data, cands: Dict[str, List[str]]) -> List[dict]:
    """A source with no request/commitment cue (a context line, an announcement) moves to the same sender's
    explicit ask right after it: "party on the 27th" -> "invite to be made"."""
    if not config.SHIFT_SOURCES:
        return raw
    out = []
    for l in raw:
        src = data.by_id[l["source_msg"]]
        if set(cands.get(src["id"], [])) & STRONG_CUES:
            out.append(l)
            continue
        chat = data.by_chat[src["chat_id"]]
        i = next(k for k, m in enumerate(chat) if m["id"] == src["id"])
        new = None
        for m in chat[i + 1 : i + 1 + config.SHIFT_MAX_MSGS]:
            if m["ts"] - src["ts"] > config.SHIFT_MAX_SECS:
                break
            if m["sender_id"] == src["sender_id"] and set(cands.get(m["id"], [])) & ASK_CUES:
                new = m["id"]
                break
        out.append(dict(l, source_msg=new) if new else l)
    return out


def _is_payment_request(text: str) -> bool:
    return bool(lx.PAY.search(text)) or (bool(lx.MONEY.search(text)) and bool(lx.REQUEST.search(text)))


def media_closure(raw: List[dict], data: Data) -> List[dict]:
    """The user's media (a payment screenshot) right after a payment request is evidence it was paid: move part of
    p_open to p_done and set close. Status is still decided by the 2x rule in decide.py."""
    if not config.MEDIA_CLOSURE:
        return raw
    out = []
    for l in raw:
        src = data.by_id[l["source_msg"]]
        if not _is_payment_request(src["text"]):
            out.append(l)
            continue
        chat = data.by_chat[src["chat_id"]]
        i = next(k for k, m in enumerate(chat) if m["id"] == src["id"])
        evidence = None
        for m in chat[i + 1 : i + 1 + config.MEDIA_CLOSURE_MAX_MSGS]:
            if m["ts"] - src["ts"] > config.MEDIA_CLOSURE_MAX_SECS:
                break
            if m["sender_id"] == "ME":
                if candidates.is_media_only(m):
                    evidence = m["id"]
                    break
                continue
            if candidates.reasons_for(m):
                break  # a new request in between: the media may belong to that one
        if evidence is None:
            out.append(l)
            continue
        moved = config.MEDIA_CLOSURE_SHIFT * l["p_open"]
        out.append(dict(l, p_open=l["p_open"] - moved, p_done=l["p_done"] + moved, close=l["close"] or evidence))
    return out


def halves(data: Data, batch: List[Window]) -> List[List[Window]]:
    """A batch whose reply was cut: two half batches, or one window cut into two overlapping pieces."""
    if len(batch) > 1:
        return [batch[: len(batch) // 2], batch[len(batch) // 2:]]
    w = batch[0]
    size = sum(min(len(data.by_id[m]["text"]), config.MAX_MSG_CHARS) + 24 for m in w.msg_ids)
    pieces = split(data, w, budget=size // 2 + 1, tag="h") if len(w.msg_ids) > config.SPLIT_OVERLAP + 1 else [w]
    return [[p] for p in pieces] if len(pieces) > 1 else []


def _ask_splitting(data: Data, client: LLM, prompts: List[str], maps: List[Dict[str, Tuple[str, Dict[str, str]]]],
                   wins_by_id: Dict[str, Window]) -> Tuple[List[object], List[Dict[str, Tuple[str, Dict[str, str]]]]]:
    """Ask every prompt; a reply cut at max_tokens is replaced by replies to its two halves (up to
    TRUNCATION_SPLITS levels). If it can't be split further, the repaired part of the cut reply is kept."""
    replies: List[object] = []
    out_maps: List[Dict[str, Tuple[str, Dict[str, str]]]] = []
    todo = list(zip(prompts, maps, [0] * len(prompts)))
    while todo:
        answers = client.map_json_ex([p for p, _, _ in todo], system=SYSTEM, max_tokens=config.LOOPS_MAX_TOKENS)
        nxt = []
        for (prompt, bmap, depth), (reply, cut) in zip(todo, answers):
            parts = halves(data, [wins_by_id[wid] for wid, _ in bmap.values()]) if cut else []
            if cut and depth < config.TRUNCATION_SPLITS and parts:
                for part in parts:
                    wins_by_id.update({w.wid: w for w in part})
                    ps, ms = build_prompts(data, part, char_budget=10**9, per_call=10**6)
                    nxt += [(ps[0], ms[0], depth + 1)]
                log(f"loops: a reply was cut at max_tokens; retrying it as {len(parts)} smaller calls")
                continue
            replies.append(reply)
            out_maps.append(bmap)
        todo = nxt
    return replies, out_maps


def extract(data: Data, client: LLM) -> List[dict]:
    cands = candidates.find(data)
    wins = [p for w in candidates.windows(data, cands) for p in split(data, w)]
    log(f"loops: {len(cands)} candidates in {len(wins)} windows (after splitting large ones)")
    prompts, maps = build_prompts(data, wins)
    wins_by_id = {w.wid: w for w in wins}
    with client.stage("loops"):
        replies, maps = _ask_splitting(data, client, prompts, maps, wins_by_id)
    raw: List[dict] = []
    for reply, bmap in zip(replies, maps):
        raw += parse_reply(reply, bmap, data, wins_by_id)
    failed = sum(r is None for r in replies)
    raw = media_closure(shift_sources(raw, data, cands), data)
    loops = merge(raw, data)
    log(f"loops: {len(raw)} raw -> {len(loops)} after merge ({failed}/{len(replies)} calls failed)")
    return loops
