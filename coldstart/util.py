"""Small shared helpers: jsonl io, atomic writes, logging, text tokens."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, List

_T0 = time.time()


def log(msg: str) -> None:
    print(f"[coldstart {time.time() - _T0:6.1f}s] {msg}", file=sys.stderr, flush=True)


def read_jsonl(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json_atomic(path: Path, obj: Any) -> None:
    """Write to a temp file then rename, so a crash never leaves a half-written output."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def tokens(text: str) -> List[str]:
    """Lowercase word tokens (letters/digits), emoji and punctuation dropped."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def acronym(name: str) -> str:
    """'BlueSkyLabs' -> 'bsl', 'Red Fox Media' -> 'rfm'. Empty if the name has < 2 parts."""
    parts: List[str] = []
    for word in _TOKEN_RE.findall(name or ""):
        parts.extend(_CAMEL_RE.findall(word) or [word])
    return "".join(p[0] for p in parts).lower() if len(parts) >= 2 else ""


def uniq(items: Iterable[Any]) -> List[Any]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
