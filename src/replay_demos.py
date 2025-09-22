"""
Run the three demos using run_goal (Gemini LLM) and close the app after each.
This is NOT low-level ADB replay; it re-runs the scenarios end-to-end.

Usage:
  python src/replay_demos.py --gemini-model gemini-1.5-flash --gemini-api-key <KEY>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from typing import List

HERE = Path(__file__).resolve().parent

SCENARIOS: List[str] = [
    "open chrome and search for 'massive socks' and press enter",
    "open settings and scroll down to find system and tap on it",
    "open messages and search Antonio",
]


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(HERE))


def force_stop_current_app() -> None:
    # Best-effort: ask observer for current package, then force-stop
    try:
        from observer import observe  # type: ignore
        from adb_wrapper import run_adb_cmd  # type: ignore
        obs = observe()
        act = (obs.get("package_activity") or "")
        pkg = act.split("/", 1)[0].strip() if act else ""
        if pkg:
            run_adb_cmd(["shell", "am", "force-stop", pkg])
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemini-model", required=True)
    ap.add_argument("--gemini-api-key", required=True)
    ap.add_argument("--max-steps", type=int, default=20)
    args = ap.parse_args()

    for goal in SCENARIOS:
        rc = run([
            sys.executable,
            str(HERE / "run_goal.py"),
            "--goal", goal,
            "--auto-config",
            "--max-steps", str(args.max_steps),
            "--gemini-model", args.gemini_model,
            "--gemini-api-key", args.gemini_api_key,
        ])
        if rc != 0:
            print(f"WARN: run_goal exited with code {rc} for: {goal}")
        # Close the app after each scenario
        force_stop_current_app()


if __name__ == "__main__":
    main()
