# actuator.py
from typing import Dict, Tuple, Optional, List
from adb_wrapper import input_tap, input_text, input_swipe, key_back, am_start, input_keyevent
from math import floor
from selector_resolver import resolve_selector

def centroid_from_element(element: Dict) -> Tuple[int, int]:
    return tuple(element.get("center", [0, 0]))

def find_by_selector(state_norm: Dict, by: str, value: str) -> Optional[Dict]:
    elems = state_norm.get("elements", [])
    def clickable_wrapper(inner_bounds):
        for e in elems:
            if e.get("clickable"):
                b = e.get("bounds") or [0,0,0,0]
                if b[0] <= inner_bounds[0] and b[1] <= inner_bounds[1] and b[2] >= inner_bounds[2] and b[3] >= inner_bounds[3]:
                    return e
        return None
    if by == "resource-id":
        # prefer clickable exact match
        for e in elems:
            if e.get("resource_id") == value and e.get("clickable"):
                return e
        for e in elems:
            if e.get("resource_id") == value:
                return e
    if by == "element_id":
        return state_norm.get("by_id", {}).get(value)
    if by == "text":
        val = (value or "").strip()
        # exact clickable, exact any, contains clickable, contains any
        for e in elems:
            if (e.get("text") or "").strip() == val and e.get("clickable"):
                return e
        for e in elems:
            if (e.get("text") or "").strip() == val:
                # bubble up to clickable wrapper if needed
                w = clickable_wrapper(e.get("bounds") or [0,0,0,0])
                return w or e
        if val:
            low = val.lower()
            for e in elems:
                if e.get("clickable") and low in (e.get("text") or "").strip().lower():
                    return e
            for e in elems:
                if low in (e.get("text") or "").strip().lower():
                    w = clickable_wrapper(e.get("bounds") or [0,0,0,0])
                    return w or e
    if by == "content-desc":
        for e in elems:
            if e.get("content_desc") == value and e.get("clickable"):
                return e
        for e in elems:
            if e.get("content_desc") == value:
                w = clickable_wrapper(e.get("bounds") or [0,0,0,0])
                return w or e
    if by == "bounds":
        try:
            import ast
            if value.strip().startswith("["):
                b = ast.literal_eval(value)
                for e in elems:
                    if e.get("bounds") == b:
                        return e
        except Exception:
            pass
    # Fallback: use resolver's fuzzy/contains logic
    try:
        cand = resolve_selector(state_norm, {"by": by, "value": value})
        if cand:
            return cand
    except Exception:
        pass
    return None

def _resolve_tap_target(target: Dict, state_norm: Dict) -> Tuple[int, int]:
    if target:
        elem = find_by_selector(state_norm, target.get("by"), target.get("value"))
        if elem:
            return centroid_from_element(elem)
        # Allow raw "x,y"
        try:
            coords = target.get("value")
            if isinstance(coords, str) and "," in coords:
                px, py = coords.split(",", 1)
                return int(px), int(py)
        except Exception:
            pass
    return 0, 0

def _screen_center(state_norm: Dict) -> Tuple[int, int]:
    # UiAutomator dump doesn't provide screen size; approximate from max bounds
    max_r = 0
    max_b = 0
    for e in state_norm.get("elements", []):
        b = e.get("bounds") or [0, 0, 0, 0]
        max_r = max(max_r, b[2])
        max_b = max(max_b, b[3])
    return max_r // 2, max_b // 2

