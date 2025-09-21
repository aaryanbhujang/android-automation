# run_goal.py
import argparse
import json
import time
from typing import Dict, Any, List

from observer import observe
from normalizer import normalize
from actuator import exec_action
from verifier import verify_and_retry
from planner import plan_next_action
from logger import RunLogger

def goal_satisfied(obs: Dict, state_norm: Dict, termination: Dict) -> bool:
    ok = True
    if not termination:
        return False
    if "must_contain_text" in termination:
        needle = termination["must_contain_text"].strip().lower()
        ok = ok and any(needle in (e.get("text") or "").strip().lower() for e in state_norm.get("elements", []))
    if "activity" in termination:
        act = obs.get("package_activity") or ""
        ok = ok and (termination["activity"] in act)
    if "package" in termination:
        act = obs.get("package_activity") or ""
        ok = ok and (termination["package"] in act)
    return ok

def main():
    ap = argparse.ArgumentParser(description="Day 2 NL-goal runner (Observe → Plan → Act → Verify loop)")
    ap.add_argument("--goal", required=True, help='Natural language goal, e.g. "Add a MacBook Pro to the cart and go to the payment page"')
    ap.add_argument("--config", required=True, help="Target config JSON path")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--verify-retries", type=int, default=3)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    logger = RunLogger()
    history: List[Dict[str, Any]] = []
    try:
        start_ts = int(time.time() * 1000)
        for step in range(1, args.max_steps + 1):
            # Observe
            obs = observe()
            state_norm = normalize(obs["raw_xml"])
            logger.log({"ts": obs["ts"], "step": "observe", "obs_meta": {"activity": obs.get("package_activity")}, "normalized_count": len(state_norm["elements"])})

            # Termination?
            if goal_satisfied(obs, state_norm, cfg.get("termination", {})):
                logger.log({"ts": int(time.time() * 1000), "step": "done", "reason": "termination satisfied", "goal": args.goal})
                print("SUCCESS: Goal satisfied.")
                return

            # Plan
            try:
                plan = plan_next_action(args.goal, obs, state_norm, cfg, history)
            except Exception as e:
                logger.log({"ts": int(time.time() * 1000), "step": "plan_error", "error": str(e)})
                print("FAILURE: Planner error:", str(e))
                exit(3)

            logger.log({"ts": int(time.time() * 1000), "step": "plan", "plan": plan})

            # Act
            exec_report = exec_action(plan, state_norm)
            logger.log({"ts": int(time.time() * 1000), "step": "act", "action": plan, "exec_report": exec_report})

            if not exec_report.get("success"):
                logger.log({"ts": int(time.time() * 1000), "step": "act_failure", "note": exec_report.get("note")})
                print("FAILURE: Action execution failed:", exec_report.get("note"))
                exit(4)

            # Verify (derive minimal expectations from config or from plan)
            expect = {}
            # Heuristic: after open_app, expect activity/package
            if plan.get("action") == "open_app" and cfg.get("package"):
                expect["activity"] = cfg.get("package")
            # Only expect text after typing to avoid redundant checks causing loops
            if plan.get("action") == "type" and cfg.get("verify_after_action_text"):
                expect["must_contain_text"] = cfg["verify_after_action_text"]

            # If typing a phone number, verify the "Send to <number>" suggestion appears exactly
            if plan.get("action") == "type":
                args_text = ((plan.get("args") or {}).get("text") or "").strip()
                if args_text.isdigit() and len(args_text) >= 5:  # simple phone-like heuristic
                    expect = {"must_equal_text": f"Send to {args_text}"}

            verify_report = verify_and_retry(plan, expect or None, max_retries=args.verify_retries)
            logger.log({"ts": int(time.time() * 1000), "step": "verify", "verify_report": {"ok": verify_report["ok"], "attempts": verify_report["attempts"]}})

            history.append({"obs": obs, "plan": plan, "exec": exec_report, "verify": verify_report})
            # Small pacing
            time.sleep(0.25)

        # If loop exits without termination
        logger.log({"ts": int(time.time() * 1000), "step": "fail_termination", "goal": args.goal})
        print("FAILURE: Max steps exceeded without satisfying goal.")
        exit(5)
    finally:
        logger.close()

if __name__ == "__main__":
    main()