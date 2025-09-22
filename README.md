# Android NL Automation (Observe → Plan → Act → Verify)

ADB-driven Android agent that takes a natural-language goal and executes it on an emulator/device. It observes the UI (UiAutomator XML + screenshot), plans one action (rule-based or LLM), acts via ADB, and verifies with explicit checks.

## What I did different (I think)
I made it to generate an auto config file for the target app on the fly

## TL;DR
- Observe: dump XML + screenshot; parse elements.
- Plan: pick one atomic JSON action (tap/type/scroll/open_app/etc.).
- Act: `adb shell input`/`am start`.
- Verify: re-observe and assert (text/activity), with retries.

Logs are stored under `android-automation/src/logs/`.

---

## Quickstart

Prereqs
- ADB in PATH; Python 3.10+; an emulator or USB‑debuggable device.

Install (run from repo root):

```powershell
pip install -r .\requirements.txt
```

Run a goal (Gemini planner):

```powershell
cd .\android-automation\src
$env:GOOGLE_API_KEY = "<YOUR_GOOGLE_API_KEY>"
python .\run_goal.py --goal "open settings and search vpn" --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash"
```

Tip: omit `--gemini-model` to use the deterministic rule‑based planner only.

---

## Demo (run these three commands)

```powershell
python .\run_goal.py --goal "open chrome and search for 'massive socks' and click on Go/or inupt [return] to make a request" --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash" --gemini-api-key ""
python .\run_goal.py --goal "open settings and then after opening scroll down to find system and tap on it" --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash" --gemini-api-key "" 
python .\run_goal.py --goal "open messages and search Antonio"  --auto-config --max-steps 20 --gemini-model "gemini-1.5-flash" --gemini-api-key
```


https://github.com/user-attachments/assets/73a376e0-3ab0-43cf-a891-3142c833fbb3


---

## Minimal CLI reference

- `run_goal.py` — end‑to‑end Observe → Plan → Act → Verify loop.
  - Key flags: `--goal`, `--auto-config`, `--max-steps`, `--gemini-model`, `--remote-llm-model`, `--llama-model`.
- `run_step.py` — one‑off tap/type/scroll for debugging selectors.

Action JSON (returned by planners):

```json
{ "action": "tap|type|swipe|back|scroll|open_app|wait|keyevent",
  "target": { "by": "resource-id|text|content-desc|bounds|element_id|component", "value": "..." },
  "args": { "text": "...", "duration_ms": 500, "direction": "down" } }
```

---

## Architecture (diagram placeholder)

<img width="2958" height="1242" alt="diagram-export-22-9-2025-9_52_42-pm" src="https://github.com/user-attachments/assets/e60121be-119b-48e0-b322-c82d790dd196" />


---

## Why it sucks
- UI disambiguation can be imperfect without app‑specific hints.
- Hosted LLMs may require explicit phrasing (e.g., mention “press enter/go”).
- App drawer gestures vary by launcher; router includes basic fallbacks.
- Cannot effectively locate modals and buttons to interact with
- --llm-verify basically useless
- Cannot perform complex operations
- Fails verification on complex applications (Chrome search, for example)
- Gotta give low level instructions
- Couldn't make it work on locally run cheap model.
