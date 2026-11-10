"""Wrapper around the starter llm.py: thread pool, retries, timeout, JSON parsing/repair,
per-stage token tally (from usage.jsonl) and an opt-in disk cache for dev runs.

    client = LLM()
    with client.stage("loops"):
        replies = client.map_json([prompt_a, prompt_b], system=SYS)   # list of parsed JSON or None
"""
from __future__ import annotations

import hashlib
import socket
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm  # noqa: E402

from .util import log  # noqa: E402

USAGE_FILE = Path("usage.jsonl")  # llm.py appends here, relative to the working directory
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}  # overload / transient; anything else 4xx is final
OVERLOAD_HTTP = {429, 503}
OVERLOADS_TO_THROTTLE = 3  # this many overload errors in a row halve the concurrency
QUOTA_GIVE_UP_S = 120  # a 429 asking us to wait longer than this means the quota is spent: stop calling
# "Please retry in 17h31m41.6s" (Gemini), "Please try again in 7.5s" / "in 1m2.5s" (Groq)
_RETRY_IN = re.compile(r"(?:retry|try again) in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", re.IGNORECASE)
_DAILY = re.compile(r"per ?day|PerDay|daily|\bRPD\b|\bTPD\b", re.IGNORECASE)
REASK = "Your previous reply was not valid JSON ({err}). Reply again with ONLY the JSON, no prose, no code fences."


def available() -> bool:
    return all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


