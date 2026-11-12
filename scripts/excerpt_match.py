"""Excerpt and personal-path matching shared by scripts/leak_check.py and scripts/redact_logs.py (zero calls).

- n-gram overlap: any run of N_GRAM+ consecutive words of a log that also occurs, consecutively, in a message body.
  Words are normalised (case-folded; punctuation and whitespace ignored), so partial quotes, re-wrapped and
  re-punctuated quotes match too. Overlapping/adjacent matches merge into one span.
- quoted short bodies: a whole message body of 2-4 words appearing inside quotation marks ("ok will send").
- user paths: the local user's folder name in Windows / WSL / Linux paths (C:\\Users\\<name>\\, /mnt/c/Users/<name>/,
  /home/<user>/, and the dash-encoded project-folder form -Users-<name>-), found by pattern; the name is never
  hard-coded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Set, Tuple

N_GRAM = 5
SHORT_MIN, SHORT_MAX = 2, 4
WORDTOK = re.compile(r"[^\W_]+", re.UNICODE)
QUOTED = re.compile(r"([\"“‘`]|(?<![A-Za-z])')([^\"“”‘’`\n]{2,120}?)([\"”’`]|'(?![A-Za-z]))")


def words(text: str) -> List[Tuple[str, int, int]]:
    """(normalised word, start, end) for every word of the text."""
    return [(m.group(0).casefold(), m.start(), m.end()) for m in WORDTOK.finditer(text)]


class BodyIndex:
    def __init__(self, bodies: Iterable[str]) -> None:
        self.grams: Set[Tuple[str, ...]] = set()
        self.short: Set[Tuple[str, ...]] = set()
        for b in bodies:
            w = tuple(x for x, _, _ in words(b or ""))
            if SHORT_MIN <= len(w) <= SHORT_MAX:
                self.short.add(w)
            for i in range(len(w) - N_GRAM + 1):
                self.grams.add(w[i:i + N_GRAM])

    @staticmethod
    def from_data(dirs: Iterable[Path]) -> "BodyIndex":
        bodies: List[str] = []
        for d in dirs:
            p = d / "messages.jsonl"
            if p.exists():
                bodies += [json.loads(l).get("body") or "" for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return BodyIndex(bodies)


def ngram_spans(text: str, idx: BodyIndex) -> List[Tuple[int, int]]:
    """Merged character spans of every run of N_GRAM+ words also found in a body."""
    w = words(text)
    covered = [False] * len(w)
    toks = [x for x, _, _ in w]
    for i in range(len(w) - N_GRAM + 1):
        if tuple(toks[i:i + N_GRAM]) in idx.grams:
            for j in range(i, i + N_GRAM):
                covered[j] = True
    spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(w):
        if covered[i]:
            j = i
            while j + 1 < len(w) and covered[j + 1]:
                j += 1
            spans.append((w[i][1], w[j][2]))
            i = j + 1
        else:
            i += 1
    return spans


def quoted_short_spans(text: str, idx: BodyIndex) -> List[Tuple[int, int]]:
    """Spans of quoted strings whose words are exactly a 2-4 word message body."""
    out = []
    for m in QUOTED.finditer(text):
        w = tuple(x for x, _, _ in words(m.group(2)))
        if SHORT_MIN <= len(w) <= SHORT_MAX and w in idx.short:
            out.append((m.start(2), m.end(2)))
    return out


# The folder name may be wrapped onto the next line by a transcript export ("First\n      Last/..."), so the name
# segment may contain line breaks and indentation (at most 80 characters).
USER_PATHS = [
    re.compile(r"(?i)(\b[A-Z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/))(?!\[USER\])([^\\/\"'`<>|*?]{1,80}?)(?=\\|/)"),
    re.compile(r"(?i)(/mnt/[a-z]/Users/)(?!\[USER\])([^/\"'`<>|*?]{1,80}?)(?=/)"),
    re.compile(r"(/home/)(?!\[USER\])([A-Za-z0-9._\-]+)(?=/|\b)"),
]


def user_names(root: Path) -> List[str]:
    """The local user's folder name(s), read from the repo's own path and home directory (never hard-coded)."""
    names = []
    parts = root.resolve().parts
    for i, p in enumerate(parts[:-1]):
        if p.lower() == "users":
            names.append(parts[i + 1])
    names.append(Path.home().name)
    return [n for n in dict.fromkeys(names) if n and n not in ("root",)]


def dash_forms(names: List[str]) -> List[re.Pattern]:
    """Dash-encoded project folders (e.g. -mnt-c-Users-First-Last-OneDrive-...)."""
    out = []
    for n in names:
        parts = [re.escape(x) for x in re.split(r"[\s_\-]+", n) if x]
        out.append(re.compile(r"(?i)(-Users-)" + r"[-\s]+".join(parts) + r"(?=-|\b)"))
    return out


def user_path_spans(text: str, names: List[str]) -> List[Tuple[int, int]]:
    spans = [(m.start(2), m.end(2)) for p in USER_PATHS for m in p.finditer(text)]
    for p in dash_forms(names):
        spans += [(m.end(1), m.end()) for m in p.finditer(text)]
    return sorted(spans)


def replace_spans(text: str, spans: List[Tuple[int, int]], token: str) -> Tuple[str, int]:
    """Replace non-overlapping spans (merged if they overlap) with a token."""
    merged: List[List[int]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, last = [], 0
    for s, e in merged:
        out += [text[last:s], token]
        last = e
    return "".join(out) + text[last:], len(merged)
