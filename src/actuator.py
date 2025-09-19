# actuator.py
from typing import Dict, Tuple, Optional, List
from adb_wrapper import input_tap, input_text, input_swipe, key_back, am_start
from normalizer import sha1_of

def centroid_from_element(element: Dict) -> Tuple[int,int]:
    return tuple(element.get("center", [0,0]))

def find_by_selector(state_norm: Dict, by: str, value: str) -> Optional[Dict]:
    elems = state_norm.get("elements", [])
    # resource-id exact match
    if by == "resource-id":
        for e in elems:
            if e.get("resource_id") == value:
                return e
    if by == "element_id":
        return state_norm.get("by_id", {}).get(value)
    if by == "text":
        for e in elems:
            if e.get("text") and e.get("text").strip() == value.strip():
                return e
    if by == "content-desc":
        for e in elems:
            if e.get("content_desc") == value:
                return e
    # bounds expected as "[l,t][r,b]" or "l,t,r,b" or JSON list
    if by == "bounds":
        try:
            import ast
            if value.strip().startswith("["):
                b = ast.literal_eval(value)
                # find element whose bounds equal
                for e in elems:
                    if e.get("bounds") == b:
                        return e
        except Exception:
            pass
    return None

def exec_action(action: Dict, state_norm: Dict) -> Dict:
    """
    action: {"action":"tap"|"type"|"swipe"|"back"|"open_app",
             "target":{"by":"resource-id"|"text"|"element_id"|"bounds"|"content-desc", "value":"..."},
             "args": {"text": "...", "duration_ms": 300}
            }
    Returns execution report.
    """
    act = action.get("action")
    target = action.get("target", {})
    args = action.get("args", {}) or {}
    adb_cmds = []
    result = {"success": False, "adb_cmds": [], "note": ""}

    if act == "open_app":
        comp = target.get("value")
        ok, out = am_start(comp)
        adb_cmds.append(f"am start -n {comp}")
        result["success"] = ok
        result["note"] = out
        result["adb_cmds"] = adb_cmds
        return result

    if act == "back":
        ok, out = key_back()
        adb_cmds.append("input keyevent KEYCODE_BACK")
        result["success"] = ok
        result["note"] = out
        result["adb_cmds"] = adb_cmds
        return result

    if act in ("tap", "type"):
        # resolve element
        if target:
            elem = find_by_selector(state_norm, target.get("by"), target.get("value"))
            if elem:
                x, y = centroid_from_element(elem)
            else:
                # if target value might be raw "x,y" coords
                try:
                    coords = target.get("value")
                    if isinstance(coords, str) and "," in coords:
                        parts = coords.split(",")
                        x, y = int(parts[0]), int(parts[1])
                    else:
                        x, y = 0, 0
                except Exception:
                    x, y = 0, 0
        else:
            x, y = 0, 0

        if act == "tap":
            ok, out = input_tap(x, y)
            adb_cmds.append(f"input tap {x} {y}")
            result["success"] = ok
            result["note"] = out
            result["adb_cmds"] = adb_cmds
            return result

        if act == "type":
            text = args.get("text", "")
            # Focus the field by tapping if coords available
            if x and y:
                input_tap(x, y)
                adb_cmds.append(f"input tap {x} {y}")
            ok, out = input_text(text)
            adb_cmds.append(f"input text {text}")
            result["success"] = ok
            result["note"] = out
            result["adb_cmds"] = adb_cmds
            return result

    if act == "swipe":
        dur = args.get("duration_ms", 300)
        tv = target.get("value")
        # expect target.value to be "x1,y1,x2,y2"
        try:
            x1,y1,x2,y2 = map(int, tv.split(","))
            ok, out = input_swipe(x1,y1,x2,y2,dur)
            adb_cmds.append(f"input swipe {x1} {y1} {x2} {y2} {dur}")
            result["success"] = ok
            result["note"] = out
            result["adb_cmds"] = adb_cmds
            return result
        except Exception as e:
            result["note"] = f"swipe parse error: {e}"
            return result

    result["note"] = f"unsupported action {act}"
    return result
