#!/usr/bin/env python3
"""Compare LLM set-ups on one fixed sample: the windows whose default small-batch Gemini answers are cached.

    # Gemini small (cached, no calls) + big batches (~3 calls; failed ones narrow the compared sample):
    LLM_MODEL=gemini-3.5-flash COLDSTART_LLM_CACHE=.llm_cache python3 eval/batch_compare.py gemini --calls 3
    # Groq (env from .env.groq), windows split into pieces that fit a small tokens-per-minute limit:
    python3 eval/batch_compare.py groq --char-budget 7000 --max-tokens 2000 --effort low --stop-tokens 150000
    # One table of every saved variant:
    python3 eval/batch_compare.py table

Each variant is saved to eval/runs/compare.json. All go through the same parse -> merge -> decide and are
scored on key loops whose source lies inside the compared windows.
Exit 3 if the provider quota is spent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import candidates, decide, ingest, loops  # noqa: E402
from coldstart.candidates import Window  # noqa: E402
from coldstart.llm_client import LLM, parse_json  # noqa: E402

USAGE = Path("usage.jsonl")
SAVED = ROOT / "eval" / "runs" / "compare.json"
NEAR = 10
SPLIT_OVERLAP = 6  # context carried into the next piece when a window is split
# This tool replays and extends the v1 comparison: it always uses the archived v1 prompt, so cache keys still match.
SYSTEM_V1 = (ROOT / "eval" / "prompt_archive" / "loops_system_v1.txt").read_text(encoding="utf-8")
BIG_CALL_MIN_PROMPT = 15000  # usage rows above this many prompt tokens are big-batch calls (small ones are < 10k)


def cache_key(model: str, prompt: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_V1}, {"role": "user", "content": prompt}]
    return hashlib.sha256(json.dumps([model, messages, None, True], sort_keys=True).encode()).hexdigest()


def sample(data, sample_model: str, cache: Path):
    """The windows of the cached default batches, plus those prompts and maps."""
    wins = candidates.windows(data, candidates.find(data))
    prompts, maps = loops.build_prompts(data, wins)
    hits = [i for i, p in enumerate(prompts) if (cache / f"{cache_key(sample_model, p)}.txt").exists()]
    wins_by_id = {w.wid: w for w in wins}
    sample_wins = [wins_by_id[wid] for i in hits for wid, _ in maps[i].values()]
    return wins_by_id, sample_wins, [prompts[i] for i in hits], [maps[i] for i in hits]


def split(data, w: Window, budget: int, tag: str = "") -> List[Window]:
    """Cut an oversized window into overlapping pieces that each fit the prompt budget (now coldstart.loops.split)."""
    assert loops.config.SPLIT_OVERLAP == SPLIT_OVERLAP
    return loops.split(data, w, budget, tag)


def halve(w: Window) -> List[Window]:
    """Two overlapping halves of one window (for a truncated reply)."""
    if len(w.msg_ids) < 2:
        return [w]
    mid = len(w.msg_ids) // 2
    parts = [w.msg_ids[: mid + SPLIT_OVERLAP // 2], w.msg_ids[max(0, mid - SPLIT_OVERLAP // 2):]]
    return [Window(wid=f"{w.wid}.h{i}", chat_id=w.chat_id, msg_ids=ids, candidates=[c for c in w.candidates if c in ids],
                   reasons={c: r for c, r in w.reasons.items() if c in ids}) for i, ids in enumerate(parts, 1)]


def v1_settings(data) -> None:
    """Window and prompt settings of the v1 runs (no candidate cap per window, no runtime name), for cache replay."""
    from coldstart import config
    config.WINDOW_MAX_CANDIDATES = 10**6
    config.PROMPT_COMPACT = False
    config.LOOPS_USE_ME_NAME = False
    config.PROMPT_CHAR_BUDGET, config.WINDOWS_PER_CALL = 16000, 10  # batching of the original v1 runs (cache keys)
    data.me_names, data.me_pattern = [], None


SYSTEM_V2 = (ROOT / "eval" / "prompt_archive" / "loops_system_v2.txt").read_text(encoding="utf-8")


def v2_settings() -> None:
    """Window and prompt settings of v2 (the slice was built with them): candidate cap 8, compact format, runtime name."""
    from coldstart import config
    config.WINDOW_MAX_CANDIDATES = 8
    config.PROMPT_COMPACT = True
    config.LOOPS_USE_ME_NAME = True


def v1_reply(reply: Any) -> Any:
    """v1 replies used an older name for the needs-the-user-only feature; map it to the current field."""
    if isinstance(reply, dict) and isinstance(reply.get("loops"), list):
        for r in reply["loops"]:
            if isinstance(r, dict):
                for k in [k for k in r if k.startswith("needs_") and k.endswith("_only") and k != "needs_me_only"]:
                    r["needs_me_only"] = r.pop(k)
    return reply


def evaluate(data, maps, replies, wins_by_id, key, pos, scope: Set[str], json_fail: int = 0,
             split_msgs: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Score the replies on `scope` (message ids): our loops and key loops with a source inside it."""
    raw, failed = [], 0
    for reply, bmap in zip(replies, maps):
        if reply is None:
            failed += 1
            continue
        raw += loops.parse_reply(v1_reply(reply), bmap, data, wins_by_id)
    ours = [l for l in decide.decide(loops.merge(raw, data), data) if l["source_msg"] in scope]
    key = [a for a in key if any(s in scope for s in a["source_msgs"])]
    pairs, used = [], set()
    for l in ours:
        for j, a in enumerate(key):
            if j not in used and l["source_msg"] in a["source_msgs"]:
                used.add(j)
                pairs.append((l, a))
                break
    n = len(pairs)
    p = n / len(ours) if ours else 0.0
    r = n / len(key) if key else 0.0
    src = {"exact": 0, "earlier": 0, "later": 0, "none": 0}
    for j, a in enumerate(key):
        if j in used:
            src["exact"] += 1
            continue
        s = min(a["source_msgs"], key=lambda x: data.by_id[x]["ts"])
        cs, i = pos[s]
        near = [pos[l["source_msg"]][1] - i for l in ours
                if pos[l["source_msg"]][0] == cs and abs(pos[l["source_msg"]][1] - i) <= NEAR]
        if not near:
            src["none"] += 1
        else:
            src["earlier" if min(near, key=abs) < 0 else "later"] += 1
    res = {
        "calls": len(maps), "failed_calls": failed - json_fail, "json_fail": json_fail, "raw": len(raw),
        "key_loops": len(key), "proposed": len(ours), "matched": n,
        "P": round(p, 3), "R": round(r, 3), "F1": round(2 * p * r / (p + r), 3) if p + r else 0.0, "source": src,
        "status_right": sum(l["status"] == a["status"] for l, a in pairs),
        "wrong_done_on_open": sum(a["status"] == "open" and l["status"] != "open" for l, a in pairs),
        "sources": sorted(l["source_msg"] for l in ours),
    }
    if split_msgs is not None:
        in_split = [j for j, a in enumerate(key) if any(s in split_msgs for s in a["source_msgs"])]
        res["key_in_split_windows"] = f"{len(in_split)} ({sum(j in used for j in in_split)} matched)"
    return res


