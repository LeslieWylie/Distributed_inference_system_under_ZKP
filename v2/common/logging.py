"""
v2/common/logging.py — 结构化日志模块。

所有 pipeline 事件都写入 JSON Lines 格式的审计日志。
"""

import json
import os
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(PROJECT_ROOT, "v2", "logs")


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def log_event(req_id: str, event: str, **kwargs):
    """写入结构化事件日志 (JSON Lines)。"""
    _ensure_log_dir()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "req_id": req_id,
        "event": event,
        **kwargs,
    }
    log_path = os.path.join(LOG_DIR, "pipeline.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
