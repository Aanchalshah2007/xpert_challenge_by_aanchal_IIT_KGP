#!/usr/bin/env python3
"""Print the frozen pipeline settings (and save them to eval/runs/frozen_config.json). Zero calls.

    python3 eval/frozen_config.py            # print + save
    python3 eval/frozen_config.py --check    # exit 1 if the code no longer matches the saved freeze
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from coldstart import circles, config, goals, loops  # noqa: E402

OUT = ROOT / "eval" / "runs" / "frozen_config.json"
DEV_ONLY = ["COLDSTART_CHAR_BUDGET", "COLDSTART_MAX_TOKENS", "COLDSTART_CONCURRENCY", "COLDSTART_LLM_CACHE",
            "COLDSTART_STATS", "COLDSTART_DEADLINE_S", "COLDSTART_ABLATE"]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def frozen() -> dict:
    return {
        "model": "openai/gpt-oss-120b",
        "reasoning": config.LLM_EXTRA_BY_HOST,
        "loops_prompt": "v1 + shift + 'If unsure, choose the concrete message.'",
        "loops_prompt_sha": _sha(loops.SYSTEM),
        "circles_prompt_sha": _sha(circles.SYSTEM),
        "lexicons_sha": _sha((ROOT / "coldstart" / "lexicons.py").read_text(encoding="utf-8")),  # candidate cues
        "goals_prompt_sha": _sha(goals.SYSTEM) if hasattr(goals, "SYSTEM") else None,
        "PROMPT_COMPACT": config.PROMPT_COMPACT,
        "LOOPS_USE_ME_NAME": config.LOOPS_USE_ME_NAME,
        "WINDOW_MAX_CANDIDATES": config.WINDOW_MAX_CANDIDATES,
        "PROMPT_CHAR_BUDGET": config.PROMPT_CHAR_BUDGET,
        "WINDOWS_PER_CALL": config.WINDOWS_PER_CALL,
        "SPLIT_OVERLAP": config.SPLIT_OVERLAP,
        "LOOPS_MAX_TOKENS": config.LOOPS_MAX_TOKENS,
        "CIRCLES_CHAR_BUDGET": config.CIRCLES_CHAR_BUDGET,
        "CIRCLES_MIN_CONF": config.CIRCLES_MIN_CONF,
        "CIRCLES_SAMPLES": config.CIRCLES_SAMPLES,
        "LLM_CONCURRENCY": config.LLM_CONCURRENCY,
        "STATUS_RATIO": config.STATUS_RATIO,
        "help": "both agree: ownership rule AND 2x probability rule (decide.help_for); overdue = due date before today IST",
        "HELP_RULE": config.HELP_RULE,
        "HELP_RATIO": config.HELP_RATIO,
        "ABLATE": sorted(config.ABLATE),
        "TRUNCATION_SPLITS": config.TRUNCATION_SPLITS,
        "HELP_PRIVATE_GATE": config.HELP_PRIVATE_GATE,
        "HELP_FOLLOWUP_THEIRS": config.HELP_FOLLOWUP_THEIRS,
    }


def main() -> int:
    set_vars = [v for v in DEV_ONLY if os.environ.get(v)]
    if set_vars:
        print(f"WARNING: dev-only variables set, the values below include them: {', '.join(set_vars)}")
    f = frozen()
    if "--check" in sys.argv:
        saved = json.loads(OUT.read_text())
        diff = {k: (saved.get(k), v) for k, v in f.items() if saved.get(k) != v}
        print("frozen config: unchanged" if not diff else f"frozen config CHANGED: {diff}")
        return 1 if diff else 0
    for k, v in f.items():
        print(f"  {k:<22} {v}")
    OUT.write_text(json.dumps(f, indent=1))
    print(f"saved {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
