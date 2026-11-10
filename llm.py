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
import time
import urllib.request


def chat(messages, model=None, temperature=0, max_tokens=None, response_format=None, timeout=120, extra=None):
    """extra: optional provider-specific body fields, e.g. {"reasoning_effort": "low"}."""
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = {"model": model or os.environ["LLM_MODEL"], "messages": messages, "temperature": temperature}
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
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"], "Content-Type": "application/json",
                 "User-Agent": "coldstart-llm-client/1.0"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # Failed attempts are logged too: free tiers count them against the daily request quota.
        with open("usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "model": body["model"],
                "prompt_tokens": None,
                "completion_tokens": None,
                "error": getattr(e, "code", None) or type(e).__name__,
                "seconds": round(time.time() - started, 2),
                "ts": round(started, 1),
            }) + "\n")
        raise
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    with open("usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "model": body["model"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
            "seconds": round(time.time() - started, 2),
            "ts": round(started, 1),
        }) + "\n")
    return data["choices"][0]["message"]["content"]
