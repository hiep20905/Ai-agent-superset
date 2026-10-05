"""Backend REST endpoint for the Hospital Chat extension.

Flow for each question:
  ChatPanel (browser)  ->  GET /extensions/demo/hospital-chat/ask
                              ?question=...&conversation_id=...
      -> this endpoint calls the configured model (local Ollama server or the
         Gemini API) with two tools, `query_dataset` and `draw_chart`, plus the
         earlier turns of the conversation (kept in the Superset cache)
      -> the tools query Superset *datasets* (saved metrics, column
         descriptions) through Superset's own chart-data pipeline, running as
         the logged-in user: dataset permissions and Row Level Security apply
         exactly as on dashboards
      -> the rows go back to the model, which writes the final answer; charts
         are built from the query results (never from model-written numbers)
         and returned alongside the answer.

Configuration (environment variables read at call time):
  HOSPITAL_CHAT_PROVIDER         "ollama" (local/internal AI) or "gemini" (default)
  HOSPITAL_CHAT_OLLAMA_URL       Ollama server (default: http://host.docker.internal:11434)
  HOSPITAL_CHAT_OLLAMA_MODEL     Ollama model (default: qwen3:8b)
  HOSPITAL_CHAT_OLLAMA_CTX       Context window in tokens (default: 16384)
  HOSPITAL_CHAT_OLLAMA_TIMEOUT   Seconds to wait per model call (default: 300)
  GEMINI_API_KEY                 Google AI Studio API key, needed for "gemini"
                                 (GOOGLE_API_KEY also accepted)
  HOSPITAL_CHAT_MODEL            Gemini model id (default: gemini-3.8-flash)
  HOSPITAL_CHAT_FALLBACK_MODELS  Comma-separated Gemini models tried on 503/429
  HOSPITAL_CHAT_DATASETS         Comma-separated Superset dataset ids the assistant
                                 may query (default: 24,30)
  HOSPITAL_CHAT_VALUE_COLUMNS    Columns whose distinct values are shown to the
                                 model so it filters on exact names
  HOSPITAL_CHAT_MAX_ROWS         Row cap per query (default: 200)
  HOSPITAL_CHAT_HISTORY_TURNS    Question/answer pairs remembered per conversation
                                 (default: 8)
"""

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

from flask import g, request, Response
from flask_appbuilder.api import expose, permission_name, protect, safe
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

logger = logging.getLogger(__name__)

DEFAULT_VALUE_COLUMNS = (
    "ward,bed_status,gender,care_level,encounter_status,note_type,severity_level"
)
FILTER_OPS = [
    "==", "!=", ">", "<", ">=", "<=", "IN", "NOT IN", "LIKE", "ILIKE",
    "IS NULL", "IS NOT NULL", "TEMPORAL_RANGE",
]
CHART_TYPES = ["bar", "line", "pie"]
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_HISTORY_TTL = 2 * 60 * 60  # seconds
_CATALOG_TTL = 10 * 60  # seconds
# Tool results larger than this are cut (rows dropped) so they fit the
# context window of small local models.
_MAX_TOOL_RESULT_CHARS = 15000
_MAX_CHART_POINTS = 50


def _provider() -> str:
    return os.getenv("HOSPITAL_CHAT_PROVIDER", "gemini").strip().lower()


def _ollama_url() -> str:
    return os.getenv(
        "HOSPITAL_CHAT_OLLAMA_URL", "http://host.docker.internal:11434"
    ).rstrip("/")


def _ollama_model() -> str:
    return os.getenv("HOSPITAL_CHAT_OLLAMA_MODEL", "qwen3:8b")


def _model() -> str:
    return os.getenv("HOSPITAL_CHAT_MODEL", "gemini-3.8-flash")


