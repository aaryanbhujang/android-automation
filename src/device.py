# device.py
from typing import Tuple
from adb_wrapper import run_adb_cmd

def get_screen_size() -> Tuple[int, int]:
    """
    Returns (width, height) using `adb shell wm size`.
    Fallback to 1080x1920 if parsing fails.
    """
    rc, out, err = run_adb_cmd(["shell", "wm", "size"])
    if rc == 0 and out:
        # Typical: "Physical size: 1080x2400"
        for line in out.splitlines():
            if ":" in line and "x" in line:
                try:
                    sz = line.split(":")[1].strip()
                    w, h = sz.split("x")
                    return int(w), int(h)
                except Exception:
                    continue
    return 1080, 1920