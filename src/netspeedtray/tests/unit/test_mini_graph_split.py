"""
Separate upload/download mini graphs (DU Meter-style), each on its own scale.

Where the panels go depends on the taskbar and the display mode:
- Network Only: two panels - side by side on a horizontal taskbar (wider), stacked on a side one (taller).
- Side-by-Side: on a horizontal taskbar the network segment becomes the two panels and CPU/GPU follow;
  on a side taskbar it is left exactly as it is (that layout already clips CPU/GPU - a separate issue).
- Auto-Cycle: upload and download become phases of their own, so the widget keeps its size.

These tests drive the real render_widget path into offscreen images and probe the painted pixels, plus
the layout manager's sizing and the cycle rotation.
"""
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray.utils.widget_paint import (SPLIT_PHASES, WidgetMetrics, _resolve_mode, font_from_config,
                                             render_widget)
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.views.widget.main import NetworkSpeedWidget
from netspeedtray.views.widget.preview import PreviewWidget

MBPS_IN_BYTES = 125_000
GAP = constants.renderer.GRAPH_SPLIT_GAP
UP_RGB = tuple(QColor(constants.graph.UPLOAD_LINE_COLOR).getRgb()[:3])
DOWN_RGB = tuple(QColor(constants.graph.DOWNLOAD_LINE_COLOR).getRgb()[:3])

# A video call's upload (1.5-4.5 Mbps) next to a 90 Mbps download: on one shared axis the upload is a
# flat line along the bottom.
HISTORY = [AggregatedSpeedData(upload=(3.0 + 1.5 * ((i % 20) / 10 - 1)) * MBPS_IN_BYTES,
                               download=(90.0 if i >= 30 else 1.0) * MBPS_IN_BYTES,
                               timestamp=datetime(2026, 1, 1) + timedelta(seconds=i))
           for i in range(60)]


def _config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"graph_enabled": True, "graph_opacity": 100, "widget_display_mode": "network_only"})
    cfg.update(overrides)
    return cfg


def _metrics(**extra):
    last = HISTORY[-1]
    return WidgetMetrics(upload_mbps=last.upload / MBPS_IN_BYTES, download_mbps=last.download / MBPS_IN_BYTES,
                         cpu_usage=35.0, gpu_usage=50.0, net_history=HISTORY, **extra)


def _panel_width(cfg):
    m = _metrics()
    up, dw = m.net_bytes()
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    return r.network_panel_width(up, dw, r.config)


def _render(cfg, w, h, layout_mode="horizontal", cycle_mode="network_only", network_width=None, metrics=None):
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    render_widget(painter, QRect(0, 0, w, h), renderer, renderer.config, metrics or _metrics(), layout_mode=layout_mode,
                  cycle_mode=cycle_mode, network_width=network_width, font=font_from_config(cfg))
    painter.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    return np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy(), renderer


def _where(arr, rgb):
    """(rows, cols) of solid stroke pixels in exactly that series color."""
    mask = (arr[:, :, 0] == rgb[0]) & (arr[:, :, 1] == rgb[1]) & (arr[:, :, 2] == rgb[2]) & (arr[:, :, 3] == 255)
    return np.nonzero(mask)


# --- Scale per panel --------------------------------------------------------------------------------

def test_each_panel_auto_scales_to_its_own_direction():
    rc = RenderConfig.from_dict(_config())
    both, _ = WidgetRenderer._network_graph_scale(HISTORY, rc, "both")
    up, _ = WidgetRenderer._network_graph_scale(HISTORY, rc, "upload")
    assert up < both / 10   # the upload no longer shares the 90 Mbps axis
    assert up == pytest.approx(max(d.upload for d in HISTORY) * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR)


def test_fixed_scale_gives_each_panel_its_own_plan_speed():
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=100, plan_up_mbps=20))
    assert WidgetRenderer._network_graph_scale(HISTORY, rc, "upload")[1] == pytest.approx(20 * MBPS_IN_BYTES)
    assert WidgetRenderer._network_graph_scale(HISTORY, rc, "download")[1] == pytest.approx(100 * MBPS_IN_BYTES)
    assert WidgetRenderer._network_graph_scale(HISTORY, rc, "both")[1] == pytest.approx(100 * MBPS_IN_BYTES)


def test_a_direction_without_a_plan_speed_stays_automatic():
    rc = RenderConfig.from_dict(_config(graph_fixed_scale=True, plan_down_mbps=100))
    assert WidgetRenderer._network_graph_scale(HISTORY, rc, "upload")[1] is None
    assert WidgetRenderer._network_graph_scale(HISTORY, rc, "download")[1] == pytest.approx(100 * MBPS_IN_BYTES)