def _csv(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _dataset_ids() -> list[int]:
    return [int(x) for x in _csv("HOSPITAL_CHAT_DATASETS", "24,30") if x.isdigit()]


def _max_rows() -> int:
    return int(os.getenv("HOSPITAL_CHAT_MAX_ROWS", "200"))


def _history_turns() -> int:
    return int(os.getenv("HOSPITAL_CHAT_HISTORY_TURNS", "8"))


def _user_key() -> str:
    user = getattr(g, "user", None)
    return str(getattr(user, "id", None) or "anon")


# --------------------------------------------------------------------------
# Dataset catalog (what the current user may query)
# --------------------------------------------------------------------------

_catalog_cache: dict[str, tuple[float, dict]] = {}


def _catalog() -> dict[int, dict]:
    """Datasets the current user can access, with columns/metrics/values.

    Cached per user: dataset access and RLS (which shapes the sampled values)
    differ between users.
    """
    key = f"{_user_key()}:{_dataset_ids()}"
    at, cached = _catalog_cache.get(key, (0.0, {}))
    if cached and time.time() - at < _CATALOG_TTL:
        return cached

    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable

    value_cols = set(_csv("HOSPITAL_CHAT_VALUE_COLUMNS", DEFAULT_VALUE_COLUMNS))
    catalog: dict[int, dict] = {}
    for ds_id in _dataset_ids():
        ds = db.session.get(SqlaTable, ds_id)
        if ds is None or not security_manager.can_access_datasource(ds):
            continue
        entry = {
            "id": ds.id,
            "name": ds.table_name,
            "description": ds.description or "",
            "columns": {
                c.column_name: {
                    "type": str(c.type or ""),
                    "label": c.verbose_name or "",
                    "description": c.description or "",
                    "is_dttm": bool(c.is_dttm),
                    "numeric": bool(
                        re.search(r"INT|NUM|DEC|FLOAT|DOUBLE|REAL", str(c.type or ""), re.I)
                    ),
                }
                for c in ds.columns
            },
            "metrics": {
                m.metric_name: {
                    "label": m.verbose_name or "",
                    "description": m.description or "",
                }
                for m in ds.metrics
                if m.metric_name != "count"
            },
            "dttm": ds.main_dttm_col,
            "values": {},
        }
        catalog[ds.id] = entry
        for col in entry["columns"]:
            if col not in value_cols:
                continue
            res = _query(
                entry, {"columns": [col], "order_by": [col], "order_desc": False,
                        "row_limit": 50},
            )
            if "rows" in res:
                entry["values"][col] = [
                    r.get(col) for r in res["rows"] if r.get(col) is not None
                ]
    _catalog_cache[key] = (time.time(), catalog)
    return catalog


def _system_prompt(catalog: dict[int, dict]) -> str:
    lines = [
        "Bạn là trợ lý phân tích dữ liệu bệnh viện, trả lời bằng tiếng Việt.",
        "",
        "Dữ liệu nằm trong các dataset Superset sau (gọi bằng dataset_id):",
    ]
    for ds in catalog.values():
        lines.append(f"\n## dataset_id={ds['id']}: {ds['name']}")
        if ds["description"]:
            lines.append(ds["description"])
        lines.append("Metric có sẵn (dùng trong metrics):")
        for name, m in ds["metrics"].items():
            text = m["label"] + (f" - {m['description']}" if m["description"] else "")
            lines.append(f"  - {name}: {text}")
        lines.append("Cột (dùng trong columns / filters / order_by):")
        for name, c in ds["columns"].items():
            parts = [p for p in (c["label"], c["description"]) if p]
            values = ds["values"].get(name)
            if values:
                parts.append("giá trị: " + ", ".join(f"'{v}'" for v in values))
            if c["is_dttm"]:
                parts.append("cột thời gian")
            lines.append(f"  - {name}" + (f": {'; '.join(parts)}" if parts else ""))
    lines.append(PROMPT_RULES)
    return "\n".join(lines)


PROMPT_RULES = """
Quy tắc:
- Lấy số liệu bằng công cụ query_dataset. KHÔNG tự tính hay bịa số liệu.
- Thống kê/tổng hợp: đặt metrics (tên metric có sẵn) và columns (cột để nhóm).
  Ví dụ công suất giường theo khoa: dataset_id=24,
  metrics=["giuong_dang_su_dung","tong_giuong","cong_suat_giuong_pct"],
  columns=["ward"].
- Liệt kê chi tiết (danh sách bệnh nhân, giường...): để metrics rỗng và đặt
  columns là các cột cần xem.
- Lọc bằng filters, ví dụ {"col":"ward","op":"==","val":"Khoa Tim mạch"}.
  Dùng đúng giá trị đã liệt kê ở trên. Lọc theo thời gian dùng
  {"col":"<cột thời gian>","op":"TEMPORAL_RANGE","val":"Last week"} hoặc
  "2026-07-01 : 2026-08-01".
- "Nhiều nhất/ít nhất": dùng order_by + order_desc + row_limit.
- Chỉ dùng tên cột/metric đã liệt kê. Nếu công cụ báo lỗi, đọc lỗi, sửa và
  gọi lại ngay; không trả lời người dùng cho tới khi có số liệu.
- Khi người dùng muốn xem biểu đồ (hoặc so sánh nhiều nhóm sẽ dễ hiểu hơn
  bằng hình), gọi draw_chart với chart_type ("bar" cột, "line" đường theo
  thời gian, "pie" tỷ trọng) và THƯỜNG CHỈ MỘT metric đúng với điều được hỏi
  (ví dụ công suất -> cong_suat_giuong_pct; số lượng theo nhóm -> metric đếm
  tổng như so_ghi_chu, tong_giuong). Biểu đồ tự hiển thị cho người dùng; câu
  trả lời chỉ nhận xét ngắn dựa trên kết quả draw_chart trả về.
- Khi trả lời tỷ lệ, nêu cả tử số và mẫu số (ví dụ "3/4 giường, 75%").
- Đây là một cuộc hội thoại: dùng các lượt trước để hiểu câu hỏi nối tiếp
  ("khoa đó", "bệnh nhân này"). Khi nêu bệnh nhân/giường, kèm mã
  (patient_id, bed_code) để câu sau tra tiếp được.
- Ghi chú lâm sàng chỉ có thống kê; không có và không được đọc nội dung.
- Nếu dữ liệu trả về rỗng, có thể do quyền xem của người dùng: nói rõ là
  không có dữ liệu trong phạm vi được phép xem.
- Trả lời ngắn gọn bằng Markdown; danh sách nhiều cột thì dùng bảng.
"""

_QUERY_PROPERTIES: dict[str, Any] = {
    "dataset_id": {"type": "integer", "description": "dataset_id cần truy vấn."},
    "metrics": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tên metric có sẵn. Để rỗng khi liệt kê chi tiết.",
    },
    "columns": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Cột để nhóm (khi có metrics) hoặc cột cần liệt kê.",
    },
    "filters": {
        "type": "array",
        "description": "Điều kiện lọc.",
        "items": {
            "type": "object",
            "properties": {
                "col": {"type": "string"},
                "op": {"type": "string", "enum": FILTER_OPS},
                "val": {"type": "string", "description": "Giá trị so sánh."},
                "vals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách giá trị cho IN / NOT IN.",
                },
            },
            "required": ["col", "op"],
        },
    },
    "order_by": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Sắp xếp theo metric hoặc cột.",
    },
    "order_desc": {"type": "boolean", "description": "true = giảm dần."},
    "row_limit": {"type": "integer", "description": "Số dòng tối đa."},
}

