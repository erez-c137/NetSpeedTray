"""
The Monitor graph's time axis must never print the same label twice.

Regression: on a ~6-minute Session the Network graph read "18:11, 18:12, 18:12, 18:13, 18:13, ..." -
AutoDateLocator put a tick every 30 s while the formatter printed only %H:%M. The same class of bug
hid in every fixed per-period format (e.g. TIMELINE_ALL printed %Y-%m-%d under hourly ticks when the
database held only a day or two).

These tests drive the real ``GraphRenderer._configure_xaxis_format`` on an Agg figure the size of the
Monitor graph, draw it, and read back the labels matplotlib actually painted.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import matplotlib
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from netspeedtray.views.graph.renderer import GraphRenderer

END = datetime(2026, 10, 19, 18, 16, 37)

SPANS = [
    # (span, period_key)
    (timedelta(minutes=2), "TIMELINE_SESSION"),
    (timedelta(minutes=4, seconds=30), "TIMELINE_SESSION"),  # the screenshot: 18:12, 18:13, 18:13, ...
    (timedelta(minutes=5, seconds=32), "TIMELINE_SESSION"),
    (timedelta(minutes=6), "TIMELINE_SESSION"),
    (timedelta(minutes=6), "TIMELINE_SYSTEM_UPTIME"),
    (timedelta(minutes=45), "TIMELINE_1_HOUR"),
    (timedelta(hours=3), "TIMELINE_4_HOURS"),
    (timedelta(days=1), "TIMELINE_24_HOURS"),
    (timedelta(hours=48), "TIMELINE_48_HOURS"),
    (timedelta(days=7), "TIMELINE_WEEK"),
    (timedelta(days=30), "TIMELINE_MONTH"),
    (timedelta(days=2), "TIMELINE_ALL"),       # a young database: "All" covers only two days
    (timedelta(days=400), "TIMELINE_ALL"),
    (timedelta(seconds=40), "TIMELINE_SESSION"),
]


def _labels(span, period_key, width_in=8.0, language=None):
    fig = Figure(figsize=(width_in, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    start = END - span
    ax.plot([start, END], [0, 1])
    ax.set_xlim(start, END)
    host = SimpleNamespace(ax_upload=ax)
    if language is not None:
        host.i18n = SimpleNamespace(language=language)
    GraphRenderer._configure_xaxis_format(host, period_key, axis=ax)
    fig.canvas.draw()
    lo, hi = ax.get_xlim()
    return [
        t.get_text() for t, loc in zip(ax.xaxis.get_majorticklabels(), ax.xaxis.get_majorticklocs())
        if lo <= loc <= hi and t.get_text()
    ]


@pytest.mark.parametrize("span,period_key", SPANS, ids=lambda v: str(v))
def test_time_axis_labels_are_unique(span, period_key):
    labels = _labels(span, period_key)
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    assert not dupes, f"{period_key} over {span}: repeated labels {dupes} in {labels}"


@pytest.mark.parametrize("span,period_key", SPANS, ids=lambda v: str(v))
def test_time_axis_label_count_is_reasonable(span, period_key):
    labels = _labels(span, period_key)
    assert 3 <= len(labels) <= 10, f"{period_key} over {span}: {len(labels)} labels {labels}"


def test_narrow_axis_gets_fewer_labels():
    """The tick count follows the axis width, so a narrow Monitor window does not crowd its labels."""
    wide = _labels(timedelta(hours=3), "TIMELINE_4_HOURS", width_in=10.0)
    narrow = _labels(timedelta(hours=3), "TIMELINE_4_HOURS", width_in=4.0)
    assert len(narrow) < len(wide)
    assert len(set(narrow)) == len(narrow)


def test_labels_stay_unique_when_a_live_session_grows():
    """Session/live views move the x-limits without re-running the configuration (update_data) -
    the locator and formatter must re-decide on every draw, not freeze the first span's choice."""
    fig = Figure(figsize=(8, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.plot([END - timedelta(days=1), END], [0, 1])
    ax.set_xlim(END - timedelta(seconds=50), END)
    GraphRenderer._configure_xaxis_format(SimpleNamespace(ax_upload=ax), "TIMELINE_SESSION", axis=ax)
    fig.canvas.draw()
    for span in (timedelta(minutes=6), timedelta(hours=5), timedelta(days=1)):
        ax.set_xlim(END - span, END)
        fig.canvas.draw()
        lo, hi = ax.get_xlim()
        labels = [t.get_text() for t, loc in zip(ax.xaxis.get_majorticklabels(), ax.xaxis.get_majorticklocs())
                  if lo <= loc <= hi and t.get_text()]
        assert len(set(labels)) == len(labels), f"{span}: {labels}"
        assert 3 <= len(labels) <= 10, f"{span}: {labels}"


# --- weekday / month names follow the app language, not matplotlib's C locale -------------------

_ENGLISH_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
                  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
_NAMED_SPANS = [
    (timedelta(days=1), "TIMELINE_24_HOURS"),     # "%a %H:%M"
    (timedelta(days=7), "TIMELINE_WEEK"),         # "%a %d"
    (timedelta(days=30), "TIMELINE_MONTH"),       # "%b %d"
    (timedelta(days=400), "TIMELINE_ALL"),        # "%b %Y"
]


def _qt_names(language):
    from PyQt6.QtCore import QLocale
    q = QLocale(language)
    short = QLocale.FormatType.ShortFormat
    return ({q.dayName(d, short) for d in range(1, 8)}, {q.monthName(m, short) for m in range(1, 13)})


@pytest.mark.parametrize("language", ["ja_JP", "de_DE", "he_IL", "en_US"])
@pytest.mark.parametrize("span,period_key", _NAMED_SPANS, ids=lambda v: str(v))
def test_day_and_month_names_follow_the_app_language(language, span, period_key):
    from netspeedtray.utils.mpl_fonts import shape_rtl
    labels = _labels(span, period_key, language=language)
    assert len(set(labels)) == len(labels), f"{language} {period_key}: repeated labels {labels}"
    assert 3 <= len(labels) <= 10, f"{language} {period_key}: {labels}"
    days, months = _qt_names(language)
    names = days if period_key in ("TIMELINE_24_HOURS", "TIMELINE_WEEK") else months
    # Every label carries one of the language's own names (Hebrew in display order, as painted).
    shaped = {shape_rtl(n, language) for n in names} | names
    for lbl in labels:
        assert any(n in lbl for n in shaped), f"{language} {period_key}: {lbl!r} has no {sorted(names)}"
    if language != "en_US":
        own = {n.rstrip(".") for n in days | months}           # German "Nov." is German too
        words = {w for lbl in labels for w in lbl.replace(".", " ").split()} - own
        assert not (words & _ENGLISH_NAMES), f"{language} {period_key}: English names in {labels}"


def test_hebrew_axis_labels_are_shaped_for_display():
    from netspeedtray.utils.mpl_fonts import shape_rtl
    from netspeedtray.views.graph.time_axis import localized_strftime
    from datetime import datetime as _dt
    logical = localized_strftime(_dt(2026, 10, 19, 16, 0), "%a %H:%M", "he_IL", shape=False)
    assert logical.startswith("יום ב")                     # Monday, Hebrew, logical order
    assert localized_strftime(_dt(2026, 10, 19, 16, 0), "%a %H:%M", "he_IL") == shape_rtl(logical, "he_IL")
