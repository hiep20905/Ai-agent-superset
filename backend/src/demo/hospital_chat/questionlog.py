"""Append each question, its tool calls and the answer to a JSONL file
(HOSPITAL_CHAT_LOG_FILE), to review real usage and turn it into test cases.

The file holds query results, so it is as sensitive as the data itself.
"""

import json
import logging
import threading
import time
from typing import Any

from . import config
from .catalog import user_key

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def write(question: str, context: dict, state: Any, answer: str, error: str | None,
          started: float) -> None:
    path = config.log_file()
    if not path:
        return
    dashboard = context.get("dashboard") if context else None
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user": user_key(),
        "dashboard": dashboard and dashboard["id"],
        "provider": config.provider(),
        "model": config.ollama_model() if config.provider() == "ollama"
        else config.gemini_models()[0],
        "question": question,
        "previous": getattr(state, "recent", ""),
        "calls": getattr(state, "calls", []),
        "answer": answer,
        "error": error,
        "seconds": round(time.time() - started, 1),
    }
    try:
        with _lock, open(path, "a", encoding="utf8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as ex:
        logger.warning("hospital-chat: cannot write question log %s: %s", path, ex)
