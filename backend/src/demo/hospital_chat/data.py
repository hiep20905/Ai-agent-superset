"""Run a query object through Superset's chart-data pipeline as the current user.

This is the same path dashboards use, so dataset permissions and Row Level
Security apply to every query the assistant makes.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def execute(dataset_id: int, query: dict[str, Any]) -> dict[str, Any]:
    """Return {"columns", "rows", "row_count"} or {"error"}."""
    from superset.commands.chart.data.get_data_command import ChartDataCommand
    from superset.common.query_context_factory import QueryContextFactory

    try:
        context = QueryContextFactory().create(
            datasource={"id": dataset_id, "type": "table"},
            queries=[query],
            form_data={},
        )
        command = ChartDataCommand(context)
        command.validate()  # dataset access check for the current user
        result = command.run()
    except Exception as ex:  # pylint: disable=broad-except
        logger.info("hospital-chat: query failed on dataset %s: %s", dataset_id, ex)
        return {"error": f"Truy vấn thất bại: {ex}"}

    first = ((result or {}).get("queries") or [{}])[0]
    if first.get("error"):
        return {"error": str(first["error"])}
    rows = first.get("data") or []
    return {
        "columns": first.get("colnames") or (list(rows[0]) if rows else []),
        "rows": rows,
        "row_count": len(rows),
    }