TOOLS = [
    {
        "name": "query_dataset",
        "description": (
            "Truy vấn một dataset Superset bằng metric/cột/bộ lọc và trả về các "
            "dòng kết quả dạng JSON (theo quyền xem của người dùng)."
        ),
        "parameters": {
            "type": "object",
            "properties": _QUERY_PROPERTIES,
            "required": ["dataset_id"],
        },
    },
    {
        "name": "draw_chart",
        "description": (
            "Vẽ biểu đồ cho người dùng từ một truy vấn dataset. Cột đầu tiên "
            "trong columns là trục/nhãn, các metric là giá trị."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": CHART_TYPES},
                "title": {"type": "string", "description": "Tiêu đề biểu đồ."},
                **_QUERY_PROPERTIES,
            },
            "required": ["chart_type", "title", "dataset_id", "metrics", "columns"],
        },
    },
]


# --------------------------------------------------------------------------
# Dataset queries (Superset chart-data pipeline, as the current user)
# --------------------------------------------------------------------------


def _filters(ds: dict, raw: list) -> list[dict]:
    out = []
    for f in raw or []:
        col, op = f.get("col"), str(f.get("op", "")).upper()
        if col not in ds["columns"]:
            raise ValueError(f"Cột lọc '{col}' không có trong dataset {ds['id']}.")
        if op not in FILTER_OPS:
            raise ValueError(f"Toán tử '{op}' không hợp lệ. Dùng: {', '.join(FILTER_OPS)}.")
        numeric = ds["columns"][col]["numeric"]

        def cast(v: Any) -> Any:
            if numeric and isinstance(v, str):
                try:
                    return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
                except ValueError:
                    return v
            return v

        if op in ("IN", "NOT IN"):
            vals = f.get("vals") or [
                v.strip() for v in str(f.get("val", "")).split(",") if v.strip()
            ]
            out.append({"col": col, "op": op, "val": [cast(v) for v in vals]})
        elif op in ("IS NULL", "IS NOT NULL"):
            out.append({"col": col, "op": op})
        else:
            out.append({"col": col, "op": op, "val": cast(f.get("val"))})
    return out


