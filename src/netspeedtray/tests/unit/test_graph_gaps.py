"""
Gaps in the Monitor graphs (2.2.0-beta.1 smoke test on the owner's migrated history, 'All' window):

* Hardware drew straight lines across months with no data, while Network broke them (dashed zero
  bridge). Both now use one gap rule (``GraphRenderer._gap_threshold``): a hardware series is one line
  per gap-free segment.
* Network filled under the line only from ~Sep 6: every segment of a gapped series reused the ONE
  cached gradient fill per axis, so each segment moved it on and only the last kept it. Each segment
  now gets its own fill.

Real GraphRenderer code paths on real matplotlib axes (no Qt canvas), as test_graph_series_labels.
"""
from datetime import datetime, timedelta

import numpy as np
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.tests.unit.test_graph_series_labels import _bare_init
from netspeedtray.views.graph.renderer import GraphRenderer
from unittest.mock import MagicMock

T0 = datetime(2026, 7, 1, 0, 0, 0)
PERIOD = "TIMELINE_ALL"


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    return GraphRenderer(MagicMock(), I18nStrings("en_US"))


def _hourly(start, hours, value):
    return [(start + timedelta(hours=i), value) for i in range(hours)]


# Two weeks of hourly data, a month with none, then two more weeks.
CHUNK_A = _hourly(T0, 14 * 24, 60.0)
CHUNK_B = _hourly(T0 + timedelta(days=45), 14 * 24, 20.0)
GAP_START, GAP_END = CHUNK_A[-1][0], CHUNK_B[0][0]


def _measured(ax, label):
    return [ln for ln in ax.get_lines() if str(ln.get_label()) == label]


def _spans_gap(line):
    xs = [x if isinstance(x, datetime) else None for x in line.get_xdata()]
    return any(a is not None and b is not None and a <= GAP_START and b >= GAP_END for a, b in zip(xs, xs[1:]))


def test_network_every_segment_keeps_its_fill(renderer):
    dts = np.array([t for t, _ in CHUNK_A + CHUNK_B])
    vals = np.array([v for _, v in CHUNK_A + CHUNK_B])
    renderer._plot_high_res(dts, vals / 10, vals)
    segments = len(_measured(renderer.ax_download, renderer.i18n.DOWNLOAD_LABEL))
    assert segments == 2
    assert len(renderer.ax_download.images) == segments      # one gradient fill per segment
    assert len(renderer.ax_upload.images) == segments
    # Each fill sits under its own segment: the first one ends where chunk A ends.
    import matplotlib.dates as mdates
    extents = sorted(im.get_extent()[:2] for im in renderer.ax_download.images)
    assert extents[0][1] == pytest.approx(mdates.date2num(GAP_START))
    assert extents[1][0] == pytest.approx(mdates.date2num(GAP_END))


def test_network_live_update_still_reuses_one_fill(renderer):
    """The fast path (no gap) keeps the cached, reused fill - live updates must not pile up images."""
    dts = np.array([t for t, _ in CHUNK_A])
    vals = np.array([v for _, v in CHUNK_A])
    for _ in range(3):
        renderer._plot_high_res(dts, vals, vals)
    assert len(renderer.ax_download.images) == 1


@pytest.mark.parametrize("layout", ["combined", "separate", "single"])
def test_hardware_lines_break_at_gaps(renderer, layout):
    data = {"cpu": CHUNK_A + CHUNK_B, "gpu": CHUNK_A + CHUNK_B, "ram": CHUNK_A + CHUNK_B}
    end = CHUNK_B[-1][0]
    if layout == "combined":
        renderer._render_hwcombined(data, T0, end, PERIOD, hw_styles={})
        ax = renderer.ax_download
    elif layout == "separate":
        renderer._render_hwseparate(data, T0, end, PERIOD, hw_styles={})
        ax = renderer.ax_cpu
    else:
        renderer._render_hwsingle(data["cpu"], T0, end, PERIOD, "cpu", hw_styles={})
        ax = renderer.ax_download
    cpu = _measured(ax, renderer._hw_role_label("cpu"))
    assert len(cpu) == 2
    assert not any(_spans_gap(ln) for ln in ax.get_lines())


def test_combined_legend_has_one_entry_per_role(renderer):
    data = {"cpu": CHUNK_A + CHUNK_B, "gpu": CHUNK_A + CHUNK_B}
    renderer._render_hwcombined(data, T0, CHUNK_B[-1][0], PERIOD, hw_styles={})
    texts = [t.get_text() for t in renderer.ax_download.get_legend().get_texts()]
    assert texts == [renderer._hw_role_label("cpu"), renderer._hw_role_label("gpu")]


def test_smoothing_never_crosses_a_gap(renderer):
    renderer._render_hwsingle(CHUNK_A + CHUNK_B, T0, CHUNK_B[-1][0], PERIOD, "cpu",
                              hw_styles={"smoothing": True, "smooth_window": 9})
    a, b = _measured(renderer.ax_download, renderer._hw_role_label("cpu"))
    assert np.allclose(a.get_ydata(), 60.0) and np.allclose(b.get_ydata(), 20.0)


def test_contiguous_hardware_series_is_one_line(renderer):
    renderer._render_hwsingle(CHUNK_A, T0, CHUNK_A[-1][0], PERIOD, "cpu", hw_styles={})
    assert len(_measured(renderer.ax_download, renderer._hw_role_label("cpu"))) == 1


def test_one_gap_rule_for_both_graphs():
    ts = np.array([0, 60, 120, 180, 3600 * 5, 3600 * 5 + 60], dtype=float)
    assert GraphRenderer._gap_threshold(ts) == 150.0          # median 60 s x 2.5
    assert [(s.start, s.stop) for s in GraphRenderer._gap_segments(ts)] == [(0, 4), (4, 6)]
    assert GraphRenderer._gap_segments([]) == []
