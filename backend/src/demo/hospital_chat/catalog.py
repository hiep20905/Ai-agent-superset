"""What the current user may query: the datasets of the dashboard being viewed
plus the configured ones, with their columns, metrics and sample values. Other
datasets the user can access are found with search_datasets and loaded on use.

Sensitive columns are dropped here, so they never reach the model and cannot be
selected, filtered or grouped by.
"""

import json
import re
import time
from types import SimpleNamespace
from typing import Any

from flask import g

from . import chartquery, config
from .data import execute
from .text import best_match, fold, tokens

_TTL = 10 * 60  # seconds
_cache: dict[str, tuple[float, dict | None]] = {}
_MAX_CHARTS = 20
_MAX_LISTED_VALUES = 50
_SEARCH_LIMIT = 10


class ContextError(RuntimeError):
    """The requested dashboard cannot be used (missing or no access)."""


def user_key() -> str:
    user = getattr(g, "user", None)
    return str(getattr(user, "id", None) or "anon")


def _extra(obj: Any) -> dict:
    try:
        extra = json.loads(getattr(obj, "extra", None) or "{}")
    except (TypeError, ValueError):
        return {}
    return extra if isinstance(extra, dict) else {}


def _column_hidden(ds_id: int, column: Any) -> bool:
    sensitive = config.sensitive_columns()
    if column.column_name in sensitive or f"{ds_id}.{column.column_name}" in sensitive:
        return True
    extra = _extra(column)
    return bool(extra.get("ai_sensitive")) or extra.get("ai_queryable") is False


def _ai_notes(ds: Any) -> str:
    """Notes for the assistant kept with the dataset: {"ai_notes": "..." | [...]}."""
    notes = _extra(ds).get("ai_notes") or ""
    if isinstance(notes, list):
        notes = "\n".join(f"- {n}" for n in notes if n)
    return str(notes).strip()


def _dataset_entry(ds_id: int) -> dict | None:
    """Catalog entry for one dataset, or None when the user cannot access it.

    Cached per user: access and RLS (which shapes the sample values) differ
    between users.
    """
    key = f"{user_key()}:{ds_id}"
    at, cached = _cache.get(key, (0.0, None))
    if key in _cache and time.time() - at < _TTL:
        return cached

    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable

    ds = db.session.get(SqlaTable, ds_id)
    if ds is None or not security_manager.can_access_datasource(ds):
        _cache[key] = (time.time(), None)
        return None

    columns: dict[str, dict] = {}
    hidden: set[str] = set()
    list_values: dict[str, bool | None] = {}  # column -> ai_values flag
    for c in ds.columns:
        if _column_hidden(ds.id, c):
            hidden.add(c.column_name)
            continue
        list_values[c.column_name] = _extra(c).get("ai_values")
        col_type = str(c.type or "")
        columns[c.column_name] = {
            "label": c.verbose_name or "",
            "description": c.description or "",
            "type": col_type,
            "is_dttm": bool(c.is_dttm),
            "numeric": bool(re.search(r"INT|NUM|DEC|FLOAT|DOUBLE|REAL", col_type, re.I)),
        }
    dttm_cols = [n for n, c in columns.items() if c["is_dttm"]]
    entry = {
        "id": ds.id,
        "name": ds.table_name,
        "description": ds.description or "",
        "notes": _ai_notes(ds),
        "columns": columns,
        "hidden": hidden,
        "metrics": {
            m.metric_name: {
                "label": m.verbose_name or "",
                "description": m.description or "",
            }
            for m in ds.metrics
            if m.metric_name != "count"
        },
        "dttm_col": ds.main_dttm_col if ds.main_dttm_col in columns else (dttm_cols or [None])[0],
        "dttm_cols": dttm_cols,
        "values": {},
    }
    for col in _value_columns(ds.id, columns, list_values):
        res = column_values(entry, col, limit=_MAX_LISTED_VALUES)
        if res.get("values"):
            entry["values"][col] = res["values"]
    _cache[key] = (time.time(), entry)
    return entry


