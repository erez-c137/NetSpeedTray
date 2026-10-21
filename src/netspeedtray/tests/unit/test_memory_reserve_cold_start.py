"""
RAM/VRAM on a horizontal bar: the memory column is reserved on the FIRST resize.

The cpu_only / gpu_only / combined / cycle branches reserved the memory width only once a live
``ram_used`` existed, which is never true at the first resize, and nothing re-sizes when it arrives -
so every cold start clipped the memory (81 px reserved for a 145 px readout; also in 2.1.7). The fixed
" | 16.0/16.0G" reference was also short for a machine with >= 100 GB of RAM.

The widget is sized by the real WidgetLayoutManager with no memory reading yet, then painted by the
real render_widget with live memory, onto a canvas larger than the widget: nothing may land outside.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

EN = I18nStrings("en_US")
HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True, monitor_ram_enabled=True,
          monitor_vram_enabled=True)
MODES = {
    "combined": (dict(widget_display_mode="combined"), ["network_only"]),
    "combined_temps_power": (dict(widget_display_mode="combined", show_hardware_temps=True,
                                  show_hardware_power=True), ["network_only"]),
    "combined_text_labels": (dict(widget_display_mode="combined", hardware_label_style="text"),
                             ["network_only"]),
    "combined_memory_labels": (dict(widget_display_mode="combined", show_memory_labels=True),
                               ["network_only"]),
    "cpu_only": (dict(widget_display_mode="cpu_only"), ["network_only"]),
    "gpu_only": (dict(widget_display_mode="gpu_only"), ["network_only"]),
    "cycle": (dict(widget_display_mode="cycle"), ["network_only", "cpu_only", "gpu_only"]),
}


def _config(**over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "background_opacity": 0, "graph_enabled": False, **HW})
    cfg.update(over)
    return cfg


def _bottom_taskbar():
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale, tb.height = (0, 1032, 1920, 1080), 1, 1.0, 48
    tb.tasklist_rect = tb.widgets_rect = None
    tb.get_edge_position.return_value = constants.TaskbarEdge.BOTTOM
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (1700, 1032, 1900, 1080)
    return tb


def _cold_size(cfg, ram_total, vram_total=0.0):
    """Size the widget as at a cold start: totals may be known, no used reading yet."""
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = EN
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = None
    widget.ram_total, widget.vram_total = ram_total, vram_total
    widget.ram_used = widget.vram_used = None
    lm = WidgetLayoutManager(widget)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_bottom_taskbar()), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.set_font(resize=False)
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm._network_width


def _ink_outside(cfg, w, h, network_width, phase, metrics):
    renderer = WidgetRenderer(cfg, EN)
    img = QImage(3 * w, 3 * h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(w, h)
    # The paint path clips to the widget rect; a clip would hide exactly the overflow we look for.
    with patch.object(QPainter, "setClipRect", lambda *a, **k: None):
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, metrics, layout_mode="horizontal",
                      cycle_mode=phase, network_width=network_width, font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    assert (a[h:2 * h, w:2 * w] > 24).any(), "nothing painted"
    a[h:2 * h, w:2 * w] = 0
    ys, xs = np.nonzero(a > 24)
    return None if not len(xs) else (int(xs.min()) - w, int(xs.max()) - w, w)


@pytest.mark.parametrize("ram_total,ram_used", [(16.0, 15.9), (31.7, 30.2), (128.0, 127.9)],
                         ids=["16GB", "32GB", "128GB"])
@pytest.mark.parametrize("font_size", [7, 9, 11])
@pytest.mark.parametrize("mode", MODES)
def test_memory_fits_from_the_first_resize(q_app, mode, font_size, ram_total, ram_used):
    over, phases = MODES[mode]
    cfg = _config(font_size=font_size, **over)
    w, h, nw = _cold_size(cfg, ram_total)
    live = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=100.0, gpu_usage=100.0,
                         cpu_temp=99.0, gpu_temp=99.0, cpu_power=250.0, gpu_power=250.0,
                         ram_used=ram_used, ram_total=ram_total, vram_used=7.9, vram_total=8.0)
    for phase in phases:
        assert _ink_outside(cfg, w, h, nw, phase, live) is None, (mode, phase, w)


def test_vram_total_unknown_at_first_resize_still_fits_a_two_digit_card(q_app):
    """VRAM-only, total not known yet: the reference keeps a two-digit floor (a 12 GB card on a
    machine whose first resize knew nothing)."""
    cfg = _config(widget_display_mode="gpu_only", monitor_ram_enabled=False)
    w, h, nw = _cold_size(cfg, ram_total=0.0, vram_total=0.0)
    live = WidgetMetrics(upload_mbps=1.0, download_mbps=1.0, gpu_usage=100.0, vram_used=11.9, vram_total=12.0)
    assert _ink_outside(cfg, w, h, nw, "network_only", live) is None
