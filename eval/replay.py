#!/usr/bin/env python3
"""Zero-call replay of a paid run from its saved replies: raw loops (with probabilities) and re-scored outputs.

    python3 eval/replay.py .llm_cache/run1-frozen eval/runs/out_run1.json     # check: replay == the run's output

Library use (eval/analyse_status_help.py, ablations):
    raw = replay_raw(cache)                  # merged raw loops, before decide
    out = with_loops(base_out, decide(raw))  # the run's output with re-decided loops
A cache miss is never sent anywhere: it raises, so a replay can only use replies the run really got.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import check  # noqa: E402
import help_goal_score  # noqa: E402
from coldstart import decide as decide_mod, ingest, llm_client, loops  # noqa: E402

DEV = ROOT / "data" / "dev"
OR_ENV = {"LLM_BASE_URL": "https://openrouter.ai/api/v1", "LLM_MODEL": "openai/gpt-oss-120b", "LLM_API_KEY": "replay"}


class CacheMiss(RuntimeError):
    pass


def _no_calls(*a: Any, **k: Any) -> str:
    raise CacheMiss("not in the run's cache")


def client_for(cache: str, env: Optional[Dict[str, str]] = None) -> llm_client.LLM:
    os.environ.update(env or OR_ENV)
    sub = Path(cache) / (llm_client._host() or "unknown-host")
    return llm_client.LLM(concurrency=1, cache_dir=str(sub if sub.exists() else cache), chat_fn=_no_calls, retries=0,
                          extra=llm_client.provider_extra())


_DATA: Dict[str, ingest.Data] = {}


def data(data_dir: str = str(DEV)) -> ingest.Data:
    if data_dir not in _DATA:
        _DATA[data_dir] = ingest.load(data_dir)
    return _DATA[data_dir]


def replay_raw(cache: str, data_dir: str = str(DEV), env: Optional[Dict[str, str]] = None,
               legacy_reask: Optional[bool] = None) -> List[dict]:
    """legacy_reask: replay a run made before the truncation split (changelog 18), where a cut reply was re-asked.
    Default: on when the cache has no finish_reason sidecars."""
    client = client_for(cache, env)
    if legacy_reask is None:
        legacy_reask = not any(client.cache_dir.glob("*.meta.json"))
    if legacy_reask:
        client.map_json_ex = lambda prompts, **k: [(r, False) for r in client.map_json(prompts, **k)]  # type: ignore
    raw = loops.extract(data(data_dir), client)
    missed = client.failures.get("loops", 0)
    if missed:
        print(f"replay: {missed} loops prompts were not in the cache", file=sys.stderr)
    return raw


def with_loops(base_out: dict, open_loops: List[dict]) -> dict:
    out = copy.deepcopy(base_out)
    out["open_loops"] = open_loops
    return out


def decide(raw: List[dict], data_dir: str = str(DEV), status_fn: Optional[Callable[[dict], str]] = None,
           help_fn: Optional[Callable[..., dict]] = None) -> List[dict]:
    """decide.decide with optional status / help rules swapped in (for ablations)."""
    d = data(data_dir)
    old_s, old_h = decide_mod.status, decide_mod.help_for
    try:
        if status_fn:
            decide_mod.status = status_fn
        if help_fn:
            decide_mod.help_for = help_fn
        return decide_mod.decide(copy.deepcopy(raw), d)
    finally:
        decide_mod.status, decide_mod.help_for = old_s, old_h


def score(out: dict, data_dir: str = str(DEV)) -> Dict[str, Optional[float]]:
    chats, messages, seed, answers = check.load_input(data_dir)
    s = check.score(out, answers, seed, messages)
    hg = help_goal_score.run_obj(data_dir, out)
    s.update({"help": hg["help"], "goals": hg["goals"]})
    return s


def points(s: Dict[str, Optional[float]]) -> Dict[str, float]:
    return {k: round((s.get(k) or 0.0) * v, 2) for k, v in check.POINTS.items() if s.get(k) is not None}


def main(argv: List[str]) -> int:
    cache, base = argv[1], argv[2]
    base_out = json.loads(Path(base).read_text(encoding="utf-8"))
    raw = replay_raw(cache)
    out = with_loops(base_out, decide(raw))
    same = out["open_loops"] == base_out["open_loops"]
    print(f"replayed {len(raw)} loops; identical to the run's open_loops: {same}")
    print("points:", points(score(out)))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
