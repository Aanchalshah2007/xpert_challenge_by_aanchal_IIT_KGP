#!/usr/bin/env python3
"""Convert a filled labels/to_label.md into labels/help.json and labels/goals.json.

    python3 eval/labels_from_sheet.py data/dev

Blank or invalid entries are skipped and reported. Goals sharing a `group` tag merge into one goal.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402

FIELD = re.compile(r"^- (\w+):[ \t]*(.*)$")


def parse(text: str) -> List[Dict[str, str]]:
    blocks, cur = [], None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = {"_id": line[4:].strip()}
            blocks.append(cur)
        elif cur is not None:
            m = FIELD.match(line)
            if m:
                cur[m.group(1)] = m.group(2).strip()
    return blocks


def main(argv: List[str]) -> int:
    folder = Path(argv[1] if len(argv) > 1 else ROOT / "data" / "dev")
    blocks = parse((folder / "labels" / "to_label.md").read_text(encoding="utf-8"))
    warn: List[str] = []

    help_out = []
    for b in (b for b in blocks if b["_id"].startswith("H")):
        action = b.get("help", "").lower()
        if not action:
            continue
        if action not in check.ACTIONS:
            warn.append(f"{b['_id']}: help '{action}' is not one of {sorted(check.ACTIONS)}")
            continue
        help_out.append({"source_msg": b["source_msg"], "help": action, "why": b.get("why", "")})

    groups: Dict[str, dict] = {}
    for b in (b for b in blocks if b["_id"].startswith("G")):
        if b.get("keep", "").lower() not in ("y", "yes"):
            continue
        tag = b.get("group") or b["_id"]
        g = groups.get(tag)
        if g is None:
            g = groups[tag] = {"title": "", "area": "", "type": "", "horizon": "", "evidence": [], "_ids": []}
        for k in ("title", "area", "type", "horizon"):
            if b.get(k) and not g[k]:
                g[k] = b[k].strip().lower() if k != "title" else b[k].strip()
        g["evidence"].append(b["msg"])
        g["_ids"].append(b["_id"])
    goals_out = []
    for tag, g in groups.items():
        ids = ",".join(g.pop("_ids"))
        bad = [k for k, allowed in (("area", check.AREAS), ("type", check.GOAL_TYPES), ("horizon", check.HORIZONS))
               if g[k] not in allowed]
        if bad or not g["title"]:
            warn.append(f"goal {tag} ({ids}): missing/invalid {bad or ['title']}")
            continue
        goals_out.append(g)

    (folder / "labels" / "help.json").write_text(json.dumps(help_out, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "labels" / "goals.json").write_text(json.dumps(goals_out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"help.json: {len(help_out)} labels | goals.json: {len(goals_out)} goals")
    for w in warn:
        print("  skipped:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
