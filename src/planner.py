# planner.py
import json
import os
from typing import Any, Dict, Optional, List, Tuple
from selector_resolver import resolve_selector

# Minimal JSON-schema validator to avoid heavy deps.
ACTION_SCHEMA = {
    "action_enum": {"tap", "type", "swipe", "back", "scroll", "open_app", "wait"},
    "by_enum": {"resource-id", "text", "content-desc", "bounds", "element_id"},
}

def _is_action_dict(d: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(d, dict):
        return False, "planner output is not an object"
    if "action" not in d:
        return False, "missing action"
    if d["action"] not in ACTION_SCHEMA["action_enum"]:
        return False, f"invalid action {d['action']}"
    if d["action"] not in ["back", "wait"] and "target" not in d:
        return False, "missing target for non-back/non-wait action"
    if "target" in d:
        tgt = d["target"]
        if not isinstance(tgt, dict):
            return False, "target must be an object"
        if "by" not in tgt or "value" not in tgt:
            return False, "target missing by/value"
        if tgt["by"] not in ACTION_SCHEMA["by_enum"]:
            return False, f"invalid target.by {tgt['by']}"
        if not isinstance(tgt["value"], str):
            return False, "target.value must be string"
    if "args" in d and not isinstance(d["args"], dict):
        return False, "args must be object"
    if "args" in d and "duration_ms" in d["args"]:
        try:
            ms = int(d["args"]["duration_ms"])
            if ms < 0:
                return False, "duration_ms must be >= 0"
        except Exception:
            return False, "duration_ms must be integer"
    return True, ""

def validate_action_schema(d: Dict[str, Any]) -> Dict[str, Any]:
    ok, err = _is_action_dict(d)
    if not ok:
        raise ValueError(f"Planner action invalid: {err}")
    return d

def _contains(text: Optional[str], needle: str) -> bool:
    if not text:
        return False
    return needle.lower() in text.lower()

def _find_any_text(state_norm: Dict[str, Any], texts: List[str]) -> Optional[Dict[str, Any]]:
    for e in state_norm.get("elements", []):
        for t in texts:
            if _contains(e.get("text"), t) or _contains(e.get("content_desc"), t):
                return e
    return None

def plan_action_rules(goal: str, state_norm: Dict[str, Any], target_conf: Dict[str, Any]) -> Dict[str, Any]:
    # 1) If goal implies opening target app and we are not in it, plan open_app
    launch_comp = target_conf.get("launch_component")
    package_hint = target_conf.get("package_hint")
    # The loop runner handles checking actual focused activity; here we only use hint if present.
    if launch_comp and package_hint:
        # If no element is found and we have a launch component, consider opening app first.
        # This is a heuristic: first step in a session usually needs opening the app.
        # The loop runner can short-circuit if already in the right package via termination checks.
        return validate_action_schema({
            "action": "open_app",
            "target": {"by": "text", "value": launch_comp},
            "args": {},
            "reason": "Open target app"
        })

    # 2) If the goal implies typing (search/query words), prefer configured selector
    synonyms = target_conf.get("synonyms", {})
    selectors = target_conf.get("selectors", {})
    intents_search = set([w for w in synonyms.get("search", [])])
    goal_l = goal.lower()
    if any(w in goal_l for w in intents_search) and "search_field" in selectors:
        sel = selectors["search_field"]
        return validate_action_schema({
            "action": "type",
            "target": {"by": sel.get("by", "resource-id"), "value": sel.get("value", "")},
            "args": {"text": goal},  # naive: type the whole goal; caller may trim in config
            "reason": "Type into search field"
        })

    # 3) If any visible label matches a key term from goal, tap it
    goal_terms = goal_l.split()
    candidate = None
    best_len = 0
    for e in state_norm.get("elements", []):
        txt = (e.get("text") or "") + " " + (e.get("content_desc") or "")
        tl = txt.lower()
        for term in goal_terms:
            if term and term in tl and len(term) > best_len:
                candidate = e
                best_len = len(term)
    if candidate:
        return validate_action_schema({
            "action": "tap",
            "target": {"by": "element_id", "value": candidate.get("element_id", "")},
            "args": {},
            "reason": "Tap best-matching visible text"
        })

    # 4) Fallback: scroll to discover more items
    return validate_action_schema({
        "action": "scroll",
        "target": {"by": "bounds", "value": "auto"},  # loop will compute coords
        "args": {"duration_ms": target_conf.get("scroll_defaults", {}).get("duration_ms", 300)},
        "reason": "Scroll to reveal target"
    })

def _try_llm_plan(_goal: str, _state: Dict[str, Any], _schema_json: str) -> Optional[Dict[str, Any]]:
    # Placeholder; avoid external deps. Return None to fall back to rules.
    # If you add an LLM, ensure it strictly returns a JSON object and validate via validate_action_schema.
    return None

def plan_action_auto(goal: str, state_norm: Dict[str, Any], target_conf: Dict[str, Any], mode: str = "auto") -> Dict[str, Any]:
    schema_json = json.dumps({
        "action": "tap|type|swipe|back|scroll|open_app|wait",
        "target": {"by": "resource-id|text|content-desc|bounds|element_id", "value": "<string-or-bounds>"},
        "args": {"text": "...", "duration_ms": 300},
        "reason": "short explanation"
    })
    mode = (mode or "auto").lower()
    if mode == "llm":
        out = _try_llm_plan(goal, state_norm, schema_json)
        if out:
            return validate_action_schema(out)
        return plan_action_rules(goal, state_norm, target_conf)
    if mode == "rules":
        return plan_action_rules(goal, state_norm, target_conf)
    # auto: try llm then rules
    out = _try_llm_plan(goal, state_norm, schema_json)
    if out:
        return validate_action_schema(out)
    return plan_action_rules(goal, state_norm, target_conf)