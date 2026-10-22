"""
Side (LEFT/RIGHT) taskbar + side_by_side + CPU/GPU.

On a side taskbar the widget is only as wide as the taskbar and two text rows tall, but side_by_side
still laid its segments out left-to-right, so the CPU/GPU stats were painted past the widget's right
edge and cut off. On a side taskbar the segments now stack top-to-bottom (network over CPU/GPU) and the
layout reserves the summed height; stacked CPU+GPU put their RAM/VRAM on rows of their own, not inline.

The widget is sized by the real WidgetLayoutManager (taskbar mocked on the RIGHT edge) and painted by
the real render_widget, onto a canvas larger than the widget, so anything painted outside it shows up.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

# Wide enough for any single segment even in CI's fallback font (wider than Segoe UI), so what is
# checked is where the segments go, not whether one long readout fits a narrow taskbar.
TB_WIDTH = 400

METRICS = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=37.0, gpu_usage=52.0,
                        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0)

CPU = QColor(constants.renderer.CPU_LINE_COLOR).getRgb()[:3]
GPU = QColor(constants.renderer.GPU_LINE_COLOR).getRgb()[:3]

CASES = {
    "cpu_and_gpu": dict(stack_hardware_stats=False),
    "stacked_hardware": dict(stack_hardware_stats=True),
    "with_memory": dict(stack_hardware_stats=False, monitor_ram_enabled=True, monitor_vram_enabled=True),
    "stacked_with_memory": dict(stack_hardware_stats=True, monitor_ram_enabled=True, monitor_vram_enabled=True),
}


def _config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({
        "free_float": False,
        "widget_display_mode": "side_by_side",
        "monitor_cpu_enabled": True,
        "monitor_gpu_enabled": True,
        "show_hardware_temps": False,
        "hardware_label_style": "text",   # CPU/GPU text labels, no icon assets
        "hardware_label_color": "custom",  # at its defaults == the CPU/GPU colours probed below
        "graph_enabled": False,
        "background_opacity": 0,
    })
    cfg.update(overrides)
    return cfg


def _side_taskbar_size(cfg):
    """(width, height) the layout manager gives the widget on a TB_WIDTH-wide RIGHT taskbar."""
    widget = MagicMock()
    widget.config = cfg
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(font_from_config(cfg))
    lm.arrow_metrics = lm.metrics

    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920 - TB_WIDTH, 1080)
    taskbar = MagicMock()
    taskbar.dpi_scale = 1.0
    taskbar.get_edge_position.return_value = constants.TaskbarEdge.RIGHT
    taskbar.get_screen.return_value = screen

    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=taskbar), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False):
        lm.resize_widget_for_font()
    (width, height), _ = widget.setFixedSize.call_args
    return width, height, lm.metrics.height()


def _paint(cfg, width, height):
    """Paint a width x height widget onto a 3x larger transparent canvas; RGBA array."""
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    img = QImage(width * 3, height * 3, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    render_widget(painter, QRect(0, 0, width, height), renderer, renderer.config, METRICS,
                  layout_mode="vertical", network_width=None, font=font_from_config(cfg))
    painter.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    # Copy: the array must outlive `img`, which owns the buffer.
    return np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4)).copy()


def _pixels_of(arr, rgb):
    """(ys, xs) of the solid glyph-core pixels painted in `rgb`."""
    solid = arr[:, :, 3] >= 250
    return np.nonzero(solid & np.all(arr[:, :, :3] == rgb, axis=2))


@pytest.mark.parametrize("overrides", CASES.values(), ids=CASES.keys())
def test_side_taskbar_stacks_every_segment_inside_the_widget(q_app, overrides):
    cfg = _config(**overrides)
    width, height, _ = _side_taskbar_size(cfg)
    arr = _paint(cfg, width, height)

    outside = arr[:, :, 3] > 0
    outside[:height, :width] = False
    ys, xs = np.nonzero(outside)
    assert not len(xs), (f"painted outside the {width}x{height} widget "
                         f"(x {xs.min()}..{xs.max()}, y {ys.min()}..{ys.max()})")

    cpu_ys, cpu_xs = _pixels_of(arr, CPU)
    gpu_ys, gpu_xs = _pixels_of(arr, GPU)
    assert len(cpu_xs) and len(gpu_xs), "the CPU/GPU labels were not painted"
    # Same left edge as the network readout, not to its right ...
    assert cpu_xs.min() < 8 and gpu_xs.min() < 8, (cpu_xs.min(), gpu_xs.min())
    # ... with the network above CPU, and CPU above GPU (widget_display_order).
    assert (arr[:cpu_ys.min(), :, 3] >= 250).sum() > 20, "nothing painted above the CPU row"
    assert gpu_ys.min() > cpu_ys.max(), (cpu_ys.max(), gpu_ys.min())


def test_side_taskbar_reserves_the_height_of_every_stacked_segment(q_app):
    margin = constants.renderer.TEXT_MARGIN
    gap = constants.layout.WIDGET_SEGMENT_GAP_STACKED_PX
    _, _, line = _side_taskbar_size(_config())
    network = 2 * line + 1   # draw_network_speeds: two rows + 1px

    # One band per segment: a lone CPU/GPU is one row (two with its memory on a row of its own),
    # the stacked hardware segment is two rows, plus a row per memory value. Same 4*margin padding.
    assert _side_taskbar_size(_config(stack_hardware_stats=False))[1] == \
        network + line + line + 2 * gap + 4 * margin
    assert _side_taskbar_size(_config(stack_hardware_stats=True))[1] == \
        network + 2 * line + gap + 4 * margin
    assert _side_taskbar_size(_config(stack_hardware_stats=False, monitor_ram_enabled=True,
                                      monitor_vram_enabled=True))[1] == \
        network + 2 * line + 2 * line + 2 * gap + 4 * margin
    assert _side_taskbar_size(_config(stack_hardware_stats=True, monitor_ram_enabled=True))[1] == \
        network + 3 * line + gap + 4 * margin
    assert _side_taskbar_size(_config(stack_hardware_stats=True, monitor_ram_enabled=True,
                                      monitor_vram_enabled=True))[1] == \
        network + 4 * line + gap + 4 * margin


def test_side_taskbar_puts_stacked_memory_on_rows_of_its_own(q_app):
    """Stacked CPU+GPU shows the memory inline ("37% (58°C) 9.2/16.0G"), a row wider than a side
    taskbar. There each value gets a row of its own under its stat instead, as it does when CPU and GPU
    are separate segments, so stacking them no longer widens the readout."""
    memory = dict(monitor_ram_enabled=True, monitor_vram_enabled=True, show_hardware_temps=True)

    cfg = _config(stack_hardware_stats=True, **memory)
    width, height, line = _side_taskbar_size(cfg)
    arr = _paint(cfg, width, height)
    cpu_ys, _ = _pixels_of(arr, CPU)
    gpu_ys, _ = _pixels_of(arr, GPU)
    # The RAM row sits between the CPU and GPU rows.
    assert abs(gpu_ys.min() - cpu_ys.min() - 2 * line) <= 1, (cpu_ys.min(), gpu_ys.min(), line)

    # The hardware rows (from the CPU row down; the network readout is above) reach no further right
    # stacked than as separate segments. Icons, so both have the same fixed label column; solid glyph
    # pixels only, as the background is not quite transparent.
    top = cpu_ys.min()
    right_edge = {}
    for stack in (True, False):
        cfg = _config(stack_hardware_stats=stack, hardware_label_style="icons", **memory)
        width, height, _ = _side_taskbar_size(cfg)
        arr = _paint(cfg, width, height)
        right_edge[stack] = np.nonzero((arr[top:, :, 3] >= 250).any(axis=0))[0].max()
    assert right_edge[True] <= right_edge[False], right_edge


def test_side_taskbar_places_a_tall_stacked_widget_above_the_tray(q_app):
    """The stack can outgrow the 100px height cap (5 rows at the largest font). Positioning by the
    capped height would sink the widget's bottom over the tray; it must sit above the tray."""
    from netspeedtray.core.position_manager import PositionCalculator
    from netspeedtray.utils.taskbar_utils import TaskbarInfo

    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920 - TB_WIDTH, 1080)
    taskbar = MagicMock(spec=TaskbarInfo)
    taskbar.hwnd = 12345
    taskbar.dpi_scale = 1.0
    taskbar.rect = (1920 - TB_WIDTH, 0, 1920, 1080)
    taskbar.tasklist_rect = None
    taskbar.get_tray_rect.return_value = (1920 - TB_WIDTH, 900, 1920, 1080)
    taskbar.get_edge_position.return_value = constants.taskbar.edge.RIGHT
    taskbar.get_screen.return_value = screen

    height = constants.ui.widget.MAX_WIDGET_HEIGHT_PX + 37
    pos = PositionCalculator().calculate_position(taskbar, (TB_WIDTH, height), {"tray_offset_y": 3})
    # Phase B: a native side bar keeps the #161 tray gap (it was the 3 px default offset).
    assert pos.y + height == 900 - constants.layout.TRAY_EDGE_MIN_GAP_PX, pos


@pytest.mark.parametrize("overrides", [
    dict(widget_display_mode="network_only"),
    dict(widget_display_mode="cycle"),
    dict(monitor_cpu_enabled=False, monitor_gpu_enabled=False),
], ids=["network_only", "cycle", "side_by_side_without_hardware"])
def test_side_taskbar_height_unchanged_without_stacked_segments(q_app, overrides):
    _, height, line = _side_taskbar_size(_config(**overrides))
    assert height == 2 * line + 4 * constants.renderer.TEXT_MARGIN
