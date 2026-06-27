"""
Shared widget paint path (refactor C1 — the keystone).

The live taskbar widget (`views/widget/main.py`) and every off-taskbar *preview* of it
(the Settings live-preview, the Monitor Overview tile) must draw **identically** — same
modes, same layouts, same arrows, same graph. Historically that logic lived inside
`NetworkSpeedWidget.paintEvent` and read directly from `self.*`, so a preview could only
be faked by spinning up a real widget.

This module lifts that logic into one pure function, `render_widget`, parameterised on a
`WidgetMetrics` snapshot plus the layout context. `paintEvent` builds a snapshot from its
live state and calls it; `PreviewWidget` builds a synthetic snapshot and calls the same
function. There is exactly one place the widget knows how to draw itself.

Nothing here owns a QWidget or any live state — it takes a `QPainter`, a `WidgetRenderer`,
a `RenderConfig`, and the snapshot, and draws. That keeps it trivially testable and reusable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QFont, QPainter

from netspeedtray import constants
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer


@dataclass
class WidgetMetrics:
    """
    An immutable-ish snapshot of everything the widget draws in a single frame.

    The live widget fills this from its update slots; a preview fills it with synthetic or
    held values. Histories are plain lists of floats (bytes/sec for network, percent for
    CPU/GPU) sized to the mini-graph window.
    """
    upload_mbps: float = 0.0
    download_mbps: float = 0.0
    cpu_usage: Optional[float] = 0.0
    gpu_usage: Optional[float] = 0.0
    cpu_temp: Optional[float] = None
    gpu_temp: Optional[float] = None
    cpu_power: Optional[float] = None
    gpu_power: Optional[float] = None
    ram_used: Optional[float] = None
    ram_total: Optional[float] = None
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    net_history: List[Any] = field(default_factory=list)   # AggregatedSpeedData items
    cpu_history: List[float] = field(default_factory=list)
    gpu_history: List[float] = field(default_factory=list)

    def net_bytes(self) -> Tuple[float, float]:
        """(upload, download) in bytes/sec from the stored Mbps — the renderer's unit."""
        up = (self.upload_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        dw = (self.download_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        return up, dw


def font_from_config(config: Dict[str, Any]) -> QFont:
    """
    Build the main display QFont from a config dict — mirrors
    ``WidgetLayoutManager.set_font`` so a preview's text metrics match the live widget.
    """
    family = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
    size = config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
    weight_val = config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
    if isinstance(weight_val, int):
        weight = weight_val
    elif isinstance(weight_val, str):
        weight = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}.get(
            weight_val.lower(), QFont.Weight.Normal)
    else:
        weight = QFont.Weight.Normal
    return QFont(family, int(size), weight)


def demo_metrics() -> WidgetMetrics:
    """
    Synthetic, self-explanatory values for previews. Numbers are chosen to read clearly
    (a busy-but-not-maxed machine) so a glance teaches what each field shows. Histories are
    left empty here; the Settings preview feed (C5) supplies a live synthetic stream.
    """
    return WidgetMetrics(
        upload_mbps=12.4, download_mbps=88.6,
        cpu_usage=37.0, gpu_usage=52.0,
        cpu_temp=58.0, gpu_temp=64.0,
        cpu_power=35.0, gpu_power=120.0,
        ram_used=9.2, ram_total=16.0,
        vram_used=3.1, vram_total=8.0,
    )


def _resolve_mode(config: RenderConfig, cycle_mode: str) -> str:
    """`cycle` resolves to whichever single metric is currently showing."""
    mode = config.widget_display_mode
    return cycle_mode if mode == "cycle" else mode


def _draw_graph(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str) -> None:
    """Mini-graph background layer (skipped in side_by_side — scoped per-segment there)."""
    if mode == "side_by_side":
        return
    if mode == "cpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.cpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.CPU_LINE_COLOR)
    elif mode == "gpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.gpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.GPU_LINE_COLOR)
    else:
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.net_history), layout)


