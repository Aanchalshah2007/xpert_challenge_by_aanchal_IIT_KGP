#!/usr/bin/env python3
"""Zero-call check of the implemented circles consensus (circles.extract + circles.vote + people.build):
each run's saved circles replies act as one sample; every triple of runs is scored.

    python3 eval/consensus_check.py run1-frozen rep2 rep3 abl_a1 ...
"""
from __future__ import annotations

import itertools
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
import replay as rp  # noqa: E402
from coldstart import circles, orgs, people, profile  # noqa: E402


def main(argv):
    d = rp.data()
    prof = profile.build(d)
    o = orgs.build(prof, d.seed)
    pinned = dict(d.seed.get("circles") or {})
    chats, messages, seed, ans = check.load_input(str(rp.DEV))
    base = json.loads((ROOT / "eval" / "runs" / "out_rep2.json").read_text())
    labels = {r: circles.extract(d, prof, pinned, rp.client_for(f".llm_cache/{r}")) for r in argv[1:]}

    def pts(lab):
        out = dict(base, people=people.build(prof, o, d.seed, lab))
        return check.score(out, ans, seed, messages)["circles"] * check.POINTS["circles"]

    single = [pts(l) for l in labels.values()]
    tri = [pts(circles.vote([labels[r] for r in t])) for t in itertools.combinations(labels, 3)]
    print(f"implemented path: single mean {st.mean(single):.2f} SD {st.pstdev(single):.2f} (n={len(single)}) | "
          f"consensus mean {st.mean(tri):.2f} SD {st.pstdev(tri):.2f} (n={len(tri)} triples)")


if __name__ == "__main__":
    main(sys.argv)
