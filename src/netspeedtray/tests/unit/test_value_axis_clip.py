"""
Long windows (Week / Month / All) clip the speed axis at the 99th percentile when a few outliers
would otherwise squash everything else (2.2 scope C: "two outliers stop squashing a month into 3% of
the height").

The percentile comes from the points actually plotted (there is no throughput histogram - plan scope
A defers it). Clipped points must not vanish: each run above the top edge gets a caret marker, the
axis states the true peak, and the hover still reads the true value from the line's own data.
Live / short windows keep the old max-scaled behaviour.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import matplotlib.dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.graph.value_axis import clipped_axis_max, clipped_runs

END = datetime(2026, 10, 19, 18, 0, 0)
MBPS = 125_000.0          # bytes/sec per Mbps


def _month_mbps(spikes=(), n=720, seed=7):
    """An hourly month: ordinary 5-25 Mbps traffic, with the given (index, Mbps) spikes."""
    rng = np.random.default_rng(seed)
    ys = rng.uniform(5.0, 25.0, n)
    for i, v in spikes:
        ys[i] = v
    return ys


# ----------------------------------------------------------------------------- the helper

def test_normal_month_is_not_clipped():
    ys = _month_mbps()
    top, clipped = clipped_axis_max(ys, "TIMELINE_MONTH")
    assert clipped is False
    assert top == pytest.approx(ys.max())


def test_month_with_two_50x_spikes_is_clipped():
    ys = _month_mbps(spikes=[(200, 900.0), (500, 950.0)])
    top, clipped = clipped_axis_max(ys, "TIMELINE_MONTH")
    assert clipped is True
    assert top < 100.0, top                      # scaled to the month, not to the 950 Mbps outlier


def test_clip_never_goes_below_the_percentile():
    ys = _month_mbps(spikes=[(200, 900.0), (500, 950.0)])
    p = np.percentile(ys, constants.graph.VALUE_AXIS_CLIP_PERCENTILE)
    top, clipped = clipped_axis_max(ys, "TIMELINE_MONTH")
    assert clipped and top >= p


def test_sustained_high_traffic_is_not_an_outlier():
    """More than 1% of the month high is a pattern, not an outlier: the p99 sees it, no clip."""
    ys = _month_mbps(spikes=[(i, 900.0) for i in range(0, 720, 50)])     # 15 hours, ~2%
    _top, clipped = clipped_axis_max(ys, "TIMELINE_MONTH")
    assert clipped is False


@pytest.mark.parametrize("period", ["TIMELINE_SESSION", "TIMELINE_1_HOUR", "TIMELINE_24_HOURS",
                                    "TIMELINE_48_HOURS", "TIMELINE_SYSTEM_UPTIME"])
def test_short_and_live_windows_never_clip(period):
    ys = _month_mbps(spikes=[(200, 900.0), (500, 950.0)])
    top, clipped = clipped_axis_max(ys, period)
    assert clipped is False and top == pytest.approx(950.0)


@pytest.mark.parametrize("period", ["TIMELINE_WEEK", "TIMELINE_MONTH", "TIMELINE_ALL"])
def test_long_windows_clip(period):
    ys = _month_mbps(spikes=[(200, 900.0), (500, 950.0)])
    assert clipped_axis_max(ys, period)[1] is True


def test_too_few_points_or_idle_month_never_clip():
    assert clipped_axis_max([1.0, 2.0, 900.0], "TIMELINE_MONTH")[1] is False
    idle = np.zeros(720)
    idle[10] = 50.0
    assert clipped_axis_max(idle, "TIMELINE_MONTH")[1] is False    # p99 of 0 is no scale
    assert clipped_axis_max([], "TIMELINE_MONTH") == (0.0, False)


def test_clipped_runs_mark_one_point_per_run_at_its_peak():
    xs = np.arange(10, dtype=float)
    ys = np.array([1, 9, 12, 8, 1, 1, 20, 1, 1, 15], dtype=float)
    assert clipped_runs(xs, ys, 5.0) == [(2.0, 12.0), (6.0, 20.0), (9.0, 15.0)]
    assert clipped_runs(xs, ys, 50.0) == []


# ----------------------------------------------------------------------------- the real renderer

def _agg_init(self):
    self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
    self.canvas = FigureCanvasAgg(self.figure)
    self._setup_standard_axes()


@pytest.fixture
def agg(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _agg_init)


def _render_month(period="TIMELINE_MONTH", spikes=((200, 900.0), (500, 950.0))):
    r = GraphRenderer(MagicMock(), I18nStrings("en_US"))
    down = _month_mbps(spikes=spikes)
    up = _month_mbps(seed=3) / 5.0
    start = END - timedelta(hours=len(down))
    data = [(start + timedelta(hours=i), float(up[i]) * MBPS, float(down[i]) * MBPS)
            for i in range(len(down))]
    r.render(data, start, END, period, stat_type="network")
    return r, start


def _clip_markers(ax):
    return [c for c in ax.collections if c.get_gid() == "value_axis_clip_markers"]


def _peak_texts(ax):
    return [t for t in ax.texts if t.get_gid() == "value_axis_clip_peak"]


def test_month_render_clips_the_download_axis_and_marks_the_outliers(agg):
    r, _start = _render_month()
    top = r.ax_download.get_ylim()[1]
    assert top < 100.0, top
    markers = _clip_markers(r.ax_download)
    assert len(markers) == 1
    assert len(markers[0].get_offsets()) == 2              # one caret per outlier
    peak = _peak_texts(r.ax_download)
    assert len(peak) == 1 and "950" in peak[0].get_text(), [t.get_text() for t in peak]
    # The un-spiked upload axis is untouched.
    assert not _clip_markers(r.ax_upload) and not _peak_texts(r.ax_upload)


def test_24h_render_keeps_the_max_scaled_axis(agg):
    r, _start = _render_month(period="TIMELINE_24_HOURS")
    assert r.ax_download.get_ylim()[1] >= 950.0
    assert not _clip_markers(r.ax_download) and not _peak_texts(r.ax_download)


def test_rerender_without_outliers_removes_the_markers(agg):
    r, start = _render_month()
    down = _month_mbps()
    data = [(start + timedelta(hours=i), MBPS, float(down[i]) * MBPS) for i in range(len(down))]
    r.render(data, start, END, "TIMELINE_MONTH", stat_type="network")
    assert not _clip_markers(r.ax_download) and not _peak_texts(r.ax_download)


def test_hover_reports_the_true_value_of_a_clipped_point(agg):
    from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip
    r, start = _render_month()
    r.figure.canvas.draw()
    assert r.ax_download.get_ylim()[1] < 950.0
    hover = GraphHoverTooltip(SimpleNamespace(_current_stat="network", config={}, i18n=r.i18n))
    x = mdates.date2num(start + timedelta(hours=500))
    rows, _at = hover._rows_at(r.ax_download, x)
    values = {lbl: y for lbl, y, _c in rows}
    assert values[r.i18n.DOWNLOAD_LABEL] == pytest.approx(950.0, rel=1e-6)