def exec_action(action: Dict, state_norm: Dict) -> Dict:
    """
    action:
      {
    "action": "tap|type|swipe|back|scroll|open_app|wait|keyevent",
        "target": {"by": "resource-id|text|content-desc|bounds|element_id", "value": "<...>"},
        "args": {"text": "...", "duration_ms": 300, "direction": "down|up|left|right"}
      }
    Returns: {"success": bool, "adb_cmds": [str], "note": str}
    """
    act = action.get("action")
    target = action.get("target", {}) or {}
    args = action.get("args", {}) or {}
    adb_cmds: List[str] = []
    result = {"success": False, "adb_cmds": [], "note": ""}

    if act == "open_app":
        comp = target.get("value") if target else None
        if not comp:
            result["note"] = "open_app missing target.value (component)"
            result["adb_cmds"] = adb_cmds
            return result
        ok, out = am_start(comp)
        adb_cmds.append(f"am start -n {comp}")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    if act == "back":
        ok, out = key_back()
        adb_cmds.append("input keyevent KEYCODE_BACK")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    if act == "keyevent":
        key = (args.get("key") or "").strip() if args else ""
        if not key:
            result.update({"success": False, "note": "keyevent requires args.key", "adb_cmds": adb_cmds})
            return result
        ok, out = input_keyevent(key)
        adb_cmds.append(f"input keyevent {key}")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    if act == "wait":
        import time
        dur_ms = int(args.get("duration_ms", 500))
        time.sleep(dur_ms / 1000.0)
        result.update({"success": True, "note": f"waited {dur_ms}ms", "adb_cmds": ["(sleep)"]})
        return result

    if act == "tap":
        x, y = _resolve_tap_target(target, state_norm)
        # Avoid tapping (0,0) when a target is specified but not found
        if target and (x, y) == (0, 0):
            result.update({
                "success": False,
                "note": f"tap target not found: by={target.get('by')} value={target.get('value')}",
                "adb_cmds": adb_cmds
            })
            return result
        # Attach resolved element metadata (best-effort)
        resolved = None
        try:
            if target:
                resolved = find_by_selector(state_norm, target.get("by"), target.get("value"))
        except Exception:
            resolved = None
        ok, out = input_tap(x, y)
        adb_cmds.append(f"input tap {x} {y}")
        extra = {"resolved": {k: resolved.get(k) for k in ("resource_id","text","content_desc","bounds","clickable")}} if resolved else {}
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds, **extra})
        return result

    if act == "type":
        # Tap target first if provided
        x, y = _resolve_tap_target(target, state_norm)
        if (x, y) != (0, 0):
            ok_tap, out_tap = input_tap(x, y)
            adb_cmds.append(f"input tap {x} {y}")
            if not ok_tap:
                result.update({"success": False, "note": f"tap-before-type failed: {out_tap}", "adb_cmds": adb_cmds})
                return result
        text = args.get("text", "")
        ok, out = input_text(text)
        adb_cmds.append(f"input text <{len(text)} chars>")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    if act == "swipe":
        x1 = int(args.get("x1", 0)); y1 = int(args.get("y1", 0))
        x2 = int(args.get("x2", 0)); y2 = int(args.get("y2", 0))
        dur = int(args.get("duration_ms", 300))
        ok, out = input_swipe(x1, y1, x2, y2, dur)
        adb_cmds.append(f"input swipe {x1} {y1} {x2} {y2} {dur}")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    if act == "scroll":
        # direction-based swipe with configurable start position (default: center)
        cx, cy = _screen_center(state_norm)
        # Derive screen bounds for edge-origin swipes
        max_r = 0
        max_b = 0
        for e in state_norm.get("elements", []):
            b = e.get("bounds") or [0, 0, 0, 0]
            max_r = max(max_r, b[2])
            max_b = max(max_b, b[3])
        start_pos = (args.get("start_pos") or "center").lower()
        x_start, y_start = cx, cy
        if start_pos == "bottom":
            x_start, y_start = cx, int(max_b * 0.85)
        elif start_pos == "top":
            x_start, y_start = cx, int(max_b * 0.15)
        elif start_pos == "left":
            x_start, y_start = int(max_r * 0.15), cy
        elif start_pos == "right":
            x_start, y_start = int(max_r * 0.85), cy
        # Support either absolute length (pixels) or relative length via length_factor
        if "length" in args and args.get("length") is not None:
            length = int(args.get("length"))
        else:
            factor = float(args.get("length_factor", 0.7))  # default ~70% of screen
            length = max(200, int(factor * max(cy, cx)))
        dur = int(args.get("duration_ms", 500))
        direction = (args.get("direction") or "down").lower()
        dx, dy = 0, 0
        # Note: Swipe direction is opposite of scroll direction.
        # To scroll content down, you swipe finger UP (negative dy).
        if direction == "down":
            dx, dy = 0, -length   # swipe up -> scroll down
        elif direction == "up":
            dx, dy = 0, length    # swipe down -> scroll up
        elif direction == "left":
            dx, dy = -length, 0
        elif direction == "right":
            dx, dy = length, 0
        x1, y1, x2, y2 = x_start, y_start, x_start + dx, y_start + dy
        ok, out = input_swipe(x1, y1, x2, y2, dur)
        adb_cmds.append(f"input swipe {x1} {y1} {x2} {y2} {dur}")
        result.update({"success": ok, "note": out, "adb_cmds": adb_cmds})
        return result

    result.update({"success": False, "note": f"unknown action {act}", "adb_cmds": adb_cmds})
    return result