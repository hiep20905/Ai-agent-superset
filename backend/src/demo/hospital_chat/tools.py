"""Tools offered to the model and their execution."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import catalog, chartquery, charts, prompt, timerange
from .data import execute as run_query
from .query import AGGREGATIONS, FILTER_OPS, TIME_GRAINS, QueryError, build

logger = logging.getLogger(__name__)

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
    "time_column": {
        "type": "string",
        "description": "Cột thời gian dùng cho time_range / time_grain.",
    },
    "time_grain": {
        "type": "string",
        "enum": list(TIME_GRAINS),
        "description": "Gộp theo ngày/tuần/tháng... của time_column để xem xu hướng "
        "(cần metrics). Kết quả có một dòng cho mỗi kỳ.",
    },
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
        "name": "query_chart",
        "description": "Lấy số liệu của một chart trên dashboard đang mở, đúng như chart "
        "đang hiển thị (cùng chỉ số, cách nhóm, bộ lọc). Có thể thu hẹp bằng filters, "
        "đổi kỳ bằng time_range ('No filter' = bỏ kỳ của chart). Nhanh và khớp dashboard "
        "nhất khi câu hỏi nói về số liệu một chart đang thể hiện.",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_id": {"type": "integer", "description": "chart_id trên dashboard."},
                "filters": _QUERY_PROPERTIES["filters"],
                "time_range": _QUERY_PROPERTIES["time_range"],
                "row_limit": _QUERY_PROPERTIES["row_limit"],
            },
            "required": ["chart_id"],
        },
    },
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
        "description": "Vẽ biểu đồ cho người dùng từ một truy vấn dataset. Trục/nhãn là "
        "kỳ thời gian (khi có time_grain) hoặc cột đầu tiên trong group_by; các metric "
        "là giá trị.",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": charts.CHART_TYPES},
                "title": {"type": "string", "description": "Tiêu đề biểu đồ."},
                **_QUERY_PROPERTIES,
            },
            "required": ["chart_type", "title", "dataset_id", "metrics"],
        },
    },
    {
        "name": "describe_dataset",
        "description": "Xem cột, metric và giá trị mẫu của một dataset (chỉ metadata, "
        "không có số liệu).",
        "parameters": {
            "type": "object",
            "properties": {"dataset_id": {"type": "integer"}},
            "required": ["dataset_id"],
        },
    },
    {
        "name": "search_datasets",
        "description": "Tìm dataset người dùng được phép xem theo từ khóa (tên, mô tả, "
        "cột, metric), khi dữ liệu cần dùng không có trong các dataset đã liệt kê. "
        "Chỉ trả metadata; sau đó gọi describe_dataset rồi query_dataset.",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Chủ đề dữ liệu, ví dụ 'thuốc', 'chi phí', "
                    "'phẫu thuật'. Để trống để liệt kê.",
                },
            },
        },
    },
    {
        "name": "get_column_values",
        "description": "Tra các giá trị có trong một cột (ví dụ tên chính xác của một "
        "nhóm, mã, người) để lọc cho đúng, khi cột đó chưa được liệt kê giá trị.",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "integer"},
                "column": {"type": "string", "description": "Tên cột."},
                "search": {
                    "type": "string",
                    "description": "Chỉ lấy giá trị chứa chuỗi này (không phân biệt dấu). "
                    "Để trống để liệt kê.",
                },
            },
            "required": ["dataset_id", "column"],
        },
    },
    {
        "name": "get_chart_catalog",
        "description": "Liệt kê các chart trên dashboard đang mở cùng metric, chiều "
        "phân tích, bộ lọc và khoảng thời gian của từng chart. Dùng khi người dùng "
        "hỏi về chart/dashboard hoặc để biết chart đang đo chỉ số nào. Không chứa số liệu.",
        "parameters": {"type": "object", "properties": {}},
    },
]

TOOL_LABELS = {
    "query_chart": "Đang lấy số liệu của chart",
    "query_dataset": "Đang truy vấn dữ liệu",
    "draw_chart": "Đang vẽ biểu đồ",
    "describe_dataset": "Đang xem cấu trúc dataset",
    "search_datasets": "Đang tìm dataset",
    "get_chart_catalog": "Đang xem các chart trên dashboard",
    "get_column_values": "Đang tra giá trị của cột",
}
_DATASET_TOOLS = ("query_dataset", "draw_chart", "describe_dataset", "get_column_values")


@dataclass
class RequestState:
    question: str
    context: dict
    # The previous question, so a follow-up ("còn khoa Nhi?") keeps its period.
    recent: str = ""
    time_hint: tuple[str, str] | None = None
    mentions_time: bool = False
    time_nudges: int = 0
    charts: list[dict] = field(default_factory=list)
    # Every tool call: {"tool", "args" (as run), "ok", "summary", "row_count"?,
    # "dropped_time_range"?} - for the question log and the evaluation runner.
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.time_hint = timerange.suggest(self.question)
        self.mentions_time = timerange.mentions_time(self.question) or timerange.mentions_time(
            self.recent
        )


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
    if name == "search_datasets":
        return f"Tìm thấy {len(out.get('datasets', []))} dataset"
    if name == "get_chart_catalog":
        return f"Đã đọc {len(out.get('charts', []))} chart"
    if name == "get_column_values":
        return f"Đã tra {len(out.get('values', []))} giá trị"
    if name == "query_chart":
        return f"Đã lấy {out.get('row_count', 0)} dòng từ chart «{out.get('chart', '')}»"
    count = out.get("row_count", 0)
    return f"{'Đã vẽ biểu đồ từ' if name == 'draw_chart' else 'Đã lấy'} {count} dòng dữ liệu"


def _label_periods(out: dict, meta: dict) -> None:
    """Readable period labels, oldest first (the query fetched the latest periods).

    Rows without a date (e.g. empty beds, no admission time) form a period of
    their own, which means nothing on a timeline: they are dropped and counted.
    """
    col, grain = meta["columns"][0], meta["time_grain"]
    dated = [r for r in out["rows"] if not timerange.is_missing(r.get(col))]
    if len(dated) < len(out["rows"]):
        out["undated_rows_dropped"] = len(out["rows"]) - len(dated)
    dated.sort(key=lambda r: r[col] if isinstance(r[col], (int, float)) else str(r[col]))
    for r in dated:
        r[col] = timerange.format_period(r[col], grain)
    out["rows"], out["row_count"] = dated, len(dated)


def _empty_reason(ds_id: int) -> str:
    return (
        "Có thể do giới hạn quyền xem dòng (RLS) của người dùng, hoặc do bộ lọc."
        if catalog.row_limited(ds_id) else
        "KHÔNG phải do quyền: người dùng xem được toàn bộ dataset này. Không có "
        "dòng nào khớp bộ lọc/kỳ thời gian: kiểm tra lại giá trị lọc "
        "(get_column_values) hoặc nói rõ là không có dữ liệu khớp."
    )


def _query_chart(context: dict, args: dict, dropped_time: str | None) -> dict:
    chart = catalog.get_chart(context, args.get("chart_id"))
    if chart is None:
        return {
            "error": f"Không có chart {args.get('chart_id')} trên dashboard đang mở.",
            "available_charts": {c["id"]: c["name"]
                                 for c in (context["dashboard"] or {}).get("charts", [])},
            "next_step": "Dùng chart_id đã liệt kê, hoặc query_dataset nếu không có chart phù hợp.",
        }
    ds = context["datasets"].get(chart["dataset_id"])
    if ds is None:
        return {"error": "Người dùng không có quyền truy vấn dataset của chart này."}
    hidden = catalog.chart_hidden_column(context, chart)
    if hidden:
        return {"error": f"Chart dùng cột nhạy cảm '{hidden}', trợ lý không được truy cập."}
    try:
        query = chartquery.with_overrides(ds, chart["query"], args)
    except QueryError as ex:
        return {"error": str(ex), **ex.extra}
    out = run_query(ds["id"], query)
    if "rows" not in out:
        return out
    out = {"chart": chart["name"], "dataset_id": ds["id"],
           "time_range": query.get("time_range") or "No filter", **out}
    if dropped_time:
        out["note"] = (f"Không áp dụng time_range '{dropped_time}' vì câu hỏi không nêu mốc "
                       "thời gian; giữ kỳ của chart.")
    grain_col = next((c for c in query["columns"] if isinstance(c, dict) and c.get("timeGrain")),
                     None)
    if grain_col and out["rows"]:
        _label_periods(out, {"columns": [grain_col["label"]], "time_grain": grain_col["timeGrain"]})
    if not out["rows"]:
        out["empty_reason"] = _empty_reason(ds["id"])
    elif out["row_count"] >= query["row_limit"]:
        out["truncated_at_row_limit"] = query["row_limit"]
        out["note"] = (f"Chỉ lấy {query['row_limit']} dòng đầu, chưa đủ: KHÔNG cộng các dòng "
                       "này thành tổng. Cần tổng/thống kê thì dùng query_dataset với chỉ số "
                       "của chart (metrics) và group_by phù hợp.")
    return out


def execute(name: str, args: dict, state: RequestState) -> tuple[str, dict]:
    """Run one tool call; return (JSON result for the model, progress event)."""
    context = state.context
    chart = None
    ds = None
    if name in _DATASET_TOOLS:
        ds = catalog.get_dataset(context, args.get("dataset_id"))
    dropped_time = None
    if name in ("query_dataset", "draw_chart", "query_chart"):
        args = {**args, "time_range": timerange.clean(args.get("time_range"))}
        # Models like to add a period nobody asked for; on a snapshot dataset that
        # silently empties the result (e.g. empty beds have no admission time).
        # A chart keeps its own period: only the model's override is dropped.
        if args["time_range"] and args["time_range"].lower() != "no filter" \
                and not state.mentions_time:
            dropped_time = args["time_range"]
            args["time_range"] = "" if name == "query_chart" else "No filter"

    if name not in TOOL_LABELS:
        out: dict = {"error": f"Không có công cụ {name}.", "available_tools": list(TOOL_LABELS)}
    elif name == "search_datasets":
        found = catalog.search_datasets(context, str(args.get("keyword") or ""))
        out = {"datasets": found}
        if not found:
            out["note"] = "Không có dataset nào khớp trong phạm vi được phép xem."
    elif name == "query_chart":
        out = _query_chart(context, args, dropped_time)
    elif name == "get_chart_catalog":
        out = (
            {"charts": catalog.chart_catalog(context)}
            if context["dashboard"]
            else {"charts": [], "note": "Người dùng không mở dashboard nào."}
        )
    elif ds is None:
        out = {
            "error": "dataset_id không hợp lệ hoặc người dùng không có quyền xem.",
            "allowed_dataset_ids": list(context["datasets"]),
            "next_step": "Dùng đúng dataset_id đã liệt kê"
            + (" hoặc gọi search_datasets để tìm." if catalog.can_search(context) else "."),
        }
    elif name == "describe_dataset":
        out = {"description": prompt.describe(ds)}
    elif name == "get_column_values":
        column = catalog.resolve_column(ds, args.get("column"))
        if column is None:
            out = {"error": f"Không có cột '{args.get('column')}' trong dataset {ds['id']}"
                   + (" (cột bị ẩn vì là dữ liệu nhạy cảm)." if args.get("column") in ds["hidden"]
                      else "."),
                   "available_columns": list(ds["columns"])}
        else:
            out = catalog.column_values(ds, column, str(args.get("search") or ""))
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
            "liệu này (ví dụ hỏi tình trạng hiện tại), gọi lại với "
            "time_range='No filter'."
        }
    else:
        try:
            query, meta = build(ds, args)
        except QueryError as ex:
            out = {"error": str(ex), **ex.extra}
        else:
            out = run_query(ds["id"], query)
            if "rows" in out:
                out["labels"] = dict(meta["series"])
                if dropped_time:
                    out["note"] = (f"Đã bỏ time_range '{dropped_time}' vì câu hỏi không nêu "
                                   "mốc thời gian: đây là số liệu hiện tại.")
                if not out["rows"]:
                    out["empty_reason"] = _empty_reason(ds["id"])
                if meta["time_grain"]:
                    _label_periods(out, meta)
            if name == "draw_chart" and "rows" in out:
                if not (meta["columns"] and meta["series"]):
                    out = {"error": "draw_chart cần time_grain hoặc ít nhất một cột trong "
                           "group_by, và một metric."}
                elif out["rows"]:
                    chart = charts.build(
                        ds, meta, out["rows"], str(args.get("chart_type") or "bar"),
                        str(args.get("title") or ""),
                    )
                    state.charts.append(chart)
                    out = {"ok": "Đã vẽ biểu đồ cho người dùng.", **out}

    logger.info("hospital-chat: %s %s -> %s", name, json.dumps(args, ensure_ascii=False),
                _summary(name, out))
    call = {"tool": name, "args": args, "ok": "error" not in out, "summary": _summary(name, out)}
    if "row_count" in out:
        call["row_count"] = out["row_count"]
    if name == "query_chart" and "dataset_id" in out:
        call["dataset_id"] = out["dataset_id"]
    if dropped_time:
        call["dropped_time_range"] = dropped_time
    state.calls.append(call)
    event = {"type": "tool_result", "tool": name, "summary": _summary(name, out),
             "ok": "error" not in out}
    if chart:
        event["chart"] = chart
    return _dump(out), event
