"""Turn the model's structured query arguments into a Superset query object.

Names are resolved accent-insensitively against the catalog (column/metric name
or Vietnamese label). Besides saved metrics, the model may aggregate any numeric
column (sum/avg/min/max) or count values.
"""

from typing import Any

from . import config
from .catalog import column_label, metric_label, resolve_column, resolve_metric, resolve_value

AGGREGATIONS = ["sum", "avg", "min", "max", "count", "count_distinct"]
_AGG_SQL = {
    "sum": "SUM", "avg": "AVG", "average": "AVG", "mean": "AVG", "min": "MIN",
    "max": "MAX", "count": "COUNT", "count_distinct": "COUNT_DISTINCT",
}
_AGG_VI = {
    "SUM": "Tổng", "AVG": "Trung bình", "MIN": "Nhỏ nhất", "MAX": "Lớn nhất",
    "COUNT": "Số lượng", "COUNT_DISTINCT": "Số giá trị khác nhau",
}
FILTER_OPS = [
    "==", "!=", ">", "<", ">=", "<=", "IN", "NOT IN", "LIKE", "ILIKE", "contains",
    "between", "IS NULL", "IS NOT NULL",
]
_OP_ALIASES = {"=": "==", "eq": "==", "in": "IN", "not in": "NOT IN", "not_in": "NOT IN",
               "like": "LIKE", "ilike": "ILIKE", "contains": "contains",
               "between": "between", "is null": "IS NULL", "is not null": "IS NOT NULL"}


class QueryError(ValueError):
    """Invalid arguments; the message goes back to the model so it can fix them."""


def _unknown(ds: dict, kind: str, name: object) -> QueryError:
    hidden = " (cột này bị ẩn vì là dữ liệu nhạy cảm)" if name in ds["hidden"] else ""
    return QueryError(
        f"Không có {kind} '{name}' trong dataset {ds['id']}{hidden}. "
        f"Metric hợp lệ: {', '.join(ds['metrics']) or '(không có)'}. "
        f"Cột hợp lệ: {', '.join(ds['columns'])}."
    )


def _cast(ds: dict, column: str, value: Any) -> Any:
    if ds["columns"][column]["numeric"] and isinstance(value, str):
        try:
            return int(value) if value.strip().lstrip("-").isdigit() else float(value)
        except ValueError:
            return value
    return resolve_value(ds, column, value)


def _metrics(ds: dict, raw: list) -> tuple[list, list[tuple[str, str]]]:
    """(query metrics, [(result key, display label)])."""
    metrics: list = []
    series: list[tuple[str, str]] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("metric") or item.get("column")
            agg_raw = item.get("aggregation")
        else:
            name, agg_raw = item, None
        agg = _AGG_SQL.get(str(agg_raw).strip().lower()) if agg_raw else None
        if agg_raw and not agg:
            raise QueryError(f"aggregation '{agg_raw}' không hợp lệ. Dùng: {', '.join(AGGREGATIONS)}.")
        if agg is None:
            metric = resolve_metric(ds, name)
            if metric:
                metrics.append(metric)
                series.append((metric, metric_label(ds, metric)))
                continue
            if resolve_column(ds, name):
                raise QueryError(
                    f"'{name}' là cột, cần chỉ rõ aggregation "
                    f"({', '.join(AGGREGATIONS)}) hoặc dùng metric có sẵn."
                )
            raise _unknown(ds, "metric", name)
        column = resolve_column(ds, name)
        if column is None:
            if resolve_metric(ds, name):
                raise QueryError(f"'{name}' là metric có sẵn, không áp dụng thêm aggregation.")
            raise _unknown(ds, "cột", name)
        if agg in ("SUM", "AVG") and not ds["columns"][column]["numeric"]:
            raise QueryError(f"Cột '{column}' không phải kiểu số, không dùng {agg} được.")
        label = f"{agg}({column})"
        metrics.append(
            {
                "expressionType": "SIMPLE",
                "column": {"column_name": column},
                "aggregate": agg,
                "label": label,
            }
        )
        series.append((label, f"{_AGG_VI[agg]} {column_label(ds, column).lower()}"))
    return metrics, series


