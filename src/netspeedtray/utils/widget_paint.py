"""
Shared widget paint path (refactor C1 - the keystone).

The live taskbar widget (`views/widget/main.py`) and every off-taskbar *preview* of it
(the Settings live-preview, the Monitor Overview tile) must draw **identically** - same
modes, same layouts, same arrows, same graph. Historically that logic lived inside
`NetworkSpeedWidget.paintEvent` and read directly from `self.*`, so a preview could only
be faked by spinning up a real widget.

This module lifts that logic into one pure function, `render_widget`, parameterised on a
`WidgetMetrics` snapshot plus the layout context. `paintEvent` builds a snapshot from its
live state and calls it; `PreviewWidget` builds a synthetic snapshot and calls the same
function. There is exactly one place the widget knows how to draw itself.

Nothing here owns a QWidget or any live state - it takes a `QPainter`, a `WidgetRenderer`,
a `RenderConfig`, and the snapshot, and draws. That keeps it trivially testable and reusable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QFont, QPainter, QImage

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
    identity_band: Optional[str] = None        # v2.1 Wi-Fi band tag ("5G"/"2.4G") or None when off/unknown/hidden
    identity_band_color: Optional[str] = None  # band tint (hex) or None for the widget's default text color
    identity_band_solid: bool = False          # True = solid alert pill; False = outline pill
    identity_ssid: Optional[str] = None        # v2.1 truncated SSID / connection name, or None when off/blocked

    def net_bytes(self) -> Tuple[float, float]:
        """(upload, download) in bytes/sec from the stored Mbps - the renderer's unit."""
        up = (self.upload_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        dw = (self.download_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        return up, dw


def font_from_config(config: Dict[str, Any]) -> QFont:
    """
    Build the main display QFont from a config dict - mirrors
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


# Auto-Cycle phases of the separate upload/download graphs: with the split on, they replace the combined
# "network_only" phase in the rotation, so each direction gets the whole widget and the widget keeps its size.
SPLIT_PHASES: Tuple[str, str] = ("network_upload", "network_download")


def split_active(config: RenderConfig) -> bool:
    """Separate upload/download graphs are on - they need the mini graph itself to be on."""
    return bool(config.graph_enabled and config.graph_split_directions)


def split_network_phases(swap_upload_download: bool) -> List[str]:
    """The two network phases of the Auto-Cycle rotation, in the readout's order (upload first unless swapped)."""
    return list(reversed(SPLIT_PHASES)) if swap_upload_download else list(SPLIT_PHASES)


def _split_geometry(renderer: WidgetRenderer, config: RenderConfig, metrics: WidgetMetrics,
                    network_width: Optional[int]) -> Tuple[int, int]:
    """(panel width, badge reserve) of the side-by-side split view. A panel is one network block - the
    layout manager's network width, which includes the identity badge's reserve - and only the LAST
    panel draws the badge, so the first one is that much narrower (no badge-wide blank in it)."""
    reserve = renderer.identity_reserve(metrics.identity_band, metrics.identity_ssid)
    if network_width is not None:
        return network_width, reserve
    up_bytes, dw_bytes = metrics.net_bytes()
    return renderer.network_panel_width(up_bytes, dw_bytes, config) + reserve, reserve


def _split_rects(x: int, width: int, height: int, layout: str, panel_width: int,
                 badge_reserve: int) -> List[QRect]:
    """The two panels of the split view: stacked on a side (vertical) taskbar, side by side otherwise
    (the first one without the badge reserve - see _split_geometry)."""
    gap = constants.renderer.GRAPH_SPLIT_GAP
    if layout == "vertical":
        top = (height - gap) // 2
        return [QRect(x, 0, width, top), QRect(x, top + gap, width, height - top - gap)]
    first = panel_width - badge_reserve
    return [QRect(x, 0, first, height), QRect(x + first + gap, 0, panel_width, height)]


def _resolve_mode(config: RenderConfig, cycle_mode: str) -> str:
    """`cycle` resolves to whichever single metric is currently showing. The widget only rebuilds its
    phase list on the next step, so a phase left over from before the split was toggled is mapped onto
    the current set (the combined network phase <-> the first split phase)."""
    mode = config.widget_display_mode
    if mode != "cycle":
        return mode
    if split_active(config):
        return split_network_phases(config.swap_upload_download)[0] if cycle_mode == "network_only" else cycle_mode
    return "network_only" if cycle_mode in SPLIT_PHASES else cycle_mode


def _draw_graph(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str) -> None:
    """Mini-graph background layer (skipped in side_by_side - scoped per-segment there - and for the
    split network readout, whose panels each draw their own graph)."""
    if mode == "side_by_side":
        return
    if mode == "network_only" and split_active(config):
        return
    if mode in SPLIT_PHASES:
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.net_history), layout,
                                 series="upload" if mode == "network_upload" else "download")
    elif mode == "cpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.cpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.CPU_LINE_COLOR)
    elif mode == "gpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.gpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.GPU_LINE_COLOR)
    else:
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.net_history), layout)


