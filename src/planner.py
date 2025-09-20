# planner.py
"""
Deterministic planner for Day 2:
- Emits one atomic action following a constrained JSON schema
- Validates with JSON schema
- Uses a rule-based approach guided by a target config (no hosted LLM required)
Config structure (see target_config/flipkart_like.json):
{
  "app_component": "com.example/.MainActivity",
  "package": "com.example",
  "termination": { "must_contain_text": "Payment", "activity": ".CheckoutActivity" },
  "plan_hints": [
    {
      "when_goal_contains_any": ["search", "find"],
      "prefer": [{"by": "resource-id", "value": "com.example:id/search_src_text"},
                 {"by": "text", "value": "Search"}],
      "action": "tap"
    },
    {
      "when_goal_regex": "search for (.+)",
      "action": "type",
      "target_prefer": [{"by": "resource-id", "value": "com.example:id/search_src_text"}],
      "args_from_regex_group": {"text": 1}
    },
    {
      "when_goal_contains_any": ["add to cart", "add"],
      "prefer": [{"by": "text", "value": "ADD TO CART"}, {"by": "text", "value": "Add to cart"}],
      "action": "tap"
    },
    {
      "when_goal_contains_any": ["checkout", "payment"],
      "prefer": [{"by": "text", "value": "Buy Now"}, {"by": "text", "value": "Payment"}],
      "action": "tap"
    }
  ],
  "fallback_scroll_if_not_found": true
}
"""
import re
from typing import Dict, Any, Optional, List, Tuple
from jsonschema import validate, Draft7Validator

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tap", "type", "swipe", "back", "scroll", "open_app", "wait"]},
        "target": {
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": ["resource-id", "text", "content-desc", "bounds", "element_id"]},
                "value": {"type": "string"}
            },
            "required": ["by", "value"]
        },
        "args": {"type": "object"}
    },
    "required": ["action"],
    "additionalProperties": True
}

def _find_first_present(state_norm: Dict, candidates: List[Dict]) -> Optional[Dict]:
    from actuator import find_by_selector
    for sel in candidates:
        el = find_by_selector(state_norm, sel.get("by"), sel.get("value"))
        if el:
            return sel
    return None

def _goal_contains_any(goal: str, words: List[str]) -> bool:
    g = goal.lower()
    return any(w.lower() in g for w in words)

def _goal_regex(goal: str, pattern: str) -> Optional[re.Match]:
    return re.search(pattern, goal, flags=re.IGNORECASE)

def _validate_action(action: Dict) -> Dict:
    v = Draft7Validator(ACTION_SCHEMA)
    errs = sorted(v.iter_errors(action), key=lambda e: e.path)
    if errs:
        raise ValueError("Planner produced invalid action: " + "; ".join([e.message for e in errs]))
    return action

def _need_open_app(obs: Dict, cfg: Dict) -> Optional[Dict]:
    comp = cfg.get("app_component")
    pkg = cfg.get("package")
    act = obs.get("package_activity") or ""
    if comp and comp.split("/")[0] not in act:
        return {"action": "open_app", "target": {"by": "component", "value": comp}, "args": {}}
    if pkg and pkg not in act:
        # If only package given, still open the app component if provided
        comp = cfg.get("app_component") or ""
        if comp:
            return {"action": "open_app", "target": {"by": "component", "value": comp}, "args": {}}
    return None

def plan_next_action(goal: str, obs: Dict, state_norm: Dict, cfg: Dict, history: List[Dict]) -> Dict:
    # 1) Ensure app is open
    act = _need_open_app(obs, cfg)
    if act:
        return _validate_action(act)

    # 2) Try explicit plan hints
    for hint in cfg.get("plan_hints", []):
        if "when_goal_contains_any" in hint:
            if not _goal_contains_any(goal, hint["when_goal_contains_any"]):
                continue
        if "when_goal_regex" in hint:
            m = _goal_regex(goal, hint["when_goal_regex"])
            if not m:
                continue
        action = hint.get("action")
        target = None

        # Find target on screen if specified
        prefer = hint.get("prefer") or hint.get("target_prefer") or []
        if prefer:
            sel = _find_first_present(state_norm, prefer)
            if sel:
                target = sel

        args = {}
        if "args_from_regex_group" in hint and "when_goal_regex" in hint:
            m = _goal_regex(goal, hint["when_goal_regex"])
            if m:
                for k, grp_idx in hint["args_from_regex_group"].items():
                    args[k] = m.group(int(grp_idx))

        # If action is 'type' but no explicit text and history shows last action was tap on input, skip args – caller should supply per config, but we try to use regex above.
        act_obj = {"action": action}
        if target:
            act_obj["target"] = target
        if args:
            act_obj["args"] = args

        try:
            return _validate_action(act_obj)
        except Exception:
            continue

    # 3) Fallback: if not found and allowed, scroll to reveal
    if cfg.get("fallback_scroll_if_not_found", True):
        return _validate_action({"action": "scroll", "args": {"direction": "down", "duration_ms": 400}})

    # 4) As a last resort, wait a bit
    return _validate_action({"action": "wait", "args": {"duration_ms": 500}})