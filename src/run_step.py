# run_step.py
import argparse
import json
import time
from observer import observe
from normalizer import normalize
from actuator import exec_action, find_by_selector
from verifier import verify_and_retry
from logger import RunLogger

def build_action_from_args(args, state_norm):
    # args specify --by and --value, --action
    action = {
        "action": args.action,
        "target": {"by": args.by, "value": args.value} if args.by else {},
        "args": {}
    }
    if args.text:
        action["args"]["text"] = args.text
    return action

def main():
    p = argparse.ArgumentParser(description="Day-1 ADB Observe->Act->Verify step")
    p.add_argument("--action", required=True, choices=["tap","type","swipe","back","open_app"], help="atomic action to perform")
    p.add_argument("--by", choices=["resource-id","text","content-desc","element_id","bounds"], help="how to find target")
    p.add_argument("--value", help="value for selector (resource-id, exact text, element_id, or coords)")
    p.add_argument("--text", help="text to type (for type action)")
    p.add_argument("--verify_text", help="text expected after action (fuzzy match)")
    p.add_argument("--verify_activity", help="activity expected after action (package/.Activity)")
    args = p.parse_args()

    logger = RunLogger()
    try:
        # initial observe
        obs_before = observe()
        state_norm = normalize(obs_before["raw_xml"])
        logger.log({"ts": int(time.time()*1000), "step":"observe_before", "obs": obs_before, "normalized_count": len(state_norm["elements"])})
        # If action refers to text or resource-id, attempt to resolve
        action = build_action_from_args(args, state_norm)
        # Execute action
        exec_report = exec_action(action, state_norm)
        logger.log({"ts": int(time.time()*1000), "step":"act", "action": action, "exec_report": exec_report, "state_before_snapshot": obs_before})
        # Verify with retries
        expect = {}
        if args.verify_text:
            expect["must_contain_text"] = args.verify_text
        if args.verify_activity:
            expect["activity"] = args.verify_activity
        verify_report = verify_and_retry(action, expect, max_retries=3)
        logger.log({"ts": int(time.time()*1000), "step":"verify", "verify_report": verify_report})
        # final observation saved to log by verifier
        print("Action execution:", exec_report)
        print("Verify result:", verify_report["ok"], "attempts:", verify_report["attempts"])
        if not verify_report["ok"]:
            exit(2)
    finally:
        logger.close()

if __name__ == "__main__":
    main()