def side_by_side_keys(display_order: List[str], cpu_enabled: bool, gpu_enabled: bool,
                      stack_hw: bool) -> List[str]:
    """The side_by_side segments in display order: 'network', 'cpu', 'gpu', or one 'hardware' segment
    when CPU and GPU are stacked together. Shared by the paint path and the layout's size reservation."""
    keys: List[str] = []
    for k in display_order:
        if k == "network":
            keys.append(k)
        elif k == "cpu" and cpu_enabled:
            if stack_hw and gpu_enabled:
                if "hardware" not in keys:
                    keys.append("hardware")
            else:
                keys.append("cpu")
        elif k == "gpu" and gpu_enabled:
            if stack_hw and cpu_enabled:
                if "hardware" not in keys:
                    keys.append("hardware")
            else:
                keys.append("gpu")
    return keys


def stacked_segment_height(key: str, line_height: int, ram_enabled: bool, vram_enabled: bool) -> int:
    """Height of one side_by_side segment when a side taskbar stacks the segments top-to-bottom.

    Matches what the renderer paints: the network readout is two rows + 1px (draw_network_speeds);
    CPU and GPU are one row each, plus a row of its own for the RAM/VRAM, stacked CPU+GPU included
    (draw_hardware_stats, memory_below). Worst case, so a reading that arrives later never clips.
    """
    if key == "network":
        return line_height * 2 + 1
    if key == "hardware":
        return line_height * (2 + (1 if ram_enabled else 0) + (1 if vram_enabled else 0))
    has_memory = ram_enabled if key == "cpu" else vram_enabled
    return line_height * (2 if has_memory else 1)


def _draw_segment(painter: QPainter, renderer: WidgetRenderer, key: str, width: int, height: int,
                  config: RenderConfig, metrics: WidgetMetrics, layout: str, x_offset: int = 0,
                  y_offset: int = 0, slot_width: Optional[int] = None, memory_below: bool = False) -> None:
    """Paint the text/stats of one side_by_side segment (the network graph is the caller's)."""
    if key == "network":
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout,
                                     x_offset=x_offset, slot_width=slot_width,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid, y_offset=y_offset)
    elif key == "cpu" and config.monitor_cpu_enabled:
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, None, width, height, config,
                                     metrics.cpu_temp, None, ram, None, layout,
                                     x_offset=x_offset, cpu_power=metrics.cpu_power, y_offset=y_offset)
    elif key == "gpu" and config.monitor_gpu_enabled:
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, None, metrics.gpu_usage, width, height, config,
                                     None, metrics.gpu_temp, None, vram, layout,
                                     x_offset=x_offset, gpu_power=metrics.gpu_power, y_offset=y_offset)
    elif key == "hardware":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, metrics.gpu_usage, width, height, config,
                                     metrics.cpu_temp, metrics.gpu_temp, ram, vram, layout,
                                     x_offset=x_offset, cpu_power=metrics.cpu_power, gpu_power=metrics.gpu_power,
                                     y_offset=y_offset, memory_below=memory_below)


