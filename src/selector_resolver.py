# selector_resolver.py
from typing import Dict, Any, Optional, List, Tuple
import difflib

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def element_center(e: Dict[str, Any]) -> Tuple[int, int]:
    c = e.get("center") or [0, 0]
    return int(c[0]), int(c[1])

def resolve_selector(state_norm: Dict[str, Any], target: Dict[str, str], fallback_policy: str = "default") -> Optional[Dict[str, Any]]:
    if not state_norm or not target:
        return None
    elems = state_norm.get("elements", [])
    by = target.get("by")
    val = target.get("value", "")

    # 1) resource-id exact
    if by == "resource-id":
        for e in elems:
            if _norm(e.get("resource_id")) == _norm(val):
                return e

    # 2) text exact (trimmed)
    if by == "text":
        for e in elems:
            if _norm(e.get("text")) == _norm(val):
                return e

    # 3) content-desc exact
    if by == "content-desc":
        for e in elems:
            if _norm(e.get("content_desc")) == _norm(val):
                return e

    # 4) element_id exact
    if by == "element_id":
        e = state_norm.get("by_id", {}).get(val)
        if e:
            return e

    # 5) bounds equality (expects list)
    if by == "bounds":
        # bounds matching is handled in actuator/find_by_selector, but we still add here for completeness
        for e in elems:
            if str(e.get("bounds")) == val or str(e.get("bounds")).replace(" ", "") == val.replace(" ", ""):
                return e

    # Fuzzy search if target is by text/content-desc
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for e in elems:
        blob = " ".join([_norm(e.get("text")), _norm(e.get("content_desc"))]).strip()
        if not blob:
            continue
        ratio = difflib.SequenceMatcher(None, blob.lower(), _norm(val).lower()).ratio()
        contains = _norm(val).lower() in blob.lower()
        if contains or ratio >= 0.6:
            candidates.append((ratio + (0.1 if contains else 0.0), e))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return None