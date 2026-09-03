"""
GraphHoverTooltip - the Monitor graph's lightweight hover readout (ported feature: the one thing the
old Graph window had that the Monitor lacked). The mouse-snap path needs a live Qt canvas, so that is
covered by the GUI smoke test; here we cover everything canvas-free: which lines the tooltip reads,
how a series drawn in several segments collapses to one row, and the formatting - the network graph
as speed in the user's unit, the hardware graphs as percent, timestamp and series name shown.

Two 2.1.5 fixes are pinned here. The network axes are plotted in Mbps (the renderer converts
bytes/sec before plotting) while `format_speed()` takes bytes/sec; the tooltip fed one to the other,
reading 40 Mbps as 40 B/s. Nobody saw it because no network line carried a label, so the hover never
fired at all on that tab. The test that used to assert the bytes/sec contract asserted the bug.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import matplotlib.dates as mdates
import pytest
from matplotlib.figure import Figure

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip


def _host(stat, **cfg):
    config = {"unit_type": "bits_decimal", "decimal_places": 1}
    config.update(cfg)
    return SimpleNamespace(_current_stat=stat, config=config, i18n=I18nStrings("en_US"))


# ----------------------------------------------------------------------------- formatting

def test_network_hover_formats_the_plotted_mbps_as_speed():
    t = GraphHoverTooltip(_host("network"))
    x = mdates.date2num(datetime(2026, 6, 28, 14, 30, 0))
    html = t._format(x, [("Download", 40.0, "#42B883")])      # 40 Mbps, as the axis carries it
    assert "14:30:00" in html
    assert "Download" in html and "Mbps" in html
    assert "40.0" in html
    assert "320" not in html                                   # the old readout: 40 B/s -> 320 bps


def test_network_hover_honours_the_bytes_unit_setting():
    t = GraphHoverTooltip(_host("network", unit_type="bytes_decimal"))
    x = mdates.date2num(datetime(2026, 6, 28, 14, 30, 0))
    html = t._format(x, [("Download", 40.0, "#42B883")])      # 40 Mbps == 5 MB/s
    assert "5.0" in html and "MB/s" in html


def test_hardware_hover_formats_percent():
    t = GraphHoverTooltip(_host("hwcombined"))
    x = mdates.date2num(datetime(2026, 6, 28, 9, 5, 0))
    html = t._format(x, [("CPU", 42.0, "#00BCD4"), ("RAM", 67.0, "#4CAF50")])
    assert "09:05:00" in html
    assert "CPU" in html and "42%" in html
    assert "RAM" in html and "67%" in html             # multiple series at the cursor time


# ----------------------------------------------------------------------------- which lines are read

T0 = datetime(2026, 6, 28, 12, 0, 0)


def _axes_with(lines):
    """A real matplotlib Axes (bare Figure, no Qt) with datetime-plotted lines, the way the renderer
    draws them. The hover must read x in axis units - `event.xdata` is a date number, and the
    lines' original data are datetimes, which cannot be compared to it."""
    fig = Figure()
    ax = fig.add_subplot(111)
    for label, start, ys, style in lines:
        xs = [start + timedelta(seconds=i) for i in range(len(ys))]
        ax.plot(xs, ys, label=label, **style)
    return ax


def test_rows_at_reads_datetime_lines_and_skips_unlabelled_helpers():
    ax = _axes_with([
        ("Download", T0, [1.0, 2.0, 3.0, 4.0], {}),
        ("Upload", T0, [0.5, 0.6, 0.7, 0.8], {}),
        ("_bridge", T0, [0.0, 0.0, 0.0, 0.0], {"linestyle": "--"}),   # the renderer's gap bridge
    ])
    x = mdates.date2num(T0 + timedelta(seconds=2))
    rows, nearest = GraphHoverTooltip(_host("network"))._rows_at(ax, x)
    assert {r[0]: r[1] for r in rows} == {"Download": 3.0, "Upload": 0.7}
    assert nearest == pytest.approx(x)


def test_rows_at_collapses_a_segmented_series_to_the_segment_under_the_cursor():
    """With gaps in the data the renderer plots one series as several segments sharing a label.
    The tooltip shows that series once, from the segment the cursor is on - not once per segment."""
    ax = _axes_with([
        ("Download", T0, [1.0, 1.0, 1.0], {}),
        ("Download", T0 + timedelta(seconds=10), [9.0, 9.0, 9.0], {}),
    ])
    ax.set_xlim(mdates.date2num(T0), mdates.date2num(T0 + timedelta(seconds=13)))
    x = mdates.date2num(T0 + timedelta(seconds=11))
    rows, _ = GraphHoverTooltip(_host("network"))._rows_at(ax, x)
    assert [(r[0], r[1]) for r in rows] == [("Download", 9.0)]


def test_rows_at_returns_nothing_far_from_any_sample():
    ax = _axes_with([("Download", T0, [1.0, 2.0, 3.0], {})])
    ax.set_xlim(mdates.date2num(T0), mdates.date2num(T0 + timedelta(minutes=10)))
    x = mdates.date2num(T0 + timedelta(minutes=5))
    assert GraphHoverTooltip(_host("network"))._rows_at(ax, x) == ([], None)
