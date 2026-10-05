"""Settings, read from environment variables at call time.

  HOSPITAL_CHAT_PROVIDER         "ollama" (local/internal AI) or "gemini" (default)
  HOSPITAL_CHAT_OLLAMA_URL       Ollama server (default: http://host.docker.internal:11434)
  HOSPITAL_CHAT_OLLAMA_MODEL     Ollama model (default: qwen3:8b)
  HOSPITAL_CHAT_OLLAMA_CTX       Context window in tokens (default: 16384)
  HOSPITAL_CHAT_OLLAMA_TIMEOUT   Seconds to wait per model call (default: 300)
  GEMINI_API_KEY                 Google AI Studio API key, needed for "gemini"
                                 (GOOGLE_API_KEY also accepted)
  HOSPITAL_CHAT_MODEL            Gemini model id (default: gemini-3.8-flash)
  HOSPITAL_CHAT_FALLBACK_MODELS  Comma-separated Gemini models tried on 503/429
  HOSPITAL_CHAT_DATASETS         Dataset ids always available, in addition to the
                                 datasets of the dashboard being viewed (default: 24,30)
  HOSPITAL_CHAT_DASHBOARD_ONLY   "true": on a dashboard, only its datasets are used
  HOSPITAL_CHAT_VALUE_COLUMNS    Columns whose distinct values are shown to the
                                 model so it filters on exact names
  HOSPITAL_CHAT_SENSITIVE_COLUMNS
                                 Columns hidden from the assistant: "column" (any
                                 dataset) or "<dataset_id>.column". A column can also
                                 be hidden with {"ai_sensitive": true} or
                                 {"ai_queryable": false} in its Superset `extra` JSON.
  HOSPITAL_CHAT_MAX_ROWS         Row cap per query (default: 200)
  HOSPITAL_CHAT_HISTORY_TURNS    Question/answer pairs remembered per conversation
                                 (default: 8)
"""

import os

DEFAULT_VALUE_COLUMNS = (
    "ward,bed_status,gender,care_level,encounter_status,note_type,severity_level"
)


def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def provider() -> str:
    return os.getenv("HOSPITAL_CHAT_PROVIDER", "gemini").strip().lower()


def ollama_url() -> str:
    return os.getenv(
        "HOSPITAL_CHAT_OLLAMA_URL", "http://host.docker.internal:11434"
    ).rstrip("/")


def ollama_model() -> str:
    return os.getenv("HOSPITAL_CHAT_OLLAMA_MODEL", "qwen3:8b")


def ollama_ctx() -> int:
    return int(os.getenv("HOSPITAL_CHAT_OLLAMA_CTX", "16384"))


def ollama_timeout() -> int:
    return int(os.getenv("HOSPITAL_CHAT_OLLAMA_TIMEOUT", "300"))


def gemini_api_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def gemini_models() -> list[str]:
    primary = os.getenv("HOSPITAL_CHAT_MODEL", "gemini-3.8-flash")
    fallbacks = _csv("HOSPITAL_CHAT_FALLBACK_MODELS")
    return [primary] + [m for m in fallbacks if m != primary]


def dataset_ids() -> list[int]:
    return [int(x) for x in _csv("HOSPITAL_CHAT_DATASETS", "24,30") if x.isdigit()]


def dashboard_only() -> bool:
    return os.getenv("HOSPITAL_CHAT_DASHBOARD_ONLY", "false").strip().lower() == "true"


def value_columns() -> set[str]:
    return set(_csv("HOSPITAL_CHAT_VALUE_COLUMNS", DEFAULT_VALUE_COLUMNS))


def sensitive_columns() -> set[str]:
    return set(_csv("HOSPITAL_CHAT_SENSITIVE_COLUMNS"))


def max_rows() -> int:
    return int(os.getenv("HOSPITAL_CHAT_MAX_ROWS", "200"))


def history_turns() -> int:
    return int(os.getenv("HOSPITAL_CHAT_HISTORY_TURNS", "8"))