def _value_columns(ds_id: int, columns: dict, flags: dict) -> list[str]:
    """Columns whose values are listed for the model: the configured/opted-in ones,
    plus text columns with few distinct values (counted in one query)."""
    wanted = [c for c in columns if c in config.value_columns() or flags.get(c) is True]
    limit = config.auto_values_max()
    candidates = [
        c for c, info in columns.items()
        if limit > 0 and c not in wanted and flags.get(c) is not False
        and not info["is_dttm"] and not info["numeric"]
    ]
    if candidates:
        res = execute(
            ds_id,
            {
                "metrics": [
                    {"expressionType": "SIMPLE", "column": {"column_name": c},
                     "aggregate": "COUNT_DISTINCT", "label": c}
                    for c in candidates
                ] + [{"expressionType": "SQL", "sqlExpression": "COUNT(*)",
                      "label": "__rows__"}],
                "columns": [], "filters": [], "orderby": [], "row_limit": 1,
            },
        )
        counts = (res.get("rows") or [{}])[0]
        rows = counts.get("__rows__") or 0
        # A column with about one value per row is an identifier, not a category.
        wanted += [c for c in candidates
                   if 0 < (counts.get(c) or 0) <= limit and (counts.get(c) or 0) * 2 < rows]
    return wanted


def column_values(ds: dict, column: str, search: str = "", limit: int = 50) -> dict:
    """Distinct values of a column (as the user may see them, RLS applies),
    optionally those containing `search`, accent-insensitively."""
    query = {"columns": [column], "metrics": [], "row_limit": limit,
             "orderby": [(column, True)], "filters": []}
    info = ds["columns"].get(column, {})
    if search and not (info.get("numeric") or info.get("is_dttm")):
        query["filters"] = [{"col": column, "op": "ILIKE", "val": f"%{search}%"}]
    res = execute(ds["id"], query)
    if "error" in res:
        return res
    values = [r.get(column) for r in res["rows"] if r.get(column) is not None]
    if search and (not values or not query["filters"]):
        # ILIKE is accent-sensitive ("tim mach" misses "Tim mạch") and does not
        # apply to numbers/dates: scan and match on the folded text instead.
        res = execute(ds["id"], {**query, "filters": [], "row_limit": 2000})
        wanted = fold(search)
        values = [
            r.get(column) for r in res.get("rows", [])
            if r.get(column) is not None and wanted in fold(r.get(column))
        ][:limit]
    return {"column": column, "values": values, "truncated": len(values) >= limit}


def load_context(dashboard_ref: str | int | None) -> dict:
    """{"dashboard": {...} | None, "datasets": {id: entry}} for this request."""
    dashboard = None
    order: list[int] = []
    on_dashboard: set[int] = set()
    if dashboard_ref not in (None, ""):
        from superset.commands.dashboard.exceptions import (
            DashboardAccessDeniedError,
            DashboardNotFoundError,
        )
        from superset.daos.dashboard import DashboardDAO

        try:
            dash = DashboardDAO.get_by_id_or_slug(str(dashboard_ref))
        except DashboardNotFoundError as ex:
            raise ContextError("Không tìm thấy dashboard đang mở.") from ex
        except DashboardAccessDeniedError as ex:
            raise ContextError("Bạn không có quyền xem dashboard này.") from ex
        dashboard = {
            "id": dash.id,
            "title": dash.dashboard_title or f"Dashboard {dash.id}",
            "charts": [_chart_info(s) for s in dash.slices[:_MAX_CHARTS]],
        }
        on_dashboard = {d.id for d in dash.datasources if getattr(d, "type", "table") == "table"}
        order += sorted(on_dashboard)
    if not (dashboard and config.dashboard_only()):
        order += [i for i in config.dataset_ids() if i not in order]

    datasets: dict[int, dict] = {}
    for ds_id in order:
        entry = _dataset_entry(ds_id)
        if entry is not None:
            datasets[ds_id] = {**entry, "on_dashboard": ds_id in on_dashboard}
    context = {"dashboard": dashboard, "datasets": datasets}
    _add_chart_metrics(context)
    return context


def can_search(context: dict) -> bool:
    """Whether datasets outside the dashboard/configured ones may be used."""
    return config.search_all() and not (context["dashboard"] and config.dashboard_only())


def get_dataset(context: dict, ds_id: object) -> dict | None:
    """Catalog entry for a dataset id from the model, loading it on first use
    when searching is allowed. None when unknown or not accessible."""
    try:
        ds_id = int(ds_id or 0)
    except (TypeError, ValueError):
        return None
    if ds_id in context["datasets"]:
        return context["datasets"][ds_id]
    if not ds_id or not can_search(context):
        return None
    entry = _dataset_entry(ds_id)
    if entry is None:
        return None
    context["datasets"][ds_id] = {**entry, "on_dashboard": False, "chart_metrics": {}}
    return context["datasets"][ds_id]


