"""
logger.py

Simple JSONL logger for run traces. One record per line, UTF-8, appended.
Records are minimal dicts like { ts, step, ... } so they can be replayed or
analyzed later. The file path is announced at creation time by the runner.
"""
import json
import time
import os
from typing import Dict, Any

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

def make_logfile_path() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(LOG_DIR, f"run-{ts}.jsonl")

class RunLogger:
    """Append-only JSONL logger with explicit flush after each record."""
    def __init__(self) -> None:
        self.path: str = make_logfile_path()
        self.f = open(self.path, "a", encoding="utf-8")

    def log(self, record: Dict[str, Any]) -> None:
        self.f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()
