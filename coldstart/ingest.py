"""Load the export, sort messages per chat, normalise text, fix the reference time."""
from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .util import read_jsonl

LOOP_WINDOW_DAYS = 16
KIND_TAGS = {"image_caption": "[IMG] ", "document": "[DOC] ", "voice_transcript": "[VOICE] "}


@dataclass
class Data:
    chats: Dict[str, dict]
    messages: List[dict]  # sorted by (ts, id)
    by_id: Dict[str, dict]
    by_chat: Dict[str, List[dict]]  # sorted by ts
    seed: dict
    names: Dict[str, str]  # person id -> best display name
    now: int  # ts of the last message
    loop_from: int  # start of the loop extraction window
    people: List[str] = field(default_factory=list)

    def other(self, chat_id: str) -> Optional[str]:
        """The other person in a 1:1 chat."""
        c = self.chats[chat_id]
        if c["is_group"]:
            return None
        rest = [p for p in c["participants"] if p != "ME"]
        return rest[0] if len(rest) == 1 else None


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("‎", "").replace("‏", "").replace("‪", "").replace("‬", "")
    return " ".join(text.split())


def load(folder: str) -> Data:
    root = Path(folder)
    chats = {c["chat_id"]: c for c in read_jsonl(root / "chats.jsonl")}
    messages = read_jsonl(root / "messages.jsonl")
    seed_path = root / "seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
    for m in messages:
        m["text"] = KIND_TAGS.get(m.get("kind"), "") + normalise(m.get("body"))
    messages.sort(key=lambda m: (m["ts"], m["id"]))
    by_chat: Dict[str, List[dict]] = defaultdict(list)
    for m in messages:
        by_chat[m["chat_id"]].append(m)
    for cid in chats:
        by_chat.setdefault(cid, [])

    name_votes: Dict[str, Counter] = defaultdict(Counter)
    for m in messages:
        if m["sender_id"] != "ME" and m.get("sender_name"):
            name_votes[m["sender_id"]][normalise(m["sender_name"])] += 1
    names = {pid: votes.most_common(1)[0][0] for pid, votes in name_votes.items()}
    for c in chats.values():
        rest = [p for p in c["participants"] if p != "ME"]
        if not c["is_group"] and len(rest) == 1 and normalise(c.get("name")):
            names.setdefault(rest[0], normalise(c["name"]))

    now = messages[-1]["ts"] if messages else 0
    people = sorted({p for c in chats.values() for p in c["participants"]} - {"ME"})
    return Data(
        chats=chats,
        messages=messages,
        by_id={m["id"]: m for m in messages},
        by_chat=dict(by_chat),
        seed=seed,
        names=names,
        now=now,
        loop_from=now - LOOP_WINDOW_DAYS * 86400,
        people=people,
    )
