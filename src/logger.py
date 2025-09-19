# logger.py
import json
import time
import os
from typing import Dict, Any

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

def make_logfile_path():
    ts = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(LOG_DIR, f"run-{ts}.jsonl")

class RunLogger:
    def __init__(self):
        self.path = make_logfile_path()
        self.f = open(self.path, "a", encoding="utf-8")
    def log(self, record: Dict[str, Any]):
        self.f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.f.flush()
    def close(self):
        self.f.close()