def _filters(ds: dict, raw: list) -> list[dict]:
    out: list[dict] = []
    for f in raw or []:
        if not isinstance(f, dict):
            raise QueryError("Mỗi filter phải là object {col, op, val}.")
        name = f.get("col") or f.get("column") or f.get("field")
        column = resolve_column(ds, name)
        if column is None:
            raise _unknown(ds, "cột lọc", name)
        op_raw = str(f.get("op") or f.get("operator") or "==").strip()
        op = _OP_ALIASES.get(op_raw.lower(), op_raw.upper())
        if op not in FILTER_OPS:
            raise QueryError(f"Toán tử '{op_raw}' không hợp lệ. Dùng: {', '.join(FILTER_OPS)}.")
        vals = f.get("vals")
        if vals is None and isinstance(f.get("val"), list):
            vals = f["val"]
        if op in ("IN", "NOT IN"):
            vals = vals or [v.strip() for v in str(f.get("val", "")).split(",") if v.strip()]
            out.append({"col": column, "op": op, "val": [_cast(ds, column, v) for v in vals]})
        elif op == "between":
            pair = vals or [v.strip() for v in str(f.get("val", "")).split(",")]
            if len(pair) != 2:
                raise QueryError("Toán tử between cần đúng 2 giá trị trong vals.")
            out += [
                {"col": column, "op": ">=", "val": _cast(ds, column, pair[0])},
                {"col": column, "op": "<=", "val": _cast(ds, column, pair[1])},
            ]
        elif op == "contains":
            out.append({"col": column, "op": "ILIKE", "val": f"%{f.get('val', '')}%"})
        elif op in ("IS NULL", "IS NOT NULL"):
            out.append({"col": column, "op": op})
        else:
            out.append({"col": column, "op": op, "val": _cast(ds, column, f.get("val"))})
    return out


def build(ds: dict, args: dict) -> tuple[dict, dict]:
    """(Superset query object, meta for charts/explore). Raises QueryError."""
    metrics, series = _metrics(ds, args.get("metrics") or [])

    columns: list[str] = []
    for name in [*(args.get("columns") or []), *(args.get("group_by") or [])]:
        column = resolve_column(ds, name)
        if column is None:
            raise _unknown(ds, "cột", name)
        if column not in columns:
            columns.append(column)
    if not metrics and not columns:
        raise QueryError("Cần ít nhất một metric hoặc một cột.")

    filters = _filters(ds, args.get("filters") or [])

    time_range = str(args.get("time_range") or "").strip() or None
    if time_range and time_range.lower() == "no filter":
        time_range = None  # the model confirmed the period does not apply
    time_col = None
    if time_range:
        time_col = resolve_column(ds, args.get("time_column")) if args.get("time_column") else ds["dttm_col"]
        if not time_col or not ds["columns"].get(time_col, {}).get("is_dttm"):
            raise QueryError(
                "Không xác định được cột thời gian. Đặt time_column là một trong: "
                f"{', '.join(ds['dttm_cols']) or '(dataset không có cột thời gian)'}."
            )
        filters.append({"col": time_col, "op": "TEMPORAL_RANGE", "val": time_range})

    desc_default = bool(args.get("order_desc", True))
    # A metric can be referenced by its result key or its display label.
    by_key = {key: key for key, _ in series} | {label: key for key, label in series}
    orderby: list = []
    for name in args.get("order_by") or []:
        name = str(name).strip()
        desc = desc_default
        if name.startswith("-"):
            name, desc = name[1:], True
        key = by_key.get(name) or resolve_metric(ds, name)
        if key and key not in by_key:
            key = None  # a saved metric that is not part of this query
        if key is None:
            column = resolve_column(ds, name)
            adhoc = next((k for k, _ in series if column and k.endswith(f"({column})")), None)
            if adhoc:
                key = adhoc
            elif column and (not metrics or column in columns):
                key = column
            elif column:
                raise QueryError(
                    f"Không sắp xếp được theo '{name}' trong truy vấn tổng hợp vì cột "
                    "này không nằm trong columns/group_by; sắp xếp theo metric thay thế."
                )
            else:
                raise _unknown(ds, "trường sắp xếp", name)
        orderby.append((key, not desc))
    if not orderby and metrics:
        orderby = [(series[0][0], False)]

    row_limit = min(int(args.get("row_limit") or config.max_rows()), config.max_rows())
    query = {
        "metrics": metrics,
        "columns": columns,
        "filters": filters,
        "orderby": orderby,
        "row_limit": row_limit,
    }
    if time_col:
        query["granularity"] = time_col
    meta = {
        "columns": columns,
        "series": series,
        "metrics": metrics,
        "filters": filters,
        "time_range": time_range,
        "time_col": time_col,
    }
    return query, meta
