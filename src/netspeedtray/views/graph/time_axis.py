"""
Time (x) axis ticks for every Monitor / graph view: a locator and formatter that decide together.

Why: AutoDateLocator picks its tick step on its own, and the old code paired it with a fixed
per-period format. On a 2-5 minute Session it chose 30 s ticks under a "%H:%M" label, so the axis
read "18:12, 18:13, 18:13, 18:14, 18:14"; TIMELINE_ALL on a young database printed "%Y-%m-%d" under
12-hourly ticks. Here the step is chosen first (from the visible span and the axis width), and the
format is derived from that step, so a label can never repeat.

Both objects re-plan on every draw from the axis' current view interval, so live views that move the
x-limits without reconfiguring the axis (update_data, a growing Session) stay correct.
"""
from __future__ import annotations

import functools
import math
from typing import Optional, Tuple

import matplotlib.dates as mdates
from matplotlib.ticker import Formatter, Locator

from netspeedtray import constants

_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30.44 * 86400,   # average; only used to size the step, ticks come from MonthLocator
    "year": 365.25 * 86400,
}
_DAY_S = 86400
# Day-of-month alone is unique for spans under 28 days ("%a %d"); month + day is unique within a year.
_DAY_OF_MONTH_UNIQUE_S = 28 * _DAY_S
_MONTH_DAY_UNIQUE_S = 365 * _DAY_S


def max_ticks_for_width(width_pt: Optional[float]) -> int:
    """How many labels fit on an axis ``width_pt`` points wide, clamped to the configured range."""
    g = constants.graph
    if not width_pt or width_pt <= 0:
        return g.TIME_AXIS_MAX_TICKS
    fit = int(width_pt // g.TIME_AXIS_LABEL_SPACING_PT)
    return max(g.TIME_AXIS_MIN_TICKS, min(g.TIME_AXIS_MAX_TICKS, fit))


def choose_step(span_s: float, max_ticks: int) -> Tuple[str, int]:
    """The finest step from TIME_AXIS_STEPS that puts at most ``max_ticks`` ticks on ``span_s`` seconds."""
    steps = constants.graph.TIME_AXIS_STEPS
    for unit, n in steps:
        # At most floor(span / step) + 1 round instants fit in the span (the epsilon absorbs float noise).
        if math.floor(span_s / (_UNIT_SECONDS[unit] * n) + 1e-9) + 1 <= max_ticks:
            return unit, n
    # Longer than the ladder covers: whole years, scaled up.
    years = span_s / _UNIT_SECONDS["year"]
    return "year", max(1, math.ceil(years / max(1, max_ticks - 1)))


def format_for_step(unit: str, n: int, span_s: float) -> str:
    """A strftime format whose resolution matches the step, so two distinct ticks never share a label."""
    g = constants.graph
    if unit == "second":
        return g.TIME_AXIS_FMT_SECONDS
    if unit in ("minute", "hour"):
        # Two ticks print the same "%H:%M" only if they are exactly a day apart.
        return g.TIME_AXIS_FMT_HOURS_MULTIDAY if span_s >= _DAY_S else g.TIME_AXIS_FMT_MINUTES
    if unit in ("day", "week"):
        if span_s < _DAY_OF_MONTH_UNIQUE_S:
            return g.TIME_AXIS_FMT_DAYS_SHORT
        if span_s < _MONTH_DAY_UNIQUE_S:
            return g.TIME_AXIS_FMT_DAYS
        return g.TIME_AXIS_FMT_DAYS_LONG
    if unit == "month":
        return g.TIME_AXIS_FMT_MONTHS
    return g.TIME_AXIS_FMT_YEARS


def _sub_locator(unit: str, n: int) -> mdates.DateLocator:
    if unit == "second":
        return mdates.SecondLocator(bysecond=range(0, 60, n))
    if unit == "minute":
        return mdates.MinuteLocator(byminute=range(0, 60, n))
    if unit == "hour":
        return mdates.HourLocator(byhour=range(0, 24, n))
    if unit == "day":
        return mdates.DayLocator(interval=n)
    if unit == "week":
        return mdates.WeekdayLocator(byweekday=mdates.MO, interval=n)
    if unit == "month":
        return mdates.MonthLocator(bymonth=range(1, 13, n))
    return mdates.YearLocator(n)


class TimeAxisLocator(Locator):
    """Places ticks at a round step sized to the visible span and the axis width."""

    def _axis_width_pt(self) -> Optional[float]:
        try:
            axes = self.axis.axes
            return axes.bbox.width / axes.figure.dpi * 72.0
        except Exception:
            return None

    def plan(self, vmin: Optional[float] = None, vmax: Optional[float] = None):
        """(unit, n, format) for the given (or current) view interval in matplotlib date numbers."""
        if vmin is None or vmax is None:
            vmin, vmax = self.axis.get_view_interval()
        vmin, vmax = sorted((vmin, vmax))
        span_s = max(0.0, (vmax - vmin) * _DAY_S)
        unit, n = choose_step(span_s, max_ticks_for_width(self._axis_width_pt()))
        return unit, n, format_for_step(unit, n, span_s)

    def __call__(self):
        vmin, vmax = self.axis.get_view_interval()
        return self.tick_values(vmin, vmax)

    def tick_values(self, vmin, vmax):
        vmin, vmax = sorted((vmin, vmax))
        if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
            return []
        unit, n, _fmt = self.plan(vmin, vmax)
        sub = _sub_locator(unit, n)
        if self.axis is not None:
            sub.set_axis(self.axis)
        # matplotlib's date locators take datetimes, not date numbers.
        ticks = sub.tick_values(mdates.num2date(vmin, sub.tz), mdates.num2date(vmax, sub.tz))
        return [t for t in ticks if vmin <= t <= vmax]


@functools.lru_cache(maxsize=None)
def _locale_names(language: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], bool]:
    """(short weekday names Mon..Sun, short month names Jan..Dec, day-before-month) from Qt's CLDR data
    for ``language``. Qt, not strftime: matplotlib formats under the C locale, so "%a" / "%b" printed
    English names in every language."""
    from PyQt6.QtCore import QLocale
    q = QLocale(language)
    short = QLocale.FormatType.ShortFormat
    days = tuple(q.dayName(d, short) for d in range(1, 8))
    months = tuple(q.monthName(m, short) for m in range(1, 13))
    pattern = q.dateFormat(short)                       # e.g. "M/d/yy", "dd.MM.yy", "yyyy/MM/dd"
    d_at, m_at = pattern.find("d"), pattern.find("M")
    day_first = 0 <= d_at < m_at
    return days, months, day_first


