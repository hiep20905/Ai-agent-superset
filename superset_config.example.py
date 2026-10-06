# Hospital AI Agent: add these lines to superset_config.py.
# The extension backend reads them via os.getenv at request time.
import os

FEATURE_FLAGS = {"ENABLE_EXTENSIONS": True}  # merge into your existing FEATURE_FLAGS
EXTENSIONS_PATH = "/app/docker/extensions"

# AI provider: "ollama" = internal AI (data stays on the local network, no API
# key, no quota); "gemini" = Google Gemini API (needs GEMINI_API_KEY).
os.environ.setdefault("HOSPITAL_CHAT_PROVIDER", "ollama")
# Ollama on the Docker host; point at the internal GPU server's IP if you have one.
os.environ.setdefault("HOSPITAL_CHAT_OLLAMA_URL", "http://host.docker.internal:11434")
os.environ.setdefault("HOSPITAL_CHAT_OLLAMA_MODEL", "qwen3:8b")

# Gemini (only when HOSPITAL_CHAT_PROVIDER = "gemini"). Never commit the key:
# set GEMINI_API_KEY in the container environment (e.g. docker/.env-local).
os.environ.setdefault("HOSPITAL_CHAT_MODEL", "gemini-3.8-flash")
# Tried in order when the primary model is overloaded (503) or out of quota (429).
os.environ.setdefault(
    "HOSPITAL_CHAT_FALLBACK_MODELS",
    "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest",
)

# Superset datasets the assistant may query. Queries run as the asking user,
# so dataset permissions and Row Level Security apply as on dashboards.
# Always available, in addition to the datasets of the dashboard being viewed.
os.environ.setdefault("HOSPITAL_CHAT_DATASETS", "24,30")
# "true": on a dashboard, use only that dashboard's datasets.
os.environ.setdefault("HOSPITAL_CHAT_DASHBOARD_ONLY", "false")
# "true": the assistant may search (search_datasets) and query any other dataset
# the user can access. Off when DASHBOARD_ONLY applies.
os.environ.setdefault("HOSPITAL_CHAT_SEARCH_ALL", "true")
# Who the assistant is (the extension code itself is domain-agnostic).
os.environ.setdefault(
    "HOSPITAL_CHAT_ROLE",
    "Bạn là trợ lý phân tích dữ liệu bệnh viện (giường bệnh, bệnh nhân nội trú, "
    "ghi chú lâm sàng) trên Superset.",
)
# Notes for every question. Notes about one dataset live on the dataset itself:
# "ai_notes" in its Superset `extra` JSON (see setup_ai_notes.py).
os.environ.setdefault("HOSPITAL_CHAT_DOMAIN_NOTES", "")
# Every question, tool call and answer, for review and test cases. Contains
# query results: protect it like the data itself. Empty = off.
os.environ.setdefault("HOSPITAL_CHAT_LOG_FILE", "/app/superset_home/hospital_chat_questions.jsonl")
# Columns hidden from the assistant: "column" (any dataset) or "<dataset_id>.column".
os.environ.setdefault("HOSPITAL_CHAT_SENSITIVE_COLUMNS", "")
os.environ.setdefault("HOSPITAL_CHAT_MAX_ROWS", "200")
# Question/answer pairs remembered per conversation (stored in the Superset cache).
os.environ.setdefault("HOSPITAL_CHAT_HISTORY_TURNS", "8")