def _query(ds: dict, args: dict) -> dict:
    """Run one dataset query as the current user; return {columns, rows} or {error}."""
    from superset.commands.chart.data.get_data_command import ChartDataCommand
    from superset.common.query_context_factory import QueryContextFactory

    metrics = [m for m in args.get("metrics") or [] if m]
    columns = [c for c in args.get("columns") or [] if c]
    order_by = [o for o in args.get("order_by") or [] if o]
    errors = [f"metric '{m}'" for m in metrics if m not in ds["metrics"]]
    errors += [f"cột '{c}'" for c in columns if c not in ds["columns"]]
    errors += [
        f"order_by '{o}'"
        for o in order_by
        if o not in ds["metrics"] and o not in ds["columns"]
    ]
    if errors:
        return {
            "error": "Không có trong dataset "
            f"{ds['id']}: {', '.join(errors)}. Metric hợp lệ: "
            f"{', '.join(ds['metrics'])}. Cột hợp lệ: {', '.join(ds['columns'])}."
        }
    if not metrics and not columns:
        return {"error": "Cần ít nhất một metric hoặc một cột."}
    try:
        filters = _filters(ds, args.get("filters") or [])
    except ValueError as ex:
        return {"error": str(ex)}

    row_limit = min(int(args.get("row_limit") or _max_rows()), _max_rows())
    query: dict[str, Any] = {
        "metrics": metrics,
        "columns": columns,
        "filters": filters,
        "row_limit": row_limit,
        "order_desc": bool(args.get("order_desc", True)),
    }
    if order_by:
        query["orderby"] = [(o, not query["order_desc"]) for o in order_by]
    elif metrics:
        query["orderby"] = [(metrics[0], False)]
    if any(f["op"] == "TEMPORAL_RANGE" for f in filters):
        query["granularity"] = next(
            f["col"] for f in filters if f["op"] == "TEMPORAL_RANGE"
        )

    try:
        context = QueryContextFactory().create(
            datasource={"id": ds["id"], "type": "table"},
            queries=[query],
            form_data={},
        )
        command = ChartDataCommand(context)
        command.validate()  # dataset access check for the current user
        result = command.run()
    except Exception as ex:  # pylint: disable=broad-except
        logger.info("hospital-chat: query failed on dataset %s: %s", ds["id"], ex)
        return {"error": f"Truy vấn thất bại: {ex}"}

    payload = (result or {}).get("queries") or [{}]
    first = payload[0]
    if first.get("error"):
        return {"error": str(first["error"])}
    rows = first.get("data") or []
    return {
        "columns": first.get("colnames") or (list(rows[0]) if rows else []),
        "rows": rows,
        "row_count": len(rows),
    }


