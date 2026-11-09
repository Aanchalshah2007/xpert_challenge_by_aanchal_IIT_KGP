"""Wrapper around the starter llm.py: thread pool, retries, timeout, JSON parsing/repair,
per-stage token tally (from usage.jsonl) and an opt-in disk cache for dev runs.

    client = LLM()
    with client.stage("loops"):
        replies = client.map_json([prompt_a, prompt_b], system=SYS)   # list of parsed JSON or None
"""
from __future__ import annotations

import hashlib
import json
import os
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
        retries: int = 3,
        backoff: float = 2.0,
        cache_dir: Optional[str] = None,
        chat_fn: Optional[Callable[..., str]] = None,
    ) -> None:
        self.concurrency = concurrency
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        cache = cache_dir or os.environ.get("COLDSTART_LLM_CACHE")  # dev only; unset for cost runs
        self.cache_dir: Optional[Path] = Path(cache) if cache else None
        self._chat = chat_fn or llm.chat
        self._json_mode = True  # dropped after the first 4xx that looks like "unsupported"
        self._lock = threading.Lock()
        self.calls: Dict[str, int] = {}
        self.failures: Dict[str, int] = {}
        self.tokens: Dict[str, Dict[str, int]] = {}
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
            tally = self.tokens.setdefault(name, {"prompt": 0, "completion": 0, "calls": 0})
            for rec in _usage_since(start):
                tally["prompt"] += rec.get("prompt_tokens") or 0
                tally["completion"] += rec.get("completion_tokens") or 0
                tally["calls"] += 1
            log(f"llm[{name}]: {tally['calls']} calls, {tally['prompt']}+{tally['completion']} tokens")

    def _count(self, table: Dict[str, int]) -> None:
        with self._lock:
            table[self._stage] = table.get(self._stage, 0) + 1

    # ---- single call with retries + cache

    def complete(self, messages: List[dict], max_tokens: Optional[int] = None, json_mode: bool = True) -> str:
        key = None
        if self.cache_dir is not None:
            key = hashlib.sha256(
                json.dumps([os.environ.get("LLM_MODEL"), messages, max_tokens, json_mode], sort_keys=True).encode()
            ).hexdigest()
            hit = self.cache_dir / f"{key}.txt"
            if hit.exists():
                return hit.read_text(encoding="utf-8")
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                fmt = {"type": "json_object"} if (json_mode and self._json_mode) else None
                self._count(self.calls)
                text = self._chat(messages, max_tokens=max_tokens, response_format=fmt, timeout=self.timeout)
                if self.cache_dir is not None and key is not None and text is not None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    (self.cache_dir / f"{key}.txt").write_text(text, encoding="utf-8")
                return text
            except Exception as e:  # urllib errors, timeouts, bad payloads
                last = e
                code = getattr(e, "code", None)
                if code == 400 and self._json_mode and json_mode:
                    self._json_mode = False  # provider rejects response_format; retry without it
                    continue
                if code in (400, 401, 403, 404):
                    break
                time.sleep(self.backoff * (2 ** attempt))
        self._count(self.failures)
        raise RuntimeError(f"LLM call failed after retries: {last}")

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
            "stages": self.tokens,
            "total_tokens": total,
            "tokens_per_1k_msgs": round(1000 * total / n_messages, 1) if n_messages else None,
            "failures": self.failures,
        }


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
