# verifier.py
import time
from typing import Dict, Any
from observer import observe
from normalizer import normalize

def fuzzy_text_present(state_norm: Dict, needle: str) -> bool:
    for e in state_norm.get("elements", []):
        # Ignore text inside input fields; we want to see results, not just the query typed
        clazz = (e.get("class") or "").lower()
        if "edittext" in clazz:
            continue
        txt = e.get("text") or ""
        if needle.strip().lower() in txt.strip().lower():
            return True
    return False

def verify_and_retry(action: Dict, expect: Dict, max_retries: int = 3) -> Dict:
    """
    expect example:
     {"must_contain_text": "Transfer successful", "activity": "com.example/.CheckoutActivity"}
    """
    attempt = 0
    backoffs = [0.5, 1.0, 2.0]
    last_state = None
    while attempt < max_retries:
        attempt += 1
        # re-observe
        obs = observe()
        state_norm = normalize(obs["raw_xml"])
        last_state = {"obs": obs, "norm": state_norm}
        ok_text = True
        ok_act = True
        if expect is None:
            return {"ok": True, "attempts": attempt, "state": last_state}
        if expect.get("must_contain_text"):
            ok_text = fuzzy_text_present(state_norm, expect["must_contain_text"])
        if expect.get("activity"):
            current_act = obs.get("package_activity")
            ok_act = (expect["activity"] in current_act) if current_act else False
        # Additional: exact text check (useful for Send to <number>)
        if expect.get("must_equal_text"):
            ok_txt_eq = False
            needle_eq = expect["must_equal_text"].strip()
            for e in state_norm.get("elements", []):
                if (e.get("text") or "").strip() == needle_eq:
                    ok_txt_eq = True
                    break
            ok_text = ok_text and ok_txt_eq
        if ok_text and ok_act:
            return {"ok": True, "attempts": attempt, "state": last_state}
        # else wait/backoff and maybe retry action if flagged (caller decides)
        if attempt < max_retries:
            time.sleep(backoffs[min(attempt-1, len(backoffs)-1)])
    return {"ok": False, "attempts": attempt, "state": last_state}