def _explore_url(ds: dict, args: dict, chart_type: str) -> str:
    """Link that opens the same chart in Superset Explore for editing/saving."""
    adhoc = []
    for f in _filters(ds, args.get("filters") or []):
        adhoc.append(
            {
                "expressionType": "SIMPLE",
                "clause": "WHERE",
                "subject": f["col"],
                "operator": f["op"],
                "comparator": f.get("val"),
            }
        )
    columns = args.get("columns") or []
    metrics = args.get("metrics") or []
    form_data: dict[str, Any] = {
        "datasource": f"{ds['id']}__table",
        "adhoc_filters": adhoc,
        "row_limit": _max_rows(),
    }
    if chart_type == "pie":
        form_data.update(viz_type="pie", groupby=columns[:1], metric=metrics[0])
    else:
        form_data.update(
            viz_type="echarts_timeseries_line"
            if chart_type == "line"
            else "echarts_timeseries_bar",
            x_axis=columns[0],
            groupby=columns[1:2],
            metrics=metrics,
        )
    return "/explore/?form_data=" + quote(json.dumps(form_data, ensure_ascii=False))


def _number(value: Any) -> float | None:
    """Metric values may arrive as Decimal/str; charts need plain numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chart(ds: dict, args: dict, res: dict) -> dict:
    """Chart spec for the panel, built from the query result."""
    chart_type = args.get("chart_type") if args.get("chart_type") in CHART_TYPES else "bar"
    x = (args.get("columns") or [None])[0]
    metrics = args.get("metrics") or []
    if chart_type == "pie":
        metrics = metrics[:1]
    # Percentages and counts on one axis are unreadable; keep the percentages.
    pct = [m for m in metrics if "%" in (ds["metrics"].get(m, {}).get("label") or "")]
    if pct and len(pct) < len(metrics):
        metrics = pct
    rows = res["rows"][:_MAX_CHART_POINTS]
    label = lambda m: ds["metrics"].get(m, {}).get("label") or m  # noqa: E731
    return {
        "type": chart_type,
        "title": str(args.get("title") or ""),
        "x_label": ds["columns"].get(x, {}).get("label") or x,
        "labels": ["(Không có)" if r.get(x) is None else str(r.get(x)) for r in rows],
        "series": [
            {"name": label(m), "values": [_number(r.get(m)) for r in rows]}
            for m in metrics
        ],
        "explore_url": _explore_url(ds, args, chart_type),
        "truncated": len(res["rows"]) > _MAX_CHART_POINTS,
    }


def _execute_tool(name: str, args: dict, charts: list[dict]) -> str:
    """Run one tool call from the model and return its JSON result."""
    catalog = _catalog()
    try:
        ds = catalog.get(int(args.get("dataset_id") or 0))
    except (TypeError, ValueError):
        ds = None
    if name not in ("query_dataset", "draw_chart"):
        out: dict = {"error": f"Không có công cụ {name}."}
    elif ds is None:
        out = {
            "error": "dataset_id không hợp lệ hoặc người dùng không có quyền xem. "
            f"Dataset được phép: {', '.join(str(i) for i in catalog)}."
        }
    else:
        out = _query(ds, args)
        if name == "draw_chart" and "rows" in out:
            if not (args.get("columns") and args.get("metrics")):
                out = {"error": "draw_chart cần ít nhất một cột (nhãn) và một metric."}
            elif out["rows"]:
                charts.append(_chart(ds, args, out))
                out = {"ok": "Đã vẽ biểu đồ cho người dùng.", **out}
    text = json.dumps(out, ensure_ascii=False, default=str)
    rows = out.get("rows")
    while rows and len(text) > _MAX_TOOL_RESULT_CHARS:
        rows = rows[: len(rows) // 2]
        out = {**out, "rows": rows, "truncated": True}
        text = json.dumps(out, ensure_ascii=False, default=str)
    return text


# --------------------------------------------------------------------------
# Conversation memory (Superset cache, falls back to process memory)
# --------------------------------------------------------------------------

_local_history: dict[str, tuple[float, list]] = {}


def _history_key(conversation_id: str) -> str:
    return f"hospital_chat:{_user_key()}:{conversation_id}"


def _load_history(key: str) -> list[dict]:
    try:
        from superset.extensions import cache_manager

        return cache_manager.cache.get(key) or []
    except Exception:  # pylint: disable=broad-except
        at, turns = _local_history.get(key, (0.0, []))
        return turns if time.time() - at < _HISTORY_TTL else []


def _save_history(key: str, turns: list[dict]) -> None:
    turns = turns[-2 * _history_turns() :]
    try:
        from superset.extensions import cache_manager

        cache_manager.cache.set(key, turns, timeout=_HISTORY_TTL)
    except Exception:  # pylint: disable=broad-except
        _local_history[key] = (time.time(), turns)


# --------------------------------------------------------------------------
# LLM loops
# --------------------------------------------------------------------------

# model id -> time until which it is skipped after a quota (429) error.
_exhausted: dict[str, float] = {}
_EXHAUSTED_TTL = 60 * 60  # seconds


class ModelsUnavailable(RuntimeError):
    """The model provider is unreachable, overloaded or out of quota."""


def _ask_llm(question: str, history: list[dict]) -> tuple[str, list[dict]]:
    """Answer one question with the configured provider; return (text, charts)."""
    catalog = _catalog()
    if not catalog:
        return (
            "Bạn chưa có quyền xem dataset nào mà trợ lý được cấu hình "
            "(HOSPITAL_CHAT_DATASETS). Liên hệ quản trị viên.",
            [],
        )
    prompt = _system_prompt(catalog)
    charts: list[dict] = []
    if _provider() == "ollama":
        text = _ask_ollama(prompt, question, history, charts)
    else:
        text = _ask_gemini(prompt, question, history, charts)
    return text, charts


def _ask_ollama(
    prompt: str, question: str, history: list[dict], charts: list[dict]
) -> str:
    """Run a bounded tool-calling loop against a local Ollama server."""
    import requests

    messages = [{"role": "system", "content": prompt}]
    messages += [
        {"role": "assistant" if t["role"] == "model" else "user", "content": t["text"]}
        for t in history
    ]
    messages.append({"role": "user", "content": question})
    tools = [{"type": "function", "function": t} for t in TOOLS]
    url = f"{_ollama_url()}/api/chat"
    timeout = int(os.getenv("HOSPITAL_CHAT_OLLAMA_TIMEOUT", "300"))
    last_tool_failed = False
    nudges = 0

    for _ in range(8):  # safety cap on tool-call rounds
        try:
            resp = requests.post(
                url,
                json={
                    "model": _ollama_model(),
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                    # Reasoning mode multiplies the wait on CPU-only machines.
                    "think": False,
                    "keep_alive": "30m",
                    "options": {
                        "temperature": 0,
                        "num_ctx": int(os.getenv("HOSPITAL_CHAT_OLLAMA_CTX", "16384")),
                    },
                },
                timeout=timeout,
            )
        except requests.ConnectionError as ex:
            raise ModelsUnavailable(
                f"Không kết nối được AI nội bộ (Ollama) tại {_ollama_url()}. "
                "Kiểm tra Ollama đã chạy chưa."
            ) from ex
        except requests.Timeout as ex:
            raise ModelsUnavailable(
                f"AI nội bộ không trả lời trong {timeout} giây. Thử câu hỏi ngắn "
                "hơn hoặc dùng máy có GPU."
            ) from ex
        if resp.status_code != 200:
            raise ModelsUnavailable(f"AI nội bộ báo lỗi: {resp.text[:300]}")

        msg = resp.json().get("message") or {}
        calls = msg.get("tool_calls") or []
        if calls:
            messages.append(msg)  # assistant turn with the calls
            for call in calls:
                fn = call.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = _execute_tool(fn.get("name", ""), args, charts)
                last_tool_failed = result.startswith('{"error"')
                messages.append(
                    {"role": "tool", "tool_name": fn.get("name", ""), "content": result}
                )
            continue

        text = msg.get("content") or ""
        if last_tool_failed and nudges < 2:
            # Small models often reply "I'll fix it" and stop; push them to
            # actually retry.
            nudges += 1
            last_tool_failed = False
            messages.append(msg)
            messages.append(
                {
                    "role": "user",
                    "content": "Lệnh vừa rồi bị lỗi. Hãy sửa theo thông báo lỗi và "
                    "gọi lại công cụ ngay, chưa trả lời người dùng.",
                }
            )
            continue
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return "Xin lỗi, tôi không hoàn tất được yêu cầu (quá nhiều bước)."


def _ask_gemini(
    prompt: str, question: str, history: list[dict], charts: list[dict]
) -> str:
    """Run a bounded Gemini function-calling loop and return the final answer."""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return "Chưa cấu hình GEMINI_API_KEY trên máy chủ Superset."

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=prompt,
        tools=[types.Tool(function_declarations=TOOLS)],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [
        types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])])
        for turn in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

    def _generate_with_retry():
        # Retry brief overloads, skip models whose quota is used up, and fall
        # back through HOSPITAL_CHAT_FALLBACK_MODELS.
        models = [_model()] + [
            m for m in _csv("HOSPITAL_CHAT_FALLBACK_MODELS", "") if m != _model()
        ]
        now = time.time()
        available = [m for m in models if _exhausted.get(m, 0) <= now] or models
        overloaded = False
        for model in available:
            for attempt in range(2):
                try:
                    return client.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        # Quota (often per day): waiting seconds won't help.
                        _exhausted[model] = time.time() + _EXHAUSTED_TTL
                        logger.warning("hospital-chat: quota exhausted for %s", model)
                        break
                    if "503" in msg or "UNAVAILABLE" in msg:
                        overloaded = True
                        if attempt == 0:
                            time.sleep(3)
                            continue
                        break
                    raise
        raise ModelsUnavailable(
            "Máy chủ AI (Google Gemini) đang quá tải, vui lòng thử lại sau ít phút."
            if overloaded
            else "Đã hết hạn mức sử dụng Gemini miễn phí hôm nay cho tất cả các "
            "model đã cấu hình. Vui lòng thử lại sau, hoặc nâng cấp gói trả phí "
            "/ thêm model vào HOSPITAL_CHAT_FALLBACK_MODELS."
        )

    for _ in range(6):  # safety cap on tool-call rounds
        resp = _generate_with_retry()
        candidate = resp.candidates[0]
        parts = candidate.content.parts or []
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if calls:
            contents.append(candidate.content)  # model turn with the calls
            tool_parts = [
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": _execute_tool(fc.name, dict(fc.args or {}), charts)},
                )
                for fc in calls
            ]
            contents.append(types.Content(role="user", parts=tool_parts))
            continue

        # Final answer
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        return text.strip()

    return "Xin lỗi, tôi không hoàn tất được yêu cầu (quá nhiều bước)."


@api(
    id="hospital_chat_api",
    name="Hospital Chat API",
    description="Trả lời câu hỏi về dữ liệu bệnh viện từ dataset Superset bằng AI.",
)
class HospitalChatAPI(RestApi):
    openapi_spec_tag = "Hospital Chat"
    class_permission_name = "hospital_chat_api"

    @expose("/ask", methods=("GET",))
    @protect()
    @safe
    @permission_name("read")
    def ask(self) -> Response:
        question = (request.args.get("question") or "").strip()
        if not question:
            return self.response(400, message="Thiếu tham số 'question'.")
        conversation_id = request.args.get("conversation_id") or ""
        key = (
            _history_key(conversation_id)
            if _CONVERSATION_ID.match(conversation_id)
            else None
        )
        history = _load_history(key) if key else []
        try:
            answer, charts = _ask_llm(question, history)
        except ModelsUnavailable as exc:
            return self.response(200, result={"answer": str(exc), "charts": []})
        except Exception as exc:  # never 500 the panel; show the error inline
            logger.exception("hospital-chat: ask failed")
            return self.response(
                200, result={"answer": f"Lỗi máy chủ: {exc}", "charts": []}
            )
        if key and answer:
            _save_history(
                key,
                history
                + [
                    {"role": "user", "text": question},
                    {"role": "model", "text": answer[:4000]},
                ],
            )
        return self.response(200, result={"answer": answer, "charts": charts})