# --- Network Only ------------------------------------------------------------------------------------

def test_horizontal_taskbar_puts_the_panels_side_by_side(q_app):
    cfg = _config(graph_split_directions=True)
    pw = _panel_width(cfg)
    arr, _ = _render(cfg, 2 * pw + GAP, 48, network_width=pw)
    _, up_x = _where(arr, UP_RGB)
    _, down_x = _where(arr, DOWN_RGB)
    assert up_x.size and down_x.size
    assert up_x.max() < pw <= pw + GAP <= down_x.min()


def test_swap_puts_download_first(q_app):
    cfg = _config(graph_split_directions=True, swap_upload_download=True)
    pw = _panel_width(cfg)
    arr, _ = _render(cfg, 2 * pw + GAP, 48, network_width=pw)
    _, up_x = _where(arr, UP_RGB)
    _, down_x = _where(arr, DOWN_RGB)
    assert down_x.max() < pw <= pw + GAP <= up_x.min()


def test_vertical_taskbar_stacks_the_panels(q_app):
    h = 2 * 40 + GAP
    arr, _ = _render(_config(graph_split_directions=True), 140, h, layout_mode="vertical")
    up_y, _ = _where(arr, UP_RGB)
    down_y, _ = _where(arr, DOWN_RGB)
    top = (h - GAP) // 2
    assert up_y.size and down_y.size
    assert up_y.max() < top <= top + GAP <= down_y.min()


def test_the_upload_panel_uses_its_whole_height(q_app):
    """On the shared axis a 3 Mbps upload under a 90 Mbps download hugs the bottom; in its own panel it
    climbs near the top - the point of separating them."""
    cfg = _config(graph_split_directions=True)
    pw = _panel_width(cfg)
    split, _ = _render(cfg, 2 * pw + GAP, 48, network_width=pw)
    combined, _ = _render(_config(), pw, 48, network_width=pw)
    assert _where(split, UP_RGB)[0].min() < 12
    assert _where(combined, UP_RGB)[0].min() > 36


def test_the_wifi_badge_widens_only_the_last_panel(q_app):
    """Review on #336: the badge is drawn once, in the last panel, so only that panel reserves its width -
    the first panel's graph runs right up to the divider instead of leaving a badge-wide blank."""
    cfg = _config(graph_split_directions=True)
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    reserve = renderer.identity_reserve("5G", None)
    assert reserve > 20   # well above the tolerances below, so a badge-wide blank would show
    block = _panel_width(cfg) + reserve            # the layout's network width, badge included
    width = 2 * block - reserve + GAP
    arr, _ = _render(cfg, width, 48, network_width=block, metrics=_metrics(identity_band="5G"))
    _, up_x = _where(arr, UP_RGB)
    _, down_x = _where(arr, DOWN_RGB)
    first = block - reserve
    assert first - 8 <= up_x.max() < first          # the upload graph reaches the end of its panel
    assert down_x.min() >= first + GAP and down_x.max() >= width - 8


def test_split_needs_the_mini_graph(q_app):
    """With the mini graph off the toggle does nothing: the readout renders exactly as without it."""
    on, _ = _render(_config(graph_enabled=False, graph_split_directions=True), 200, 48, network_width=90)
    off, _ = _render(_config(graph_enabled=False), 200, 48, network_width=90)
    assert np.array_equal(on, off)


def test_off_by_default():
    assert constants.config.defaults.DEFAULT_CONFIG["graph_split_directions"] is False
    assert RenderConfig.from_dict(_config()).graph_split_directions is False


# --- Side-by-Side -------------------------------------------------------------------------------------

def test_horizontal_side_by_side_shifts_cpu_gpu_by_one_panel(q_app):
    base = dict(widget_display_mode="side_by_side", monitor_cpu_enabled=True, monitor_gpu_enabled=True)
    pw = _panel_width(_config(**base))
    _, today = _render(_config(**base), 600, 48, network_width=pw)
    _, split = _render(_config(graph_split_directions=True, **base), 600, 48, network_width=pw)
    grown = split.get_content_bounds().width() - today.get_content_bounds().width()
    assert abs(grown - (pw + GAP)) <= 2, grown


