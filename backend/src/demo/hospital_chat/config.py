"""Settings, read from environment variables at call time.

Nothing here (or in the prompt rules) is specific to one business domain: the
domain comes from HOSPITAL_CHAT_ROLE / HOSPITAL_CHAT_DOMAIN_NOTES and from the
Superset metadata (dataset/column/metric descriptions and the "ai_notes" key of
a dataset's `extra` JSON).

  HOSPITAL_CHAT_PROVIDER         "ollama" (local/internal AI) or "gemini" (default)
  HOSPITAL_CHAT_OLLAMA_URL       Ollama server (default: http://host.docker.internal:11434)
  HOSPITAL_CHAT_OLLAMA_MODEL     Ollama model (default: qwen3:8b)
  HOSPITAL_CHAT_OLLAMA_CTX       Context window in tokens (default: 16384)
  HOSPITAL_CHAT_OLLAMA_TIMEOUT   Seconds to wait per model call (default: 300)
  GEMINI_API_KEY                 Google AI Studio API key, needed for "gemini"
                                 (GOOGLE_API_KEY also accepted)
  HOSPITAL_CHAT_MODEL            Gemini model id (default: gemini-3.8-flash)
  HOSPITAL_CHAT_FALLBACK_MODELS  Comma-separated Gemini models used, in order, while
                                 the primary is out of quota (429, skipped 1 h) or
                                 overloaded (503, skipped 5 min)
  HOSPITAL_CHAT_GEMINI_THINKING  Gemini thinking level: minimal, low (default),
                                 medium, high; empty = model default. Higher is
                                 slower (measured: "low" 1.5 s vs default 4.7 s
                                 for one tool call on gemini-3.6-flash)
  HOSPITAL_CHAT_ROLE             Who the assistant is, first line of the prompt
                                 (default: a generic Superset data analyst)
  HOSPITAL_CHAT_DATASETS         Dataset ids always available, in addition to the
                                 datasets of the dashboard being viewed (default: none)
  HOSPITAL_CHAT_DASHBOARD_ONLY   "true": on a dashboard, only its datasets are used
  HOSPITAL_CHAT_SEARCH_ALL       "true" (default): the model may search and query any
                                 other dataset the user can access (search_datasets);
                                 off when DASHBOARD_ONLY applies
  HOSPITAL_CHAT_DOMAIN_NOTES     Notes for every question (glossary, how a figure is
                                 computed). Notes about one dataset belong in its
                                 Superset `extra` JSON: {"ai_notes": "..."}
  HOSPITAL_CHAT_DOMAIN_NOTES_FILE
                                 Same, read from a UTF-8 text file
  HOSPITAL_CHAT_AUTO_VALUES_MAX  Text columns with at most this many distinct values
                                 have their values listed for the model, so it
                                 filters on exact names (default: 30; 0 disables)
  HOSPITAL_CHAT_VALUE_COLUMNS    Columns whose values are always listed. A column can
                                 also opt in/out with {"ai_values": true/false} in
                                 its Superset `extra` JSON
  HOSPITAL_CHAT_SENSITIVE_COLUMNS
                                 Columns hidden from the assistant: "column" (any
                                 dataset) or "<dataset_id>.column". A column can also
                                 be hidden with {"ai_sensitive": true} or
                                 {"ai_queryable": false} in its Superset `extra` JSON.
  HOSPITAL_CHAT_MAX_ROWS         Row cap per query (default: 200)
  HOSPITAL_CHAT_HISTORY_TURNS    Question/answer pairs remembered per conversation
                                 (default: 8)
  HOSPITAL_CHAT_LOG_FILE         JSONL file receiving every question, the tool calls
                                 and the answer, to review real usage and build test
                                 cases. Contains query results: protect it like the
                                 data itself (default: off)
"""

import os

DEFAULT_ROLE = "Bạn là trợ lý phân tích dữ liệu trên Superset."


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


def gemini_thinking() -> str:
    """Gemini thinking level (minimal/low/medium/high); empty = model default."""
    return os.getenv("HOSPITAL_CHAT_GEMINI_THINKING", "low").strip().lower()


def role() -> str:
    return os.getenv("HOSPITAL_CHAT_ROLE", "").strip() or DEFAULT_ROLE


def dataset_ids() -> list[int]:
    return [int(x) for x in _csv("HOSPITAL_CHAT_DATASETS") if x.isdigit()]


def dashboard_only() -> bool:
    return os.getenv("HOSPITAL_CHAT_DASHBOARD_ONLY", "false").strip().lower() == "true"


def search_all() -> bool:
    return os.getenv("HOSPITAL_CHAT_SEARCH_ALL", "true").strip().lower() != "false"


def domain_notes() -> str:
    notes = os.getenv("HOSPITAL_CHAT_DOMAIN_NOTES", "").strip()
    path = os.getenv("HOSPITAL_CHAT_DOMAIN_NOTES_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf8") as f:
                notes = "\n".join(x for x in (notes, f.read().strip()) if x)
        except OSError:
            pass
    return notes


def auto_values_max() -> int:
    return int(os.getenv("HOSPITAL_CHAT_AUTO_VALUES_MAX", "30"))


def value_columns() -> set[str]:
    return set(_csv("HOSPITAL_CHAT_VALUE_COLUMNS"))


def sensitive_columns() -> set[str]:
    return set(_csv("HOSPITAL_CHAT_SENSITIVE_COLUMNS"))


def max_rows() -> int:
    return int(os.getenv("HOSPITAL_CHAT_MAX_ROWS", "200"))


def history_turns() -> int:
    return int(os.getenv("HOSPITAL_CHAT_HISTORY_TURNS", "8"))


def log_file() -> str:
    return os.getenv("HOSPITAL_CHAT_LOG_FILE", "").strip()
