"""
LLM-based planner using a locally run LLaMA model via llama-cpp-python.

Usage:
  from llm_planner import LlamaPlanner
  planner = LlamaPlanner(model_path="E:/models/llama-3.1-8b-instruct.Q4_K_M.gguf")
  action = planner.plan_next_action_llm(goal, obs, state_norm, cfg, history)

Notes:
  - Requires: pip install llama-cpp-python
  - You must download a GGUF model locally and pass its path in --llama-model
  - This planner outputs a single JSON object conforming to planner.ACTION_SCHEMA
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    from llama_cpp import Llama  # type: ignore
except Exception as e:  # pragma: no cover - optional dep
    Llama = None  # fallback marker

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

# Grammar to force a single valid action JSON object
GRAMMAR_GBNF = r'''
root ::= _WS? action _WS?
action ::= "{" _WS? "\"action\"" _WS? ":" _WS? action_enum target_opt args_opt _WS? "}"
action_enum ::= "\"tap\"" | "\"type\"" | "\"swipe\"" | "\"back\"" | "\"scroll\"" | "\"open_app\"" | "\"wait\"" | "\"keyevent\""
target_opt ::= ( _WS? "," _WS? "\"target\"" _WS? ":" _WS? target_obj )?
target_obj ::= "{" _WS? "\"by\"" _WS? ":" _WS? by_enum _WS? "," _WS? "\"value\"" _WS? ":" _WS? string _WS? "}"
by_enum ::= "\"resource-id\"" | "\"text\"" | "\"content-desc\"" | "\"bounds\"" | "\"element_id\"" | "\"component\""
args_opt ::= ( _WS? "," _WS? "\"args\"" _WS? ":" _WS? args_obj )?
args_obj ::= "{" _WS? ( arg_pair ( _WS? "," _WS? arg_pair )* )? _WS? "}"
arg_pair ::= string _WS? ":" _WS? (string | number)
string ::= "\"" char* "\""
char ::= ~"[\\\"\n\r]" | "\\\\\"" | "\\\\\\" | "\\n" | "\\r" | "\\t"
number ::= "-"? DIGIT+ ( "." DIGIT+ )?
DIGIT ::= "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
_WS ::= ( " " | "\n" | "\r" | "\t" )+
'''


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


class LlamaPlanner:
    def __init__(
        self,
        model_path: str,
        n_ctx: int = 2048,
        n_threads: Optional[int] = None,
        temperature: float = 0.1,
        top_p: float = 0.9,
        max_tokens: int = 256,
        chat_format: Optional[str] = None,
    ) -> None:
        if Llama is None:
            raise RuntimeError(
                "llama-cpp-python is not installed. Install with: pip install llama-cpp-python\n"
                "Then pass --llama-model <path-to-gguf>"
            )
        # Auto-detect chat format for common models if not provided
        cf = chat_format
        name_lower = (model_path or "").lower()
        if cf is None:
            if "mistral" in name_lower:
                cf = "mistral-instruct"
            elif "llama-2" in name_lower:
                cf = "llama-2"
            # else: let llama-cpp auto-detect
        self.llm = Llama(model_path=model_path, n_ctx=n_ctx, n_threads=n_threads, chat_format=cf)
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.validator = Draft7Validator(ACTION_SCHEMA)

    def plan_next_action_llm(
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

        # Prefer chat API (lets llama-cpp apply the right chat template for Mistral/Llama)
        text: str
        try:
            res = self.llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                grammar=GRAMMAR_GBNF,
            )
            text = (res.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
            text = (text or "").strip()
        except Exception:
            # Fallback to plain completion with a generic [INST] wrapper
            res = self.llm(
                prompt=f"<s>[INST] <<SYS>>\n{SYSTEM_PROMPT}\n<</SYS>>\n{prompt} [/INST]",
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                echo=False,
                grammar=GRAMMAR_GBNF,
            )
            text = res.get("choices", [{}])[0].get("text", "").strip()
        # Parse JSON; if it fails, default to a safe fallback below
        try:
            action = _extract_json_object(text)
        except Exception:
            action = None
        # Post-fix: If open_app is needed (wrong package), enforce
        act = (obs.get("package_activity") or "")
        if action is None:
            if target_pkg and target_pkg not in act and app_component:
                action = {"action": "open_app", "target": {"by": "component", "value": app_component}}
            else:
                # Minimal, safe default: short wait to let UI settle
                action = {"action": "wait", "args": {"duration_ms": 400}}
        elif target_pkg and target_pkg not in act and app_component:
            action = {"action": "open_app", "target": {"by": "component", "value": app_component}}
        # Validate against schema
        errs = sorted(self.validator.iter_errors(action), key=lambda e: e.path)
        if errs:
            raise ValueError("LLM produced invalid action: " + "; ".join(e.message for e in errs))
        return action