def test_vertical_side_by_side_is_left_as_it_is(q_app):
    base = dict(widget_display_mode="side_by_side", monitor_cpu_enabled=True, monitor_gpu_enabled=True)
    on, _ = _render(_config(graph_split_directions=True, **base), 140, 40, layout_mode="vertical")
    off, _ = _render(_config(**base), 140, 40, layout_mode="vertical")
    assert np.array_equal(on, off)


# --- Auto-Cycle ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("layout_mode,w,h", [("horizontal", 90, 48), ("vertical", 140, 40)])
def test_cycle_phases_show_one_direction_each(q_app, layout_mode, w, h):
    cfg = _config(widget_display_mode="cycle", monitor_cpu_enabled=True, graph_split_directions=True)
    up_phase, _ = _render(cfg, w, h, layout_mode=layout_mode, cycle_mode="network_upload")
    down_phase, _ = _render(cfg, w, h, layout_mode=layout_mode, cycle_mode="network_download")
    assert _where(up_phase, UP_RGB)[0].size and not _where(up_phase, DOWN_RGB)[0].size
    assert _where(down_phase, DOWN_RGB)[0].size and not _where(down_phase, UP_RGB)[0].size


def test_a_phase_from_before_the_toggle_maps_onto_the_current_set():
    on = RenderConfig.from_dict(_config(widget_display_mode="cycle", graph_split_directions=True))
    swapped = RenderConfig.from_dict(_config(widget_display_mode="cycle", graph_split_directions=True,
                                             swap_upload_download=True))
    off = RenderConfig.from_dict(_config(widget_display_mode="cycle"))
    assert _resolve_mode(on, "network_only") == "network_upload"
    assert _resolve_mode(swapped, "network_only") == "network_download"
    assert _resolve_mode(off, "network_download") == "network_only"
    assert _resolve_mode(on, "cpu_only") == "cpu_only"


def _cycle_widget(**config):
    fake = types.SimpleNamespace()
    fake.config = {"widget_display_mode": "cycle", "monitor_cpu_enabled": True, "monitor_gpu_enabled": True,
                   **config}
    fake._cycle_index = 0
    fake._current_cycle_mode = "network_only"
    fake.update = MagicMock()
    return fake


def _rotation(fake, steps):
    seen = []
    for _ in range(steps):
        NetworkSpeedWidget._step_cycle(fake, 1)
        seen.append(fake._current_cycle_mode)
    return seen


def test_cycle_rotates_upload_and_download_as_their_own_phases():
    fake = _cycle_widget(graph_enabled=True, graph_split_directions=True)
    assert _rotation(fake, 4) == ["network_download", "cpu_only", "gpu_only", "network_upload"]


def test_cycle_rotation_follows_swap():
    fake = _cycle_widget(graph_enabled=True, graph_split_directions=True, swap_upload_download=True)
    assert _rotation(fake, 4) == ["network_upload", "cpu_only", "gpu_only", "network_download"]


def test_cycle_rotation_unchanged_without_split():
    assert _rotation(_cycle_widget(graph_enabled=True), 3) == ["cpu_only", "gpu_only", "network_only"]
    assert set(SPLIT_PHASES).isdisjoint(_rotation(_cycle_widget(graph_split_directions=True), 3))


# --- Layout sizing --------------------------------------------------------------------------------------

def _size(edge, badge=0, **config):
    """Size WidgetLayoutManager gives the widget on a 1440p screen with a 48 px taskbar on ``edge``."""
    widget = MagicMock()
    widget.config = {**constants.config.defaults.DEFAULT_CONFIG, **config}
    widget.i18n = I18nStrings("en_US")
    widget._identity_reserve_px = lambda fm: badge
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(QFont("Segoe UI", 9))
    lm.arrow_metrics = lm.metrics
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 2560, 1440)
    if edge == constants.TaskbarEdge.BOTTOM:
        screen.availableGeometry.return_value = QRect(0, 0, 2560, 1392)
    else:  # a 140 px side taskbar on the left
        screen.availableGeometry.return_value = QRect(140, 0, 2420, 1440)
    tb = MagicMock()
    tb.get_screen.return_value = screen
    tb.get_edge_position.return_value = edge
    tb.dpi_scale = 1.0
    tb.height = 48
    # The taskbar window itself (#343 caps the docked height by it): 48 px along the bottom, or
    # the 140 px side strip.
    tb.rect = (0, 1392, 2560, 1440) if edge == constants.TaskbarEdge.BOTTOM else (0, 0, 140, 1440)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    w, h = widget.setFixedSize.call_args[0]
    return w, h, getattr(lm, "_network_width", None)


