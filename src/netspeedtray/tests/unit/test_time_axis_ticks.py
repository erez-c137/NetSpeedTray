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


def _labels(span, period_key, width_in=8.0):
    fig = Figure(figsize=(width_in, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    start = END - span
    ax.plot([start, END], [0, 1])
    ax.set_xlim(start, END)
    GraphRenderer._configure_xaxis_format(SimpleNamespace(ax_upload=ax), period_key, axis=ax)
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
