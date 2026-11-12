"""A tiny client for any OpenAI-compatible chat API, standard library only.

It reads LLM_BASE_URL, LLM_API_KEY and LLM_MODEL from the environment, which is how we run your
code with our own key when we score it. Every call appends its token counts to usage.jsonl, so you
can see what a run costs before we do.

    from llm import chat
    reply = chat([{"role": "user", "content": "Hello"}])

You don't have to use this file. Any client is fine, as long as it reads those three variables and
sends nothing anywhere else.
"""
import json
import os
import re
import time
import urllib.request

# Quota facts worth keeping in usage.jsonl: rate-limit headers, and the limit/used/retry part of a 429 message
# (never the whole body, which can name the account).
_LIMIT_FACTS = re.compile(r"(on \w[\w ]* \(\w+\): Limit \d+, Used \d+, Requested \d+|(?:retry|try again) in [\d.hms]+)",
                          re.IGNORECASE)

_ORG = re.compile(r"org[_-]\w+")


def _ratelimit(headers):
    if headers is None:
        return None
    out = {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"}
    return out or None


def chat(messages, model=None, temperature=0, max_tokens=None, response_format=None, timeout=120, extra=None,
         meta=None):
    """extra: optional provider-specific body fields, e.g. {"reasoning_effort": "low"}.
    meta: optional dict, filled with this call's usage record (tokens, cost, provider, generation id)."""
    base = os.environ["LLM_BASE_URL"].strip().rstrip("/")  # tolerate CRLF / padded env files
    body = {"model": model or os.environ["LLM_MODEL"].strip(), "messages": messages, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_format:
        body["response_format"] = response_format
    if extra:
        body.update(extra)
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        # A neutral User-Agent: some providers' edge (e.g. Cloudflare error 1010) block urllib's default one.
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"].strip(), "Content-Type": "application/json",
                 "User-Agent": "coldstart-llm-client/1.0"},
    )
    started = time.time()
    limits = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            limits = _ratelimit(resp.headers)
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("choices"):  # some gateways report provider errors inside a 200 response
            err = data.get("error") or {}
            raise RuntimeError(f"no choices in reply: {err.get('code', '')} {str(err.get('message', ''))[:200]}")
    except Exception as e:
        # Failed attempts are logged too: free tiers count them against the daily request quota.
        facts = None
        if hasattr(e, "read") and getattr(e, "code", None) in (400, 404, 413, 422, 429):
            try:
                text = e.read().decode("utf-8", "replace")
                e._coldstart_body = text  # the caller reads the body for its retry hint; it can only be read once
                if e.code == 429:
                    facts = "; ".join(_LIMIT_FACTS.findall(text)) or None
                else:  # the provider's error code and the start of its message, e.g. a JSON-mode failure
                    err = (json.loads(text).get("error") or {}) if text.strip().startswith("{") else {}
                    facts = (f"{err.get('code') or err.get('type') or ''}: "
                             f"{_ORG.sub('org', str(err.get('message') or text))[:160]}") or None
            except Exception:
                pass
        with open("usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "model": body["model"],
                "prompt_tokens": None,
                "completion_tokens": None,
                "error": getattr(e, "code", None) or type(e).__name__,
                "limit": facts,
                "ratelimit": _ratelimit(getattr(e, "headers", None)),
                "seconds": round(time.time() - started, 2),
                "ts": round(started, 1),
            }) + "\n")
        raise
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    record = {
        "model": body["model"],
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost": usage.get("cost"),  # OpenRouter: the amount charged for this call, in USD (None elsewhere)
        "provider": data.get("provider"),  # OpenRouter names the serving provider here (None elsewhere)
        "id": data.get("id"),
        "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
        "ratelimit": limits,
        "seconds": round(time.time() - started, 2),
        "ts": round(started, 1),
    }
    with open("usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    if meta is not None:
        meta.update(record)
    return data["choices"][0]["message"]["content"]
