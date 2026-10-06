"""Calendar periods as Superset time ranges, and Vietnamese time expressions.

Superset's own keywords do not mean what a Vietnamese question means: "Today"
and "Yesterday" only set an upper bound (everything before today), and
"Last week/month/year" are rolling windows (7/30/365 days), not the previous
calendar week/month/year. So periods are given to the model, and suggested
from the question, as explicit "YYYY-MM-DD : YYYY-MM-DD" ranges (end excluded).
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .text import fold

_UNITS = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year"}
_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]


def today() -> date:
    # Superset evaluates its relative ranges with the server clock; so do we.
    return date.today()


def _month(year: int, month: int) -> date:
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def periods(day: date | None = None) -> list[tuple[str, str, str]]:
    """[(key, Vietnamese name, "start : end")] for the usual calendar periods."""
    d = day or today()
    week = d - timedelta(days=d.weekday())
    month = d.replace(day=1)
    quarter = _month(d.year, (d.month - 1) // 3 * 3 + 1)
    year = date(d.year, 1, 1)
    spans = [
        ("today", "hôm nay", d, d + timedelta(days=1)),
        ("yesterday", "hôm qua", d - timedelta(days=1), d),
        ("this_week", "tuần này", week, week + timedelta(days=7)),
        ("last_week", "tuần trước", week - timedelta(days=7), week),
        ("this_month", "tháng này", month, _month(d.year, d.month + 1)),
        ("last_month", "tháng trước", _month(d.year, d.month - 1), month),
        ("this_quarter", "quý này", quarter, _month(quarter.year, quarter.month + 3)),
        ("last_quarter", "quý trước", _month(quarter.year, quarter.month - 3), quarter),
        ("this_year", "năm nay", year, date(d.year + 1, 1, 1)),
        ("last_year", "năm ngoái", date(d.year - 1, 1, 1), year),
    ]
    return [(key, name, f"{start} : {end}") for key, name, start, end in spans]


def describe_today() -> str:
    """Prompt lines: today's date and the usual periods as explicit ranges."""
    d = today()
    ranges = "; ".join(f"{name} = \"{r}\"" for _, name, r in periods(d))
    return (
        f"Hôm nay là {_WEEKDAYS[d.weekday()]}, {d:%d/%m/%Y} ({d}). "
        "Mốc thời gian, chỉ dùng khi câu hỏi nêu thời gian (time_range, điểm cuối "
        f"không tính): {ranges}."
    )


# (pattern on the accent-folded question, period key)
_FIXED = [
    (r"\bhom nay\b|\btoday\b", "today"),
    (r"\bhom qua\b|\byesterday\b", "yesterday"),
    (r"\btuan (nay|hien tai)\b|\bthis week\b", "this_week"),
    (r"\btuan (truoc|qua|vua roi)\b|\blast week\b", "last_week"),
    (r"\bthang (nay|hien tai)\b|\bthis month\b", "this_month"),
    (r"\bthang (truoc|qua|vua roi)\b|\blast month\b", "last_month"),
    (r"\bquy (nay|hien tai)\b", "this_quarter"),
    (r"\bquy (truoc|qua)\b", "last_quarter"),
    (r"\bnam (nay|hien tai)\b|\bthis year\b", "this_year"),
    (r"\bnam (ngoai|truoc|qua)\b|\blast year\b", "last_year"),
]

# Superset keywords that do not filter what they seem to: replaced on the way in.
_MISLEADING = {"today": "today", "yesterday": "yesterday"}


# Any wording that names or implies a period (on the accent-folded text). "nam"
# alone is not enough: it is also "Nam" (male).
_TIME_WORDS = re.compile(
    r"\b(19|20)\d{2}\b|\d{1,2}[/-]\d{1,2}"
    r"|\b(ngay|tuan|thang|quy|nam|gio) (nay|truoc|qua|ngoai|toi|sau|vua roi|hien tai|gan day|gan nhat)\b"
    r"|\b(thang|quy|ngay|tuan) \d|\b\d+ (gio|ngay|tuan|thang|quy|nam)\b"
    r"|\bhom (nay|qua|kia)\b|\bgan day\b|\btu .+ den\b|\bsang nay\b|\btoi qua\b|\bdem qua\b"
    r"|\btheo (gio|ngay|tuan|thang|quy|nam)\b|\bxu huong\b|\bdien bien\b"
    r"|\btoday\b|\byesterday\b|\b(this|last) (week|month|year|quarter)\b"
)


def mentions_time(text: str) -> bool:
    """Whether the text names a period; when it does not, the question is about
    the current state and must not be filtered by time."""
    return bool(_TIME_WORDS.search(fold(text)))


def clean(time_range: object) -> str:
    """Strip what small models wrap around the value ('time_range="Today"')."""
    value = str(time_range or "").strip()
    value = re.sub(r"^time_range\s*[=:]\s*", "", value, flags=re.I)
    return value.strip().strip("'\"").strip()


def normalize(time_range: str) -> str:
    """Fix time_range values Superset would misread ("Today" -> today's range)."""
    key = _MISLEADING.get(time_range.strip().lower())
    if key is None:
        return time_range
    return next(r for k, _, r in periods() if k == key)


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
        return m[0], f"{start} : {_month(start.year, start.month + 1)}"

    m = re.search(r"\b(\d+) (ngay|tuan|thang|nam) (qua|gan day|gan nhat|vua qua|truoc)\b", q)
    if m:
        unit = _UNITS[m[2]]
        return m[0], f'DATEADD(DATETIME("now"), -{int(m[1])}, {unit}) : now'

    ranges = {k: r for k, _, r in periods()}
    for pattern, key in _FIXED:
        hit = re.search(pattern, q)
        if hit:
            return hit[0], ranges[key]
    return None


_PERIOD_FORMATS = {"PT1H": "%Y-%m-%d %H:00", "P1D": "%Y-%m-%d", "P1W": "%Y-%m-%d",
                   "P1M": "%Y-%m", "P1Y": "%Y"}


def is_missing(value: Any) -> bool:
    """None, or pandas' NaT/NaN for a row without a date."""
    return value is None or value != value  # NaT and NaN are not equal to themselves


def format_period(value: Any, grain: str) -> Any:
    """Readable label for a time bucket ("2026-07", "Q3/2026") from whatever the
    query returned (datetime, epoch milliseconds or ISO text)."""
    if is_missing(value):
        return None
    when = None
    if hasattr(value, "strftime"):
        when = value
    elif isinstance(value, (int, float)) and abs(value) > 1e11:
        when = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return value
    if when is None:
        return value
    if grain == "P3M":
        return f"Q{(when.month - 1) // 3 + 1}/{when.year}"
    return when.strftime(_PERIOD_FORMATS.get(grain, "%Y-%m-%d"))
