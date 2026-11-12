#!/usr/bin/env python3
"""Loop quality per serving provider, pooled over paid runs. Zero calls (replays saved replies).

    python3 eval/provider_quality.py rep2 rep3 abl_a1:burst ...

Each run's ledger lines carry the prompt hash; replaying the run records which windows went into each prompt, so
every key loop whose source sat in a window can be credited to the provider that answered that prompt.
Per provider: prompts, key loops seen in its windows, matched (recall), our loops proposed from its replies, of which
matched (precision), F1.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from ablate_decide import _set_ablate  # noqa: E402
from coldstart import loops  # noqa: E402
from coldstart.spend import read  # noqa: E402


def run_stats(run: str, flags: str, ans: dict, totals: Dict[str, Dict[str, int]]) -> None:
    ledger = {r["prompt_sha"]: r.get("provider") for r in read(ROOT / "eval" / "runs" / "spend.jsonl")
              if r.get("run") == run and r.get("prompt_sha") and r.get("stage") == "loops" and not r.get("error")}
    _set_ablate(flags)
    seen: List[tuple] = []  # (provider, set of message ids in the prompt)
    orig = loops.build_prompts

    def spy(data, wins, *a, **k):  # type: ignore[no-untyped-def]
        prompts, maps = orig(data, wins, *a, **k)
        for p, m in zip(prompts, maps):
            msgs = [{"role": "system", "content": loops.SYSTEM}, {"role": "user", "content": p}]
            sha = hashlib.sha256(json.dumps(msgs, sort_keys=True).encode()).hexdigest()[:16]
            ids = {mid for _, local in m.values() for mid in local.values()}
            seen.append((ledger.get(sha), ids, {mid for _, local in m.values() for mid in local.values()}))
        return prompts, maps

    loops.build_prompts = spy
    try:
        raw = rp.replay_raw(f".llm_cache/{run}")
    finally:
        loops.build_prompts = orig
        _set_ablate("")
    src_prov = {}
    for prov, ids, _ in seen:
        for i in ids:
            src_prov.setdefault(i, prov)
    accepted = {s: j for j, a in enumerate(ans["open_loops"]) for s in a["source_msgs"]}
    ours = rp.decide(raw)
    matched_key = set()
    for l in ours:
        prov = src_prov.get(l["source_msg"]) or "?"
        totals[prov]["proposed"] += 1
        j = accepted.get(l["source_msg"])
        if j is not None and j not in matched_key:
            matched_key.add(j)
            totals[prov]["proposed_matched"] += 1
    for j, a in enumerate(ans["open_loops"]):
        provs = {src_prov.get(s) for s in a["source_msgs"] if s in src_prov}
        prov = sorted(p or "?" for p in provs)[0] if provs else "?"
        totals[prov]["key_seen"] += 1
        totals[prov]["key_matched"] += j in matched_key
    for prov, _, _ in seen:
        totals[prov or "?"]["prompts"] += 1


def main(argv: List[str]) -> int:
    _, _, _, ans = check.load_input(str(rp.DEV))
    totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for spec in argv[1:]:
        run, _, flags = spec.partition(":")
        run_stats(run, flags, ans, totals)
    print(f"{len(argv) - 1} runs pooled. A key loop is credited to the provider of the first prompt holding its source.")
    print(f"{'provider':<16}{'prompts':>8}{'key seen':>9}{'recall':>8}{'proposed':>9}{'precision':>10}{'F1':>6}")
    for prov, t in sorted(totals.items(), key=lambda kv: -kv[1]["prompts"]):
        rec = t["key_matched"] / t["key_seen"] if t["key_seen"] else 0
        pre = t["proposed_matched"] / t["proposed"] if t["proposed"] else 0
        f1 = 2 * rec * pre / (rec + pre) if rec + pre else 0
        print(f"{prov:<16}{t['prompts']:>8}{t['key_seen']:>9}{rec:>8.2f}{t['proposed']:>9}{pre:>10.2f}{f1:>6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
