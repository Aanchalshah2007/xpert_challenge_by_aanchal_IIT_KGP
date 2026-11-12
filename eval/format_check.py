#!/usr/bin/env python3
"""Prove the compact loop-prompt format is lossless against the v1 line format. Zero calls.

    python3 eval/format_check.py

For every window of the current pipeline, builds the prompt in both formats with identical windows and
batching, parses the compact one back into (local id, cue mark, date, time, sender, text) per line, and
checks it equals the v1 line (text compared with whitespace collapsed, the only intended change).
Also checks every key-loop source, its window context and any later closing message appear in the prompt.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import candidates, config, ingest, loops  # noqa: E402

V1_LINE = re.compile(r"^([* ])(m\d+) (\w{3} \d{2} \w{3}) (\d{2}:\d{2}) (\S+): (.*)$", re.S)
NEW_LINE = re.compile(r"^([* ])(m\d+) (\d{2}:\d{2}) (\S+): (.*)$", re.S)


def parse(prompt: str, compact: bool):
    out, day = [], None
    for line in prompt.split("\n"):
        if compact and line.startswith("-- "):
            day = line[3:]
            continue
        m = (NEW_LINE if compact else V1_LINE).match(line)
        if not m:
            continue
        if compact:
            mark, mid, hm, who, text = m.groups()
            out.append((mark, mid, day, hm, who, " ".join(text.split())))
        else:
            mark, mid, d, hm, who, text = m.groups()
            out.append((mark, mid, d, hm, who, " ".join(text.split())))
    return out


def same(old: tuple, new: tuple) -> bool:
    """Equal fields; a truncated text only needs to agree on the shared prefix (collapsing whitespace lets
    a little more of a long message fit under MAX_MSG_CHARS, never less)."""
    if old[:5] != new[:5]:
        return False
    a, b = old[5], new[5]
    if a == b:
        return True
    if a.endswith("…") or b.endswith("…"):
        a, b = a.rstrip("…").rstrip(), b.rstrip("…").rstrip()
        return a.startswith(b) or b.startswith(a)
    return False


def main() -> int:
    data = ingest.load(str(ROOT / "data" / "dev"))
    wins = candidates.windows(data, candidates.find(data))
    config.PROMPT_COMPACT = False
    old_p, old_m = loops.build_prompts(data, wins)
    config.PROMPT_COMPACT = True
    new_p, new_m = loops.build_prompts(data, wins)
    assert old_m == new_m, "local id maps differ"
    bad = 0
    for a, b, bm in zip(old_p, new_p, new_m):
        la, lb = parse(a, False), parse(b, True)
        n_msgs = sum(len(local) for _, local in bm.values())
        if len(la) != len(lb) or len(lb) != n_msgs or any(not same(x, y) for x, y in zip(la, lb)):
            bad += 1
    _, _, _, ans = check.load_input(str(ROOT / "data" / "dev"))
    in_prompt = {mid for bm in new_m for _, local in bm.values() for mid in local.values()}
    srcs = {s for a in ans["open_loops"] for s in a["source_msgs"]}
    closes = {c for a in ans["open_loops"] for c in (a.get("closing_msgs") or []) if isinstance(c, str)}
    ctx_missing = 0
    for w in wins:
        if set(w.msg_ids) & srcs:
            ctx_missing += sum(m not in in_prompt for m in w.msg_ids)
    chars_old, chars_new = sum(map(len, old_p)), sum(map(len, new_p))
    print(f"prompts {len(new_p)} | windows {len(wins)} | message lines {len(in_prompt)}")
    print(f"line-by-line equal (id, cue mark, date, time, sender, text): {len(new_p) - bad}/{len(new_p)} prompts")
    print(f"key sources in prompt: {len(srcs & in_prompt)}/{len(srcs)} | context lines of their windows missing: "
          f"{ctx_missing}" + (f" | key closing msgs in prompt: {len(closes & in_prompt)}/{len(closes)}" if closes else ""))
    print(f"prompt chars v1 format {chars_old:,} -> compact {chars_new:,} ({100 * (chars_new - chars_old) / chars_old:+.1f}%)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
