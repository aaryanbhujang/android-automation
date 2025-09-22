"""
observer.py

Collect a single observation from the device:
- UiAutomator XML dump pulled to ./state/window_dump_<ts>.xml
- Screenshot saved to ./screenshots/screen_<ts>.png (best-effort)
- Current focused activity/package

Returns a small dict consumed by normalizer and planners.
"""
import os
import time
from typing import Dict, Any
from adb_wrapper import uiautomator_dump, pull, screencap, get_focused_activity

STATE_DIR = "./state"
SCREENSHOT_DIR = "./screenshots"

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def timestamp_ms() -> int:
    return int(time.time() * 1000)

def observe() -> Dict[str, Any]:
    """Perform UiAutomator dump + screenshot; return file paths and activity."""
    ts = timestamp_ms()
    xml_remote = "/sdcard/window_dump.xml"
    ok, info = uiautomator_dump(xml_remote)
    if not ok:
        raise RuntimeError(f"uiautomator dump failed: {info}")
    local_xml = f"{STATE_DIR}/window_dump_{ts}.xml"
    ok, _ = pull(xml_remote, local_xml)
    if not ok:
        raise RuntimeError("adb pull failed for window_dump.xml")
    local_png = f"{SCREENSHOT_DIR}/screen_{ts}.png"
    ok, _ = screencap("/sdcard/screen.png", local_png)
    if not ok:
        # continue but note screenshot failed
        local_png = None
    activity = get_focused_activity()
    return {
        "ts": ts,
        "package_activity": activity,
        "raw_xml": local_xml,
        "screenshot": local_png
    }
