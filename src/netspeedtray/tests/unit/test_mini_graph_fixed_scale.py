"""
Fixed y-axis for the taskbar mini graph (DU Meter-style).

The auto-scale re-fits the axis to the busiest sample in the window, so a 1 Mbps background trickle
fills the graph as tall as a 100 Mbps download once the download scrolls out - the only way to tell
them apart is to read the numbers. With graph_fixed_scale on, the axis tops out at the advertised plan
speed (plan_down_mbps / plan_up_mbps) plus a headroom, and an optional dotted line marks the plan
speed itself. These tests pin the scale math and probe the painted pixels.
"""
from datetime import datetime, timedelta

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.tests.color_helpers import themed
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer

MBPS_IN_BYTES = 125_000  # 1 Mbps in bytes/sec - the unit the graph history is kept in

W, H = 200, 40
# graph_rect = QRect(GRAPH_LEFT_PADDING, GRAPH_MARGIN, W - 2*pad, H - (GRAPH_MARGIN + GRAPH_BOTTOM_PADDING))
GRAPH_TOP = constants.renderer.GRAPH_MARGIN
GRAPH_H = H - (constants.renderer.GRAPH_MARGIN + constants.renderer.GRAPH_BOTTOM_PADDING)
GRAPH_BOTTOM = GRAPH_TOP + GRAPH_H - 1   # QRect.bottom()

LINE_RGB = (255, 0, 255)   # default_color in these tests - distinct from both series colors


def _history(mbps_values):
    t0 = datetime(2026, 1, 1)
    return [AggregatedSpeedData(upload=v * MBPS_IN_BYTES, download=v * MBPS_IN_BYTES,
                                timestamp=t0 + timedelta(seconds=i))
            for i, v in enumerate(mbps_values)]


def _config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"graph_enabled": True, "graph_opacity": 100, "default_color": "#FF00FF"})
    cfg.update(overrides)
    return themed(cfg)


def _render(cfg, history):
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)   # as the widget paints
    renderer.draw_mini_graph(painter, W, H, renderer.config, history)
    painter.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    return np.frombuffer(ptr, dtype=np.uint8).reshape((H, W, 4)).copy()


def _line_rows(arr):
    """Rows holding ceiling-line pixels (the magenta default_color)."""
    mask = (arr[:, :, 0] == LINE_RGB[0]) & (arr[:, :, 1] == LINE_RGB[1]) & (arr[:, :, 2] == LINE_RGB[2]) \
        & (arr[:, :, 3] > 0)
    return {int(r): int(mask[r].sum()) for r in np.nonzero(mask.any(axis=1))[0]}


def _topmost_painted_row(arr, min_alpha=60):
    rows = np.nonzero((arr[:, :, 3] >= min_alpha).any(axis=1))[0]
    return int(rows.min()) if rows.size else None


# --- Scale math (no painting) ---------------------------------------------------------------------

def test_auto_scale_is_still_the_default():
    rc = RenderConfig.from_dict(_config(plan_down_mbps=100))
    max_y, ceiling = WidgetRenderer._network_graph_scale(_history([1, 2, 4]), rc)
    assert ceiling is None
    assert max_y == pytest.approx(4 * MBPS_IN_BYTES * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR)


def test_fixed_scale_without_a_plan_speed_stays_automatic():
    on = RenderConfig.from_dict(_config(graph_fixed_scale=True))
    off = RenderConfig.from_dict(_config())
    history = _history([1, 2, 4])
    assert WidgetRenderer._network_graph_scale(history, on) == WidgetRenderer._network_graph_scale(history, off)


def test_fixed_scale_tops_out_at_plan_plus_headroom():
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=100, plan_up_mbps=20,
                                        graph_scale_headroom=20))
    max_y, ceiling = WidgetRenderer._network_graph_scale(_history([1, 2, 4]), rc)
    assert ceiling == pytest.approx(100 * MBPS_IN_BYTES)
    assert max_y == pytest.approx(100 * MBPS_IN_BYTES * 1.2)


