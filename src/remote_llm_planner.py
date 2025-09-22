"""
Remote LLM planner for hosted models (OpenAI-compatible APIs).

Supports providers like OpenAI, OpenRouter, Together via base_url + api_key.

Env vars used if args not provided:
  - OPENAI_API_KEY (preferred), or OPENROUTER_API_KEY, or TOGETHER_API_KEY
  - OPENAI_BASE_URL (optional)

Example usage:
  from remote_llm_planner import RemoteLLMPlanner
  planner = RemoteLLMPlanner(model="gpt-4o-mini", base_url=None, api_key=None)
  action = planner.plan_next_action_remote(goal, obs, state_norm, cfg, history)
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI  # type: ignore
except Exception as e:  # pragma: no cover - optional dep
    OpenAI = None

from jsonschema import Draft7Validator
from planner import ACTION_SCHEMA


SYSTEM_PROMPT = (
    "You are a deterministic planner for Android UI automation. "
    "Given the goal, the current activity, and a compact list of visible UI elements, "
    "produce exactly ONE next atomic action as strict JSON only. "
    "No prose, no markdown. Use the schema: {\n"
    "  action: one of ['tap','type','swipe','back','scroll','open_app','wait','keyevent'],\n"
    "  target?: {by: 'resource-id'|'text'|'content-desc'|'bounds'|'element_id'|'component', value: string},\n"
    "  args?: object\n"
    "}.\n"
    "Rules:\n"
    "- If the app is not the target package, emit open_app with target.component.\n"
    "- Prefer tapping obvious buttons or rows.\n"
    "- Only 'type' when an input/search field is visible.\n"
    "- Use 'scroll' to reveal content if needed.\n"
    "- Keep actions minimal and safe.\n"
)


def _summarize_ui(obs: Dict[str, Any], state_norm: Dict[str, Any], max_items: int = 40) -> str:
    act = obs.get("package_activity") or ""
    parts: List[str] = [f"activity: {act}", "elements:"]
    elems = state_norm.get("elements", [])[:max_items]
    for i, e in enumerate(elems, 1):
        rid = e.get("resource_id") or ""
        txt = e.get("text") or ""
        cd = e.get("content_desc") or ""
        clazz = e.get("class") or ""
        clickable = bool(e.get("clickable"))
        parts.append(f"- {i}: [clickable={clickable}] class={clazz} id={rid} text={txt} desc={cd}")
    return "\n".join(parts)


def _extract_json_object(s: str) -> Dict[str, Any]:
    # Try strict json first
    try:
        return json.loads(s)
    except Exception:
        pass
    # Fallback: find first balanced {...}
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")
    brace = 0
    for i in range(start, len(s)):
        if s[i] == '{':
            brace += 1
        elif s[i] == '}':
            brace -= 1
            if brace == 0:
                chunk = s[start:i+1]
                return json.loads(chunk)
    raise ValueError("Unbalanced JSON braces in LLM output")


class RemoteLLMPlanner:
    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.1,
        top_p: float = 0.9,
        max_tokens: int = 300,
        json_mode: bool = True,
    ) -> None:
        if OpenAI is None:
            raise RuntimeError(
                "openai package not installed. Install with: pip install openai\n"
                "Then pass --remote-llm-model and optionally --remote-llm-base-url/--remote-llm-api-key"
            )
        # Resolve API key
        key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("TOGETHER_API_KEY")
        if not key:
            raise RuntimeError("No API key found. Set OPENAI_API_KEY (or OPENROUTER_API_KEY / TOGETHER_API_KEY) or pass --remote-llm-api-key.")
        self.client = OpenAI(base_url=base_url or os.getenv("OPENAI_BASE_URL"), api_key=key)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self.validator = Draft7Validator(ACTION_SCHEMA)

    def plan_next_action_remote(
        self,
        goal: str,
        obs: Dict[str, Any],
        state_norm: Dict[str, Any],
        cfg: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ui_text = _summarize_ui(obs, state_norm)
        target_pkg = cfg.get("package") or ""
        app_component = cfg.get("app_component") or ""
        prompt = (
            f"Goal: {goal}\n"
            f"Target package: {target_pkg}\n"
            f"App component (for open_app): {app_component}\n\n"
            f"{ui_text}\n\n"
            "Respond with a single JSON object only."
        )

        try:
            kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )
            if self.json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = self.client.chat.completions.create(**kwargs)
            text = (resp.choices[0].message.content or "").strip()
        except Exception:
            # Fallback to a plain completion-like prompt if provider doesn't support chat
            resp = self.client.completions.create(model=self.model, prompt=f"{SYSTEM_PROMPT}\n\n{prompt}\n\nJSON:")
            text = (resp.choices[0].text or "").strip()

        # Parse JSON; if it fails, default to a safe fallback below
        try:
            action = _extract_json_object(text)
        except Exception:
            action = None

        # Enforce open_app if wrong package or default wait if parsing failed
        act = (obs.get("package_activity") or "")
        if action is None:
            if target_pkg and target_pkg not in act and app_component:
                action = {"action": "open_app", "target": {"by": "component", "value": app_component}}
            else:
                action = {"action": "wait", "args": {"duration_ms": 400}}
        elif target_pkg and target_pkg not in act and app_component:
            action = {"action": "open_app", "target": {"by": "component", "value": app_component}}

        errs = sorted(self.validator.iter_errors(action), key=lambda e: e.path)
        if errs:
            # Last resort safety
            return {"action": "wait", "args": {"duration_ms": 400}}
        return action
