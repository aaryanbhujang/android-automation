"""
Gemini LLM planner (Google Generative AI) for hosted models.

Usage:
  from gemini_llm_planner import GeminiPlanner
  planner = GeminiPlanner(model="gemini-1.5-flash", api_key=os.getenv("GOOGLE_API_KEY"))
  action = planner.plan_next_action_gemini(goal, obs, state_norm, cfg, history)
"""
from __future__ import annotations

import json
import os
import base64
from typing import Any, Dict, List, Optional
import re

try:
    import google.generativeai as genai  # type: ignore
except Exception:
    genai = None

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
    try:
        return json.loads(s)
    except Exception:
        pass
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
                return json.loads(s[start:i+1])
    raise ValueError("Unbalanced JSON braces in LLM output")


class GeminiPlanner:
    def __init__(
        self,
        model: str = "gemini-1.5-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.1,
        top_p: float = 0.9,
        max_tokens: int = 300,
        json_mode: bool = True,
    ) -> None:
        if genai is None:
            raise RuntimeError(
                "google-generativeai not installed. Install with: pip install google-generativeai"
            )
        key = api_key or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("No Google API key. Set GOOGLE_API_KEY or pass --gemini-api-key.")
        genai.configure(api_key=key)
        self.model_name = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self.validator = Draft7Validator(ACTION_SCHEMA)

        # Prefer JSON responses
        self.generation_config = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_tokens,
            "response_mime_type": "application/json" if self.json_mode else "text/plain",
        }
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=SYSTEM_PROMPT,
            generation_config=self.generation_config,
        )

    def plan_next_action_gemini(
        self,
        goal: str,
        obs: Dict[str, Any],
        state_norm: Dict[str, Any],
        cfg: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Lightweight deterministic assists for common intents (keeps behavior generic)
        def _goal_has_scroll() -> Optional[str]:
            g = (goal or "").lower()
            if "scroll up" in g:
                return "up"
            if "scroll down" in g:
                return "down"
            if "scroll" in g:
                # Default to down if unspecified
                return "down"
            return None

        def _visible_text_contains_any(terms: List[str]) -> Optional[Dict[str, Any]]:
            lows = [t.lower() for t in terms if t]
            for e in state_norm.get("elements", []):
                txt = ((e.get("text") or "") + " " + (e.get("content_desc") or "")).lower()
                if any(t in txt for t in lows):
                    return e
            return None

        # If the goal explicitly asks to scroll and the target text isn't visible yet, emit a scroll first.
        scroll_dir = _goal_has_scroll()
        if scroll_dir:
            # Try to infer a simple target term after 'find'/'search for', else look for common words in the goal
            m = re.search(r"(?:find|search(?:\s+for)?)\s+([a-z0-9 &._-]+)", goal, flags=re.IGNORECASE)
            target_terms: List[str] = []
            if m:
                # Split on conjunctions to handle phrases like "system and tap on it"
                raw = (m.group(1) or "").lower()
                # keep the first token that looks like a UI label word
                # heuristics: drop trailing instructions like 'and tap on it'
                raw = re.split(r"\band\b|\btap\b|\bon\b|\bit\b", raw)[0].strip()
                if raw:
                    # limit to first 3 words to avoid long phrases
                    target_terms = [" ".join(raw.split()[:3])]
            # Also include a few single-word candidates present in the goal (e.g., 'system')
            for w in ["system", "about", "apps", "privacy", "security", "developer"]:
                if w in (goal or "").lower():
                    target_terms.append(w)

            target_el = _visible_text_contains_any(target_terms) if target_terms else None
            if target_el:
                # If a matching element is visible, prefer to tap it directly
                return {
                    "action": "tap",
                    "target": {"by": "element_id", "value": target_el.get("element_id")}
                }
            # Otherwise, perform a strong scroll in the requested direction
            start_pos = "bottom" if scroll_dir == "down" else "top"
            return {
                "action": "scroll",
                "args": {"direction": scroll_dir, "duration_ms": 700, "length_factor": 0.9, "start_pos": start_pos}
            }
        # Helper: find a visible search/text input field
        def _first_edit_field() -> Optional[Dict[str, Any]]:
            for e in state_norm.get("elements", []):
                rid = (e.get("resource_id") or "").lower()
                clazz = (e.get("class") or "").lower()
                if ("edittext" in clazz) or ("search_src_text" in rid) or ("search_edit_text" in rid):
                    return e
            return None

        def _already_typed(txt: str) -> bool:
            t = (txt or "").strip().lower()
            for h in list(history)[-3:][::-1]:
                prev = (h.get("plan") or {})
                if prev.get("action") == "type":
                    prev_text = ((prev.get("args") or {}).get("text") or "").strip().lower()
                    if t and t == prev_text:
                        return True
            return False

        def _goal_search_term() -> Optional[str]:
            # Prefer termination must_contain_text injected by router
            term = ((cfg.get("termination") or {}).get("must_contain_text") or "").strip()
            if term:
                return term
            # Fallback to regex on goal
            m = re.search(r"search\s+(for\s+)?(.+)", goal, flags=re.IGNORECASE)
            if m:
                return (m.group(2) or "").strip()
            m = re.search(r"find\s+(.+)", goal, flags=re.IGNORECASE)
            if m:
                return (m.group(1) or "").strip()
            return None

        # Proactive guard: if a search/input field is present and the goal has a term, type instead of re-tapping search
        edit = _first_edit_field()
        term = _goal_search_term()
        if edit and term and not _already_typed(term):
            return {
                "action": "type",
                "target": {"by": "element_id", "value": edit.get("element_id")},
                "args": {"text": term}
            }
        ui_text = _summarize_ui(obs, state_norm)
        target_pkg = cfg.get("package") or ""
        app_component = cfg.get("app_component") or ""
        prompt = (
            f"Goal: {goal}\n"
            f"Target package: {target_pkg}\n"
            f"App component (for open_app): {app_component}\n\n"
            f"{ui_text}\n\n"
            "If an image is attached, use it to disambiguate targets (colors/icons/layout).\n"
            "Respond with a single JSON object only."
        )
        parts: List[Any] = [prompt]
        # Attach screenshot if available for multimodal planning
        screenshot = obs.get("screenshot")
        try:
            if screenshot and isinstance(screenshot, str) and os.path.exists(screenshot):
                with open(screenshot, "rb") as f:
                    data_b64 = base64.b64encode(f.read()).decode("ascii")
                parts.append({
                    "inline_data": {"mime_type": "image/png", "data": data_b64}
                })
        except Exception:
            # Continue without image if anything goes wrong
            pass
        try:
            resp = self.model.generate_content(parts)
            text = (getattr(resp, "text", None) or getattr(resp, "candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "") or "").strip()
        except Exception:
            text = ""

        # Parse JSON
        try:
            action = _extract_json_object(text)
        except Exception:
            action = None

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
            return {"action": "wait", "args": {"duration_ms": 400}}
        return action

    def verify_goal_gemini(
        self,
        goal: str,
        obs: Dict[str, Any],
        state_norm: Dict[str, Any],
        termination: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Ask Gemini to judge whether the goal/termination appear satisfied in the current UI.
        Returns {"ok": bool, "reason": str}
        """
        ui_text = _summarize_ui(obs, state_norm)
        act = obs.get("package_activity") or ""
        prompt = (
            "You are verifying goal satisfaction for Android UI automation.\n"
            "Given the goal, the termination assertions, the current activity, and a list of visible elements,\n"
            "respond ONLY with a JSON object of the form { ok: boolean, reason: string }.\n"
            "Be strict: if it's ambiguous, return ok=false and explain briefly.\n\n"
            f"Goal: {goal}\n"
            f"Termination assertions (JSON): {json.dumps(termination, ensure_ascii=False)}\n"
            f"Current activity: {act}\n"
            f"Elements (compact):\n{ui_text}\n"
        )
        parts: List[Any] = [prompt]
        # Attach screenshot if available (helps judge visuals like colors/icons)
        screenshot = obs.get("screenshot")
        try:
            if screenshot and isinstance(screenshot, str) and os.path.exists(screenshot):
                with open(screenshot, "rb") as f:
                    data_b64 = base64.b64encode(f.read()).decode("ascii")
                parts.append({
                    "inline_data": {"mime_type": "image/png", "data": data_b64}
                })
        except Exception:
            pass
        try:
            resp = self.model.generate_content(parts)
            text = (getattr(resp, "text", None) or getattr(resp, "candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "") or "").strip()
        except Exception:
            text = ""
        try:
            obj = _extract_json_object(text)
            ok = bool(obj.get("ok"))
            reason = str(obj.get("reason") or "")
            return {"ok": ok, "reason": reason}
        except Exception:
            return {"ok": False, "reason": "LLM verification parse failure"}
