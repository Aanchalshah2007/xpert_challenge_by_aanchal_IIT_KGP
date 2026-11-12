#!/usr/bin/env python3
"""Circles: rules vs the LLM card pass, scored like check.py (seeded people excluded).

    set -a; source <(tr -d '\\r' < .env.groq); set +a
    COLDSTART_CHAR_BUDGET=7000 COLDSTART_MAX_TOKENS=2000 COLDSTART_LLM_CACHE=.llm_cache python3 eval/circles_eval.py [--cache-only]

With --cache-only no requests are made (uncached calls count as failed). Prints the score of the rules, of
the LLM pass at the configured confidence, and the confusion of the remaining errors.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import circles, config, ingest, llm_client, orgs, people, profile  # noqa: E402
from coldstart.llm_client import LLM  # noqa: E402


def score(ppl, ans, seeded):
    mine = {p["id"]: p["circle"] for p in ppl}
    scored = [p for p in ans["people"] if p["id"] not in seeded]
    right = sum(mine.get(p["id"]) == p["circle"] for p in scored)
    wrong = Counter((p["circle"], mine.get(p["id"])) for p in scored if mine.get(p["id"]) != p["circle"])
    return right, len(scored), wrong


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--cache-only", action="store_true")
    args = ap.parse_args()
    data = ingest.load(args.data)
    prof = profile.build(data)
    o = orgs.build(prof, data.seed)
    pinned = dict(data.seed.get("circles") or {})
    _, _, _, ans = check.load_input(args.data)
    seeded = set(pinned)

    kw = {}
    if args.cache_only:
        def no_calls(*_a, **_k):
            raise ConnectionError("cache-only: no requests made")
        kw = {"retries": 0, "chat_fn": no_calls}
    client = LLM(concurrency=config.LLM_CONCURRENCY, timeout=300, extra=llm_client.provider_extra(),
                 cache_dir=os.environ.get("COLDSTART_LLM_CACHE") or ".llm_cache", **kw)
    labels = circles.extract(data, prof, pinned, client)
    n_cards = len(circles.cards(data, prof, pinned))

    rules = people.build(prof, o, data.seed)
    r_right, n, r_wrong = score(rules, ans, seeded)
    print(f"model {os.environ.get('LLM_MODEL')} | cards {n_cards} | labelled {len(labels)} | "
          f"scored people {n} (seeded excluded)")
    print(f"rules only:            {r_right}/{n} = {r_right / n:.3f}  -> {12 * r_right / n:.2f}/12")
    for conf in (0.0, 0.5, config.CIRCLES_MIN_CONF, 0.8):
        config.CIRCLES_MIN_CONF = conf
        right, _, wrong = score(people.build(prof, o, data.seed, labels), ans, seeded)
        print(f"LLM at conf >= {conf:.1f}:    {right}/{n} = {right / n:.3f}  -> {12 * right / n:.2f}/12")
    config.CIRCLES_MIN_CONF = 0.6
    _, _, wrong = score(people.build(prof, o, data.seed, labels), ans, seeded)
    print("remaining errors (key -> ours): " + ", ".join(f"{a}->{b} x{k}" for (a, b), k in wrong.most_common()))
    s = client.summary(len(data.messages)) if hasattr(client, "summary") else {}
    if s:
        print(f"usage: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
