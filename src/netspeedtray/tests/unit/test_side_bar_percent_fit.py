"""
Small (32 px) side taskbar at 11 pt: the compact CPU/GPU rows never clip "100%".

There the icon goes over the value (the inline row doesn't fit), but the stacked "100%" was itself 1-4
px wider than the bar, so it was clipped at both ends in cpu_only / combined / every Auto-Cycle
hardware phase. side_bar_fit now drops the "%" sign when even the stacked "100%" doesn't fit
(``hw_percent_sign``): "100" under its CPU/GPU icon, whole.

Sized by the real WidgetLayoutManager on a mocked RIGHT taskbar and painted by the real render_widget
(clip disabled) onto a larger canvas: nothing may land outside the widget.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget, side_bar_fit
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

EN = I18nStrings("en_US")
HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True)
MODES = {
    "cpu_only": (dict(widget_display_mode="cpu_only"), ["network_only"]),
    "combined": (dict(widget_display_mode="combined"), ["network_only"]),
    "combined_text_labels": (dict(widget_display_mode="combined", hardware_label_style="text"), ["network_only"]),
    "cycle": (dict(widget_display_mode="cycle"), ["network_only", "cpu_only", "gpu_only"]),
}
LIVE = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=100.0, gpu_usage=100.0)


def _config(**over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "background_opacity": 0, "graph_enabled": False, **HW})
    cfg.update(over)
    return cfg


def _right_taskbar(thickness):
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920 - thickness, 1080)
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale, tb.height = (1920 - thickness, 0, 1920, 1080), 1, 1.0, thickness
    tb.tasklist_rect = tb.widgets_rect = None
    tb.get_edge_position.return_value = constants.TaskbarEdge.RIGHT
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (1920 - thickness, 908, 1920, 1080)
    return tb


def _size(cfg, thickness):
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = EN
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = None
    widget.ram_total = widget.vram_total = 0.0
    widget.ram_used = widget.vram_used = None
    lm = WidgetLayoutManager(widget)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_right_taskbar(thickness)), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.set_font(resize=False)
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm


def _ink_outside(cfg, w, h, phase):
    renderer = WidgetRenderer(cfg, EN)
    img = QImage(3 * w, 3 * h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(w, h)
    with patch.object(QPainter, "setClipRect", lambda *a, **k: None):
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, LIVE, layout_mode="vertical",
                      cycle_mode=phase, network_width=None, font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    assert (a[h:2 * h, w:2 * w] > 24).any(), "nothing painted"
    a[h:2 * h, w:2 * w] = 0
    xs = np.nonzero(a > 24)[1]
    return None if not len(xs) else (int(xs.min()) - w, int(xs.max()) - w)


# Every size where at least "100" fits: the drop case is 11 pt under Segoe UI / the Windows platform,
# 7 pt under the wider offscreen fallback font CI renders with.
@pytest.mark.parametrize("font_size", range(constants.fonts.FONT_SIZE_MIN, 13))
@pytest.mark.parametrize("mode", MODES)
def test_compact_hardware_rows_never_clip_on_a_small_side_bar(q_app, mode, font_size):
    over, phases = MODES[mode]
    cfg = _config(font_size=font_size, **over)
    w, h, lm = _size(cfg, 32)
    inner = w - 2 * constants.renderer.TEXT_MARGIN
    if lm.metrics.horizontalAdvance("100") > inner:
        pytest.skip("this font is too wide even for '100' at 32 px (the ladder clips by design)")
    for phase in phases:
        assert _ink_outside(cfg, w, h, phase) is None, (mode, phase, font_size, w)


def _fit(cfg, width):
    lm = _size(cfg, width)[2]
    return side_bar_fit(RenderConfig.from_dict(cfg), lm.metrics, lm.arrow_metrics, EN, width)


@pytest.mark.parametrize("width", [32, 48])
@pytest.mark.parametrize("font_size", range(constants.fonts.FONT_SIZE_MIN, 13))
def test_percent_sign_drops_only_when_the_stacked_percent_cannot_fit(q_app, width, font_size):
    cfg = _config(font_size=font_size, widget_display_mode="combined")
    fit = _fit(cfg, width)
    fm = _size(cfg, width)[2].metrics
    inner = width - 2 * constants.renderer.TEXT_MARGIN
    assert fit.hw_percent_sign == (fit.hw_inline or fm.horizontalAdvance("100%") <= inner)


def test_draw_hardware_compact_without_the_sign_draws_the_bare_number(q_app):
    cfg = _config(font_size=9)
    renderer = WidgetRenderer(cfg, EN)
    drawn = []
    painter = MagicMock()
    painter.drawText.side_effect = lambda *a: drawn.append(a[-1])
    renderer.draw_hardware_compact(painter, [("CPU", 100.0), ("GPU", 7.0)], 32, 80, renderer.config,
                                   inline=False, percent_sign=False)
    assert "100" in drawn and "7" in drawn and not any("%" in str(t) for t in drawn)
