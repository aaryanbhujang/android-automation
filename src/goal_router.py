"""
Goal router: infer target app and synthesize a minimal config from a natural-language goal.

This enables a "single NL input" mode by selecting a base config and tweaking
termination from the goal (e.g., search terms).
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Any, Optional
import time


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CONF_DIR = os.path.join(BASE_DIR, "target-conf")
AUTO_DIR = os.path.join(BASE_DIR, "auto-configs")
APP_REGISTRY_PATH = os.path.join(CONF_DIR, "app_registry.json")
LAUNCHER_PACKAGES = [
    "com.google.android.apps.nexuslauncher",
    "com.android.launcher3",
    "com.miui.home",
    "com.sec.android.app.launcher",
]


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_registry() -> Dict[str, Any]:
    if os.path.exists(APP_REGISTRY_PATH):
        return _read_json(APP_REGISTRY_PATH)
    return {}

def _base_config_for_app(app: str) -> Optional[Dict[str, Any]]:
    app = (app or "").lower()
    reg = _load_registry()
    if not reg:
        return None
    # Match by key exact or alias contains
    chosen = None
    if app in reg:
        chosen = reg[app]
    else:
        for name, meta in reg.items():
            for alias in meta.get("aliases", []):
                if alias.lower() == app:
                    chosen = meta
                    break
            if chosen:
                break
    if not chosen:
        return None
    cfg = {
        "app_component": chosen.get("app_component"),
        "package": chosen.get("package"),
    }
    if chosen.get("allowed_packages"):
        cfg["allowed_packages"] = chosen["allowed_packages"]
    if chosen.get("plan_hints"):
        cfg["plan_hints"] = chosen["plan_hints"]
    # Minimal default termination is presence of the package (updated later with goal term)
    cfg["termination"] = {"package": cfg.get("package")}
    return cfg


def _infer_app(goal: str) -> Optional[str]:
    g = goal.lower()
    reg = _load_registry()
    # Try exact names
    for name in reg.keys():
        if name in g:
            return name
    # Try aliases
    for name, meta in reg.items():
        for alias in meta.get("aliases", []):
            if alias.lower() in g:
                return name
    return None


def _extract_search_term(goal: str) -> Optional[str]:
    # Common patterns: "search <term>", "search for <term>", or "find <term>"
    for pat in [r"search\s+(for\s+)?(.+)", r"find\s+(.+)"]:
        m = re.search(pat, goal, re.IGNORECASE)
        if m:
            grp = m.group(2) if len(m.groups()) >= 2 else m.group(1)
            if grp:
                return grp.strip()
    # Quoted phrase fallback
    m = re.search(r"['\"]([^'\"]+)['\"]", goal)
    if m:
        return m.group(1).strip()
    return None


def _slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "goal"


def synthesize_config_from_goal(goal: str) -> Optional[Dict[str, Any]]:
    app = _infer_app(goal)
    base = _base_config_for_app(app) if app else None
    # If no known app matched, fall back to a generic "App Drawer" flow
    if not base:
        # Minimal config to open app drawer from launcher and search for the app name
        # We try to extract a target app token from the goal (e.g., the word after 'open ')
        # Prefer a short token: stop at 'and'/'then' or punctuation
        m = re.search(r"open\s+([a-zA-Z0-9 ._-]+?)(?:\s+(?:and|then)\b|\s*[,;]|$)", goal, re.IGNORECASE)
        target_name = (m.group(1).strip() if m else "").strip()
        # If registry can infer a canonical app from the whole goal, prefer that as the search token
        try:
            canon = _infer_app(goal)
        except Exception:
            canon = None
        if canon and canon not in (target_name or "").lower():
            target_name = canon
        target_title = target_name.title() if target_name else ""
        # Base config focuses on leaving launcher state (not in any launcher package)
        cfg = {
            "app_component": None,
            "package": None,
            "termination": {"not_package_in": LAUNCHER_PACKAGES},
                                    "plan_hints": [
                                # Ensure we are on the home screen first
                                {"action": "keyevent", "args": {"key": "HOME"}},
                                {"action": "wait", "args": {"duration_ms": 200}},
                                            # Swipe up from near bottom to open app drawer on many launchers
                                            {"action": "scroll", "args": {"direction": "down", "duration_ms": 700, "length_factor": 0.9, "start_pos": "bottom"}},
                                # If first swipe didn’t catch, try a second, longer swipe
                                {"action": "scroll", "args": {"direction": "down", "duration_ms": 900, "length_factor": 1.0, "start_pos": "bottom"}},
                                            # Optional: tap Apps label (some launchers)
                                            {"action": "tap", "prefer": [ {"by": "text", "value": "Apps"}, {"by": "text", "value": "App drawer"} ] },
                                # Tap the search bar if present
                                {
                                    "action": "tap",
                                    "prefer": [
                                        {"by": "content-desc", "value": "Search apps"},
                                        {"by": "text", "value": "Search apps"},
                                        {"by": "content-desc", "value": "Search your phone"},
                                        {"by": "text", "value": "Search your phone"},
                                        {"by": "text", "value": "Search your phone & more"}
                                    ]
                                },
                                # Small wait to let the keyboard appear
                                {"action": "wait", "args": {"duration_ms": 200}},
                # Type the app name if we extracted one
                                *([{ "action": "type", "args": {"text": target_name} }] if target_name else []),
                                # Tap an icon matching the label (try Title Case then original)
                                *([
                                        { "action": "tap", "prefer": [
                                                {"by": "text", "value": target_title},
                                                {"by": "text", "value": target_name}
                                        ] }
                                ] if target_name else []),
                        ]
        }
        # Persist like other auto-configs
        try:
            os.makedirs(AUTO_DIR, exist_ok=True)
            ts = int(time.time() * 1000)
            goal_slug = _slugify(goal)
            filename = f"app-drawer-{goal_slug}-{ts}.json"
            out_path = os.path.join(AUTO_DIR, filename)
            meta = {"generated_by": "goal_router.auto", "ts": ts, "goal": goal, "app": "app-drawer"}
            cfg_with_meta = dict(cfg)
            cfg_with_meta.setdefault("meta", {}).update(meta)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(cfg_with_meta, f, ensure_ascii=False, indent=2)
            cfg_with_meta["_auto_config_path"] = out_path
            return cfg_with_meta
        except Exception:
            return cfg
    cfg = dict(base)
    # Copy optional fields if present
    for k in ("allowed_packages", "fallback_scroll_if_not_found", "fallback_scroll_args"):
        if k in base:
            cfg[k] = base[k]
    # If goal contains a search term, use it for termination text
    term = _extract_search_term(goal)
    if term:
        term_cfg = dict(cfg.get("termination", {}))
        term_cfg["must_contain_text"] = term
        cfg["termination"] = term_cfg
    # As a minimal safety net, require package presence
    pkg = cfg.get("package")
    if pkg:
        term_cfg = dict(cfg.get("termination", {}))
        if "package" not in term_cfg:
            term_cfg["package"] = pkg
        cfg["termination"] = term_cfg

    # Persist auto-generated config to auto-configs directory for transparency/reuse
    try:
        os.makedirs(AUTO_DIR, exist_ok=True)
        ts = int(time.time() * 1000)
        app_slug = (app or "app")
        goal_slug = _slugify(goal)
        filename = f"{app_slug}-{goal_slug}-{ts}.json"
        out_path = os.path.join(AUTO_DIR, filename)
        # Attach minimal metadata
        meta = {
            "generated_by": "goal_router.auto",
            "ts": ts,
            "goal": goal,
            "app": app,
        }
        cfg_with_meta = dict(cfg)
        cfg_with_meta.setdefault("meta", {}).update(meta)
        # Write file
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(cfg_with_meta, f, ensure_ascii=False, indent=2)
        # annotate path so the caller can log/print it
        cfg_with_meta["_auto_config_path"] = out_path
        return cfg_with_meta
    except Exception:
        # If writing fails, just return the in-memory config
        return cfg
