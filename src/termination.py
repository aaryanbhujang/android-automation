# termination.py
from typing import Dict, Any, List, Optional

def _ci_contains(hay: Optional[str], needle: str) -> bool:
    return bool(hay) and needle.lower() in hay.lower()

def _any_text_present(state_norm: Dict[str, Any], texts: List[str]) -> bool:
    for e in state_norm.get("elements", []):
        for t in texts:
            if _ci_contains(e.get("text"), t) or _ci_contains(e.get("content_desc"), t):
                return True
    return False

def _one_text_present(state_norm: Dict[str, Any], t: str) -> bool:
    return _any_text_present(state_norm, [t])

def evaluate_termination(state_norm: Dict[str, Any], obs_meta: Dict[str, Any], termination_conf: Dict[str, Any]) -> Dict[str, Any]:
    """
    termination_conf supports:
      - package: substring match against obs_meta['package_activity']
      - activity: substring match against obs_meta['package_activity']
      - must_contain_text: string
      - any_of_texts: list[str]
      - all_of_texts: list[str]
    """
    pkg_act = obs_meta.get("package_activity", "") or ""
    ok = True
    reasons = []
    if "package" in termination_conf:
        exp = termination_conf["package"]
        cond = exp.lower() in pkg_act.lower()
        ok = ok and cond
        reasons.append(f"package~={exp}: {cond}")
    if "activity" in termination_conf:
        exp = termination_conf["activity"]
        cond = exp.lower() in pkg_act.lower()
        ok = ok and cond
        reasons.append(f"activity~={exp}: {cond}")
    if "must_contain_text" in termination_conf:
        exp = termination_conf["must_contain_text"]
        cond = _one_text_present(state_norm, exp)
        ok = ok and cond
        reasons.append(f"must_contain_text '{exp}': {cond}")
    if "any_of_texts" in termination_conf:
        arr = termination_conf["any_of_texts"] or []
        cond = _any_text_present(state_norm, arr)
        ok = ok and cond
        reasons.append(f"any_of_texts {arr}: {cond}")
    if "all_of_texts" in termination_conf:
        arr = termination_conf["all_of_texts"] or []
        cond = all(_one_text_present(state_norm, t) for t in arr)
        ok = ok and cond
        reasons.append(f"all_of_texts {arr}: {cond}")
    return {"ok": ok, "detail": "; ".join(reasons), "package_activity": pkg_act}