def _draw_side_by_side(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                       config: RenderConfig, metrics: WidgetMetrics, layout: str,
                       network_width: Optional[int]) -> None:
    """Multi-segment side-by-side painting (network + cpu/gpu/combined laid out in a row)."""
    active_keys: List[str] = []
    stack_hw = getattr(config, "stack_hardware_stats", False)

    for k in config.widget_display_order:
        if k == "network":
            active_keys.append(k)
        elif k == "cpu" and config.monitor_cpu_enabled:
            if stack_hw and config.monitor_gpu_enabled:
                if "hardware" not in active_keys:
                    active_keys.append("hardware")
            else:
                active_keys.append("cpu")
        elif k == "gpu" and config.monitor_gpu_enabled:
            if stack_hw and config.monitor_cpu_enabled:
                if "hardware" not in active_keys:
                    active_keys.append("hardware")
            else:
                active_keys.append("gpu")
    if not active_keys:
        active_keys = ["network"]

    net_w = network_width if network_width is not None else width
    current_x = 0
    for key in active_keys:
        if key == "network":
            if config.graph_enabled:
                painter.save()
                painter.translate(current_x, 0)
                renderer.draw_mini_graph(painter, net_w, height, config, list(metrics.net_history), layout)
                painter.restore()
            up_bytes, dw_bytes = metrics.net_bytes()
            renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout, x_offset=current_x)
        elif key == "cpu" and config.monitor_cpu_enabled:
            ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
            renderer.draw_hardware_stats(painter, metrics.cpu_usage, None, width, height, config,
                                         metrics.cpu_temp, None, ram, None, layout,
                                         x_offset=current_x, cpu_power=metrics.cpu_power)
        elif key == "gpu" and config.monitor_gpu_enabled:
            vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
            renderer.draw_hardware_stats(painter, None, metrics.gpu_usage, width, height, config,
                                         None, metrics.gpu_temp, None, vram, layout,
                                         x_offset=current_x, gpu_power=metrics.gpu_power)
        elif key == "hardware":
            ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
            vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
            renderer.draw_hardware_stats(painter, metrics.cpu_usage, metrics.gpu_usage, width, height, config,
                                         metrics.cpu_temp, metrics.gpu_temp, ram, vram, layout,
                                         x_offset=current_x, cpu_power=metrics.cpu_power, gpu_power=metrics.gpu_power)

        if key == "network":
            # Advance by the explicit network width if given (live widget), else by the
            # width the renderer just measured (preview has no layout-manager width).
            advance = network_width if network_width is not None else renderer.get_last_text_rect().width()
            current_x += advance + constants.layout.WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX
        else:
            current_x += renderer.get_last_text_rect().width() + constants.layout.WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX


def _draw_foreground(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                     config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str,
                     network_width: Optional[int]) -> None:
    """Text/stats foreground layer for the resolved single-metric or side-by-side mode."""
    if mode == "side_by_side":
        _draw_side_by_side(painter, renderer, width, height, config, metrics, layout, network_width)
    elif mode == "network_only":
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout)
    elif mode == "cpu_only":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, None, width, height, config,
                                     metrics.cpu_temp, None, ram, None, layout, cpu_power=metrics.cpu_power)
    elif mode == "gpu_only":
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, None, metrics.gpu_usage, width, height, config,
                                     None, metrics.gpu_temp, None, vram, layout, gpu_power=metrics.gpu_power)
    elif mode == "combined":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, metrics.gpu_usage, width, height, config,
                                     metrics.cpu_temp, metrics.gpu_temp, ram, vram, layout,
                                     cpu_power=metrics.cpu_power, gpu_power=metrics.gpu_power)


def render_widget(painter: QPainter, rect: QRect, renderer: WidgetRenderer, config: RenderConfig,
                  metrics: WidgetMetrics, *, layout_mode: str = "horizontal",
                  cycle_mode: str = "network_only", network_width: Optional[int] = None,
                  font: Optional[QFont] = None) -> None:
    """
    Draw the full widget content (background + mini-graph + foreground) onto `painter`.

    This is the single shared paint path used by both the live widget and any preview.
    The caller owns the painter lifecycle and any widget-specific layers (e.g. the live
    widget's near-transparent hit-test fill); this function only draws the visible widget.

    Args:
        rect: the target rectangle (the widget/preview rect).
        renderer: a `WidgetRenderer` bound to `config`'s i18n/theme.
        metrics: the frame snapshot to draw.
        layout_mode: 'horizontal' or 'vertical' (small-taskbar two-row).
        cycle_mode: which single metric to show when `config.widget_display_mode == 'cycle'`.
        network_width: width of the network segment for side_by_side graph scoping.
        font: font to apply before drawing foreground text.
    """
    width, height = rect.width(), rect.height()
    mode = _resolve_mode(config, cycle_mode)

    renderer.draw_background(painter, rect, config)

    if config.graph_enabled:
        _draw_graph(painter, renderer, width, height, config, metrics, mode, layout_mode)

    if font is not None:
        painter.setFont(font)
    _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout_mode, network_width)
