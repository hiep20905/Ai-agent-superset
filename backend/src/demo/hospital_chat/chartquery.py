"""A dashboard chart's own query, so the assistant answers with the numbers the
dashboard shows - including metrics that only exist in the chart (SQL metrics of
custom plugins) - and can narrow it with extra filters or another period.

The query comes from the chart's saved query_context when it has one (exactly
what the dashboard runs, whatever the plugin), else it is rebuilt from the
chart's form_data for the usual chart types.
"""

import json
import re
from typing import Any

from . import config, timerange

# Keys a query object may carry from a saved query_context.
_QUERY_KEYS = (
    "metrics", "columns", "filters", "extras", "time_range", "granularity", "row_limit",
    "orderby", "order_desc", "post_processing", "series_columns", "series_limit",
    "series_limit_metric", "is_timeseries", "time_offsets", "apply_fetch_values_predicate",
)
# form_data keys holding metrics, across chart types.
_METRIC_KEYS = ("metrics", "metric", "metric_2", "percent_metrics", "secondary_metric",
                "timeseries_limit_metric", "size", "x", "y")
_DIMENSION_KEYS = ("groupby", "series", "entity", "groupbyRows", "groupbyColumns")


def field_label(value: Any) -> str | None:
    """Display name of a metric/column entry (saved name or adhoc dict)."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("label", "metric_name", "column_name"):
        if isinstance(value.get(key), str) and value[key]:
            return value[key]
    column = (value.get("column") or {}).get("column_name")
    if column and value.get("aggregate"):
        return f"{value['aggregate']}({column})"
    return value.get("sqlExpression")


def _is_metric(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, dict) and value.get("expressionType") in ("SIMPLE", "SQL")
    )


def _listify(value: Any) -> list:
    if value in (None, "", []):
        return []
    return value if isinstance(value, list) else [value]


def saved_query(chart: Any) -> dict | None:
    """First query of the chart's saved query_context, if any."""
    try:
        qc = json.loads(chart.query_context or "null")
        query = (qc or {}).get("queries", [None])[0]
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(query, dict):
        return None
    return {k: query[k] for k in _QUERY_KEYS if query.get(k) not in (None, "", [], {})}


def from_form_data(fd: dict) -> dict:
    """Query object rebuilt from form_data (charts saved without query_context)."""
    metrics: list = []
    keys = list(_METRIC_KEYS) + sorted(
        k for k in fd if "metric" in k and k not in _METRIC_KEYS  # e.g. multiple_metric_1
    )
    for key in keys:
        for value in _listify(fd.get(key)):
            if _is_metric(value) and (isinstance(value, dict) or key in _METRIC_KEYS) \
                    and value not in metrics:
                metrics.append(value)

    raw = fd.get("query_mode") == "raw"
    columns: list = []
    if raw:
        columns = list(_listify(fd.get("all_columns")))
        metrics = []
    else:
        for key in ("x_axis", *_DIMENSION_KEYS, "columns"):
            for value in _listify(fd.get(key)):
                if value not in columns:
                    columns.append(value)

    query: dict = {"metrics": metrics, "columns": columns, "filters": []}
    time_col = fd.get("granularity_sqla") or (
        fd.get("x_axis") if isinstance(fd.get("x_axis"), str) else None
    )
    grain = fd.get("time_grain_sqla")
    if grain and isinstance(fd.get("x_axis"), str) and columns and columns[0] == fd["x_axis"]:
        columns[0] = {"expressionType": "SQL", "sqlExpression": fd["x_axis"],
                      "label": fd["x_axis"], "columnType": "BASE_AXIS", "timeGrain": grain}
    where, having = [], []
    time_range = fd.get("time_range")
    for f in fd.get("adhoc_filters") or []:
        if f.get("expressionType") == "SQL" and f.get("sqlExpression"):
            (having if f.get("clause") == "HAVING" else where).append(f"({f['sqlExpression']})")
        elif f.get("operator") == "TEMPORAL_RANGE":
            time_col, time_range = f.get("subject") or time_col, f.get("comparator")
        elif f.get("subject") and f.get("operator"):
            flt = {"col": f["subject"], "op": f["operator"]}
            if f.get("comparator") is not None:
                flt["val"] = f["comparator"]
            query["filters"].append(flt)
    if where or having:
        query["extras"] = {"where": " AND ".join(where), "having": " AND ".join(having)}
    if time_range and time_range != "No filter" and time_col:
        query["filters"].append({"col": time_col, "op": "TEMPORAL_RANGE", "val": time_range})
        query["time_range"] = time_range
    if time_col:
        query["granularity"] = time_col
    if fd.get("row_limit"):
        query["row_limit"] = fd["row_limit"]
    return query


def describe(query: dict) -> dict:
    """What a chart query measures, for the prompt and get_chart_catalog."""
    filters = []
    for f in query.get("filters") or []:
        if f.get("op") == "TEMPORAL_RANGE":
            continue
        filters.append({"col": field_label(f.get("col")), "op": f.get("op"), "val": f.get("val")})
    extras = query.get("extras") or {}
    if extras.get("where") or extras.get("having"):
        filters.append({"col": None, "op": "SQL", "val": None})
    temporal = [f for f in query.get("filters") or [] if f.get("op") == "TEMPORAL_RANGE"]
    grains = [c.get("timeGrain") for c in query.get("columns") or []
              if isinstance(c, dict) and c.get("timeGrain")]
    return {
        "metrics": [m for m in (field_label(x) for x in query.get("metrics") or []) if m],
        "metric_defs": [m for m in query.get("metrics") or [] if isinstance(m, dict)],
        "dimensions": [d for d in (field_label(x) for x in query.get("columns") or []) if d],
        "filters": filters,
        "time_range": query.get("time_range") or (temporal[0].get("val") if temporal else None),
        "time_grain": grains[0] if grains else None,
    }


def mentions_hidden(query: dict, hidden: set[str]) -> str | None:
    """A sensitive column the chart query uses, if any."""
    text = json.dumps(query, ensure_ascii=False, default=str)
    return next((h for h in hidden if re.search(rf"(?<![\w]){re.escape(h)}(?![\w])", text)), None)


def with_overrides(ds: dict, base: dict, args: dict) -> dict:
    """The chart query narrowed by the model: extra filters, another period
    (time_range, or "No filter" to drop the chart's own), a row limit."""
    from .query import QueryError, _filters

    query = json.loads(json.dumps(base, default=str))  # deep copy
    query["filters"] = list(query.get("filters") or []) + _filters(ds, args.get("filters") or [])

    time_range = str(args.get("time_range") or "").strip()
    if time_range:
        temporal = [f for f in query["filters"] if f.get("op") == "TEMPORAL_RANGE"]
        query["filters"] = [f for f in query["filters"] if f.get("op") != "TEMPORAL_RANGE"]
        query.pop("time_range", None)
        if time_range.lower() != "no filter":
            time_col = (temporal[0]["col"] if temporal else None) or query.get("granularity") \
                or ds["dttm_col"]
            if not time_col:
                raise QueryError("Chart này không có cột thời gian để lọc theo kỳ.",
                                 next_step="Dùng query_dataset với time_column phù hợp.")
            time_range = timerange.normalize(time_range)
            query["filters"].append({"col": time_col, "op": "TEMPORAL_RANGE", "val": time_range})
            query["time_range"] = time_range
            query["granularity"] = time_col
    limit = int(args.get("row_limit") or query.get("row_limit") or config.max_rows())
    query["row_limit"] = min(limit, config.max_rows())
    query.setdefault("metrics", [])
    query.setdefault("columns", [])
    return query
