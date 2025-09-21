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
        "action": {"type": "string", "enum": ["tap", "type", "swipe", "back", "scroll", "open_app", "wait", "keyevent"]},
        "target": {
            "type": "object",
            "properties": {
                # Allow "component" so open_app can validate cleanly
                "by": {"type": "string", "enum": ["resource-id", "text", "content-desc", "bounds", "element_id", "component"]},
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
    # Acceptable packages: primary + optional allowed_packages
    allowed = [p for p in [pkg] if p] + list(cfg.get("allowed_packages", []))
    if any(p and p in act for p in allowed):
        return None
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

        # Resolve target: static target wins, otherwise use first present from preferences
        if hint.get("target"):
            target = hint["target"]
        else:
            prefer = hint.get("prefer") or hint.get("target_prefer") or []
            if prefer:
                sel = _find_first_present(state_norm, prefer)
                if sel:
                    target = sel

        # Build args from regex groups if configured
        args = {}
        if "args_from_regex_group" in hint and "when_goal_regex" in hint:
            m = _goal_regex(goal, hint["when_goal_regex"])
            if m:
                for k, grp_idx in hint["args_from_regex_group"].items():
                    args[k] = m.group(int(grp_idx))

        # Merge any static args from the hint itself
        if isinstance(hint.get("args"), dict):
            for k, v in hint["args"].items():
                if v is not None:
                    args[k] = v

        # Allow target to be constructed dynamically from regex group
        if not target and "target_from_regex_group" in hint and "when_goal_regex" in hint:
            m = _goal_regex(goal, hint["when_goal_regex"])
            if m:
                cfg_dyn = hint["target_from_regex_group"]
                by = cfg_dyn.get("by", "text")
                grp_idx = int(cfg_dyn.get("group", 1))
                try:
                    val = m.group(grp_idx)
                    prefix = cfg_dyn.get("prefix", "")
                    suffix = cfg_dyn.get("suffix", "")
                    if prefix or suffix:
                        val = f"{prefix}{val}{suffix}"
                    if val:
                        target = {"by": by, "value": val}
                except Exception:
                    pass

        # Avoid redundant open_app if already in the package
        if action == "open_app":
            current = obs.get("package_activity") or ""
            allowed = [p for p in [cfg.get("package")] if p] + list(cfg.get("allowed_packages", []))
            if any(p and p in current for p in allowed):
                continue

        # For tap/open_app require a concrete target
        if action in ("tap", "open_app") and not target:
            continue

        act_obj = {"action": action}
        if target:
            act_obj["target"] = target
        if args:
            act_obj["args"] = args

        # Avoid typing the same query repeatedly; if we already typed recently, skip this hint
        if action == "type":
            typed_same_recently = False
            text_arg = (args.get("text") or "").strip().lower()
            for h in list(history)[-3:][::-1]:  # look back a few steps
                prev = (h.get("plan") or {})
                if prev.get("action") == "type":
                    prev_text = ((prev.get("args") or {}).get("text") or "").strip().lower()
                    if not text_arg or text_arg == prev_text:
                        typed_same_recently = True
                        break
            if typed_same_recently:
                continue

        # Heuristic: if goal is a search and a search edit field is present, skip further 'tap search' hints
        if action == "tap" and _goal_contains_any(goal, ["search"]):
            # Detect presence of a visible search edit field to avoid re-tapping search
            has_search_field = False
            for e in state_norm.get("elements", []):
                rid = (e.get("resource_id") or "").lower()
                clazz = (e.get("class") or "").lower()
                # Consider any EditText as a candidate (search UIs usually expose an EditText)
                if ("edittext" in clazz) or ("search_src_text" in rid) or ("search_edit_text" in rid):
                    has_search_field = True
                    break
            # Also skip retapping if we're in a Search activity already
            cur = (obs.get("package_activity") or "").lower()
            if "searchactivity" in cur or ".search" in cur:
                has_search_field = True
            if has_search_field:
                continue

        try:
            return _validate_action(act_obj)
        except Exception:
            continue

    # 3) Fallback: if not found and allowed, scroll to reveal
    if cfg.get("fallback_scroll_if_not_found", True):
        scroll_args = {"direction": "down", "duration_ms": 500, "length_factor": 0.7}
        if isinstance(cfg.get("fallback_scroll_args"), dict):
            scroll_args.update(cfg["fallback_scroll_args"])
        return _validate_action({"action": "scroll", "args": scroll_args})

    # 4) As a last resort, wait a bit
    return _validate_action({"action": "wait", "args": {"duration_ms": 500}})