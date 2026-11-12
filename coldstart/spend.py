"""Dev-only spend ledger and hard spending cap for paid runs.

Active only when COLDSTART_SPEND_LEDGER names a file (eval/runs/spend.jsonl). The scoring run never sets it,
so there this module does nothing and the request body is unchanged.

- Before each call: refuse it if (spent so far, all runs) + (calls in flight) + (this call's worst case) would pass
  the cap. The cap is COLDSTART_SPEND_CAP (default 3.00 USD), never above HARD_CAP_USD whatever the variable says.
  COLDSTART_RUN_BUDGET optionally caps this run alone.
- After each call: append one ledger line with the provider's own cost (usage.cost on OpenRouter). If a reply has no
  cost, the line says so and the worst-case estimate is counted against the cap instead (never reported as spend).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HARD_CAP_USD = 4.00
DEFAULT_CAP_USD = 3.00
# Worst-case reserve while a call is in flight, USD per token. Not used to report spend, only to stay under the cap:
# above the dearest listed endpoint of the models we call (checked with eval/route_check.py).
RESERVE_PROMPT_USD_PER_TOK = 0.5e-6
RESERVE_COMPLETION_USD_PER_TOK = 1.5e-6
RESERVE_UNCAPPED_COMPLETION = 8000  # completion tokens assumed for a call sent without max_tokens


class SpendCapError(RuntimeError):
    pass


def _env_float(name: str, default: Optional[float]) -> Optional[float]:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


class Ledger:
    def __init__(self, path: str, cap: Optional[float] = None, run_budget: Optional[float] = None,
                 run_id: Optional[str] = None) -> None:
        self.path = Path(path)
        want = cap if cap is not None else _env_float("COLDSTART_SPEND_CAP", DEFAULT_CAP_USD)
        self.cap = min(want if want is not None else DEFAULT_CAP_USD, HARD_CAP_USD)
        self.run_budget = run_budget if run_budget is not None else _env_float("COLDSTART_RUN_BUDGET", None)
        self.run_id = run_id or os.environ.get("COLDSTART_RUN_ID") or time.strftime("run-%Y%m%d-%H%M%S")
        self.spent_before = sum(_charged(r) for r in read(self.path))
        self.spent_run = 0.0
        self.in_flight = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def from_env() -> Optional["Ledger"]:
        path = os.environ.get("COLDSTART_SPEND_LEDGER")
        return Ledger(path) if path else None

    @staticmethod
    def estimate(messages: List[dict], max_tokens: Optional[int]) -> float:
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        prompt_tok = chars / 2.5 + 50  # deliberately high: real text runs ~3.5-4 chars per token
        completion = max_tokens or RESERVE_UNCAPPED_COMPLETION
        return prompt_tok * RESERVE_PROMPT_USD_PER_TOK + completion * RESERVE_COMPLETION_USD_PER_TOK

    def reserve(self, est: float) -> None:
        with self._lock:
            total = self.spent_before + self.spent_run + self.in_flight + est
            if total > self.cap:
                raise SpendCapError(f"spend cap: ${total:.4f} would pass the ${self.cap:.2f} cap")
            if self.run_budget is not None and self.spent_run + self.in_flight + est > self.run_budget:
                raise SpendCapError(f"run budget: ${self.spent_run + self.in_flight + est:.4f} would pass "
                                    f"the approved ${self.run_budget:.4f}")
            self.in_flight += est

    def settle(self, est: float, stage: str, meta: Dict[str, Any], error: Optional[str] = None) -> None:
        cost = meta.get("cost")
        rec = {
            "ts": round(time.time(), 1), "run": self.run_id, "stage": stage, "model": meta.get("model"),
            "provider": meta.get("provider"), "id": meta.get("id"), "prompt_sha": meta.get("prompt_sha"),
            "prompt_tokens": meta.get("prompt_tokens"), "completion_tokens": meta.get("completion_tokens"),
            "reasoning_tokens": meta.get("reasoning_tokens"), "cached_tokens": meta.get("cached_tokens"),
            "cost": cost, "finish_reason": meta.get("finish_reason"), "error": error,
        }
        if cost is None and error is None:
            rec["cost_missing_reserved"] = round(est, 6)  # counted against the cap, not reported as spend
        with self._lock:
            self.in_flight = max(0.0, self.in_flight - est)
            self.spent_run += _charged(rec)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")


def _charged(rec: dict) -> float:
    """What a ledger line counts against the cap: the real cost, or the reserve when the reply had no cost."""
    if rec.get("cost") is not None:
        return float(rec["cost"])
    return float(rec.get("cost_missing_reserved") or 0.0)


def read(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