def search_datasets(context: dict, keyword: str) -> list[dict]:
    """Datasets the user can access, ranked by how well their name, description,
    column and metric labels match `keyword` (accent-insensitive)."""
    if not can_search(context):
        candidates = [
            (ds["id"], ds["name"], ds["description"]) for ds in context["datasets"].values()
        ]
        texts = {
            ds["id"]: " ".join(
                [*ds["columns"], *(c["label"] for c in ds["columns"].values()),
                 *ds["metrics"], *(m["label"] for m in ds["metrics"].values())]
            )
            for ds in context["datasets"].values()
        }
    else:
        from superset import db
        from superset.connectors.sqla.models import SqlaTable, SqlMetric, TableColumn

        candidates = db.session.query(
            SqlaTable.id, SqlaTable.table_name, SqlaTable.description
        ).all()
        texts = {}
        for table_id, name, label, extra in db.session.query(
            TableColumn.table_id, TableColumn.column_name, TableColumn.verbose_name,
            TableColumn.extra,
        ):
            column = SimpleNamespace(column_name=name, extra=extra)
            if not _column_hidden(table_id, column):
                texts[table_id] = f"{texts.get(table_id, '')} {name} {label or ''}"
        for table_id, name, label in db.session.query(
            SqlMetric.table_id, SqlMetric.metric_name, SqlMetric.verbose_name
        ):
            texts[table_id] = f"{texts.get(table_id, '')} {name} {label or ''}"

    wanted = tokens(keyword)
    scored = []
    for ds_id, name, description in candidates:
        head = tokens(f"{name} {description or ''}")
        body = tokens(texts.get(ds_id, ""))
        # A hit in the name/description counts more than one in a column label.
        score = sum(2 if t in head else 1 if t in body else 0 for t in wanted)
        if score or not wanted:
            scored.append((-score, ds_id, name, description or ""))
    scored.sort()

    found = []
    for _, ds_id, name, description in scored:
        if ds_id not in context["datasets"] and not _accessible(ds_id):
            continue
        found.append(
            {
                "dataset_id": ds_id,
                "name": name,
                "description": description[:200],
                "on_dashboard": bool(context["datasets"].get(ds_id, {}).get("on_dashboard")),
            }
        )
        if len(found) >= _SEARCH_LIMIT:
            break
    return found


def _accessible(ds_id: int) -> bool:
    key = f"{user_key()}:{ds_id}"
    at, cached = _cache.get(key, (0.0, None))
    if key in _cache and time.time() - at < _TTL:
        return cached is not None

    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable

    ds = db.session.get(SqlaTable, ds_id)
    return ds is not None and security_manager.can_access_datasource(ds)


def row_limited(ds_id: int) -> bool:
    """Whether Row Level Security restricts the rows this user sees in a dataset;
    when it does not, an empty result is about the filters, never permissions."""
    from superset import db, security_manager
    from superset.connectors.sqla.models import SqlaTable

    ds = db.session.get(SqlaTable, ds_id)
    try:
        return ds is None or bool(security_manager.get_rls_filters(ds))
    except Exception:  # pylint: disable=broad-except
        return True  # unknown: do not rule permissions out


# --------------------------------------------------------------------------
# Charts of the dashboard being viewed
# --------------------------------------------------------------------------


def _chart_info(chart: Any) -> dict:
    saved = chartquery.saved_query(chart)
    query = saved or chartquery.from_form_data(chart.form_data or {})
    return {
        "id": chart.id,
        "name": chart.slice_name,
        "description": chart.description or "",
        "viz_type": chart.viz_type,
        "dataset_id": chart.datasource_id,
        "query": query,
        "exact": saved is not None,  # the dashboard's own query, not a rebuild
        **chartquery.describe(query),
    }


def get_chart(context: dict, chart_id: object) -> dict | None:
    for chart in (context["dashboard"] or {}).get("charts", []):
        if str(chart["id"]) == str(chart_id).strip():
            return chart
    return None


def chart_hidden_column(context: dict, chart: dict) -> str | None:
    ds = context["datasets"].get(chart["dataset_id"])
    return chartquery.mentions_hidden(chart["query"], ds["hidden"]) if ds else None


