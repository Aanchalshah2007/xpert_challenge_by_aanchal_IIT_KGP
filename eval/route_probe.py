#!/usr/bin/env python3
"""Tiny paid probes of OpenRouter routing variants (max_tokens 64, a few dozen tokens each), through the spend ledger.

    COLDSTART_SPEND_LEDGER=eval/runs/spend.jsonl COLDSTART_RUN_ID=route-probe eval/with_env.sh .env.openrouter \
        python3 eval/route_probe.py
Prints status and the provider's error message per variant; never the key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import llm  # noqa: E402
from coldstart.spend import Ledger  # noqa: E402

R = {"reasoning": {"effort": "low"}}
VARIANTS = {
    "reasoning only": dict(R),
    "+require_parameters": {**R, "provider": {"require_parameters": True}},
    "+allow_fallbacks": {**R, "provider": {"allow_fallbacks": True}},
    "+data_collection deny": {**R, "provider": {"data_collection": "deny"}},
    "frozen (all three)": {**R, "provider": {"require_parameters": True, "allow_fallbacks": True, "data_collection": "deny"}},
}


def main() -> int:
    led = Ledger.from_env()
    assert led is not None, "set COLDSTART_SPEND_LEDGER"
    names = sys.argv[1:] or list(VARIANTS)
    msgs = [{"role": "user", "content": 'Reply with {"ok": true} only.'}]
    for name in names:
        extra = VARIANTS[name]
        est = Ledger.estimate(msgs, 64)
        led.reserve(est)
        meta: dict = {}
        try:
            llm.chat(msgs, max_tokens=64, response_format={"type": "json_object"}, extra=extra, meta=meta, timeout=60)
            led.settle(est, "probe:" + name, meta)
            print(f"{name:<24} OK  provider={meta.get('provider')} cost={meta.get('cost')}")
        except Exception as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else str(e)
            except Exception:
                body = getattr(e, "_coldstart_body", "") or str(e)
            try:
                body = (json.loads(body).get("error") or {}).get("message") or body
            except Exception:
                pass
            led.settle(est, "probe:" + name, meta, error=str(getattr(e, "code", type(e).__name__)))
            print(f"{name:<24} {getattr(e, 'code', type(e).__name__)}  {str(body)[:220]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