BOTTOM, LEFT = constants.TaskbarEdge.BOTTOM, constants.TaskbarEdge.LEFT
SPLIT = dict(graph_enabled=True, graph_split_directions=True)
HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True)


def test_layout_horizontal_network_only_is_two_blocks_wide(q_app):
    w, h, block = _size(BOTTOM, widget_display_mode="network_only", graph_enabled=True)
    ws, hs, block_s = _size(BOTTOM, widget_display_mode="network_only", **SPLIT)
    assert block_s == block                    # _network_width still means one block
    assert ws == 2 * block + GAP and hs == h


def test_layout_counts_the_wifi_badge_once(q_app):
    _, _, block = _size(BOTTOM, badge=30, widget_display_mode="network_only", graph_enabled=True)
    ws, _, _ = _size(BOTTOM, badge=30, widget_display_mode="network_only", **SPLIT)
    assert ws == 2 * block - 30 + GAP


def test_layout_horizontal_side_by_side_grows_by_one_block(q_app):
    w, _, block = _size(BOTTOM, widget_display_mode="side_by_side", graph_enabled=True, **HW)
    ws, _, _ = _size(BOTTOM, widget_display_mode="side_by_side", **SPLIT, **HW)
    assert ws - w == pytest.approx(block + GAP, abs=1)


def test_layout_cycle_keeps_its_size(q_app):
    assert _size(BOTTOM, widget_display_mode="cycle", graph_enabled=True, **HW)[:2] == \
        _size(BOTTOM, widget_display_mode="cycle", **SPLIT, **HW)[:2]
    assert _size(LEFT, widget_display_mode="cycle", graph_enabled=True, **HW)[:2] == \
        _size(LEFT, widget_display_mode="cycle", **SPLIT, **HW)[:2]


def test_layout_vertical_network_only_is_two_panels_tall(q_app):
    w, h, _ = _size(LEFT, widget_display_mode="network_only", graph_enabled=True)
    ws, hs, _ = _size(LEFT, widget_display_mode="network_only", **SPLIT)
    assert ws == w == 140
    assert hs == 2 * h + GAP


def test_layout_vertical_side_by_side_unchanged(q_app):
    assert _size(LEFT, widget_display_mode="side_by_side", graph_enabled=True, **HW)[:2] == \
        _size(LEFT, widget_display_mode="side_by_side", **SPLIT, **HW)[:2]


def test_layout_split_without_the_graph_changes_nothing(q_app):
    assert _size(BOTTOM, widget_display_mode="network_only")[:2] == \
        _size(BOTTOM, widget_display_mode="network_only", graph_split_directions=True)[:2]


# --- Settings preview -------------------------------------------------------------------------------------

def test_settings_preview_grows_to_fit_the_split_side_by_side(q_app):
    base = _config(widget_display_mode="side_by_side", monitor_cpu_enabled=True, monitor_gpu_enabled=True,
                   monitor_ram_enabled=True, show_hardware_temps=True)
    today = PreviewWidget(base, I18nStrings("en_US"), width=300, height=40)
    split = PreviewWidget({**base, "graph_split_directions": True}, I18nStrings("en_US"), width=300, height=40)
    assert split.width() > today.width() >= 300
    small = PreviewWidget(_config(graph_split_directions=True), I18nStrings("en_US"), width=300, height=40)
    assert small.width() == 300   # never shrinks below the requested width


def test_settings_preview_refits_only_when_something_changed(q_app):
    """Review on #336: the fit paints a probe, so it runs on a real change - not on every update."""
    cfg = _config(graph_split_directions=True)
    with patch("netspeedtray.views.widget.preview.render_widget") as rw:
        preview = PreviewWidget(cfg, I18nStrings("en_US"), width=300, height=40)
        assert rw.call_count == 1
        preview.set_config(dict(cfg))
        assert rw.call_count == 1
        preview.set_config({**cfg, "widget_display_mode": "side_by_side"})
        assert rw.call_count == 2


def test_settings_preview_logs_a_failed_fit(q_app, caplog):
    """Review on #336: a failed fit keeps the current size but is logged, not swallowed."""
    with patch("netspeedtray.views.widget.preview.render_widget", side_effect=RuntimeError("boom")):
        with caplog.at_level("WARNING", logger="NetSpeedTray.PreviewWidget"):
            preview = PreviewWidget(_config(), I18nStrings("en_US"), width=300, height=40)
    assert preview.width() == 300
    assert any("boom" in r.getMessage() for r in caplog.records)
