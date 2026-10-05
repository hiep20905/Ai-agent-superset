"""Spot time expressions in a Vietnamese question and suggest a Superset time_range.

Used to force the model to filter by time when the question names a period,
instead of querying everything and "trimming" dates in its answer.
"""

import re
from datetime import date, timedelta

from .text import fold

# (pattern on the accent-folded question, Superset time_range)
_FIXED = [
    (r"\bhom nay\b|\btoday\b", "Today"),
    (r"\bhom qua\b|\byesterday\b", "Yesterday"),
    (r"\btuan (nay|hien tai)\b|\bthis week\b", "Current week"),
    (r"\btuan (truoc|qua|vua roi)\b|\blast week\b", "Last week"),
    (r"\bthang (nay|hien tai)\b|\bthis month\b", "Current month"),
    (r"\bthang (truoc|qua|vua roi)\b|\blast month\b", "Last month"),
    (r"\bquy (nay|hien tai)\b", "Current quarter"),
    (r"\bquy (truoc|qua)\b", "Last quarter"),
    (r"\bnam (nay|hien tai)\b|\bthis year\b", "Current year"),
    (r"\bnam (ngoai|truoc|qua)\b|\blast year\b", "Last year"),
]
_UNITS = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year"}


def _d(text: str) -> date | None:
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def suggest(question: str) -> tuple[str, str] | None:
    """(matched phrase, suggested time_range) or None when no period is named."""
    q = fold(question)

    dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", q)
    parsed = [d for d in (_d(x) for x in dates) if d]
    if len(parsed) >= 2:
        start, end = sorted(parsed[:2])
        return f"{dates[0]} - {dates[1]}", f"{start} : {end + timedelta(days=1)}"
    if len(parsed) == 1:
        day = parsed[0]
        return dates[0], f"{day} : {day + timedelta(days=1)}"

    m = re.search(r"\bthang (\d{1,2})\s*(?:/|nam\s*)(\d{4})\b", q)
    if m and 1 <= int(m[1]) <= 12:
        start = date(int(m[2]), int(m[1]), 1)
        end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        return m[0], f"{start} : {end}"

    m = re.search(r"\b(\d+) (ngay|tuan|thang|nam) (qua|gan day|gan nhat|vua qua|truoc)\b", q)
    if m:
        unit = _UNITS[m[2]]
        return m[0], f'DATEADD(DATETIME("now"), -{int(m[1])}, {unit}) : now'

    for pattern, time_range in _FIXED:
        hit = re.search(pattern, q)
        if hit:
            return hit[0], time_range
    return None