# ------------------------------------------------------------ JSON parsing

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_json(text: Optional[str]) -> Any:
    """strip fences -> json.loads -> light repair. Raises ValueError if nothing parses."""
    if text is None:
        raise ValueError("empty reply")
    s = _FENCE.sub("", text.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Light repair: outermost object/array, smart quotes, trailing commas, Python literals.
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON object or array in reply")
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        raise ValueError("unterminated JSON")
    s = s[start : end + 1]
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = _TRAILING_COMMA.sub(r"\1", s)
    s = re.sub(r"\bNone\b", "null", re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", s)))
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"unparseable JSON: {e.msg} at {e.pos}") from e


# ------------------------------------------------------------ client

class LLM:
    def __init__(
        self,
        concurrency: int = 8,
        timeout: int = 60,
        retries: int = 4,  # 5 tries in all: waits of ~2, 4, 8, 16 s between them
        backoff: float = 2.0,
        min_concurrency: int = 2,
        cache_dir: Optional[str] = None,
        chat_fn: Optional[Callable[..., str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.extra = extra  # provider-specific body fields, e.g. {"reasoning_effort": "low"}
        self.concurrency = concurrency
        self.min_concurrency = min(min_concurrency, concurrency)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        cache = cache_dir or os.environ.get("COLDSTART_LLM_CACHE")  # dev only; unset for cost runs
        self.cache_dir: Optional[Path] = Path(cache) if cache else None
        self._chat = chat_fn or llm.chat
        self._json_mode = True  # dropped after the first 4xx that looks like "unsupported"
        self._lock = threading.Lock()
        # Adaptive limit on calls in flight: halved after repeated overload errors (8 -> 4 -> 2).
        self._slots = threading.Condition()
        self._limit = concurrency
        self._active = 0
        self._overloads = 0
        self.exhausted: Optional[str] = None  # set once the provider says the quota is spent
        self.calls: Dict[str, int] = {}
        self.failures: Dict[str, int] = {}  # prompts given up on, any cause
        self.json_failures: Dict[str, int] = {}  # ...of which: replies that never parsed as JSON
        self.tokens: Dict[str, Dict[str, Any]] = {}
        self._stage = "default"

    # ---- per-stage accounting, read back from usage.jsonl

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        prev, self._stage = self._stage, name
        start = USAGE_FILE.stat().st_size if USAGE_FILE.exists() else 0
        try:
            yield
        finally:
            self._stage = prev
            tally = self.tokens.setdefault(name, {"prompt": 0, "completion": 0, "calls": 0, "failed_attempts": 0})
            for rec in _usage_since(start):
                if rec.get("error") is not None:
                    tally["failed_attempts"] += 1  # still counts against a provider's request quota
                    continue
                tally["prompt"] += rec.get("prompt_tokens") or 0
                tally["completion"] += rec.get("completion_tokens") or 0
                tally["calls"] += 1
            tally["failed_prompts"] = self.failures.get(name, 0)
            tally["models"] = sorted({str(r.get("model")) for r in _usage_since(start)})
            log(f"llm[{name}]: {tally['calls']} calls, {tally['prompt']}+{tally['completion']} tokens, "
                f"{tally['failed_attempts']} failed attempts, {tally['failed_prompts']} prompts given up, "
                f"concurrency now {self._limit}")

    def _count(self, table: Dict[str, int]) -> None:
        with self._lock:
            table[self._stage] = table.get(self._stage, 0) + 1

    # ---- adaptive concurrency

    @contextmanager
    def _slot(self) -> Iterator[None]:
        with self._slots:
            while self._active >= self._limit:
                self._slots.wait()
            self._active += 1
        try:
            yield
        finally:
            with self._slots:
                self._active -= 1
                self._slots.notify_all()

    def _overloaded(self, yes: bool) -> None:
        with self._slots:
            if not yes:
                self._overloads = 0
                return
            self._overloads += 1
            if self._overloads >= OVERLOADS_TO_THROTTLE and self._limit > self.min_concurrency:
                self._limit = max(self.min_concurrency, self._limit // 2)
                self._overloads = 0
                log(f"llm[{self._stage}]: provider overloaded, concurrency -> {self._limit}")

    def _wait(self, attempt: int) -> None:
        """Exponential backoff with jitter: ~2, 4, 8, 16 s (x0.75-1.25)."""
        time.sleep(self.backoff * (2 ** attempt) * random.uniform(0.75, 1.25))

    # ---- single call with retries + cache

    def complete(self, messages: List[dict], max_tokens: Optional[int] = None, json_mode: bool = True) -> str:
        key = None
        if self.cache_dir is not None:
            parts: List[Any] = [os.environ.get("LLM_MODEL"), messages, max_tokens, json_mode]
            if self.extra:
                parts.append(self.extra)  # only when set, so keys from before `extra` existed stay valid
            key = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
            hit = self.cache_dir / f"{key}.txt"
            if hit.exists():
                return hit.read_text(encoding="utf-8")
        last: Optional[Exception] = None
        attempt = 0
        while attempt <= self.retries:
            if self.exhausted:
                raise RuntimeError(f"LLM quota exhausted earlier in this run: {self.exhausted}")
            try:
                fmt = {"type": "json_object"} if (json_mode and self._json_mode) else None
                self._count(self.calls)
                with self._slot():
                    kw = {"extra": self.extra} if self.extra else {}
                    text = self._chat(messages, max_tokens=max_tokens, response_format=fmt, timeout=self.timeout, **kw)
                self._overloaded(False)
                if self.cache_dir is not None and key is not None and text is not None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    (self.cache_dir / f"{key}.txt").write_text(text, encoding="utf-8")
                return text
            except Exception as e:  # HTTP errors, timeouts, connection resets, bad payloads
                last = e
                code = getattr(e, "code", None)
                if code == 400 and self._json_mode and json_mode:
                    self._json_mode = False  # provider rejects response_format; retry without it (free retry)
                    continue
                if code in (401, 403):
                    with self._lock:  # bad key or blocked client: every later call would fail the same way
                        if not self.exhausted:
                            self.exhausted = _describe(e)
                            log(f"llm[{self._stage}]: access refused, no more calls this run ({self.exhausted})")
                    break
                if isinstance(code, int) and code not in RETRYABLE_HTTP:
                    break  # not found / bad request: retrying won't help
                wait_hint = _quota_wait(e) if code == 429 else None
                if wait_hint is not None and wait_hint < 0:
                    with self._lock:
                        if not self.exhausted:
                            self.exhausted = _describe(e)
                            log(f"llm[{self._stage}]: quota exhausted, no more calls this run ({self.exhausted})")
                    break
                self._overloaded(code in OVERLOAD_HTTP or isinstance(e, (TimeoutError, socket.timeout)))
                if attempt < self.retries:
                    if wait_hint:
                        time.sleep(wait_hint)  # provider told us how long
                    else:
                        self._wait(attempt)
                attempt += 1
        raise RuntimeError(f"LLM call failed after retries: {_describe(last)}")

    def ask_json(self, prompt: str, system: Optional[str] = None, max_tokens: Optional[int] = None) -> Any:
        """One prompt -> parsed JSON, with one re-ask on bad JSON. None on failure (caller falls back)."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        try:
            text = self.complete(messages, max_tokens=max_tokens)
            try:
                return parse_json(text)
            except ValueError as err:
                messages = messages + [
                    {"role": "assistant", "content": text or ""},
                    {"role": "user", "content": REASK.format(err=err)},
                ]
                return parse_json(self.complete(messages, max_tokens=max_tokens))
        except (RuntimeError, ValueError) as e:
            self._count(self.failures)
            if isinstance(e, ValueError):
                self._count(self.json_failures)
            log(f"llm[{self._stage}]: giving up on one prompt: {e}")
            return None

    def map_json(self, prompts: List[str], system: Optional[str] = None, max_tokens: Optional[int] = None) -> List[Any]:
        """Run prompts concurrently; results in input order, None where a prompt failed."""
        if not prompts:
            return []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            return list(pool.map(lambda p: self.ask_json(p, system=system, max_tokens=max_tokens), prompts))

    def summary(self, n_messages: int) -> Dict[str, Any]:
        total = sum(t["prompt"] + t["completion"] for t in self.tokens.values())
        return {
            "model": os.environ.get("LLM_MODEL"),
            "stages": self.tokens,
            "total_tokens": total,
            "tokens_per_1k_msgs": round(1000 * total / n_messages, 1) if n_messages else None,
            "failures": self.failures,
            "json_failures": self.json_failures,
        }


def _quota_wait(e: Exception) -> Optional[float]:
    """For a 429: seconds the provider asks us to wait, -1 if the quota is spent (daily / long wait), None if unknown."""
    body = getattr(e, "_coldstart_body", None)
    if body is None:
        try:
            body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        except Exception:
            body = ""
        setattr(e, "_coldstart_body", body)
    if _DAILY.search(body):
        return -1.0
    m = _RETRY_IN.search(body)
    if m:
        secs = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))
    else:
        try:  # standard header, in seconds
            secs = float((getattr(e, "headers", None) or {}).get("retry-after") or "")
        except (TypeError, ValueError):
            return None
    return -1.0 if secs > QUOTA_GIVE_UP_S else secs


def _describe(e: Optional[Exception]) -> str:
    code = getattr(e, "code", None)
    return f"{type(e).__name__}{' ' + str(code) if code else ''}: {str(e)[:120]}"


def _usage_since(offset: int) -> List[dict]:
    if not USAGE_FILE.exists():
        return []
    with open(USAGE_FILE, "rb") as f:
        f.seek(offset)
        out = []
        for line in f.read().decode("utf-8", "replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
