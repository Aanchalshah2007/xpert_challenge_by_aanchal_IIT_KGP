#!/usr/bin/env python3
"""Fail if any person name, chat title, seed org name or long number from the data appears in the pipeline or logs.

    python3 scripts/leak_check.py [data_dir ...]      (default: data/dev)

Scans coldstart/ (code + prompts), run.sh, llm.py and ai-logs/ (redacted transcripts). Exit 1 on any hit.
Terms and matching come from scripts/name_match.py, the same function scripts/redact_logs.py uses, so anything
flagged here is something the redactor replaces. In ai-logs/ it also flags secrets and contact data: API-key-like
strings (sk-/gsk_/AIza/xai- keys, 32+ hex characters, Bearer / X-API-Key values, values assigned to
*API_KEY* / *TOKEN* / *SECRET* variables), e-mail addresses, URLs, the local user's folder name in paths, any run of
5+ words also found in a message body, and quoted 2-4 word message bodies (scripts/excerpt_match.py).
Generic words that happen to be names (e.g. a person called "Home") go in scripts/leak_allow.txt,
one per line, only after checking they are not data-specific. Values found in ai-logs/ are never printed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excerpt_match  # noqa: E402
import name_match  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCAN = [ROOT / "coldstart", ROOT / "run.sh", ROOT / "llm.py", ROOT / "ai-logs"]  # ai-logs: redacted transcripts
SCAN_EXT = {".py", ".txt", ".sh", ".json", ".md", ".cfg", ".toml", ".yaml", ".yml"}
WORD = name_match.WORD  # kept for older eval scripts that import it from here

LOG_PATTERNS = {  # ai-logs only
    "api key": re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9_\-]{20,}|\bgsk_[A-Za-z0-9]{20,}|"
                          r"\bAIza[0-9A-Za-z_\-]{30,}|\bxai-[A-Za-z0-9]{20,}|(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])"),
    "auth header value": re.compile(r"(?i)\b(?:bearer|x-api-key)\b[\s:=\"']+(?!\[)[A-Za-z0-9._\-]{8,}"),
    "key assignment": re.compile(r"(?i)\b[A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN(?!S))[A-Z0-9_]*[\"']?\s*[=:]\s*[\"']?(?!\[)[^\s\"',]{8,}"),
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://[^\s\"'<>)\]]+"),
}


def needles(data_dir: Path) -> Dict[str, str]:
    """normalised term -> where it came from (shared with redact_logs)."""
    return name_match.needles(data_dir)


def _files() -> List[Path]:
    files: List[Path] = []
    for root in SCAN:
        if root.is_file():
            files.append(root)
        elif root.exists():
            files += [p for p in root.rglob("*") if p.suffix in SCAN_EXT and "__pycache__" not in p.parts]
    return files


def scan(needle_map: Dict[str, str], dirs: List[Path]) -> List[str]:
    matcher = name_match.compile_matcher(needle_map)
    body_index = excerpt_match.BodyIndex.from_data(dirs)
    users = excerpt_match.user_names(ROOT)
    hits = []
    for f in _files():
        rel = f.relative_to(ROOT)
        logs = "ai-logs" in rel.parts
        show = (lambda v: "<value hidden>") if logs else (lambda v: f"'{v}'")
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for _, _, term, src in name_match.finditer(line, needle_map, matcher):
                hits.append(f"{rel}:{i}: {show(term)} ({src})")
            if logs:
                for cat, pat in LOG_PATTERNS.items():
                    for _ in pat.finditer(line):
                        hits.append(f"{rel}:{i}: <value hidden> ({cat})")
        if logs:  # whole-file checks: excerpts and paths can span wrapped lines
            text = f.read_text(encoding="utf-8", errors="replace")
            for cat, spans in (("dataset excerpt, 5+ words", excerpt_match.ngram_spans(text, body_index)),
                               ("quoted short message", excerpt_match.quoted_short_spans(text, body_index)),
                               ("user path", excerpt_match.user_path_spans(text, users))):
                for s, _ in spans:
                    hits.append(f"{rel}:{text.count(chr(10), 0, s) + 1}: <value hidden> ({cat})")
    return hits


def main(argv: List[str]) -> int:
    dirs = [Path(a) for a in argv[1:]] or [ROOT / "data" / "dev"]
    needle_map: Dict[str, str] = {}
    for d in dirs:
        if not (d / "chats.jsonl").exists():
            print(f"leak_check: no data at {d}, nothing to check against")
            continue
        needle_map.update(needles(d))
    hits = scan(needle_map, [d for d in dirs if (d / 'messages.jsonl').exists()])
    if hits:
        print(f"leak_check: FAIL, {len(hits)} hit(s):")
        for h in hits:
            print("  " + h)
        return 1
    print(f"leak_check: OK ({len(needle_map)} names/titles/numbers checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
