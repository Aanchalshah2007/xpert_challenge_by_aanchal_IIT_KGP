"""The pipeline must always leave a valid output. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check  # noqa: E402
from coldstart import main as cs_main  # noqa: E402

EXAMPLE = ROOT / "example"


def _valid(out_path, folder=EXAMPLE):
    chats, messages, _, _ = check.load_input(folder)
    return check.validate(json.loads(Path(out_path).read_text()), chats, messages)


def _windows_bash():
    """Git Bash (or another native bash) on Windows. Skips the WSL launchers (System32 / WindowsApps bash.exe),
    which would run the script inside Linux with a different Python."""
    candidates = [os.environ.get("COLDSTART_BASH"), shutil.which("bash")]
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates += [os.path.join(root, "Git", "bin", "bash.exe"), os.path.join(root, "Programs", "Git", "bin", "bash.exe")]
    for c in candidates:
        if c and os.path.isfile(c) and "system32" not in c.lower() and "windowsapps" not in c.lower():
            return c
    return None


def run_sh_command(args, shim_dir):
    """How to invoke run.sh here. Linux/macOS: execute it directly, exactly as the graders do.
    Windows can't execute a .sh file, so go through bash, with a `python3` shim pointing at this
    interpreter (in Git Bash `python3` is usually the Microsoft Store stub). Returns (argv, env)."""
    if os.name != "nt":
        return [str(ROOT / "run.sh"), *args], None
    bash = _windows_bash()
    if bash is None:
        raise AssertionError("No bash found to run run.sh on Windows. Install Git for Windows or set COLDSTART_BASH.")
    shim = Path(shim_dir) / "python3"
    shim.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    env = dict(os.environ, PATH=str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))
    return [bash, (ROOT / "run.sh").as_posix(), *args], env


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_sh_on_example(self):
        argv, env = run_sh_command([str(EXAMPLE), str(self.out)], self.tmp.name)
        r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_valid(self.out), [])

    def test_stage_crash_keeps_valid_output(self):
        with mock.patch.object(cs_main.people, "build", side_effect=RuntimeError("boom")):
            cs_main.main(["x", str(EXAMPLE), str(self.out)])
        self.assertEqual(_valid(self.out), [])

    def test_invalid_stage_output_rolls_back(self):
        bad = [{"id": "NOT_A_PERSON", "circle": "friend"}]
        with mock.patch.object(cs_main.people, "build", return_value=bad):
            cs_main.main(["x", str(EXAMPLE), str(self.out)])
        out = json.loads(self.out.read_text())
        self.assertEqual(_valid(self.out), [])
        self.assertNotIn("NOT_A_PERSON", {p["id"] for p in out["people"]})

    def test_ingest_crash_leaves_skeleton(self):
        with mock.patch.object(cs_main.ingest, "load", side_effect=ValueError("bad jsonl")):
            cs_main.main(["x", str(EXAMPLE), str(self.out)])
        self.assertEqual(_valid(self.out), [])

    def test_watchdog_exits_with_valid_output(self):
        code = (
            "import sys, time; sys.argv=['m', sys.argv[1], sys.argv[2]];"
            "from coldstart import main as m;"
            "m.Run.s_people = lambda self: time.sleep(30);"
            "sys.exit(m.main(sys.argv))"
        )
        env = dict(os.environ, COLDSTART_DEADLINE_S="2", PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-c", code, str(EXAMPLE), str(self.out)],
                           capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("watchdog", r.stderr)
        self.assertEqual(_valid(self.out), [])


if __name__ == "__main__":
    unittest.main()
