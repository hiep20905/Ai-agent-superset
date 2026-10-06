"""Chart specs for the chat panel, built from query results (never from numbers
the model writes), plus a link that opens the same chart in Superset Explore."""

import json
from typing import Any
from urllib.parse import quote

from . import config
from .catalog import column_label

CHART_TYPES = ["bar", "line", "pie"]
MAX_POINTS = 50


def _number(value: Any) -> float | None:
    """Metric values may arrive as Decimal/str; charts need plain numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _explore_url(ds: dict, meta: dict, chart_type: str) -> str:
    adhoc = [
        {
            "expressionType": "SIMPLE",
            "clause": "WHERE",
            "subject": f["col"],
            "operator": f["op"],
            "comparator": f.get("val"),
        }
        for f in meta["filters"]
        if f["op"] != "TEMPORAL_RANGE"
    ]
    columns, metrics = meta["columns"], meta["metrics"]
    form_data: dict[str, Any] = {
        "datasource": f"{ds['id']}__table",
        "adhoc_filters": adhoc,
        "time_range": meta["time_range"] or "No filter",
        "row_limit": config.max_rows(),
    }
    if meta["time_col"]:
        form_data["granularity_sqla"] = meta["time_col"]
    if meta.get("time_grain"):
        form_data["time_grain_sqla"] = meta["time_grain"]
    if chart_type == "pie":
        form_data.update(viz_type="pie", groupby=columns[:1], metric=metrics[0])
    else:
        form_data.update(
            viz_type="echarts_timeseries_line" if chart_type == "line" else "echarts_timeseries_bar",
            x_axis=columns[0],
            groupby=columns[1:2],
            metrics=metrics,
        )
    return "/explore/?form_data=" + quote(json.dumps(form_data, ensure_ascii=False))


def build(ds: dict, meta: dict, rows: list[dict], chart_type: str, title: str) -> dict:
    chart_type = chart_type if chart_type in CHART_TYPES else "bar"
    x = meta["columns"][0]
    series = meta["series"][:1] if chart_type == "pie" else meta["series"]
    # Percentages and counts on one axis are unreadable; keep the percentages.
    pct = [s for s in series if "%" in s[1]]
    if pct and len(pct) < len(series):
        series = pct
    shown = rows[:MAX_POINTS]
    return {
        "type": chart_type,
        "title": title,
        "x_label": column_label(ds, x),
        "labels": ["(Không có)" if r.get(x) is None else str(r.get(x)) for r in shown],
        "series": [
            {"name": label, "values": [_number(r.get(key)) for r in shown]}
            for key, label in series
        ],
        "explore_url": _explore_url(ds, meta, chart_type),
        "truncated": len(rows) > MAX_POINTS,
    }
