#!/usr/bin/env python3
"""Zero-cost check of OpenRouter routing for our request: how many endpoints stay eligible under each provider setting.

    python3 eval/route_check.py [model ...]      # default: openai/gpt-oss-120b openai/gpt-oss-20b

Reads only public, keyless endpoints: /models/{model}/endpoints and /endpoints/zdr. The endpoint listing has no
data-policy field, so data_collection "deny" is bounded from below by the ZDR list (ZDR is stricter than "deny").
"""
from __future__ import annotations

import json
import sys
import urllib.request
from typing import List

API = "https://openrouter.ai/api/v1"
SENT = {"response_format", "reasoning", "max_tokens", "temperature"}  # every body field our client sends


def get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"User-Agent": "coldstart-route-check/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def report(model: str, zdr_tags: set) -> None:
    eps: List[dict] = get(f"/models/{model}/endpoints")["data"]["endpoints"]
    live = lambda es: [e for e in es if e.get("status") == 0]  # noqa: E731
    provs = lambda es: len({e["provider_name"] for e in es})  # noqa: E731
    req = [e for e in eps if SENT <= set(e.get("supported_parameters") or [])]
    deny = [e for e in eps if (model, e["tag"]) in zdr_tags]
    both = [e for e in req if (model, e["tag"]) in zdr_tags]
    print(f"{model}: {len(eps)} endpoints ({len(live(eps))} live, {provs(live(eps))} providers)")
    for name, es in [("require_parameters", req), ("data_collection deny (>= ZDR list)", deny), ("both", both)]:
        print(f"  {name:<36} {len(es):>3} endpoints, {len(live(es)):>3} live, {provs(live(es)):>3} providers")
    for e in eps:
        if e not in req:
            print(f"    excluded by require_parameters: {e['tag']} (no {', '.join(sorted(SENT - set(e.get('supported_parameters') or [])))})")
    prices = sorted((float(e["pricing"]["prompt"]) * 1e6, float(e["pricing"]["completion"]) * 1e6, e["tag"]) for e in live(both))
    if prices:
        print(f"  price range of eligible live endpoints, $/M in/out: {prices[0][:2]} .. max in {max(p[0] for p in prices)}, "
              f"max out {max(p[1] for p in prices)}")


def main() -> int:
    models = sys.argv[1:] or ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    zdr = {(e.get("model_id"), e.get("tag")) for e in get("/endpoints/zdr").get("data", [])}
    for m in models:
        report(m, zdr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
