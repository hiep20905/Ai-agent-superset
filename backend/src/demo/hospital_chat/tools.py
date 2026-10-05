"""Tools offered to the model and their execution."""

import json
from dataclasses import dataclass, field
from typing import Any

from . import charts, prompt, timerange
from .data import execute as run_query
from .query import AGGREGATIONS, FILTER_OPS, QueryError, build

# Tool results larger than this are cut (rows dropped) so they fit the
# context window of small local models.
_MAX_RESULT_CHARS = 15000
# How many times a query that ignores a time expression in the question is
# sent back before it is allowed through (the period may not apply to it).
_MAX_TIME_NUDGES = 2

_QUERY_PROPERTIES: dict[str, Any] = {
    "dataset_id": {"type": "integer", "description": "dataset_id cần truy vấn."},
    "metrics": {
        "type": "array",
        "description": "Metric có sẵn ({name}) hoặc tổng hợp trên cột ({name, aggregation}).",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Tên metric hoặc tên cột."},
                "aggregation": {"type": "string", "enum": AGGREGATIONS},
            },
            "required": ["name"],
        },
    },
    "group_by": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Cột để nhóm khi có metrics.",
    },
    "columns": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Cột cần liệt kê (khi không có metrics).",
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
                    "description": "Danh sách giá trị cho IN / NOT IN / between.",
                },
            },
            "required": ["col", "op"],
        },
    },
    "time_range": {
        "type": "string",
        "description": 'Khoảng thời gian Superset, ví dụ "Today", "Last week", '
        '"2026-07-01 : 2026-08-01".',
    },
    "time_column": {"type": "string", "description": "Cột thời gian để lọc time_range."},
    "order_by": {
        "type": "array",
        "items": {"type": "string"},
        "description": 'Sắp xếp theo metric hoặc cột; thêm "-" ở đầu để giảm dần.',
    },
    "order_desc": {"type": "boolean", "description": "true = giảm dần (mặc định)."},
    "row_limit": {"type": "integer", "description": "Số dòng tối đa."},
}

TOOLS = [
    {
        "name": "query_dataset",
        "description": "Truy vấn một dataset Superset bằng metric/cột/bộ lọc/thời gian và "
        "trả về các dòng kết quả (theo quyền xem của người dùng). Không nhận SQL.",
        "parameters": {
            "type": "object",
            "properties": _QUERY_PROPERTIES,
            "required": ["dataset_id"],
        },
    },
    {
        "name": "draw_chart",
        "description": "Vẽ biểu đồ cho người dùng từ một truy vấn dataset. Cột đầu tiên "
        "trong group_by là trục/nhãn, các metric là giá trị.",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": charts.CHART_TYPES},
                "title": {"type": "string", "description": "Tiêu đề biểu đồ."},
                **_QUERY_PROPERTIES,
            },
            "required": ["chart_type", "title", "dataset_id", "metrics", "group_by"],
        },
    },
    {
        "name": "describe_dataset",
        "description": "Xem cột, metric và giá trị mẫu của một dataset.",
        "parameters": {
            "type": "object",
            "properties": {"dataset_id": {"type": "integer"}},
            "required": ["dataset_id"],
        },
    },
]

TOOL_LABELS = {
    "query_dataset": "Đang truy vấn dữ liệu",
    "draw_chart": "Đang vẽ biểu đồ",
    "describe_dataset": "Đang xem cấu trúc dataset",
}


@dataclass
class RequestState:
    question: str
    context: dict
    time_hint: tuple[str, str] | None = None
    time_nudges: int = 0
    charts: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.time_hint = timerange.suggest(self.question)


def _dump(out: dict) -> str:
    text = json.dumps(out, ensure_ascii=False, default=str)
    rows = out.get("rows")
    while rows and len(text) > _MAX_RESULT_CHARS:
        rows = rows[: len(rows) // 2]
        out = {**out, "rows": rows, "truncated": True}
        text = json.dumps(out, ensure_ascii=False, default=str)
    return text


def _summary(name: str, out: dict) -> str:
    if "error" in out:
        return "Lỗi: " + str(out["error"])[:160]
    if name == "describe_dataset":
        return "Đã đọc cấu trúc dataset"
    count = out.get("row_count", 0)
    return f"{'Đã vẽ biểu đồ từ' if name == 'draw_chart' else 'Đã lấy'} {count} dòng dữ liệu"


def execute(name: str, args: dict, state: RequestState) -> tuple[str, dict]:
    """Run one tool call; return (JSON result for the model, progress event)."""
    datasets = state.context["datasets"]
    try:
        ds = datasets.get(int(args.get("dataset_id") or 0))
    except (TypeError, ValueError):
        ds = None

    chart = None
    if name not in TOOL_LABELS:
        out: dict = {"error": f"Không có công cụ {name}."}
    elif ds is None:
        out = {
            "error": "dataset_id không hợp lệ hoặc người dùng không có quyền xem. "
            f"Dataset được phép: {', '.join(str(i) for i in datasets)}."
        }
    elif name == "describe_dataset":
        out = {"description": prompt.describe(ds)}
    elif (
        state.time_hint
        and ds["dttm_cols"]
        and not args.get("time_range")
        and state.time_nudges < _MAX_TIME_NUDGES
    ):
        state.time_nudges += 1
        phrase, suggestion = state.time_hint
        out = {
            "error": f"Câu hỏi có mốc thời gian '{phrase}'. Gọi lại với "
            f"time_range='{suggestion}' và time_column là một trong: "
            f"{', '.join(ds['dttm_cols'])}. Nếu mốc thời gian không dùng để lọc dữ "
            "liệu này (ví dụ hỏi tình trạng hiện tại của giường), gọi lại với "
            "time_range='No filter'."
        }
    else:
        try:
            query, meta = build(ds, args)
        except QueryError as ex:
            out = {"error": str(ex)}
        else:
            out = run_query(ds["id"], query)
            if "rows" in out:
                out["labels"] = dict(meta["series"])
            if name == "draw_chart" and "rows" in out:
                if not (meta["columns"] and meta["series"]):
                    out = {"error": "draw_chart cần ít nhất một cột trong group_by và một metric."}
                elif out["rows"]:
                    chart = charts.build(
                        ds, meta, out["rows"], str(args.get("chart_type") or "bar"),
                        str(args.get("title") or ""),
                    )
                    state.charts.append(chart)
                    out = {"ok": "Đã vẽ biểu đồ cho người dùng.", **out}

    event = {"type": "tool_result", "tool": name, "summary": _summary(name, out),
             "ok": "error" not in out}
    if chart:
        event["chart"] = chart
    return _dump(out), event
