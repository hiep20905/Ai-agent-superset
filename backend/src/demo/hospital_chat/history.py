"""Conversation memory in the Superset cache (Redis), per user and conversation.

Falls back to process memory when the cache is unavailable.
"""

import re
import time

from . import config
from .catalog import user_key

_TTL = 2 * 60 * 60  # seconds
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_local: dict[str, tuple[float, list]] = {}


def key_for(conversation_id: str) -> str | None:
    if not _CONVERSATION_ID.match(conversation_id or ""):
        return None
    return f"hospital_chat:{user_key()}:{conversation_id}"


def load(key: str | None) -> list[dict]:
    if not key:
        return []
    try:
        from superset.extensions import cache_manager

        return cache_manager.cache.get(key) or []
    except Exception:  # pylint: disable=broad-except
        at, turns = _local.get(key, (0.0, []))
        return turns if time.time() - at < _TTL else []


def append(key: str | None, history: list[dict], question: str, answer: str) -> None:
    if not key or not answer:
        return
    turns = (
        history
        + [{"role": "user", "text": question}, {"role": "model", "text": answer[:4000]}]
    )[-2 * config.history_turns() :]
    try:
        from superset.extensions import cache_manager

        cache_manager.cache.set(key, turns, timeout=_TTL)
    except Exception:  # pylint: disable=broad-except
        _local[key] = (time.time(), turns)
