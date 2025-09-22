# adb_wrapper.py
import subprocess
from typing import Tuple, List, Optional

ADB = "adb"

def run_adb_cmd(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run([ADB] + cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"

# Observation helpers
def uiautomator_dump(remote_path: str = "/sdcard/window_dump.xml") -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["shell", "uiautomator", "dump", remote_path])
    if rc != 0:
        return False, err or out
    return True, remote_path

def pull(remote_path: str, local_path: str) -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["pull", remote_path, local_path])
    if rc != 0:
        return False, err or out
    return True, local_path

def screencap(remote_path: str = "/sdcard/screen.png", local_path: str = "./screenshots/screen.png") -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["shell", "screencap", "-p", remote_path])
    if rc != 0:
        return False, err or out
    return pull(remote_path, local_path)

def get_focused_activity() -> Optional[str]:
    rc, out, err = run_adb_cmd(["shell", "dumpsys", "activity", "activities"])
    if rc != 0:
        return None
    for line in out.splitlines():
        if "mResumedActivity" in line or "mFocusedActivity" in line:
            parts = line.strip().split()
            for p in parts:
                if "/" in p and "." in p:
                    return p.strip()
    # Fallback (note: piping inside adb requires sh -c)
    rc2, out2, _ = run_adb_cmd(["shell", "sh", "-c", "dumpsys window windows | grep mCurrentFocus"])
    if rc2 == 0 and out2:
        return out2.strip()
    return None

# Actuators
def input_tap(x: int, y: int) -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["shell", "input", "tap", str(x), str(y)])
    return rc == 0, err or out

def input_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])
    return rc == 0, err or out

def key_back() -> Tuple[bool, str]:
    rc, out, err = run_adb_cmd(["shell", "input", "keyevent", "KEYCODE_BACK"])
    return rc == 0, err or out

def input_keyevent(key: str) -> Tuple[bool, str]:
    # key can be a numeric code or a KEYCODE_* name (e.g., ENTER)
    key = key.strip().upper()
    if not key.startswith("KEYCODE_"):
        key = f"KEYCODE_{key}"
    rc, out, err = run_adb_cmd(["shell", "input", "keyevent", key])
    return rc == 0, err or out

def am_start(component: str) -> Tuple[bool, str]:
        """
        Start an activity by component name.
        component examples:
            - "com.example/.MainActivity"
            - "com.example/com.example.MainActivity"
        Returns (success, message)
        """
        rc, out, err = run_adb_cmd(["shell", "am", "start", "-n", component])
        return rc == 0, (err or out)

def _escape_text_for_input(text: str) -> str:
    # Basic escaping for adb shell input text
    escaped = []
    for ch in text:
        if ch == " ":
            escaped.append("%s")
        elif ch in ["&", "<", ">", "(", ")", "|", ";", "'", "\"", "\\", "$", "`"]:
            escaped.append(" ")
        else:
            escaped.append(ch)
    return "".join(escaped)

def input_text(text: str) -> Tuple[bool, str]:
    safe = _escape_text_for_input(text)
    rc, out, err = run_adb_cmd(["shell", "input", "text", safe])
    return rc == 0, err or out
