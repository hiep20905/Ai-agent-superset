"""What the current user may query: the datasets of the dashboard being viewed
plus the configured ones, with their columns, metrics and sample values.

Sensitive columns are dropped here, so they never reach the model and cannot be
selected, filtered or grouped by.
"""

import json
import re
import time
from typing import Any

from flask import g

from . import config
from .data import execute
from .text import best_match, fold

_TTL = 10 * 60  # seconds
_cache: dict[str, tuple[float, dict | None]] = {}


class ContextError(RuntimeError):
    """The requested dashboard cannot be used (missing or no access)."""


def user_key() -> str:
    user = getattr(g, "user", None)
    return str(getattr(user, "id", None) or "anon")


def _column_hidden(ds_id: int, column: Any) -> bool:
    sensitive = config.sensitive_columns()
    if column.column_name in sensitive or f"{ds_id}.{column.column_name}" in sensitive:
        return True
    try:
        extra = json.loads(column.extra or "{}")
    except (TypeError, ValueError):
        extra = {}
    return bool(extra.get("ai_sensitive")) or extra.get("ai_queryable") is False


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
    for c in ds.columns:
        if _column_hidden(ds.id, c):
            hidden.add(c.column_name)
            continue
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
    for col in columns:
        if col not in config.value_columns():
            continue
        res = execute(
            ds.id,
            {"columns": [col], "metrics": [], "row_limit": 50,
             "orderby": [(col, True)], "filters": []},
        )
        if "rows" in res:
            entry["values"][col] = [r.get(col) for r in res["rows"] if r.get(col) is not None]
    _cache[key] = (time.time(), entry)
    return entry


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
            "charts": [
                {"name": s.slice_name, "viz_type": s.viz_type, "dataset_id": s.datasource_id}
                for s in dash.slices[:20]
            ],
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
    return {"dashboard": dashboard, "datasets": datasets}


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