def test_fixed_scale_ignores_the_traffic():
    """The whole point: the axis doesn't move with the data, so equal heights mean equal speeds."""
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=100))
    idle = WidgetRenderer._network_graph_scale(_history([0.1] * 30), rc)
    busy = WidgetRenderer._network_graph_scale(_history([0.1] * 15 + [95] * 15), rc)
    assert idle == busy


def test_fixed_scale_uses_the_larger_plan_direction():
    """Upload and download share one axis, so the ceiling is the faster of the two plan speeds."""
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=50, plan_up_mbps=300))
    _max_y, ceiling = WidgetRenderer._network_graph_scale(_history([1, 1]), rc)
    assert ceiling == pytest.approx(300 * MBPS_IN_BYTES)


def test_zero_headroom_tops_out_at_the_plan_speed():
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=100, graph_scale_headroom=0))
    max_y, ceiling = WidgetRenderer._network_graph_scale(_history([1, 1]), rc)
    assert max_y == pytest.approx(ceiling)


# --- Painted pixels -------------------------------------------------------------------------------

def test_idle_traffic_no_longer_fills_the_graph(q_app):
    """1 Mbps of background traffic: auto-scale paints it near the top; fixed to a 100 Mbps plan it
    hugs the baseline, where a reader expects 1% of the line to be."""
    history = _history([1] * 30)
    auto_top = _topmost_painted_row(_render(_config(plan_down_mbps=100), history))
    fixed_top = _topmost_painted_row(_render(_config(graph_fixed_scale=True, plan_down_mbps=100,
                                                     graph_show_ceiling=False), history))
    assert auto_top is not None and fixed_top is not None
    assert auto_top <= GRAPH_TOP + GRAPH_H // 3, auto_top
    assert fixed_top >= GRAPH_BOTTOM - 3, fixed_top


def test_plan_speed_line_sits_at_the_plan_speed(q_app):
    """20% headroom: the dotted line is drawn at 1/1.2 of the graph height, and nowhere else."""
    arr = _render(_config(graph_fixed_scale=True, plan_down_mbps=100, graph_scale_headroom=20),
                  _history([0] * 30))
    rows = _line_rows(arr)
    assert len(rows) == 1, rows   # one crisp 1px row, not smeared across two
    (row, dots), = rows.items()
    assert abs(row - (GRAPH_BOTTOM - GRAPH_H / 1.2)) <= 1, row
    # Dotted, not solid: well over a handful of dots, but clearly not the full width.
    assert 20 < dots < W * 0.8, dots


@pytest.mark.parametrize("overrides", [
    {"graph_scale_headroom": 0},                               # the plan speed IS the top edge
    {"graph_scale_headroom": 20, "graph_show_ceiling": False},  # turned off
    {"graph_fixed_scale": False, "graph_scale_headroom": 20},  # auto-scale has no plan line
])
def test_no_plan_speed_line(q_app, overrides):
    cfg = _config(graph_fixed_scale=True, plan_down_mbps=100)
    cfg.update(overrides)
    assert _line_rows(_render(cfg, _history([0] * 30))) == {}


def test_a_plan_speed_alone_changes_nothing(q_app):
    """Setting a plan speed (for the Statistics sheet) must not alter the graph unless the fixed scale
    is turned on - existing graphs render identically after an upgrade."""
    history = _history([0.5, 3, 12, 7, 1, 0, 40, 2] * 4)
    assert np.array_equal(_render(_config(), history), _render(_config(plan_down_mbps=100), history))


def test_a_burst_above_the_axis_stays_inside_the_graph(q_app):
    """A fixed axis can be outrun (e.g. 50 Mbps on a 10 Mbps plan); the curve is capped at the top
    edge - painting no higher than a burst of exactly plan + headroom - instead of running past it."""
    cfg = _config(graph_fixed_scale=True, plan_down_mbps=10, graph_show_ceiling=False)  # top = 11 Mbps
    over = _topmost_painted_row(_render(cfg, _history([1] * 10 + [50] * 5 + [1] * 10)), min_alpha=1)
    at_top = _topmost_painted_row(_render(cfg, _history([1] * 10 + [11] * 5 + [1] * 10)), min_alpha=1)
    assert over is not None and over > 0, over   # nothing in the widget's top row
    assert over == at_top, (over, at_top)
