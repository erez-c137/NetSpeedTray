"""
Every line the Monitor graph draws for a measured series carries a legend label, and every helper
line (the dashed zero bridge across a gap) does not. The hover tooltip keys on exactly that:
`GraphHoverTooltip._rows_at` reads `line.get_label()` and skips matplotlib's auto `_childN`. Until
2.1.5 only the combined-hardware path passed `label=`, so the hover was dead on the Network tab and
on the other two Hardware layouts - silently, because the tooltip simply never appeared.

These go through the real GraphRenderer code paths onto real matplotlib axes (a bare Figure, no Qt
canvas) and inspect the artists, because a mocked `ax.plot` cannot tell a labelled line from an
unlabelled one the way matplotlib does. `_init_matplotlib` is replaced per test via monkeypatch -
never by class assignment, which leaks into every later test in the session.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import matplotlib.dates as mdates
import numpy as np
import pytest
from matplotlib.figure import Figure

from netspeedtray.constants import styles as style_constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip


def _bare_init(self):
    self.figure = Figure(figsize=(8, 6), dpi=100)
    self.canvas = MagicMock()
    gs = self.figure.add_gridspec(5, 1)
    self.ax_download = self.figure.add_subplot(gs[0])
    self.ax_upload = self.figure.add_subplot(gs[1])
    self.ax_cpu = self.figure.add_subplot(gs[2])
    self.ax_gpu = self.figure.add_subplot(gs[3])
    self.ax_ram = self.figure.add_subplot(gs[4])
    self.axes = [self.ax_download, self.ax_upload, self.ax_cpu, self.ax_gpu, self.ax_ram]
    self._is_dark_mode = False
    self._current_text_color = style_constants.LIGHT_MODE_TEXT_COLOR
    self._current_grid_color = style_constants.GRID_COLOR_LIGHT


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    return GraphRenderer(MagicMock(), I18nStrings("en_US"))


def _labels(ax):
    return [str(line.get_label()) for line in ax.get_lines()]


def _measured(ax):
    return [lbl for lbl in _labels(ax) if not lbl.startswith("_")]


T0 = datetime(2026, 6, 28, 12, 0, 0)
PERIOD = "TIMELINE_24_HOURS"


def _stamps(n, start=T0, step_s=1.0):
    return [start + timedelta(seconds=i * step_s) for i in range(n)]


# ----------------------------------------------------------------------------- network

def test_network_lines_are_labelled_download_and_upload(renderer):
    dts = _stamps(30)
    renderer._plot_high_res(dts, np.full(30, 2.0), np.full(30, 10.0))
    assert _measured(renderer.ax_download) == [renderer.i18n.DOWNLOAD_LABEL]
    assert _measured(renderer.ax_upload) == [renderer.i18n.UPLOAD_LABEL]


def test_gap_segments_are_labelled_and_the_bridge_is_not(renderer):
    """Two chunks five minutes apart. Each measured segment carries the label; the dashed zero
    bridge the renderer draws across the gap does not, so the hover can never report it."""
    dts = _stamps(20) + _stamps(20, start=T0 + timedelta(minutes=5))
    renderer._plot_high_res(dts, np.full(40, 1.0), np.full(40, 10.0))
    lines = renderer.ax_download.get_lines()
    measured = [line for line in lines if not str(line.get_label()).startswith("_")]
    helpers = [line for line in lines if str(line.get_label()).startswith("_")]
    assert len(measured) >= 2
    assert {str(line.get_label()) for line in measured} == {renderer.i18n.DOWNLOAD_LABEL}
    assert helpers, "the gap bridge should be drawn, unlabelled"
    assert all(line.get_linestyle() == "--" for line in helpers)


# ----------------------------------------------------------------------------- hardware

def _hw_rows(n=30, value=50.0):
    return [(T0 + timedelta(seconds=i), value) for i in range(n)]


def test_hardware_separate_lines_carry_their_role_names(renderer):
    data = {"cpu": _hw_rows(), "gpu": _hw_rows(value=30.0), "ram": _hw_rows(value=70.0)}
    renderer._render_hwseparate(data, T0, T0 + timedelta(seconds=30), PERIOD, hw_styles={})
    assert _measured(renderer.ax_cpu) == [renderer._hw_role_label("cpu")]
    assert _measured(renderer.ax_gpu) == [renderer._hw_role_label("gpu")]
    assert _measured(renderer.ax_ram) == [renderer._hw_role_label("ram")]


def test_hardware_single_line_carries_its_role_name(renderer):
    renderer._render_hwsingle(_hw_rows(), T0, T0 + timedelta(seconds=30), PERIOD, "gpu", hw_styles={})
    assert _measured(renderer.ax_download) == [renderer._hw_role_label("gpu")]


def test_hardware_combined_uses_the_same_names(renderer):
    data = {"cpu": _hw_rows(), "gpu": _hw_rows(value=30.0)}
    renderer._render_hwcombined(data, T0, T0 + timedelta(seconds=30), PERIOD, hw_styles={})
    assert _measured(renderer.ax_download) == [renderer._hw_role_label("cpu"), renderer._hw_role_label("gpu")]


def test_role_names_are_the_localized_ui_strings(renderer):
    i18n = renderer.i18n
    assert renderer._hw_role_label("cpu") == i18n.ORDER_TYPE_CPU
    assert renderer._hw_role_label("gpu") == i18n.ORDER_TYPE_GPU
    assert renderer._hw_role_label("ram") == i18n.MONITOR_TILE_RAM
    assert renderer._hw_role_label("vram") == "VRAM"       # anything unmapped: readable, never `_child`


# ----------------------------------------------------------------------------- end to end: render() -> hover

# The tests above call the plot helpers directly. These go through the entry point the Monitor's
# GraphHost uses - `render(..., stat_type=..., hw_styles=...)` - and then ask the real hover lookup
# what it would show. A change in render()'s dispatch that routes a layout to an unlabelled plot call
# (the #281 bug) fails here even if every helper is still labelled.

def _hover_rows(renderer, stat, at):
    """What GraphHoverTooltip._rows_at returns on every visible axis, for the cursor at `at`."""
    host = SimpleNamespace(_current_stat=stat, config={}, i18n=renderer.i18n)
    hover = GraphHoverTooltip(host)
    x = mdates.date2num(at)
    return [[(lbl, round(y, 2)) for lbl, y, _c in hover._rows_at(ax, x)[0]]
            for ax in renderer.figure.axes if ax.get_visible()]


def _net_rows(n, gap_after=None):
    """(epoch, upload B/s, download B/s) rows: 100 kB/s up (0.8 Mbps), 5 MB/s down (40 Mbps)."""
    rows = []
    for i in range(n):
        shift = 300 if gap_after is not None and i > gap_after else 0
        rows.append(((T0 + timedelta(seconds=i + shift)).timestamp(), 1e5, 5e6))
    return rows


@pytest.mark.parametrize("rows", [_net_rows(60), _net_rows(60, gap_after=30)],
                         ids=["continuous", "with-gap"])
def test_network_render_gives_the_hover_both_series(renderer, rows):
    renderer.render(rows, T0, T0 + timedelta(seconds=400), PERIOD, stat_type="network",
                    force_rebuild=True)
    got = _hover_rows(renderer, "network", T0 + timedelta(seconds=10))
    assert got == [[(renderer.i18n.DOWNLOAD_LABEL, 40.0)], [(renderer.i18n.UPLOAD_LABEL, 0.8)]]


def test_network_live_refresh_keeps_the_hover_alive(renderer):
    """Later ticks update the existing lines in place (set_data); the label must survive that."""
    renderer.render(_net_rows(60), T0, T0 + timedelta(seconds=60), PERIOD, stat_type="network",
                    force_rebuild=True)
    renderer.render(_net_rows(61), T0, T0 + timedelta(seconds=61), PERIOD, stat_type="network")
    got = _hover_rows(renderer, "network", T0 + timedelta(seconds=10))
    assert got == [[(renderer.i18n.DOWNLOAD_LABEL, 40.0)], [(renderer.i18n.UPLOAD_LABEL, 0.8)]]


@pytest.mark.parametrize("stat,data,expected", [
    ("cpu", _hw_rows(value=40.0), [[("cpu", 40.0)]]),
    ("gpu", _hw_rows(value=30.0), [[("gpu", 30.0)]]),
    ("hwcombined", {"cpu": _hw_rows(value=40.0), "gpu": _hw_rows(value=30.0)},
     [[("cpu", 40.0), ("gpu", 30.0)]]),
    ("hwseparate", {"cpu": _hw_rows(value=40.0), "gpu": _hw_rows(value=30.0),
                    "ram": _hw_rows(value=70.0)},
     [[("cpu", 40.0)], [("gpu", 30.0)], [("ram", 70.0)]]),
])
def test_hardware_render_gives_the_hover_every_series(renderer, stat, data, expected):
    """Every Hardware-tab layout the Monitor can pick (combined, separate, CPU/GPU toggle). The
    Monitor always passes hw_styles, which is what routes the toggle to the labelled path."""
    renderer.render(data, T0, T0 + timedelta(seconds=30), PERIOD, stat_type=stat,
                    force_rebuild=True, hw_styles={})
    got = _hover_rows(renderer, stat, T0 + timedelta(seconds=10))
    assert got == [[(renderer._hw_role_label(role), v) for role, v in axis] for axis in expected]