def _add_chart_metrics(context: dict) -> None:
    """Metrics defined in the dashboard's charts (often SQL that exists nowhere
    else) become usable by name in query_dataset on the chart's dataset."""
    for ds in context["datasets"].values():
        ds["chart_metrics"] = {}
    found: list[tuple[dict, str, dict, str]] = []  # (dataset, label, definition, chart)
    for chart in (context["dashboard"] or {}).get("charts", []):
        ds = context["datasets"].get(chart["dataset_id"])
        if ds is None or chart_hidden_column(context, chart):
            continue
        for metric in chart["metric_defs"]:
            label = chartquery.field_label(metric)
            if label and label not in ds["metrics"]:
                found.append((ds, label, metric, chart["name"]))

    def same(a: dict, b: dict) -> bool:
        keys = ("expressionType", "sqlExpression", "aggregate", "column")
        return all(json.dumps(a.get(k), sort_keys=True, default=str)
                   == json.dumps(b.get(k), sort_keys=True, default=str) for k in keys)

    for ds, label, metric, chart_name in found:
        # One label, different formulas in different charts (e.g. the same KPI
        # title over two filters): keep both, named after their chart.
        clash = any(o_ds is ds and o_label == label and not same(o_metric, metric)
                    for o_ds, o_label, o_metric, _ in found)
        key = f"{label} [{chart_name}]" if clash else label
        ds["chart_metrics"].setdefault(key, {"definition": {**metric, "label": key},
                                             "chart": chart_name})


def chart_catalog(context: dict) -> list[dict]:
    """Charts of the open dashboard as shown to the model: what each one measures
    and how it breaks the data down. Details are left out for charts whose
    dataset the user cannot query or that use a hidden column."""
    out = []
    for chart in (context["dashboard"] or {}).get("charts", []):
        if chart["dataset_id"] not in context["datasets"]:
            out.append({"id": chart["id"], "name": chart["name"],
                        "note": "Không có quyền truy vấn dataset của chart này."})
            continue
        if chart_hidden_column(context, chart):
            out.append({"id": chart["id"], "name": chart["name"],
                        "note": "Chart dùng dữ liệu nhạy cảm, trợ lý không truy cập được."})
            continue
        filters = [
            "(điều kiện SQL tùy chỉnh)" if f["op"] == "SQL" else f"{f['col']} {f['op']} {f['val']}"
            for f in chart["filters"]
        ]
        out.append(
            {
                "id": chart["id"],
                "name": chart["name"],
                "description": chart["description"],
                "viz_type": chart["viz_type"],
                "dataset_id": chart["dataset_id"],
                "metrics": chart["metrics"],
                "dimensions": chart["dimensions"],
                "filters": filters,
                "time_range": chart["time_range"],
                "time_grain": chart["time_grain"],
            }
        )
    return out


# --------------------------------------------------------------------------
# Name resolution (accent-insensitive, by name or Vietnamese label)
# --------------------------------------------------------------------------


def resolve_column(ds: dict, name: object) -> str | None:
    # A hidden column must fail loudly, not fuzzy-match a similar visible one
    # ("diagnosis" -> "diagnosis_code").
    if any(fold(h) == fold(name) for h in ds["hidden"]):
        return None
    return best_match(
        str(name or "").strip(), {n: [c["label"]] for n, c in ds["columns"].items()}
    )


def resolve_metric(ds: dict, name: object) -> str | None:
    return best_match(
        str(name or "").strip(), {n: [m["label"]] for n, m in ds["metrics"].items()}
    )


def resolve_chart_metric(ds: dict, name: object) -> str | None:
    """Label of a metric defined in one of the dashboard's charts."""
    metrics = ds.get("chart_metrics") or {}
    return best_match(str(name or "").strip(), {label: [] for label in metrics})


def resolve_value(ds: dict, column: str, value: Any) -> Any:
    """Map a loosely written value ("khoa tim mach") onto a known one."""
    known = ds["values"].get(column)
    if not known or not isinstance(value, str) or value in known:
        return value
    wanted = fold(value)
    exact = [v for v in known if fold(v) == wanted]
    if exact:
        return exact[0]
    partial = [v for v in known if wanted and wanted in fold(v)]
    return partial[0] if len(partial) == 1 else value


def column_label(ds: dict, name: str) -> str:
    return (ds["columns"].get(name) or {}).get("label") or name


def metric_label(ds: dict, name: str) -> str:
    return (ds["metrics"].get(name) or {}).get("label") or name