def _draw_stacked(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                  config: RenderConfig, metrics: WidgetMetrics, layout: str, keys: List[str]) -> None:
    """Side taskbar: the widget is only as wide as the taskbar, so the segments stack top-to-bottom
    (network over CPU/GPU), each centered in the band the layout reserved for it. For the same reason
    the RAM/VRAM goes on a row of its own instead of inline after stacked CPU+GPU."""
    line_height = renderer.metrics.height()
    bands = [stacked_segment_height(k, line_height, config.monitor_ram_enabled, config.monitor_vram_enabled)
             for k in keys]
    gap = constants.layout.WIDGET_SEGMENT_GAP_STACKED_PX
    y = max(0, (height - sum(bands) - gap * (len(bands) - 1)) // 2)
    for key, band in zip(keys, bands):
        if key == "network" and config.graph_enabled:
            painter.save()
            painter.translate(0, y)
            renderer.draw_mini_graph(painter, width, band, config, list(metrics.net_history), layout)
            painter.restore()
        _draw_segment(painter, renderer, key, width, band, config, metrics, layout, y_offset=y,
                      memory_below=True)
        y += band + gap


def _draw_side_by_side(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                       config: RenderConfig, metrics: WidgetMetrics, layout: str,
                       network_width: Optional[int]) -> None:
    """Multi-segment side-by-side painting (network + cpu/gpu/combined laid out in a row, or stacked
    on a side taskbar)."""
    active_keys = side_by_side_keys(config.widget_display_order, config.monitor_cpu_enabled,
                                    config.monitor_gpu_enabled, getattr(config, "stack_hardware_stats", False))
    if not active_keys:
        active_keys = ["network"]

    if layout == "vertical" and len(active_keys) > 1:
        _draw_stacked(painter, renderer, width, height, config, metrics, layout, active_keys)
        return

    net_w = network_width if network_width is not None else width
    # Split graphs only in a row (horizontal taskbar): a side taskbar stacks the segments above
    # (_draw_stacked) with the network keeping its two-row band.
    split = split_active(config) and layout != "vertical"
    split_width = 0
    current_x = 0
    for key in active_keys:
        if key == "network" and split:
            up_bytes, dw_bytes = metrics.net_bytes()
            panel_w, reserve = _split_geometry(renderer, config, metrics, network_width)
            renderer.draw_network_split(painter, up_bytes, dw_bytes,
                                        _split_rects(current_x, width, height, layout, panel_w, reserve),
                                        config, list(metrics.net_history), layout,
                                        identity_text=metrics.identity_band,
                                        identity_color=metrics.identity_band_color,
                                        identity_solid=metrics.identity_band_solid,
                                        identity_ssid=metrics.identity_ssid)
            split_width = 2 * panel_w - reserve + constants.renderer.GRAPH_SPLIT_GAP
        else:
            if key == "network" and config.graph_enabled:
                painter.save()
                painter.translate(current_x, 0)
                renderer.draw_mini_graph(painter, net_w, height, config, list(metrics.net_history), layout)
                painter.restore()
            _draw_segment(painter, renderer, key, width, height, config, metrics, layout,
                          x_offset=current_x, slot_width=network_width)

        if key == "network":
            # Advance by the explicit network width if given (live widget), else by the
            # width the renderer just measured (preview has no layout-manager width).
            advance = network_width if network_width is not None else renderer.get_last_text_rect().width()
            if split:
                advance = split_width
            current_x += advance + constants.layout.WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX
        else:
            current_x += renderer.get_last_text_rect().width() + constants.layout.WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX


def _draw_foreground(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                     config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str,
                     network_width: Optional[int]) -> None:
    """Text/stats foreground layer for the resolved single-metric or side-by-side mode."""
    if mode == "side_by_side":
        _draw_side_by_side(painter, renderer, width, height, config, metrics, layout, network_width)
    elif mode == "network_only" and split_active(config):
        up_bytes, dw_bytes = metrics.net_bytes()
        panel_w, reserve = _split_geometry(renderer, config, metrics, network_width)
        renderer.draw_network_split(painter, up_bytes, dw_bytes,
                                    _split_rects(0, width, height, layout, panel_w, reserve),
                                    config, list(metrics.net_history), layout,
                                    identity_text=metrics.identity_band,
                                    identity_color=metrics.identity_band_color,
                                    identity_solid=metrics.identity_band_solid,
                                    identity_ssid=metrics.identity_ssid)
    elif mode == "network_only":
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid)
    elif mode in SPLIT_PHASES:
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_single(painter, up_bytes, dw_bytes, mode == "network_upload", width, height, config,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid)
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
        layout_mode: 'horizontal' for a TOP/BOTTOM taskbar, 'vertical' for a LEFT/RIGHT (side) taskbar
            (per NetworkSpeedWidget._layout_mode_for_edge). 'vertical' stacks the side_by_side segments
            top-to-bottom instead of in a row; every other mode draws the same either way.
        cycle_mode: which single metric to show when `config.widget_display_mode == 'cycle'`.
        network_width: width of the network segment for side_by_side graph scoping.
        font: font to apply before drawing foreground text.
    """
    width, height = rect.width(), rect.height()
    mode = _resolve_mode(config, cycle_mode)

    renderer.reset_content_bounds()   # accumulate this paint's segment union for menu centering
    renderer.draw_background(painter, rect, config)

    if config.graph_enabled:
        _draw_graph(painter, renderer, width, height, config, metrics, mode, layout_mode)

    if font is not None:
        painter.setFont(font)

    # Right-align the content so the widget's worst-case width reservation (the anti-jiggle "888.8"/"100%"
    # headroom, any hidden GPU/VRAM segment, and in cycle mode the widest-phase reserve) shows up as space
    # on the LEFT - toward the app icons, where it's invisible - instead of a gap between the content and
    # the system tray (#106). side_by_side over-reserves across segments; cycle over-reserves because it's
    # sized to the widest phase but draws one narrower phase. network_only/cpu_only/combined already fill
    # their width and vertical docking is sized to the taskbar. We MEASURE first because the renderer
    # computes its segment rects from font metrics during the draw, not before - so we run one draw onto a
    # throwaway surface (the mini-graph's point cache, keyed by data, is simply warmed by it).
    align_dx = 0
    if (mode == "side_by_side" or config.widget_display_mode == "cycle") and layout_mode != "vertical" and width > 0:
        try:
            probe = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32_Premultiplied)
            mp = QPainter(probe)
            if font is not None:
                mp.setFont(font)
            renderer.reset_content_bounds()
            # Mode-generic probe: measures side_by_side OR the single resolved cycle phase.
            _draw_foreground(mp, renderer, width, height, config, metrics, mode, layout_mode, network_width)
            mp.end()
            bounds = renderer.get_content_bounds()
            # Anchor from the content's RIGHT EDGE, not its width: the network segment is right-aligned
            # inside its own slot (draw_network_speeds), so the bounds no longer start at x=0. Using the
            # width here double-counted that shift and pushed the readout past the tray edge, clipping the
            # unit ("Mbps" -> "Mbp"). The right edge already includes the shift, so the anchor is correct.
            content_right = bounds.x() + bounds.width()
            if 0 < content_right <= width:
                # Keep deliberate breathing room from the tray-side edge so the right-anchored readout
                # doesn't crowd the tray-overflow chevron.
                align_dx = max(0, width - content_right - constants.renderer.RIGHT_ANCHOR_PADDING)
        except Exception:
            align_dx = 0
        finally:
            renderer.reset_content_bounds()

    if align_dx > 0:
        painter.save()
        painter.translate(align_dx, 0)
        _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout_mode, network_width)
        painter.restore()
        # The segment rects were measured at x_offset 0 (pre-translate); shift the accumulated bounds to
        # the on-screen position so the context menu still centers under the visible content.
        bounds = renderer.get_content_bounds()
        if bounds is not None and not bounds.isNull():
            bounds.translate(align_dx, 0)
    else:
        _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout_mode, network_width)