def usage_rows(offset: int) -> List[dict]:
    if not USAGE.exists():
        return []
    with open(USAGE, "rb") as f:
        f.seek(offset)
        return [json.loads(l) for l in f.read().decode().splitlines() if l.strip()]


def tokens(rows: List[dict]) -> Dict[str, Any]:
    reasoning, known = 0, True
    ok = [r for r in rows if r.get("error") is None]
    for r in ok:
        if r.get("reasoning_tokens") is not None:
            reasoning += r["reasoning_tokens"]
        elif r.get("total_tokens") is not None:  # Gemini: thoughts = total - prompt - completion
            reasoning += max(0, r["total_tokens"] - (r.get("prompt_tokens") or 0) - (r.get("completion_tokens") or 0))
        else:
            known = False
    return {"in": sum(r.get("prompt_tokens") or 0 for r in rows), "out": sum(r.get("completion_tokens") or 0 for r in rows),
            "reasoning": reasoning if (known and ok) else None, "logged_calls": len(ok),
            "failed_attempts": sum(r.get("error") is not None for r in rows),
            "secs_summed": round(sum(r.get("seconds") or 0 for r in rows), 1)}


def save(name: str, res: Dict[str, Any]) -> None:
    SAVED.parent.mkdir(parents=True, exist_ok=True)
    allres = json.loads(SAVED.read_text()) if SAVED.exists() else {}
    allres[name] = res
    SAVED.write_text(json.dumps(allres, indent=1))


