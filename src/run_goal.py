"""
run_goal.py

End-to-end NL goal runner implementing Observe → Plan → Act → Verify.

Flow summary
1) Observe: dump UI XML + screenshot
2) Plan: choose ONE atomic action (rule-based or LLM planners)
3) Act: execute via ADB primitives
4) Verify: re-observe and assert expectations with retries/backoff

Termination
- Uses cfg["termination"] assertions (package/activity/text) as the source of truth.
- Optional: --llm-verify asks Gemini to judge goal satisfaction, but success is
    only declared when BOTH LLM verdict ok and deterministic termination hold on a
    fresh observation.

Exit codes
2: config/router/planner unavailability
3: planner error
4: action execution failure
5: max steps exceeded without termination
"""
import argparse
import json
import time
from typing import Dict, Any, List

from observer import observe
from normalizer import normalize
from actuator import exec_action
from verifier import verify_and_retry
from planner import plan_next_action
try:
    from llm_planner import LlamaPlanner  # optional
except Exception:
    LlamaPlanner = None
try:
    from remote_llm_planner import RemoteLLMPlanner  # optional
except Exception:
    RemoteLLMPlanner = None
try:
    from gemini_llm_planner import GeminiPlanner  # optional
except Exception:
    GeminiPlanner = None
from logger import RunLogger

def goal_satisfied(obs: Dict, state_norm: Dict, termination: Dict) -> bool:
    """Deterministic termination evaluator used as the source of truth."""
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
    # New: allow termination when leaving certain packages (e.g., launcher)
    if "not_package_in" in termination:
        act = obs.get("package_activity") or ""
        bad = termination.get("not_package_in") or []
        if isinstance(bad, str):
            bad = [bad]
        ok = ok and all((p not in act) for p in bad if p)
    return ok

def _needs_enter_after_type(goal: str, history: List[Dict[str, Any]]) -> bool:
    """
    Heuristic: If the last action was a 'type' and the goal asks to press return/enter/go/search,
    emit a KEYCODE_ENTER next. Avoid sending repeatedly if we've just sent one.
    """
    if not history:
        return False
    last_plan = (history[-1].get("plan") or {})
    if last_plan.get("action") != "type":
        return False
    # Avoid duplicates if we recently sent ENTER/SEARCH
    for h in list(history)[-3:][::-1]:
        p = (h.get("plan") or {})
        if p.get("action") == "keyevent":
            key = ((p.get("args") or {}).get("key") or "").upper()
            if key in ("ENTER", "SEARCH"):
                return False
    g = goal.lower()
    triggers = [
        "[return]", " press enter", " hit enter", " press return", " hit return",
        " press go", " tap go", " hit go", " then go",
        " press search", " hit search", " then search", " input [return]"
    ]
    return any(t in g for t in triggers)

def main():
    ap = argparse.ArgumentParser(description="Day 2 NL-goal runner (Observe → Plan → Act → Verify loop)")
    ap.add_argument("--goal", required=True, help='Natural language goal, e.g. "Add a MacBook Pro to the cart and go to the payment page"')
    ap.add_argument("--config", required=False, help="Target config JSON path (optional if using --auto-config)")
    ap.add_argument("--auto-config", action="store_true", help="Infer a minimal config from the natural-language goal (uses built-ins)")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--verify-retries", type=int, default=3)
    ap.add_argument("--llama-model", type=str, default=None, help="Path to a local GGUF LLaMA/Mistral model; if set, use offline LLM planner")
    ap.add_argument("--remote-llm-model", type=str, default=None, help="Hosted LLM model name (e.g., gpt-4o-mini, openrouter/...)")
    ap.add_argument("--remote-llm-base-url", type=str, default=None, help="Custom OpenAI-compatible base URL (e.g., https://openrouter.ai/api/v1)")
    ap.add_argument("--remote-llm-api-key", type=str, default=None, help="API key (fallback to OPENAI_API_KEY/OPENROUTER_API_KEY env vars)")
    # Gemini hosted planner
    ap.add_argument("--gemini-model", type=str, default=None, help="Gemini model (e.g., gemini-1.5-flash, gemini-1.5-pro)")
    ap.add_argument("--gemini-api-key", type=str, default=None, help="Google API key (fallback to GOOGLE_API_KEY env var)")
    ap.add_argument("--llm-verify", action="store_true", help="After each action, ask the LLM to judge if the goal appears satisfied (advisory); requires --gemini-model")
    args = ap.parse_args()

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        from goal_router import synthesize_config_from_goal
        cfg = synthesize_config_from_goal(args.goal)
        if not cfg:
            print("FAILURE: No config provided and auto-config could not infer app/termination from the goal.")
            exit(2)
        auto_path = cfg.pop("_auto_config_path", None)
        if auto_path:
            print(f"Auto-config saved to: {auto_path}")

    logger = RunLogger()
    llm_planner = None
    remote_planner = None
    gemini_planner = None
    # Planner selection priority: Gemini > Remote(OpenAI-compatible) > Local Llama > Rule-based
    if args.gemini_model:
        if GeminiPlanner is None:
            print("FAILURE: google-generativeai not installed or gemini_llm_planner unavailable.")
            exit(2)
        gemini_planner = GeminiPlanner(model=args.gemini_model, api_key=args.gemini_api_key)
    if args.llm_verify and not gemini_planner:
        print("NOTE: --llm-verify currently supported only with --gemini-model; flag will be ignored.")
        args.llm_verify = False
    if args.llama_model:
        if LlamaPlanner is None:
            print("FAILURE: llama-cpp-python not installed or llm_planner unavailable.")
            exit(2)
        llm_planner = LlamaPlanner(model_path=args.llama_model)
    if args.remote_llm_model:
        if RemoteLLMPlanner is None:
            print("FAILURE: openai client not installed or remote_llm_planner unavailable.")
            exit(2)
        remote_planner = RemoteLLMPlanner(
            model=args.remote_llm_model,
            base_url=args.remote_llm_base_url,
            api_key=args.remote_llm_api_key,
        )
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

            # Plan (with optional enter-after-type nudge)
            try:
                if _needs_enter_after_type(args.goal, history):
                    plan = {"action": "keyevent", "args": {"key": "ENTER"}}
                else:
                    if gemini_planner:
                        plan = gemini_planner.plan_next_action_gemini(args.goal, obs, state_norm, cfg, history)
                    elif remote_planner:
                        plan = remote_planner.plan_next_action_remote(args.goal, obs, state_norm, cfg, history)
                    elif llm_planner:
                        plan = llm_planner.plan_next_action_llm(args.goal, obs, state_norm, cfg, history)
                    else:
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
            # Optional: LLM-based verification (advisory + potential early termination)
            if args.llm_verify and gemini_planner:
                # Re-observe after the action for the most up-to-date UI
                post_obs = observe()
                post_state = normalize(post_obs["raw_xml"])
                llm_verdict = gemini_planner.verify_goal_gemini(args.goal, post_obs, post_state, cfg.get("termination", {}))
                logger.log({"ts": int(time.time() * 1000), "step": "llm_verify", "llm_verdict": llm_verdict})
                # If both LLM says ok and deterministic termination is satisfied on the fresh observation, end early
                if llm_verdict.get("ok") and goal_satisfied(post_obs, post_state, cfg.get("termination", {})):
                    logger.log({"ts": int(time.time() * 1000), "step": "done", "reason": "llm+termination satisfied", "goal": args.goal})
                    print("SUCCESS: Goal satisfied (LLM + termination).")
                    return
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