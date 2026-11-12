"""Entry point: python3 -m coldstart.main <input_dir> <output.json>

Writes a valid skeleton first, then overwrites after every stage that validates. A watchdog exits at
25 minutes leaving the last valid output on disk. Never exits without a valid file.
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from . import assemble, chats, circles, config, decide, goals, ingest, llm_client, loops, orgs, orgs_llm, people, profile
from .util import log, read_jsonl, write_json_atomic

DEADLINE_S = float(os.environ.get("COLDSTART_DEADLINE_S", 25 * 60))


class Run:
    def __init__(self, input_dir: str, output: str) -> None:
        self.input_dir = input_dir
        self.output = Path(output)
        self.raw_chats: List[dict] = []
        self.raw_messages: List[dict] = []
        self.parts: Dict[str, list] = {}
        self.last_valid: dict = {}
        self.ctx: dict = {}  # stage outputs other stages need (data, profile, orgs)

    def write_skeleton(self) -> None:
        self.raw_chats = read_jsonl(Path(self.input_dir) / "chats.jsonl")
        self.last_valid = assemble.skeleton(self.raw_chats)
        self.parts = copy.deepcopy(self.last_valid)
        write_json_atomic(self.output, self.last_valid)
        log("skeleton written")

    def commit(self, stage: str) -> None:
        """Validate the current parts; write if valid, otherwise roll back to the last valid output."""
        msg_ids = {m["id"] for m in self.raw_messages}
        out = assemble.build(copy.deepcopy(self.parts), msg_ids)
        errs = assemble.validate(out, self.raw_chats, self.raw_messages)
        if errs:
            log(f"{stage}: output invalid ({len(errs)} problems, e.g. {errs[0]}); keeping last valid")
            self.parts = copy.deepcopy(self.last_valid)
            return
        self.last_valid = out
        write_json_atomic(self.output, out)
        log(f"{stage}: written")

    # ------------------------------------------------------------ stages

    def s_ingest(self) -> None:
        data = ingest.load(self.input_dir)
        self.ctx["data"] = data
        self.raw_chats = list(data.chats.values())
        self.raw_messages = data.messages
        log(f"ingest: {len(data.messages)} messages, {len(data.chats)} chats, {len(data.people)} people")

    def s_profile(self) -> None:
        self.ctx["profile"] = profile.build(self.ctx["data"])

    def s_orgs(self) -> None:
        o = orgs.build(self.ctx["profile"], self.ctx["data"].seed)
        if "orgs_llm" in config.ABLATE:  # dev-only ablation: one LLM call regroups the rules' organisations
            client = self._llm("orgs")
            if client is not None:
                o = orgs_llm.refine(self.ctx["profile"], self.ctx["data"].seed, o, client)
        self.ctx["orgs"] = o
        self.parts["organisations"] = o.orgs
        self.parts["chats"] = chats.build(self.ctx["data"], o)
        log(f"orgs: {len(o.orgs)} organisations")

    def s_people(self) -> None:
        self.parts["people"] = people.build(self.ctx["profile"], self.ctx["orgs"], self.ctx["data"].seed)

    def s_circles(self) -> None:
        client = self._llm("circles")
        if client is None:
            return
        data = self.ctx["data"]
        labels = circles.extract_consensus(data, self.ctx["profile"], dict(data.seed.get("circles") or {}), client)
        self.parts["people"] = people.build(self.ctx["profile"], self.ctx["orgs"], data.seed, labels)

    def _llm(self, stage: str):
        if not llm_client.available():
            log(f"{stage}: LLM_BASE_URL / LLM_API_KEY / LLM_MODEL not set, skipping")
            return None
        return self.ctx.setdefault("llm", llm_client.LLM(concurrency=config.LLM_CONCURRENCY, timeout=config.LLM_TIMEOUT_S,
                                                         extra=llm_client.provider_extra()))

    def s_goals(self) -> None:
        client = self._llm("goals")
        if client is not None:
            self.parts["goals"] = goals.extract(self.ctx["data"], client)

    def s_loops(self) -> None:
        client = self._llm("loops")
        if client is None:
            return
        raw = loops.extract(self.ctx["data"], client)
        self.parts["open_loops"] = decide.decide(raw, self.ctx["data"])

    def write_stats(self) -> None:
        """Token use per stage, for eval/run_eval.py. Only when COLDSTART_STATS names a file."""
        path = os.environ.get("COLDSTART_STATS")
        client = self.ctx.get("llm")
        if path and client is not None:
            write_json_atomic(Path(path), client.summary(len(self.raw_messages)))

    def stages(self) -> List[Tuple[str, Callable[[], None], bool]]:
        # (name, fn, writes output)
        return [
            ("ingest", self.s_ingest, False),
            ("profile", self.s_profile, False),
            ("orgs+chats", self.s_orgs, True),
            ("people", self.s_people, True),
            ("circles", self.s_circles, True),
            ("goals", self.s_goals, True),
            ("loops+decide", self.s_loops, True),
        ]

    def run(self) -> None:
        for name, fn, writes in self.stages():
            try:
                fn()
            except Exception:  # a failed stage must never cost the output
                log(f"{name}: FAILED\n{traceback.format_exc()}")
                if name in ("ingest", "profile"):
                    return  # later stages depend on these; keep what is on disk
                self.parts = copy.deepcopy(self.last_valid)
                continue
            if writes:
                self.commit(name)

    def finalise(self) -> None:
        """Re-read what is on disk and make sure it validates; otherwise fall back."""
        try:
            import json

            on_disk = json.loads(self.output.read_text(encoding="utf-8"))
            if not assemble.validate(on_disk, self.raw_chats, self.raw_messages):
                log("final: valid")
                return
        except Exception:
            pass
        log("final: output on disk invalid, rewriting last valid")
        if assemble.validate(self.last_valid, self.raw_chats, self.raw_messages):
            self.last_valid = assemble.skeleton(self.raw_chats)
        write_json_atomic(self.output, self.last_valid)


def _watchdog(run: Run) -> None:
    log(f"watchdog: {DEADLINE_S:.0f}s reached, exiting with last valid output")
    try:
        write_json_atomic(run.output, run.last_valid)
    finally:
        os._exit(0)


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("usage: python3 -m coldstart.main <input_dir> <output.json>", file=sys.stderr)
        return 2
    run = Run(argv[1], argv[2])
    run.write_skeleton()
    timer = threading.Timer(DEADLINE_S, _watchdog, args=(run,))
    timer.daemon = True
    timer.start()
    try:
        run.run()
    finally:
        run.finalise()
        run.write_stats()
        timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