def localized_strftime(dt, fmt: str, language: Optional[str], shape: bool = True) -> str:
    """``dt.strftime(fmt)`` with "%a" / "%b" in ``language`` (None = strftime as-is). A "%b %d" label
    follows the language's day/month order. Names are one-to-one with the weekday / month, so a format
    that keeps two ticks apart in English keeps them apart in every language. ``shape`` reshapes RTL
    (Hebrew) text into display order, as every other matplotlib label is (mpl_fonts.shape_rtl)."""
    if not language or ("%a" not in fmt and "%b" not in fmt):
        return dt.strftime(fmt)
    try:
        days, months, day_first = _locale_names(str(language))
    except Exception:
        return dt.strftime(fmt)
    if day_first and fmt == "%b %d":
        fmt = "%d %b"
    fmt = (fmt.replace("%a", days[dt.isoweekday() - 1].replace("%", "%%"))
              .replace("%b", months[dt.month - 1].replace("%", "%%")))
    text = dt.strftime(fmt)
    if shape:
        from netspeedtray.utils.mpl_fonts import shape_rtl
        text = shape_rtl(text, language)
    return text


class TimeAxisFormatter(Formatter):
    """Formats ticks with the format matching the locator's current step, with weekday and month
    names in ``language`` (the app's UI language; None keeps strftime's names)."""

    def __init__(self, locator: TimeAxisLocator, language: Optional[str] = None):
        self.locator = locator
        self.language = language
        self.tz = None   # matplotlib's default (rcParams["timezone"]), as DateFormatter(fmt) used

    def _fmt(self) -> str:
        try:
            _unit, _n, fmt = self.locator.plan()
        except Exception:
            fmt = constants.graph.TIME_AXIS_FMT_MINUTES
        return fmt

    def _format(self, x, fmt: str) -> str:
        return localized_strftime(mdates.num2date(x, self.tz), fmt, self.language)

    def format_ticks(self, values):
        fmt = self._fmt()
        return [self._format(v, fmt) for v in values]

    def __call__(self, x, pos=None):
        return self._format(x, self._fmt())


def apply_time_axis(axis, language: Optional[str] = None) -> None:
    """Give ``axis`` (an Axes) the shared time-axis locator + formatter. The one entry point.
    ``language`` is the app's UI language, for the weekday / month names."""
    locator = TimeAxisLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(TimeAxisFormatter(locator, language))
