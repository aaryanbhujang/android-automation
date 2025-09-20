import argparse, json, time
from observer import observe
from normalizer import normalize
from verifier import verify_and_retry
from actuator import exec_action
from planner import plan_action_auto
from adb_wrapper import get_focused_activity

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--max_steps", type=int, default=10)
    ap.add_argument("--timeout_s", type=int, default=60)
    ap.add_argument("--planner", choices=["rules","auto","llm"], default="rules")
    ap.add_argument("--verify_each", action="store_true")
    args = ap.parse_args()

    target_conf = load_json(args.target)
    deadline = time.time() + args.timeout_s

    for step in range(1, args.max_steps + 1):
        # 1) Observe and normalize
        obs = observe()  # make sure observe() includes package_activity or add it below
        if not obs.get("package_activity"):
            pa = get_focused_activity()
            if pa:
                obs["package_activity"] = pa

        state_norm = normalize(obs)

        # 2) Termination check (example: you might already be done)
        term = target_conf.get("termination", {})
        pa = obs.get("package_activity", "")
        act_ok = (term.get("activity_substring") in pa) if term.get("activity_substring") else True
        any_texts = term.get("any_of_texts") or []
        any_ok = any(t in (state_norm.get("all_text","") or "") for t in any_texts) if any_texts else False
        print(f"[OBS] step={step} package_activity={pa} pkg_hint={target_conf.get('package_hint')}")
        if act_ok and any_ok:
            print(f"[TERM] ok=True; detail=activity~={term.get('activity_substring')} any_of_texts={any_texts}")
            return 0

        # 3) Plan (pass obs so 'launch once' guard works)
        plan = plan_action_auto(args.goal, state_norm, target_conf, mode=args.planner, obs=obs)
        print(f"[STEP {step}] Plan: {plan}")

        # 4) Execute
        exec_res = exec_action(plan)
        print(f"[STEP {step}] Exec: {exec_res}")
        if not exec_res.get("success"):
            print("[WARN] exec failed; trying to recover with back")
            exec_action({"action":"back","target":{},"args":{}})

        # 5) Optional verification per step
        if args.verify_each:
            verify_and_retry(plan, state_norm)

        if time.time() > deadline:
            print("[TIMEOUT] Exiting")
            break

        # small pause between steps to allow UI to settle
        time.sleep(0.5)

    return 1

if __name__ == "__main__":
    raise SystemExit(main())