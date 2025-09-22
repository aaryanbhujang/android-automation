import subprocess
import sys
import shlex
import time
import json
import os

# Replay three exact demo commands sequentially, closing the app after each.

BASE_DIR = os.path.dirname(__file__)


def run_cmd(cmd: str) -> int:
    print("\n=== Running ===\n" + cmd + "\n================")
    # On Windows PowerShell, use shell=True so the quoting stays intact
    try:
        p = subprocess.run(cmd, shell=True, cwd=BASE_DIR)
        print(f"Exited with code: {p.returncode}")
        return p.returncode
    except Exception as e:
        print(f"ERROR running command: {e}")
        return 1


def infer_app_from_goal(goal: str) -> str:
    """Best-effort package inference using goal_router registry. Returns package or empty string."""
    try:
        sys.path.insert(0, BASE_DIR)
        from goal_router import _infer_app, _base_config_for_app  # type: ignore
        app = _infer_app(goal) or ""
        if not app:
            return ""
        cfg = _base_config_for_app(app) or {}
        return cfg.get("package") or ""
    except Exception:
        return ""


def force_stop_package(pkg: str):
    if not pkg:
        return
    print(f"Stopping package: {pkg}")
    subprocess.run(f"adb shell am force-stop {pkg}", shell=True)
    # Go HOME to reset UI
    subprocess.run("adb shell input keyevent KEYCODE_HOME", shell=True)
    time.sleep(0.5)


def main():
    # The three commands provided by the user, kept exactly as-is
    cmds = [
        "python .\\run_goal.py --goal \"open chrome and search for 'massive socks' and click on Go/or inupt [return] to make a request\" --auto-config --max-steps 20 --gemini-model \"gemini-1.5-flash\" --gemini-api-key \"\"",
        " python .\\run_goal.py --goal \"open settings and scroll down to find system and tap on it\" --auto-config --max-steps 20 --gemini-model \"gemini-1.5-flash\" --gemini-api-key \"\" ",
        "python .\\run_goal.py --goal \"open messages and search Antonio\"  --auto-config --max-steps 20 --gemini-model \"gemini-1.5-flash\" --gemini-api-key"
    ]
    goals = [
        "open chrome and search for 'massive socks' and click on Go/or inupt [return] to make a request",
        "open settings and scroll down to find system and tap on it",
        "open messages and search Antonio",
    ]

    for cmd, goal in zip(cmds, goals):
        code = run_cmd(cmd)
        # Close the corresponding app regardless of success
        pkg = infer_app_from_goal(goal)
        force_stop_package(pkg)
        # Small pause between demos
        time.sleep(0.5)
    print("\nAll demo commands executed.\n")


if __name__ == "__main__":
    main()
