# Android NL Automation (Observe → Plan → Act → Verify)

This repo is a small ADB-driven agent that takes a single natural-language goal and executes it on an Android emulator/device. It uses UiAutomator dumps for observation, a tiny planner (rule‑based or LLM), ADB for actions, and explicit verification between steps.

I built this for an internship Level‑2 task. The focus is correctness, reliability, and simple engineering—nothing too fancy.

## What it does (TL;DR)
- Observe: dump UI XML + screenshot; parse elements (text/id/desc/bounds/clickable).
- Plan: pick one atomic action as JSON (tap/type/scroll/open_app/etc.).
- Act: execute via `adb shell input`/`am start`.
- Verify: re-observe and check assertions (text/activity), with retries/backoff.

It writes a JSONL log per run under `android-automation/src/logs/`.

---

## Quickstart

Prereqs:
- Windows/macOS/Linux with ADB in PATH
- Python 3.10+ and a working Android emulator (AVD) or a device with USB debugging enabled

Install deps from repo root (one directory above `android-automation`):

```powershell
pip install -r .\requirements.txt
```

Start an emulator (Pixel/Android 13+ recommended) and ensure `adb devices` lists it.

### Run with a natural-language goal (Gemini hosted planner)
```powershell
cd .\android-automation\src
$env:GOOGLE_API_KEY="<YOUR_GOOGLE_API_KEY>"
python .\run_goal.py --goal "open settings and search vpn" --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash"
```
- This will infer a minimal config from the goal (and app registry), save it under `../auto-configs/`, then run the loop.
- You can also use the rule-based planner (no hosted LLM) by omitting `--gemini-model`.

### One-off action runner (good for debugging)
`run_step.py` is a single-step Observe→Act→Verify utility.

Examples:
- Tap by text:
  ```powershell
  python .\run_step.py --action tap --by text --value "Settings"
  ```
- Type into a field:
  ```powershell
  python .\run_step.py --action type --by resource-id --value "com.android.settings:id/search" --text "vpn"
  ```
- Scroll down from center:
  ```powershell
  python .\run_step.py --action scroll --direction down --length 800 --duration_ms 600
  ```

---

## CLI overview (what to use when)

- `run_goal.py` (end-to-end):
  - Input: `--goal "..."` (+ optionally `--auto-config`), planner flags.
  - Flow: Observe → Plan → Act → Verify loop with termination checks.
  - Planners:
    - Rule-based deterministic (default; no flags)
    - Hosted Gemini: `--gemini-model gemini-1.5-flash`
    - Hosted OpenAI-compatible: `--remote-llm-model gpt-4o-mini`
    - Local LLaMA (optional): `--llama-model <path-to-gguf>`

- `run_step.py` (single-step):
  - Useful to poke the UI or debug a selector.
  - Emits logs, re-observes, and verifies lightweight expectations.

---

## Configs and app discovery

- App registry: `android-automation/target-conf/app_registry.json`
  - Maps names/aliases to packages and components (e.g., Settings, Chrome, Clock, etc.).
- Auto-configs: `android-automation/auto-configs/`
  - When you run with `--auto-config`, the router infers a minimal config from the goal (component/package + termination + a few hints) and saves it here.
- Example configs: `android-automation/target-conf/*.json`

Termination assertions include any of:
- `package`: substring in focused activity
- `activity`: substring in focused activity
- `must_contain_text`: fuzzy text present (ignores EditText values)
- `not_package_in`: success when leaving these packages (used for app-drawer flows)

---

## How it works (mapping to rubric)

- Conceptual depth (Observe→Plan→Act→Verify):
  - Observe: `src/observer.py` dumps UiAutomator XML + screenshot; `src/normalizer.py` parses into a list of elements with bounds and a stable `element_id`.
  - Plan: either rule-based (`src/planner.py`) using small hints and schema validation, or LLM planners (`src/gemini_llm_planner.py`, `src/remote_llm_planner.py`). All planners must return a single strict-JSON action matching the schema.
  - Act: `src/actuator.py` performs taps, typing, swipes/scrolls, keyevents, and app launches. It resolves selectors with exact→contains→fuzzy fallback.
  - Verify: `src/verifier.py` re-observes and checks expectations with small retries and exponential backoff.
  - Robustness: fuzzy text matching, scroll fallback, re-typing suppression, search-field detection, and launcher-aware app‑drawer flow.

- Correctness & reliability:
  - The loop validates each action with re-observation and explicit checks; failures are deterministic with exit codes.
  - Retries: verify step backs off 0.5s→1s→2s.
  - Logs: every step appended to `logs/run-YYYYMMDD-HHMMSS.jsonl`.

- Reproducibility & docs:
  - This README shows setup and copy‑pasteable commands.
  - Auto-configs are saved for transparency and reuse.

- Engineering quality & logging:
  - Small modules with clear responsibilities; minimal global state.
  - Deterministic JSONL logs for observe/plan/act/verify.

- Extras:
  - Open-source model path: local LLaMA via `llama-cpp-python`.
  - Hosted path: Gemini or OpenAI-compatible.
  - OCR/network capture are out of scope for this submission, but can be added (see Next Steps).

---

## Schema for an action

Every planner returns exactly one JSON object:

```json
{
  "action": "tap|type|swipe|back|scroll|open_app|wait|keyevent",
  "target": { "by": "resource-id|text|content-desc|bounds|element_id|component", "value": "..." },
  "args": { "text": "...", "duration_ms": 500, "direction": "down" }
}
```

Returned JSON is validated in code (`jsonschema`).

---

## Demo script

A tiny PowerShell helper to reproduce the demo run (Gemini planner):

```powershell
# demo.ps1 (save this next to README.md or run lines directly)
$env:GOOGLE_API_KEY = "<YOUR_GOOGLE_API_KEY>"
Set-Location .\android-automation\src
python .\run_goal.py --goal "open settings and search vpn" --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash"
```

Tip: remove the `--gemini-model` flag to run the deterministic rule-based planner only.

---

## Replay a run (deterministic-ish)

You can replay the ADB actions logged by a previous run. This helps produce a short demo quickly.

```powershell
cd .\android-automation\src
# Find latest run log
$log = (Get-ChildItem .\logs\run-*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
python .\replay.py --log "$log" --delay-ms 200
```

Note: replay skips text content (we only store the length), but taps/swipes/app-starts are reproduced.

---

## Developer notes

- Logs: `android-automation/src/logs/` (auto-created). Screenshots and XML dumps in sibling folders.
- If a selector fails: use `run_step.py` to inspect candidates and try contains/fuzzy matches.
- Removing unused files: the codebase avoids dead modules; if you spot one, feel free to open an issue.

---

## Next Steps (nice-to-have)
- Stronger Wi‑Fi switch verification by reading `checked` attributes.
- Quick Settings fast path for toggles.
- Optional OCR for image-only UIs.
- Optional network capture via mitmproxy + emulator proxy config.