# ------------------------------------------------------------------ gemini

def run_gemini(args, data, wins_by_id, wins, small_prompts, small_maps, key, pos, cache) -> int:
    model = os.environ.get("LLM_MODEL", "")
    small_replies = []
    for p in small_prompts:
        try:
            small_replies.append(parse_json((cache / f"{cache_key(model, p)}.txt").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            small_replies.append(None)
    all_rows = [json.loads(l) for l in USAGE.read_text().splitlines() if l.strip()]
    small_rows = [r for r in all_rows if r.get("model") == model
                  and 1000 < (r.get("prompt_tokens") or 0) < BIG_CALL_MIN_PROMPT][: len(small_prompts)]

    budget = int(sum(len(p) for p in small_prompts) / args.calls) + 2000
    big_prompts, big_maps = loops.build_prompts(data, wins, char_budget=budget, per_call=10_000)
    if args.cache_only:
        def no_calls(*_a, **_k):
            raise ConnectionError("cache-only: no requests made")
        client = LLM(concurrency=3, timeout=600, cache_dir=str(cache), retries=0, chat_fn=no_calls)
    else:
        client = LLM(concurrency=3, timeout=600, cache_dir=str(cache))
    t0 = time.time()
    with client.stage("bigbatch"):
        replies = client.map_json(big_prompts, system=SYSTEM_V1)
    wall = round(time.time() - t0, 1)
    if client.exhausted:
        print(f"QUOTA SPENT: {client.exhausted}")
        return 3
    # Compare on the windows whose big call succeeded (all of them, when none failed).
    ok_wids = {wid for reply, bmap in zip(replies, big_maps) if reply is not None for wid, _ in bmap.values()}
    scope = {m for w in wins if w.wid in ok_wids for m in w.msg_ids}
    narrowed = len(ok_wids) < len(wins)
    note = (f"compared on the {len(ok_wids)} of {len(wins)} windows whose big call succeeded "
            f"({len(scope)} messages)" if narrowed else f"all {len(wins)} windows")
    big_rows = [r for r in all_rows + usage_rows(0)[len(all_rows):]
                if r.get("model") == model and (r.get("prompt_tokens") or 0) >= BIG_CALL_MIN_PROMPT]

    res = evaluate(data, small_maps, small_replies, wins_by_id, key, pos, scope)
    res.update(tokens(small_rows), wall="(cached run, concurrency 8)", model=model, setup="default batches",
               note=note + "; tokens are for all 12 small calls")
    save("gemini small", res)
    res = evaluate(data, big_maps, replies, wins_by_id, key, pos, scope, sum(client.json_failures.values()))
    res.update(tokens(big_rows), wall=f"{wall}s (this invocation)", model=model,
               setup=f"{len(big_prompts)} big calls, {len(big_prompts) - res['failed_calls']} succeeded",
               note=note + "; tokens are summed over the big calls that ever succeeded (some cached)")
    save("gemini big", res)
    return 0


# ------------------------------------------------------------------ groq

def big_scope(data, wins: List[Window], small_prompts: List[str], cache: Path, calls: int = 3) -> Set[str]:
    """Messages of the windows covered by the cached Gemini big calls (same packing as run_gemini)."""
    budget = int(sum(len(p) for p in small_prompts) / calls) + 2000
    bprompts, bmaps = loops.build_prompts(data, wins, char_budget=budget, per_call=10_000)
    ok = {wid for p, bm in zip(bprompts, bmaps) if (cache / f"{cache_key('gemini-3.5-flash', p)}.txt").exists()
          for wid, _ in bm.values()}
    return {m for w in wins if w.wid in ok for m in w.msg_ids}


def run_groq(args, data, wins_by_id, wins, small_prompts, key, pos, cache) -> int:
    model = os.environ.get("LLM_MODEL", "")
    pieces = [p for w in wins for p in split(data, w, args.char_budget)]
    split_msgs = {m for w in wins if len(split(data, w, args.char_budget)) > 1 for m in w.msg_ids}
    extra = {"reasoning_effort": args.effort} if args.effort else None
    if args.cache_only:
        def no_calls(*_a, **_k):
            raise ConnectionError("cache-only: no requests made")
        client = LLM(concurrency=1, timeout=300, cache_dir=str(cache), extra=extra, retries=0, chat_fn=no_calls)
    else:
        client = LLM(concurrency=1, timeout=300, cache_dir=str(cache), extra=extra)
    groups = loops._batches(data, pieces, args.char_budget, 10_000)
    offset = USAGE.stat().st_size if USAGE.exists() else 0
    t0 = time.time()
    replies: List[Any] = []
    maps: List[Dict[str, Tuple[str, Dict[str, str]]]] = []
    done_wids: Set[str] = set()
    truncations = 0
    stopped = None

    def spent() -> int:
        rows = usage_rows(offset)
        return sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows)

    def one(group: List[Window], allow_split: bool) -> None:
        nonlocal truncations
        prompts, bmaps = loops.build_prompts(data, group, char_budget=10**9, per_call=10**6)
        before = USAGE.stat().st_size if USAGE.exists() else 0
        messages = [{"role": "system", "content": SYSTEM_V1}, {"role": "user", "content": prompts[0]}]
        try:
            text = client.complete(messages, max_tokens=args.max_tokens)
        except RuntimeError as e:
            print(f"  call failed: {e}")
            replies.append(None)
            maps.append(bmaps[0])
            return
        rows = usage_rows(before)
        cut = bool(rows) and rows[-1].get("finish_reason") == "length"
        try:
            reply = parse_json(text)
        except ValueError:
            reply, cut = None, True
        if cut:
            truncations += 1
            halves = ([group[: len(group) // 2], group[len(group) // 2:]] if len(group) > 1
                      else [[h] for h in halve(group[0])])
            if allow_split and len(halves) > 1:
                for h in halves:
                    wins_by_id.update({w.wid: w for w in h})
                    one(h, allow_split=False)
                return
            replies.append(None)
            maps.append(bmaps[0])
            client.json_failures["groq"] = client.json_failures.get("groq", 0) + 1
            return
        replies.append(reply)
        maps.append(bmaps[0])
        done_wids.update(w.wid.split(".")[0] for w in group)

    with client.stage("groq"):
        for i, g in enumerate(groups, 1):
            if spent() >= args.stop_tokens:
                stopped = f"stopped at the {args.stop_tokens}-token line after {i - 1} of {len(groups)} calls"
                break
            wins_by_id.update({w.wid: w for w in g})
            one(g, allow_split=True)
            if client.exhausted:
                stopped = f"provider quota spent after {i} of {len(groups)} calls: {client.exhausted}"
                break
            print(f"  call {i}/{len(groups)} done, tokens so far {spent()}", flush=True)
    wall = round(time.time() - t0, 1)
    scope = {m for w in wins if w.wid in done_wids for m in w.msg_ids} if stopped else {m for w in wins for m in w.msg_ids}
    if args.scope == "big":  # the windows the cached Gemini big calls cover, for a like-for-like table
        scope &= big_scope(data, wins, small_prompts, cache, args.calls)
    res = evaluate(data, maps, replies, wins_by_id, key, pos, scope, sum(client.json_failures.values()), split_msgs)
    prev = (json.loads(SAVED.read_text()).get(f"groq {model.split('/')[-1]}", {}) if SAVED.exists() else {})
    run_stats = ({k: prev.get(k) for k in ("in", "out", "reasoning", "logged_calls", "failed_attempts", "secs_summed",
                                            "wall", "truncations")} if args.cache_only else
                 dict(tokens(usage_rows(offset)), wall=f"{wall}s", truncations=truncations))
    res.update(run_stats, model=model,
               setup=f"{len(groups)} calls over {len(pieces)} pieces (of {len(wins)} windows), "
                     f"effort={args.effort or 'default'}, max_tokens={args.max_tokens}",
               note=((stopped + "; ") if stopped else "") + f"compared on {len(scope)} messages"
               + ("; re-scored from cache, tokens/time are from the live run" if args.cache_only else ""))
    save(f"groq {model.split('/')[-1]}", res)
    return 3 if stopped and "quota" in stopped else 0


# ------------------------------------------------------------------ table

def table(out_md: Optional[Path] = None) -> int:
    if not SAVED.exists():
        print("nothing saved yet")
        return 1
    allres = json.loads(SAVED.read_text())
    names = [n for n in ("gemini small", "gemini big") if n in allres] + sorted(n for n in allres if n.startswith("groq"))
    rows = [("model", "model"), ("set-up", "setup"), ("prompts answered or failed", "calls"),
            ("API calls logged (ok)", "logged_calls"), ("failed attempts (count vs quota)", "failed_attempts"), ("failed calls (transport)", "failed_calls"),
            ("JSON failures", "json_fail"), ("truncations (split + retried)", "truncations"),
            ("key loops in compared windows", "key_loops"), ("loops proposed", "proposed"), ("matching key", "matched"),
            ("precision", "P"), ("recall", "R"), ("F1", "F1"), ("source exact/earlier/later/none", "source"),
            ("status right (of matched)", "status_right"), ("wrong done on open loops", "wrong_done_on_open"),
            ("key loops in split windows", "key_in_split_windows"), ("input tokens", "in"), ("output tokens", "out"),
            ("reasoning tokens", "reasoning"), ("time (wall)", "wall"), ("time (summed per call)", "secs_summed")]
    lines = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, k in rows:
        cells = []
        for n in names:
            v = allres[n].get(k)
            if isinstance(v, dict):
                v = "/".join(str(x) for x in v.values())
            cells.append("—" if v is None else str(v))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if len(names) > 1:
        sets = {n: set(allres[n]["sources"]) for n in names}
        lines.append("\nShared source_msgs: " + "; ".join(
            f"{a} & {b}: {len(sets[a] & sets[b])}" for i, a in enumerate(names) for b in names[i + 1:]))
    notes = [f"- **{n}**: {allres[n]['note']}" for n in names if allres[n].get("note")]
    if notes:
        lines += ["", "Notes:"] + notes
    text = "\n".join(lines)
    print(text)
    if out_md:
        out_md.write_text(text + "\n", encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["gemini", "groq", "table"])
    ap.add_argument("--data", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--sample-model", default="gemini-3.5-flash", help="model whose cached batches define the sample")
    ap.add_argument("--calls", type=int, default=3, help="gemini: target number of big calls")
    ap.add_argument("--char-budget", type=int, default=7000, help="groq: prompt chars per call (windows split to fit)")
    ap.add_argument("--max-tokens", type=int, default=2000, help="groq: completion cap (TPM counts it)")
    ap.add_argument("--effort", default="low", help="groq: reasoning_effort, '' to omit")
    ap.add_argument("--stop-tokens", type=int, default=150_000, help="groq: stop once in+out tokens reach this")
    ap.add_argument("--cache-only", action="store_true", help="make no requests; uncached calls count as failed")
    ap.add_argument("--scope", choices=["all", "big"], default="all", help="groq: score on all windows or the big-call sample")
    ap.add_argument("--md", help="table: also write the table to this markdown file")
    args = ap.parse_args()
    if args.variant == "table":
        return table(Path(args.md) if args.md else None)

    cache = Path(os.environ.get("COLDSTART_LLM_CACHE") or ".llm_cache")
    data = ingest.load(args.data)
    v1_settings(data)
    wins_by_id, wins, small_prompts, small_maps = sample(data, args.sample_model, cache)
    if not wins:
        print(f"no cached batches for {args.sample_model!r}: no sample")
        return 1
    _, _, _, ans = check.load_input(args.data)
    pos = {m["id"]: (cid, i) for cid, ms in data.by_chat.items() for i, m in enumerate(ms)}
    print(f"sample: {len(wins)} windows, {sum(len(w.msg_ids) for w in wins)} messages")
    if args.variant == "gemini":
        rc = run_gemini(args, data, wins_by_id, wins, small_prompts, small_maps, ans["open_loops"], pos, cache)
    else:
        rc = run_groq(args, data, wins_by_id, wins, small_prompts, ans["open_loops"], pos, cache)
    table()
    return rc


if __name__ == "__main__":
    sys.exit(main())
