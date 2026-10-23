"""
The "Usage by day / month / year" table (#334) - roll-ups and the bytes CSV.

No Qt at import time; billing periods borrow the odometer's period rule from ``WidgetState`` (a Qt
module) through a lazy import, so there is one rule, not two copies of it.

Input is ``HistoryReader.daily_volume``: exact byte totals per LOCAL day (DST-correct), over the same
adapters as the usage card (``WidgetState.metered_scope``). Everything coarser is a plain sum of those
days, so a month (or billing period, or year) is exactly the sum of the day rows shown under it - the
table can never disagree with itself, and its Today row is the usage card's Today.

Months: calendar months, or - when a data cap with a reset day other than the 1st is set - billing
periods that start on that day, the same periods the cap counts (``WidgetState._compute_period_key``,
one rule). Years: calendar years.

``estimated`` on a row: part of it was reconstructed from speeds recorded before 2.2 (``VolumeResult``
D5); the UI marks those rows and the CSV says so per row.
"""
import csv
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

KIND_DAY, KIND_MONTH, KIND_PERIOD, KIND_YEAR = "day", "month", "period", "year"

#: Fixed English machine headers (as utils/stats_exporter.py): a spreadsheet formula or a script keeps
#: working whatever the UI language. Bytes, so the rows sum exactly - never Mbps or GB.
CSV_FIELDS = ("date", "download_bytes", "upload_bytes", "total_bytes", "estimated")


@dataclass(frozen=True)
class UsageRow:
    kind: str           # KIND_*
    start: date         # first local day the row covers
    end: date           # last local day it covers (inclusive)
    up_bytes: float
    down_bytes: float
    estimated: bool

    @property
    def total_bytes(self) -> float:
        return self.up_bytes + self.down_bytes

    @property
    def key(self) -> str:
        """The CSV ``date`` column: ``YYYY-MM-DD`` (day), ``YYYY-MM`` (month), ``YYYY`` (year), and
        an ISO 8601 interval ``YYYY-MM-DD/YYYY-MM-DD`` for a billing period."""
        if self.kind == KIND_DAY:
            return self.start.isoformat()
        if self.kind == KIND_MONTH:
            return f"{self.start.year:04d}-{self.start.month:02d}"
        if self.kind == KIND_YEAR:
            return f"{self.start.year:04d}"
        return f"{self.start.isoformat()}/{self.end.isoformat()}"


def billing_reset_day(config) -> Optional[int]:
    """The data-cap reset day when the Months view should follow billing periods (a cap is set and it
    resets on a day other than the 1st - the usage card's own rule), else None (calendar months)."""
    try:
        if not config.get("data_cap_enabled") or float(config.get("data_cap_gb", 0) or 0) <= 0:
            return None
        day = max(1, min(28, int(config.get("data_cap_reset_day", 1))))
    except (TypeError, ValueError):
        return None
    return None if day == 1 else day


def _period_bounds(d: date, reset_day: int) -> Tuple[date, date]:
    """(first, last) day of the billing period holding ``d`` - the odometer's own rule for the start."""
    from netspeedtray.core.widget_state import WidgetState   # lazy: the one period rule, Qt module
    start = date.fromisoformat(WidgetState._compute_period_key(reset_day, d))
    # The reset day is clamped to 1-28, so the same day next month always exists.
    nxt = (date(start.year + 1, 1, start.day) if start.month == 12
           else date(start.year, start.month + 1, start.day))
    return start, nxt - timedelta(days=1)


def _month_bounds(d: date) -> Tuple[date, date]:
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return date(d.year, d.month, 1), nxt - timedelta(days=1)


def day_rows(days: Iterable) -> List[UsageRow]:
    """``DayVolume`` entries -> day rows, oldest first."""
    return [UsageRow(KIND_DAY, d.day, d.day, float(d.up_bytes), float(d.down_bytes), bool(d.reconstructed))
            for d in sorted(days, key=lambda v: v.day)]


def _group(rows: Sequence[UsageRow], kind: str, bounds) -> List[UsageRow]:
    out: List[UsageRow] = []
    for r in rows:
        first, last = bounds(r.start)
        if out and out[-1].start == first:
            p = out[-1]
            out[-1] = replace(p, up_bytes=p.up_bytes + r.up_bytes, down_bytes=p.down_bytes + r.down_bytes,
                              estimated=p.estimated or r.estimated)
        else:
            out.append(UsageRow(kind, first, last, r.up_bytes, r.down_bytes, r.estimated))
    return out


def month_rows(days: Sequence[UsageRow], reset_day: Optional[int] = None) -> List[UsageRow]:
    """Day rows (oldest first) -> calendar months, or billing periods when ``reset_day`` is set."""
    if reset_day:
        return _group(days, KIND_PERIOD, lambda d: _period_bounds(d, reset_day))
    return _group(days, KIND_MONTH, _month_bounds)


def year_rows(days: Sequence[UsageRow]) -> List[UsageRow]:
    return _group(days, KIND_YEAR, lambda d: (date(d.year, 1, 1), date(d.year, 12, 31)))


def clamp_day(days: List, today: date, ceiling: Optional[Tuple[float, float]]) -> List:
    """The usage card's rule for its Today row with a cap set: Today never reads above the period's
    own count (the odometer, ``(up, down)``). Applied to the same ``DayVolume`` here so the table's
    Today row shows what the card shows."""
    if ceiling is None:
        return list(days)
    return [replace(d, up_bytes=min(d.up_bytes, float(ceiling[0])),
                    down_bytes=min(d.down_bytes, float(ceiling[1]))) if d.day == today else d
            for d in days]


def clamp_current_period(rows: List[UsageRow], days: Sequence[UsageRow], today: date, period_start: date,
                         ceiling: Tuple[float, float]) -> List[UsageRow]:
    """The usage card's rule for the period, with a cap set: the card shows the odometer's count
    (``ceiling``, ``(up, down)``) for the period starting ``period_start``, never history. History
    (``days``, the day rows) can read ABOVE it - in auto mode it counts every physical adapter while the
    odometer meters the one in use - so the current period row is capped at it, and its year row loses
    the same excess (floored at 0; for a period that began last December the whole excess is taken off
    this year). A row lower than the odometer is left alone: history cannot know the closed-app traffic
    the odometer recovered, and the footnote says so. Month / period rows only change when the row is
    the odometer's period (same start); years always do."""
    hist_up = sum(d.up_bytes for d in days if period_start <= d.start <= today)
    hist_down = sum(d.down_bytes for d in days if period_start <= d.start <= today)
    ex_up = max(0.0, hist_up - float(ceiling[0]))
    ex_down = max(0.0, hist_down - float(ceiling[1]))
    if not (ex_up or ex_down):
        return list(rows)
    out = []
    for r in rows:
        current = r.start <= today <= r.end and (r.kind == KIND_YEAR or r.start == period_start)
        if current:
            r = replace(r, up_bytes=max(0.0, r.up_bytes - ex_up), down_bytes=max(0.0, r.down_bytes - ex_down))
        out.append(r)
    return out


def write_csv(path, rows: Sequence[UsageRow]) -> None:
    """``date,download_bytes,upload_bytes,total_bytes,estimated`` - whole bytes, oldest first, UTF-8,
    ``estimated`` as ``true`` / ``false``."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in sorted(rows, key=lambda x: x.start):
            down, up = int(round(r.down_bytes)), int(round(r.up_bytes))
            w.writerow((r.key, down, up, down + up, "true" if r.estimated else "false"))
