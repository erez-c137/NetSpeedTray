"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration. This renderer
supports multiple layouts (e.g., vertical, horizontal) to adapt to different UI constraints.
"""

import logging
import math
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

from netspeedtray.core.widget_state import SpeedDataSnapshot, AggregatedSpeedData
from netspeedtray.utils.helpers import (has_dedicated_vram, format_speed, calculate_monotone_cubic_interpolation,
                                        memory_label_width, effective_display_mode)
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPainterPath
from PyQt6.QtCore import Qt, QPointF, QRect, QRectF
from netspeedtray import constants
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray.utils import hardware_vendors

# v2.1 network-identity band tag, drawn as a rounded pill/badge. The layout RESERVES the fixed
# worst-case slot and the renderer DRAWS the actual (usually narrower) tag in it - both MUST use these
# same values or the tag clips (#106 class). "2.4G" is the widest band label. See KICKOFF.md §3.
IDENTITY_BAND_REFERENCE: str = "2.4G"
IDENTITY_BAND_GAP_PX: int = 6
IDENTITY_PILL_PAD_X: int = 7      # horizontal padding inside the band pill


def identity_pill_width(fm, text: str) -> int:
    """Total pill width for `text` under font metrics `fm` (advance + both-side padding)."""
    return fm.horizontalAdvance(text) + 2 * IDENTITY_PILL_PAD_X


def identity_layout(fm, ssid, band):
    """Geometry for the identity badge. Returns (total_width, parts dict).

    Three shapes, all one capsule high:
      - band only  -> a single band pill.
      - ssid only  -> a single outline pill with the name.
      - both       -> a COMPOUND capsule: an outer outline pill holding the name, with the band as a
                      same-height pill nested flush to the right edge ("one big pill with two left edges").
    `parts` carries 'mode', 'w', 'h', and the relative x offsets the drawer needs.
    """
    pad = IDENTITY_PILL_PAD_X
    h = fm.height() + 2
    ssid_w = fm.horizontalAdvance(ssid) if ssid else 0
    band_pill_w = (fm.horizontalAdvance(band) + 2 * pad) if band else 0
    if ssid and band:
        w = pad + ssid_w + pad + band_pill_w
        return w, {"mode": "both", "w": w, "h": h, "ssid_x": pad, "band_x": w - band_pill_w, "band_w": band_pill_w}
    if ssid:
        w = 2 * pad + ssid_w
        return w, {"mode": "ssid", "w": w, "h": h, "ssid_x": pad}
    if band:
        return band_pill_w, {"mode": "band", "w": band_pill_w, "h": h, "band_x": 0, "band_w": band_pill_w}
    return 0, {"mode": "none", "w": 0, "h": h}

logger = logging.getLogger("NetSpeedTray.WidgetRenderer")


def trend_window(num_points: int) -> int:
    """Smoothing window (in samples) for the Trend mini graph's interior: a fixed fraction of the
    samples on screen, so the curve is equally calm at any timespan, at least TREND_MIN_WINDOW. The
    right edge uses the short TREND_EDGE_WINDOW instead (see trend_smooth)."""
    r = constants.renderer
    return max(r.TREND_MIN_WINDOW, int(round(num_points * r.TREND_WINDOW_FRACTION)))


def _gaussian_local_linear(y, window: int):
    """The smoother behind trend_smooth for ONE window: centred Gaussian in the interior, slope-damped
    local-linear fit where the window runs off the ends. Returns a numpy array."""
    import numpy as np

    n = len(y)
    # The kernel must not be longer than the data ('same' convolution keeps len(y) only then).
    half = min(int(window) // 2, (n - 1) // 2)
    if half < 1:
        return y.copy()
    d = np.arange(-half, half + 1, dtype=float)
    sigma = max(half / 2.0, 0.5)
    g = np.exp(-0.5 * (d / sigma) ** 2)
    ones = np.ones(n)

    def corr(a, k):   # out[i] = sum_j a[j] * k(j - i), over the samples that exist
        return np.convolve(a, k[::-1], mode="same")

    s0, s1 = corr(ones, g), corr(ones, g * d)
    # Weighted second moment plus the ridge, scaled to the full kernel's own second moment.
    s2 = corr(ones, g * d * d) + constants.renderer.TREND_EDGE_SLOPE_DAMPING * float((g * d * d).sum())
    t0, t1 = corr(y, g), corr(y, g * d)
    return (s2 * t0 - s1 * t1) / (s0 * s2 - s1 * s1)


def trend_smooth(values: List[float], window: int, edge_window: Optional[int] = None) -> List[float]:
    """Smooth an evenly spaced series for the Trend mini graph: a centred Gaussian moving average,
    with a slope-damped local-linear fit where the window runs off the ends, and a short window at
    the right edge.

    Interior: a centred Gaussian average (sigma = window / 4, cut at +-2 sigma). Centred, so a burst
    stays where it happened instead of sliding right and smearing the way a trailing EMA does.

    Ends: there are no samples past them. A plain truncated average lags there (a step 2 s old reached
    75% of its level at the 3 min default); a full local-linear fit has no lag but overshoots (115%)
    and wags with the per-second noise (77% of the raw noise, vs 35% in the interior). Damping the
    fitted slope (TREND_EDGE_SLOPE_DAMPING, a ridge on the slope term) keeps most of the catch-up (87%
    after 2 s, 96% after 3 s) with no overshoot and nearly the truncated average's calm (52% of the
    raw noise). In the interior the slope term is exactly zero (symmetric weights).

    Right edge = "now": its lag must not grow with the timespan, or the line still shows half a
    finished download while the text beside it reads 0 (a 6% window is 108 samples on 30 min). So the
    newest half-window blends linearly from the interior smoothing into a fixed short window
    (``edge_window``, default TREND_EDGE_WINDOW samples): after a drop to 0 the last point is near 0
    within ~5 samples at every timespan. Long timespans stay calm where it matters - many samples
    share one pixel there.

    A few numpy convolutions, O(n * window): under a millisecond for 1,800 samples, and only when
    the history changes (the caller caches the result with its polyline)."""
    import numpy as np   # lazy, like calculate_monotone_cubic_interpolation: only with the graph on

    y = np.asarray(values, dtype=float)
    n = len(y)
    out = _gaussian_local_linear(y, window)
    edge = constants.renderer.TREND_EDGE_WINDOW if edge_window is None else int(edge_window)
    blend = min(int(window) // 2, n - 1)
    if edge < window and blend > 0:
        short = _gaussian_local_linear(y, edge)
        k = np.arange(n - 1, -1, -1, dtype=float)          # samples back from "now"
        w = np.clip(1.0 - k / blend, 0.0, 1.0)              # 1 at "now", 0 a half-window back
        out = (1.0 - w) * out + w * short
    return out.tolist()


def trend_scale_values(smoothed: List[float]) -> List[float]:
    """What the Trend auto-fit sizes the axis to: the smoothed series, with its newest half edge
    window (TREND_EDGE_WINDOW // 2 samples) stood in for by its MEAN.

    At "now" a lone 1 s spike and the first second of a sustained burst look the same, and the edge
    fit draws both high (~40% of the spike's height) before a spike settles to ~16% as it scrolls into
    the interior. Sizing the axis to that edge peak re-scaled the whole graph by 2.2x over 3 s after
    every lone spike; the edge mean keeps that within 1.3x. A burst still grows the axis as its level
    is confirmed (its line rides the top edge meanwhile, capped). The line itself is drawn unchanged."""
    h = constants.renderer.TREND_EDGE_WINDOW // 2
    if h < 1 or len(smoothed) <= h + 1:
        return smoothed
    edge = smoothed[-h:]
    return smoothed[:-h] + [sum(edge) / len(edge)]


class _SpeedColumns(NamedTuple):
    """Formatted speeds and the column geometry shared by every speed row (see _speed_columns)."""
    up_val: str
    up_unit: str
    dw_val: str
    dw_unit: str
    number_area_width: int
    max_arrow_width: int
    arrow_gap: int
    unit_gap: int
    max_unit_width: int


@dataclass
class RenderConfig:
    """A data class holding a snapshot of all configuration relevant to rendering."""
    # ... (existing fields) ...
    color_coding: bool
    graph_enabled: bool
    high_speed_threshold: float
    low_speed_threshold: float
    arrow_width: int
    font_family: str
    font_size: int
    font_weight: int
    default_color: str
    high_speed_color: str
    low_speed_color: str
    background_color: str = field(default_factory=lambda: constants.config.defaults.DEFAULT_BACKGROUND_COLOR)
    background_opacity: float = field(default_factory=lambda: constants.config.defaults.DEFAULT_BACKGROUND_OPACITY / 100.0)
    graph_opacity: float = field(default_factory=lambda: constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
    speed_display_mode: str = constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE
    decimal_places: int = constants.config.defaults.DEFAULT_DECIMAL_PLACES
    text_alignment: str = constants.config.defaults.DEFAULT_TEXT_ALIGNMENT
    force_decimals: bool = False
    unit_type: str = constants.config.defaults.DEFAULT_UNIT_TYPE
    swap_upload_download: bool = constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD
    hide_arrows: bool = constants.config.defaults.DEFAULT_HIDE_ARROWS
    hide_unit_suffix: bool = constants.config.defaults.DEFAULT_HIDE_UNIT_SUFFIX
    short_unit_labels: bool = constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS
    max_samples: int = 1800 # Default 30 mins * 60s
    # Fixed mini-graph y-axis. graph_scale_ceiling is the advertised plan speed in bytes/sec - the larger
    # of down/up, since both series share one axis - and 0 means no plan is set, which keeps the graph
    # automatic even with graph_fixed_scale on. graph_scale_headroom is a fraction (0.10 == 10%).
    graph_fixed_scale: bool = constants.config.defaults.DEFAULT_GRAPH_FIXED_SCALE
    graph_scale_ceiling: float = 0.0
    graph_scale_headroom: float = constants.config.defaults.DEFAULT_GRAPH_SCALE_HEADROOM / 100.0
    graph_show_ceiling: bool = constants.config.defaults.DEFAULT_GRAPH_SHOW_CEILING
    # Separate upload/download graphs. Each panel scales to its own direction's plan speed (0 = not set,
    # so that panel stays automatic); graph_scale_ceiling above is the shared axis of the combined graph.
    graph_split_directions: bool = constants.config.defaults.DEFAULT_GRAPH_SPLIT_DIRECTIONS
    graph_scale_ceiling_up: float = 0.0
    graph_scale_ceiling_down: float = 0.0
    # "detailed" (every sample, filled) or "trend" (smoothed line) - see draw_mini_graph.
    graph_style: str = constants.config.defaults.DEFAULT_GRAPH_STYLE
    use_separate_arrow_font: bool = False
    arrow_font_family: str = constants.config.defaults.DEFAULT_FONT_FAMILY
    arrow_font_size: int = 9
    arrow_font_weight: int = constants.fonts.WEIGHT_DEMIBOLD
    # Custom arrow glyphs; empty falls back to the native i18n arrow (the Windows default).
    arrow_up_symbol: str = ""
    arrow_down_symbol: str = ""
    use_custom_arrow_colors: bool = constants.config.defaults.DEFAULT_USE_CUSTOM_ARROW_COLORS
    arrow_up_color: str = constants.config.defaults.DEFAULT_ARROW_UP_COLOR
    arrow_down_color: str = constants.config.defaults.DEFAULT_ARROW_DOWN_COLOR
    
    # New: Hardware Monitoring Toggles
    monitor_cpu_enabled: bool = False
    monitor_gpu_enabled: bool = False
    monitor_ram_enabled: bool = False
    monitor_vram_enabled: bool = False
    show_memory_labels: bool = False
    stack_hardware_stats: bool = False
    hardware_label_style: str = constants.config.defaults.DEFAULT_HARDWARE_LABEL_STYLE
    hardware_label_color: str = constants.config.defaults.DEFAULT_HARDWARE_LABEL_COLOR
    cpu_label_color: str = constants.config.defaults.DEFAULT_CPU_LABEL_COLOR
    gpu_label_color: str = constants.config.defaults.DEFAULT_GPU_LABEL_COLOR
    # The mode the widget PAINTS with - helpers.effective_display_mode (the user's Display Mode, or
    # network_only while "Show hardware in the hover card" is active).
    widget_display_mode: str = "network_only"
    widget_display_order: List[str] = field(default_factory=lambda: ["network", "cpu", "gpu"])
    show_hardware_temps: bool = False
    show_hardware_power: bool = False


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            opacity_raw = config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)
            opacity = float(opacity_raw) / 100.0 if opacity_raw is not None else (constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
            
            hist_mins = int(config.get('history_minutes', constants.config.defaults.DEFAULT_HISTORY_MINUTES))
            rate = resolve_poll_interval_seconds(
                config.get('update_rate', constants.config.defaults.DEFAULT_UPDATE_RATE))
            max_samples = int((hist_mins * 60) / rate)

            weight_raw = config.get('font_weight', constants.fonts.WEIGHT_DEMIBOLD)
            if isinstance(weight_raw, str):
                weight_val = {
                    "normal": constants.fonts.WEIGHT_NORMAL, 
                    "bold": constants.fonts.WEIGHT_BOLD
                }.get(weight_raw.lower(), constants.fonts.WEIGHT_NORMAL)
            else:
                try: weight_val = int(weight_raw)
                except: weight_val = constants.fonts.WEIGHT_DEMIBOLD

            mbps_to_bytes = constants.network.units.MEGA_DIVISOR / constants.network.units.BITS_PER_BYTE
            plan_down = max(0.0, float(config.get('plan_down_mbps', 0) or 0)) * mbps_to_bytes
            plan_up = max(0.0, float(config.get('plan_up_mbps', 0) or 0)) * mbps_to_bytes
            scale_ceiling = max(plan_down, plan_up)
            headroom_pct = config.get('graph_scale_headroom', constants.config.defaults.DEFAULT_GRAPH_SCALE_HEADROOM)
            graph_style = str(config.get('graph_style', constants.config.defaults.DEFAULT_GRAPH_STYLE))
            if graph_style not in constants.config.defaults.GRAPH_STYLE_CHOICES:
                graph_style = constants.config.defaults.DEFAULT_GRAPH_STYLE

            return cls(
                color_coding=bool(config.get('color_coding', constants.config.defaults.DEFAULT_COLOR_CODING)),
                graph_enabled=bool(config.get('graph_enabled', constants.config.defaults.DEFAULT_GRAPH_ENABLED)),
                high_speed_threshold=float(config.get('high_speed_threshold', constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=constants.renderer.DEFAULT_ARROW_WIDTH,
                font_family=str(config.get('font_family', constants.config.defaults.DEFAULT_FONT_FAMILY)),
                font_size=int(config.get('font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                font_weight=weight_val,
                default_color=str(config.get('default_color', constants.config.defaults.DEFAULT_COLOR)),
                high_speed_color=str(config.get('high_speed_color', constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR)),
                low_speed_color=str(config.get('low_speed_color', constants.config.defaults.DEFAULT_LOW_SPEED_COLOR)),
                background_color=str(config.get('background_color', constants.config.defaults.DEFAULT_BACKGROUND_COLOR)),
                background_opacity=max(0.0, min(1.0, float(config.get('background_opacity', constants.config.defaults.DEFAULT_BACKGROUND_OPACITY)) / 100.0)),
                graph_opacity=max(0.0, min(1.0, opacity)),
                speed_display_mode=str(config.get('speed_display_mode', constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE)),
                decimal_places=int(config.get('decimal_places', constants.config.defaults.DEFAULT_DECIMAL_PLACES)),
                text_alignment=str(config.get('text_alignment', constants.config.defaults.DEFAULT_TEXT_ALIGNMENT)),
                force_decimals=bool(config.get('force_decimals', constants.config.defaults.DEFAULT_FORCE_DECIMALS)),
                unit_type=str(config.get('unit_type', constants.config.defaults.DEFAULT_UNIT_TYPE)),
                swap_upload_download=bool(config.get('swap_upload_download', constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD)),
                hide_arrows=bool(config.get('hide_arrows', constants.config.defaults.DEFAULT_HIDE_ARROWS)),
                hide_unit_suffix=bool(config.get('hide_unit_suffix', constants.config.defaults.DEFAULT_HIDE_UNIT_SUFFIX)),
                hardware_label_style=str(config.get(
                    'hardware_label_style', constants.config.defaults.DEFAULT_HARDWARE_LABEL_STYLE)),
                hardware_label_color=str(config.get(
                    'hardware_label_color', constants.config.defaults.DEFAULT_HARDWARE_LABEL_COLOR)),
                cpu_label_color=str(config.get(
                    'cpu_label_color', constants.config.defaults.DEFAULT_CPU_LABEL_COLOR)),
                gpu_label_color=str(config.get(
                    'gpu_label_color', constants.config.defaults.DEFAULT_GPU_LABEL_COLOR)),
                short_unit_labels=bool(config.get('short_unit_labels', constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)),
                max_samples=max_samples,
                graph_fixed_scale=bool(config.get('graph_fixed_scale', constants.config.defaults.DEFAULT_GRAPH_FIXED_SCALE)),
                graph_scale_ceiling=max(0.0, scale_ceiling),
                graph_scale_headroom=max(0.0, float(headroom_pct or 0)) / 100.0,
                graph_show_ceiling=bool(config.get('graph_show_ceiling', constants.config.defaults.DEFAULT_GRAPH_SHOW_CEILING)),
                graph_split_directions=bool(config.get('graph_split_directions', constants.config.defaults.DEFAULT_GRAPH_SPLIT_DIRECTIONS)),
                graph_scale_ceiling_up=plan_up,
                graph_scale_ceiling_down=plan_down,
                graph_style=graph_style,
                use_custom_arrow_colors=bool(config.get(
                    'use_custom_arrow_colors', constants.config.defaults.DEFAULT_USE_CUSTOM_ARROW_COLORS)),
                arrow_up_color=str(config.get(
                    'arrow_up_color', constants.config.defaults.DEFAULT_ARROW_UP_COLOR)),
                arrow_down_color=str(config.get(
                    'arrow_down_color', constants.config.defaults.DEFAULT_ARROW_DOWN_COLOR)),
                use_separate_arrow_font=bool(config.get('use_separate_arrow_font', False)),
                arrow_font_family=str(config.get('arrow_font_family', constants.config.defaults.DEFAULT_FONT_FAMILY)),
                arrow_font_size=int(config.get('arrow_font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                arrow_font_weight=int(config.get('arrow_font_weight', constants.fonts.WEIGHT_DEMIBOLD)),
                arrow_up_symbol=str(config.get('arrow_up_symbol', '') or ''),
                arrow_down_symbol=str(config.get('arrow_down_symbol', '') or ''),

                # New
                monitor_cpu_enabled=bool(config.get('monitor_cpu_enabled', False)),
                monitor_gpu_enabled=bool(config.get('monitor_gpu_enabled', False)),
                monitor_ram_enabled=bool(config.get('monitor_ram_enabled', False)),
                monitor_vram_enabled=bool(config.get('monitor_vram_enabled', False)),
                show_memory_labels=bool(config.get('show_memory_labels', False)),
                stack_hardware_stats=bool(config.get('stack_hardware_stats', False)),
                widget_display_mode=effective_display_mode(config),
                widget_display_order=list(config.get('widget_display_order', ["network", "cpu", "gpu"])),
                show_hardware_temps=bool(config.get('show_hardware_temps', False)),
                show_hardware_power=bool(config.get('show_hardware_power', False))
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig: %s", e)
            raise ValueError(f"Invalid rendering config: {e}")


class WidgetRenderer:
    """
    Renders network speeds and optional mini graph for NetworkSpeedWidget.
    """
    def __init__(self, config: Dict[str, Any], i18n) -> None:
            """
            Initializes renderer with config, handling setup errors.
            """
            self.logger = logger
            self.i18n = i18n
            
            # Ensure config is a RenderConfig object if a dict is passed
            if isinstance(config, dict):
                self.config = RenderConfig.from_dict(config)
            else:
                self.config = config
                
            try:
                self.paused = False
                # Pause cue (widget_paint.render_widget): while set, each speed row's arrow is NOT drawn
                # and its cell is recorded (device coords) in _pause_slots, for the pause glyph to fill.
                self._pause_cue = False
                self._pause_slots: List[QRectF] = []
                
                # Bounding rect for coordinates. _last_text_rect is the LAST segment drawn (the
                # side-by-side layout advances by it); _content_bounds is the UNION of all segments
                # this paint - what the context menu centers on, so it stays centered over the WHOLE
                # widget when CPU/GPU stats sit beside the network text (not just the last segment).
                self._last_text_rect = QRect()
                self._content_bounds = QRect()

                # Mini graph polyline cache, one slot per graph kind - (is_hardware, series, style) - since the
                # split view draws an upload and a download panel every paint, which would thrash a
                # single slot. Value: (size, history key, upload points, download points, ceiling row).
                self._graph_cache: Dict[Tuple[bool, str, str], Tuple[Any, ...]] = {}

                # Caching for high-frequency paint events
                self._cached_pens = {}
                self._cached_bg_color = None
                self._cached_bg_opacity = -1.0
                self._refresh_resource_cache()
                
                self.logger.debug("WidgetRenderer initialized.")
            except Exception as e:
                self.logger.error("Failed to initialize WidgetRenderer: %s", e)
                # Fail gracefully
                self.config = None
                self.font = QFont()
                self.metrics = QFontMetrics(self.font)
                raise RuntimeError("Renderer initialization failed") from e

    def _refresh_resource_cache(self) -> None:
        """Pre-calculates colors, fonts, and pens to avoid allocation in paint loop."""
        if not self.config:
            return
            
        self.default_color = QColor(self.config.default_color)
        self.high_color = QColor(self.config.high_speed_color)
        self.low_color = QColor(self.config.low_speed_color)
        
        weight = int(self.config.font_weight)
        self.font = QFont(self.config.font_family, self.config.font_size, weight)
        self.metrics = QFontMetrics(self.font)
        
        if self.config.use_separate_arrow_font:
            self.arrow_font = QFont(self.config.arrow_font_family, self.config.arrow_font_size, int(self.config.arrow_font_weight))
        else:
            self.arrow_font = self.font
        self.arrow_metrics = QFontMetrics(self.arrow_font)
        
        # Pre-cache pens
        self._cached_pens = {
            'default': QPen(self.default_color),
            'high': QPen(self.high_color),
            'low': QPen(self.low_color),
            'cpu': QPen(QColor(constants.renderer.CPU_LINE_COLOR)),
            'gpu': QPen(QColor(constants.renderer.GPU_LINE_COLOR)),
            # Pre-baked like every other pen - _draw_speed_line runs on every repaint, so it must
            # never allocate. Present unconditionally; whether they are USED is a config check (#168).
            'arrow_up': QPen(QColor(self.config.arrow_up_color)),
            'arrow_down': QPen(QColor(self.config.arrow_down_color)),
        }


    def _draw_error(self, painter: QPainter, rect: QRect, message: str) -> None:
        """Draws an error message on the widget."""
        painter.save()
        painter.fillRect(rect, QColor(150, 0, 0, 200))
        painter.setPen(Qt.GlobalColor.white)
        # Use simple fallback if config failed
        base_size = self.config.font_size if self.config else 9
        error_font = QFont(self.font)
        error_font.setPointSize(max(6, base_size - 2))
        painter.setFont(error_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()


    def draw_background(self, painter: QPainter, rect: QRect, config: RenderConfig) -> None:
        """Draws the widget background. Ensures at least minimal opacity for hit testing."""
        # Check if we need to refresh cache (if config values moved)
        if (self._cached_bg_color is None or 
            self._cached_bg_opacity != config.background_opacity or
            self.config.background_color != config.background_color):
            
            self._cached_bg_color = QColor(config.background_color)
            self._cached_bg_opacity = config.background_opacity
            # Ensure minimum opacity for hit-testing
            min_alpha = 1.0 / 255.0
            self._cached_bg_color.setAlphaF(max(config.background_opacity, min_alpha))
            
        painter.fillRect(rect, self._cached_bg_color)

    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, layout_mode: str = 'vertical', x_offset: int = 0, slot_width: Optional[int] = None, fixed_width: Optional[int] = None, identity_text: Optional[str] = None, identity_color: Optional[str] = None, identity_solid: bool = False, identity_ssid: Optional[str] = None, y_offset: int = 0) -> None:
        """Draws current upload and download speeds.

        identity_text: optional network-identity band tag (e.g. "5G") drawn to the right of the unit
        column, vertically centered. When present it participates in the block width (so side_by_side
        right-align accounts for it) and the content bounds. Reserved width is the fixed worst-case
        (IDENTITY_BAND_REFERENCE), so a 2.4G<->5G change never shifts the layout.
        y_offset: top of the band (`height` tall) the readout is centered in; side_by_side on a side
        taskbar stacks its segments in bands down the widget.
        """
        try:
            cols = self._speed_columns(upload, download, config)
            up_val, up_unit, dw_val, dw_unit = cols.up_val, cols.up_unit, cols.dw_val, cols.dw_unit
            number_area_width, max_arrow_width = cols.number_area_width, cols.max_arrow_width
            arrow_gap, unit_gap, max_unit_width = cols.arrow_gap, cols.unit_gap, cols.max_unit_width

            painter.setFont(self.font)
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            vertical_gap = constants.renderer.TWO_ROW_GAP_PX
            margin = constants.renderer.TEXT_MARGIN

            # Right-align the whole readout inside its reserved slot (side_by_side) so the network hugs
            # the hardware instead of floating left in the worst-case width. The slack lands on the left,
            # toward the app icons; both rows shift equally so the arrows stay mutually aligned.
            # v2.1: reserve the identity badge slot (SSID pill / band pill / compound capsule) so
            # right-align accounts for it and the layout reserves the same amount. Matches
            # NetworkSpeedWidget._identity_reserve_px (same identity_layout geometry).
            identity_badge_w, identity_parts = identity_layout(self.metrics, identity_ssid, identity_text)
            identity_reserve = (IDENTITY_BAND_GAP_PX + identity_badge_w) if identity_badge_w else 0
            block_width = max_arrow_width + arrow_gap + number_area_width + unit_gap + max_unit_width + identity_reserve
            if slot_width and slot_width > block_width + 2 * margin:
                x_offset += slot_width - block_width - 2 * margin

            # Fixed Offsets (Arrow and Number start are fixed)
            arrow_x = x_offset + margin
            number_x = arrow_x + max_arrow_width + arrow_gap
            
            # Unit offset is relative to the (potentially expanded) number area
            unit_x = number_x + number_area_width + unit_gap
            
            # Default Vertical Layout (Stack UP over DW)
            total_height = (line_height * 2) + vertical_gap
            top_y = y_offset + int((height - total_height) / 2 + ascent)
            
            # Draw top line (upload by default, download when swapped).
            # Pass the canonical bytes/sec value (not the formatted string) so color
            # banding can compare against the Mbps thresholds regardless of display unit.
            top_val, top_unit, top_is_upload = (dw_val, dw_unit, False) if config.swap_upload_download else (up_val, up_unit, True)
            top_raw = upload if top_is_upload else download
            self._draw_speed_line(painter, top_is_upload, top_val, top_unit, top_raw, arrow_x, number_x, unit_x, top_y, config, number_area_width)

            # Draw bottom line (download by default, upload when swapped)
            dw_y = top_y + line_height + vertical_gap
            bot_val, bot_unit, bot_is_upload = (up_val, up_unit, True) if config.swap_upload_download else (dw_val, dw_unit, False)
            bot_raw = upload if bot_is_upload else download
            self._draw_speed_line(painter, bot_is_upload, bot_val, bot_unit, bot_raw, arrow_x, number_x, unit_x, dw_y, config, number_area_width)

            # v2.1: draw the network-identity badge to the right of the unit column, vertically centered.
            if identity_badge_w:
                badge_x = unit_x + max_unit_width + IDENTITY_BAND_GAP_PX
                badge_y = y_offset + (height - identity_parts["h"]) / 2.0
                self._draw_identity_badge(painter, badge_x, badge_y, identity_parts,
                                          identity_ssid, identity_text, identity_color, identity_solid)

            # Update bounding rect for context menu positioning (max_unit_width + band reserve)
            total_width = (unit_x - arrow_x) + max_unit_width + identity_reserve
            self._last_text_rect = QRect(arrow_x, top_y, total_width, total_height)
            self._extend_content_bounds(self._last_text_rect)

        except Exception as e:
            self.logger.error("Failed to draw network speeds: %s", e)

    def _speed_columns(self, upload: float, download: float, config: RenderConfig) -> "_SpeedColumns":
        """Formatted speeds plus the shared arrow / number / unit column geometry of the readout. Both
        values feed it, so the two rows (or the two split panels, or the two cycle phases) line up."""
        up_val, up_unit = format_speed(
            upload, self.i18n, force_mega_unit=(config.speed_display_mode == "always_mbps"),
            decimal_places=config.decimal_places, unit_type=config.unit_type,
            short_labels=config.short_unit_labels, split_unit=True
        )
        dw_val, dw_unit = format_speed(
            download, self.i18n, force_mega_unit=(config.speed_display_mode == "always_mbps"),
            decimal_places=config.decimal_places, unit_type=config.unit_type,
            short_labels=config.short_unit_labels, split_unit=True
        )

        # --- FIXED/DYNAMIC WIDTH CALCULATIONS ---
        # We want units to stay put for 3 digits, but move for 4.
        from netspeedtray.utils.helpers import get_reference_value_string

        # 1. Base 3-digit width for alignment stability
        # We use a 3-digit ref string to pin the "normal" unit position
        ref_str_3 = get_reference_value_string(False, config.decimal_places, config.unit_type, min_digits=3)
        base_number_width = self.metrics.horizontalAdvance(ref_str_3)

        # 2. Actual max width of currently displayed values
        actual_up_width = self.metrics.horizontalAdvance(up_val)
        actual_dw_width = self.metrics.horizontalAdvance(dw_val)
        actual_max_width = max(actual_up_width, actual_dw_width)

        # The area width follows the baseline, but expands if actual values are wider (4 digits)
        number_area_width = max(base_number_width, actual_max_width)

        # 3. Arrow Width (use max of UP/DW arrows). Custom glyph or the native default.
        arrow_up = self.config.arrow_up_symbol or self.i18n.UPLOAD_ARROW
        arrow_dw = self.config.arrow_down_symbol or self.i18n.DOWNLOAD_ARROW
        max_arrow_width = max(
            self.arrow_metrics.horizontalAdvance(arrow_up),
            self.arrow_metrics.horizontalAdvance(arrow_dw)
        ) if not config.hide_arrows else 0

        # Constants for gaps
        arrow_gap = constants.renderer.ARROW_NUMBER_GAP if not config.hide_arrows else 0
        unit_gap = constants.renderer.VALUE_UNIT_GAP if not config.hide_unit_suffix else 0
        max_unit_width = max(self.metrics.horizontalAdvance(up_unit), self.metrics.horizontalAdvance(dw_unit)) if not config.hide_unit_suffix else 0
        return _SpeedColumns(up_val, up_unit, dw_val, dw_unit, number_area_width, max_arrow_width,
                             arrow_gap, unit_gap, max_unit_width)

    def identity_reserve(self, identity_text: Optional[str], identity_ssid: Optional[str]) -> int:
        """Width the identity badge adds after the unit column (0 without one) - the same geometry
        draw_network_speeds and NetworkSpeedWidget._identity_reserve_px use."""
        badge_w, _parts = identity_layout(self.metrics, identity_ssid, identity_text)
        return (IDENTITY_BAND_GAP_PX + badge_w) if badge_w else 0

    def network_panel_width(self, upload: float, download: float, config: RenderConfig) -> int:
        """Width of one split panel, without the identity badge, measured from the live values - for
        callers without the layout manager's reserved width (the Settings preview). Same span the
        two-row readout occupies."""
        cols = self._speed_columns(upload, download, config)
        return (2 * constants.renderer.TEXT_MARGIN + cols.max_arrow_width + cols.arrow_gap
                + cols.number_area_width + cols.unit_gap + cols.max_unit_width)

    def draw_network_split(self, painter: QPainter, upload: float, download: float, rects: List[QRect],
                           config: RenderConfig, history: List[Any], layout_mode: str = 'vertical',
                           identity_text: Optional[str] = None, identity_color: Optional[str] = None,
                           identity_solid: bool = False, identity_ssid: Optional[str] = None) -> None:
        """Separate upload/download graphs: two panels (``rects``, in reading order), each with its own
        graph - own scale, own plan-speed line - and its own speed row. Upload comes first unless
        swap_upload_download is on, matching the stacked readout's top row. The identity badge goes in
        the last panel, after its unit column."""
        try:
            cols = self._speed_columns(upload, download, config)
            first_is_upload = not config.swap_upload_download
            for i, (rect, is_upload) in enumerate(zip(rects, (first_is_upload, not first_is_upload))):
                painter.save()
                painter.translate(rect.x(), rect.y())
                self.draw_mini_graph(painter, rect.width(), rect.height(), config, history, layout_mode,
                                     series="upload" if is_upload else "download")
                painter.restore()
                last = i == len(rects) - 1
                self._draw_speed_row(painter, is_upload, upload, download, cols, rect, config,
                                     identity_text if last else None, identity_color,
                                     identity_solid, identity_ssid if last else None)

            self.draw_split_divider(painter, rects, config)
        except Exception as e:
            self.logger.error("Failed to draw split network graphs: %s", e)

    @staticmethod
    def draw_split_divider(painter: QPainter, rects: List[QRect], config: RenderConfig) -> None:
        """A faint divider in the gap between the two split panels, so the graphs read as two charts."""
        if len(rects) != 2:
            return
        a, b = rects
        inset = constants.renderer.GRAPH_MARGIN * 2
        divider = QColor(config.default_color)
        divider.setAlpha(60)
        painter.save()
        painter.setPen(QPen(divider, 1))
        if b.x() > a.x():   # side by side
            x = (a.right() + b.left()) / 2.0 + 0.5
            painter.drawLine(QPointF(x, a.top() + inset), QPointF(x, a.bottom() - inset))
        else:               # stacked
            y = (a.bottom() + b.top()) / 2.0 + 0.5
            painter.drawLine(QPointF(a.left() + inset, y), QPointF(a.right() - inset, y))
        painter.restore()

    def draw_network_compact(self, painter: QPainter, upload: float, download: float, width: int,
                             height: int, config: RenderConfig, fit: Any, *, y_offset: int = 0,
                             only_upload: Optional[bool] = None, unit_row: bool = True,
                             unit_only: bool = False) -> None:
        """The side-taskbar compact readout (``widget_paint.side_bar_fit`` tiers T1-T5): arrow + a
        <= 3-digit number per row in ONE shared unit, the unit inline (T1, short label) or on a row of
        its own under the numbers (T2+), whole numbers from 10 up (T3+), no arrows (T4+). Centred in
        the ``width`` x ``height`` band at ``y_offset``.

        only_upload: True/False draws just that direction's row (a split panel / cycle phase).
        unit_row: False leaves the unit row to the caller (split panels share one); unit_only draws
        only the unit row.
        """
        from netspeedtray.utils.widget_paint import TIER_INLINE_UNIT, TIER_WHOLE, TIER_NO_ARROWS, TIER_CLIP
        from netspeedtray.utils.helpers import format_speed_compact, compact_speed_reference_strings
        try:
            tier = fit.tier
            inline_unit = tier == TIER_INLINE_UNIT and not config.hide_unit_suffix
            whole = tier >= TIER_WHOLE
            short = inline_unit or fit.unit_short or config.short_unit_labels
            up_t, dw_t, unit = format_speed_compact(
                upload, download, self.i18n, force_mega_unit=(config.speed_display_mode == "always_mbps"),
                decimal_places=config.decimal_places, unit_type=config.unit_type, short_labels=short,
                whole_from_ten=whole)
            show_arrows = not config.hide_arrows and tier < TIER_NO_ARROWS
            first_up = not config.swap_upload_download
            rows = [] if unit_only else [(is_up, up_t if is_up else dw_t, upload if is_up else download)
                                         for is_up in (first_up, not first_up)
                                         if only_upload is None or is_up == only_upload]
            show_unit_row = (unit_only or unit_row) and not inline_unit and not config.hide_unit_suffix

            fm, afm = self.metrics, self.arrow_metrics
            sep = getattr(self.i18n, "DECIMAL_SEPARATOR", ".")
            sep = sep if isinstance(sep, str) and sep else "."
            refs = compact_speed_reference_strings(config.decimal_places, whole, sep)
            num_col = max([fm.horizontalAdvance(s) for s in refs] + [fm.horizontalAdvance(t) for _, t, _ in rows])
            glyphs = [config.arrow_up_symbol or self.i18n.UPLOAD_ARROW,
                      config.arrow_down_symbol or self.i18n.DOWNLOAD_ARROW]
            arrow_w = max(afm.horizontalAdvance(g) for g in glyphs) if show_arrows else 0
            arrow_gap = constants.renderer.COMPACT_ARROW_GAP_PX if show_arrows else 0
            unit_gap = constants.renderer.COMPACT_UNIT_GAP_PX if inline_unit else 0
            unit_w = fm.horizontalAdvance(unit) if (inline_unit or show_unit_row) else 0
            block_w = arrow_w + arrow_gap + num_col + (unit_gap + unit_w if inline_unit else 0)
            margin = constants.renderer.TEXT_MARGIN
            x0 = max(margin, (width - block_w) // 2)

            line_h, asc, gap = fm.height(), fm.ascent(), constants.renderer.TWO_ROW_GAP_PX
            n = len(rows) + (1 if show_unit_row else 0)
            block_h = n * line_h + max(0, n - 1) * gap
            y = y_offset + int((height - block_h) / 2 + asc)

            painter.save()
            # Never paint outside the band: at T5 nothing fits, and a long locale unit at a large font
            # ("МиБ" at 11 pt on a 32 px bar) can be wider than the bar even in its short form.
            painter.setClipRect(QRect(0, y_offset, width, height), Qt.ClipOperation.IntersectClip)
            if tier >= TIER_CLIP or (show_unit_row and unit_w > width - 2 * margin):
                if not getattr(self, "_compact_clip_logged", False):
                    self._compact_clip_logged = True
                    self.logger.warning("Side taskbar too narrow (%d px) for the compact readout at this "
                                        "font; clipping it.", width)
            row_cfg = config
            if not show_arrows or not inline_unit:
                from dataclasses import replace
                row_cfg = replace(config, hide_arrows=not show_arrows, hide_unit_suffix=not inline_unit)
            arrow_x = x0
            number_x = arrow_x + arrow_w + arrow_gap
            unit_x = number_x + num_col + unit_gap
            top = y
            for is_up, text, raw in rows:
                self._draw_speed_line(painter, is_up, text, unit, raw, arrow_x, number_x, unit_x, y,
                                      row_cfg, num_col)
                y += line_h + gap
            if show_unit_row:
                painter.setFont(self.font)
                painter.setPen(self._cached_pens['default'])
                painter.drawText(max(margin, (width - unit_w) // 2), y, unit)
            painter.restore()

            self._last_text_rect = QRect(min(x0, (width - unit_w) // 2) if show_unit_row else x0,
                                         top - asc, max(block_w, unit_w), block_h)
            self._extend_content_bounds(self._last_text_rect)
        except Exception as e:
            self.logger.error("Failed to draw compact network speeds: %s", e)

    def draw_hardware_compact(self, painter: QPainter, stats: List[Tuple[str, Optional[float]]], width: int,
                              height: int, config: RenderConfig, *, inline: bool = True,
                              y_offset: int = 0) -> None:
        """CPU/GPU on a side taskbar too narrow for today's rows: ``[icon] 37%`` per stat (``inline``)
        or the icon centred over the value; memory, temperature and power live in the hover card."""
        try:
            if not stats:
                return
            fm = self.metrics
            style = getattr(config, 'hardware_label_style', constants.config.defaults.DEFAULT_HARDWARE_LABEL_STYLE)
            cpu_color, gpu_color = self.hardware_label_colors(config)
            icon_w = (max(fm.horizontalAdvance(s) for s, _ in stats) if style == "text"
                      else constants.renderer.HW_ICON_SIZE_PX)
            gap = constants.renderer.COMPACT_HW_ICON_GAP_PX
            pct_col = fm.horizontalAdvance("100%")
            line_h, asc, row_gap = fm.height(), fm.ascent(), constants.renderer.TWO_ROW_GAP_PX
            n = len(stats) * (1 if inline else 2)
            block_h = n * line_h + max(0, n - 1) * row_gap
            y = y_offset + int((height - block_h) / 2 + asc)
            top = y
            block_w = icon_w + gap + pct_col if inline else max(icon_w, pct_col)
            x0 = max(constants.renderer.TEXT_MARGIN, (width - block_w) // 2)
            painter.save()
            painter.setClipRect(QRect(0, y_offset, width, height), Qt.ClipOperation.IntersectClip)
            painter.setFont(self.font)
            for label, val in stats:
                color = QColor(cpu_color if label == 'CPU' else gpu_color)
                pct = self._fmt_hw_percent(val if val is not None else float("nan"))
                icon_x = x0 if inline else (width - icon_w) // 2
                if style == "text":
                    painter.setPen(QPen(color))
                    painter.drawText(icon_x, y, label)
                else:
                    self._draw_icon(painter, label, icon_x, y, color)
                painter.setFont(self.font)
                painter.setPen(self.default_color)
                if inline:
                    painter.drawText(x0 + icon_w + gap + pct_col - fm.horizontalAdvance(pct), y, pct)
                else:
                    y += line_h + row_gap
                    painter.drawText((width - fm.horizontalAdvance(pct)) // 2, y, pct)
                y += line_h + row_gap
            painter.restore()
            self._last_text_rect = QRect(x0, top - asc, block_w, block_h)
            self._extend_content_bounds(self._last_text_rect)
        except Exception as e:
            self.logger.error("Failed to draw compact hardware stats: %s", e)

    def draw_network_single(self, painter: QPainter, upload: float, download: float, is_upload: bool,
                            width: int, height: int, config: RenderConfig,
                            identity_text: Optional[str] = None, identity_color: Optional[str] = None,
                            identity_solid: bool = False, identity_ssid: Optional[str] = None) -> None:
        """One direction's speed row, vertically centered - the foreground of an Auto-Cycle upload or
        download phase when the graphs are split (render_widget draws that phase's graph)."""
        try:
            cols = self._speed_columns(upload, download, config)
            self._draw_speed_row(painter, is_upload, upload, download, cols, QRect(0, 0, width, height), config,
                                 identity_text, identity_color, identity_solid, identity_ssid)
        except Exception as e:
            self.logger.error("Failed to draw network speed: %s", e)

    def _draw_speed_row(self, painter: QPainter, is_upload: bool, upload: float, download: float,
                        cols: "_SpeedColumns", rect: QRect, config: RenderConfig,
                        identity_text: Optional[str], identity_color: Optional[str],
                        identity_solid: bool, identity_ssid: Optional[str]) -> None:
        """A single arrow + value + unit row, left-aligned at the margin of ``rect`` and vertically
        centered in it (the split-panel counterpart of draw_network_speeds' two-row stack)."""
        painter.setFont(self.font)
        margin = constants.renderer.TEXT_MARGIN
        arrow_x = rect.x() + margin
        number_x = arrow_x + cols.max_arrow_width + cols.arrow_gap
        unit_x = number_x + cols.number_area_width + cols.unit_gap
        y = int(rect.y() + (rect.height() - self.metrics.height()) / 2 + self.metrics.ascent())
        val, unit = (cols.up_val, cols.up_unit) if is_upload else (cols.dw_val, cols.dw_unit)
        self._draw_speed_line(painter, is_upload, val, unit, upload if is_upload else download,
                              arrow_x, number_x, unit_x, y, config, cols.number_area_width)

        identity_badge_w, identity_parts = identity_layout(self.metrics, identity_ssid, identity_text)
        if identity_badge_w:
            badge_x = unit_x + cols.max_unit_width + IDENTITY_BAND_GAP_PX
            badge_y = rect.y() + (rect.height() - identity_parts["h"]) / 2.0
            self._draw_identity_badge(painter, badge_x, badge_y, identity_parts,
                                      identity_ssid, identity_text, identity_color, identity_solid)
        identity_reserve = (IDENTITY_BAND_GAP_PX + identity_badge_w) if identity_badge_w else 0

        row_width = (unit_x - arrow_x) + cols.max_unit_width + identity_reserve
        self._last_text_rect = QRect(arrow_x, y - self.metrics.ascent(), row_width, self.metrics.height())
        self._extend_content_bounds(self._last_text_rect)

    def _draw_pill(self, painter: QPainter, rect: QRectF, accent: QColor, text: str, solid: bool, radius: float) -> None:
        """Draw one rounded pill: SOLID fills `accent` with white text; OUTLINE strokes `accent`, text in `accent`."""
        if solid:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QColor("#FFFFFF"))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1.3))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(accent)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_identity_badge(self, painter: QPainter, x: float, y: float, parts: dict,
                             ssid: Optional[str], band: Optional[str],
                             band_color: Optional[str], band_solid: bool) -> None:
        """Draw the identity badge (band pill / ssid pill / compound capsule) per `parts` geometry."""
        h = parts["h"]
        radius = h / 2.0
        accent = QColor(band_color) if band_color else self.default_color
        mode = parts["mode"]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font)
        if mode == "band":
            self._draw_pill(painter, QRectF(x, y, parts["band_w"], h), accent, band, band_solid, radius)
        elif mode == "ssid":
            # Neutral outline pill with the name.
            self._draw_pill(painter, QRectF(x, y, parts["w"], h), self.default_color, ssid, False, radius)
        elif mode == "both":
            # Outer outline capsule holding the name; band as a same-height pill nested flush to the
            # right edge ("two left edges"). The nested band honors band_solid just like the band-only
            # pill: OUTLINE for the calm "always"/"colored" modes, SOLID only for the alert. Forcing it
            # solid here filled the neutral band with default_color (white in dark mode) under white
            # text, making it invisible.
            outer = QRectF(x, y, parts["w"], h)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.default_color, 1.3))
            painter.drawRoundedRect(outer, radius, radius)
            painter.setPen(self.default_color)
            ssid_baseline = int(y + (h + self.metrics.ascent() - self.metrics.descent()) / 2)
            painter.drawText(int(x + parts["ssid_x"]), ssid_baseline, ssid)
            band_rect = QRectF(x + parts["band_x"], y, parts["band_w"], h)
            self._draw_pill(painter, band_rect, accent, band, band_solid, radius)
        painter.restore()

    @staticmethod
    def _speed_band(raw_bytes: float, high_threshold_mbps: float, low_threshold_mbps: float) -> str:
        """
        Return the color band ('high' | 'low' | 'default') for a speed.

        `raw_bytes` is the canonical speed in bytes/sec; it is converted to Mbps -
        the unit the high/low thresholds are defined in - before comparison. This is
        deliberately independent of the on-screen display unit: parsing the displayed
        number (which may be in Kbps, MB/s, ...) banded incorrectly whenever that unit
        wasn't Mbps. Bands ascend: default (< low) -> low (low..high) -> high (>= high).
        """
        try:
            # Be order-robust: clamp so banding stays monotonic even if a caller passes
            # low > high (config validation normally enforces low < high, but this static
            # helper shouldn't silently invert if it ever doesn't).
            hi = max(high_threshold_mbps, low_threshold_mbps)
            lo = min(high_threshold_mbps, low_threshold_mbps)
            mbps = (raw_bytes * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            if mbps >= hi:
                return 'high'
            if mbps >= lo:
                return 'low'
        except (TypeError, ValueError):
            return 'default'
        # Below the low threshold (incl. idle/0) uses the Default Color so the widget
        # matches the tray default at rest. (issue #153)
        return 'default'

    def _draw_speed_line(self, painter: QPainter, is_upload: bool, val: str, unit: str, raw_bytes: float, arrow_x: int, number_x: int, unit_x: int, y: int, config: RenderConfig, number_area_width: int) -> None:
        """Unified helper to draw a single speed line (Arrow + Value + Unit) with stable alignment."""
        # Color coding - band by the canonical speed (see _speed_band), never the
        # on-screen number, so banding is correct in every display unit.
        if config.color_coding:
            band = self._speed_band(raw_bytes, config.high_speed_threshold, config.low_speed_threshold)
            painter.setPen(self._cached_pens[band])
        else:
            painter.setPen(self._cached_pens['default'])

        # 1. Draw Arrow - or, while paused, leave its cell (through the arrow gap) for the pause glyph.
        if not config.hide_arrows and self._pause_cue:
            cell = QRectF(arrow_x, y - self.metrics.ascent(), max(0, number_x - arrow_x), self.metrics.height())
            self._pause_slots.append(painter.transform().mapRect(cell))
        elif not config.hide_arrows:
            painter.setFont(self.arrow_font)
            if is_upload:
                arrow = config.arrow_up_symbol or self.i18n.UPLOAD_ARROW
            else:
                arrow = config.arrow_down_symbol or self.i18n.DOWNLOAD_ARROW
            # Opt-in per-direction arrow color (#168). Off by default, so the arrow keeps sharing
            # the band pen set above and the whole line stays one color signal.
            if config.use_custom_arrow_colors:
                painter.setPen(self._cached_pens['arrow_up' if is_upload else 'arrow_down'])
            painter.drawText(arrow_x, y, arrow)
            if config.use_custom_arrow_colors:
                # Restore the band pen BEFORE the number. Forgetting this makes the value silently
                # inherit the arrow color, which reads as a color-coding regression (cf. #153).
                if config.color_coding:
                    band = self._speed_band(raw_bytes, config.high_speed_threshold, config.low_speed_threshold)
                    painter.setPen(self._cached_pens[band])
                else:
                    painter.setPen(self._cached_pens['default'])

        # 2. Draw Value (Right-aligned within fixed/expanded number area)
        painter.setFont(self.font)
        val_width = self.metrics.horizontalAdvance(val)
        aligned_number_x = number_x + (number_area_width - val_width)
        painter.drawText(int(aligned_number_x), y, val)
        
        # 3. Draw Unit
        if not config.hide_unit_suffix:
            painter.drawText(unit_x, y, unit)




    def _fmt_hw_percent(self, val: float) -> str:
        """CPU/GPU percent as plain text ("9%" / "100%"), or N/A when there is no measurement.

        The FIXED percent COLUMN in draw_hardware_stats (not this string) provides the constant width
        now, so the value reads naturally: it's right-aligned in that column when memory is inline
        (stacked - keeps the memory column lined up across rows) and left-aligned when memory is on its own row
        (single-stat modes - lines up under the percent). Either way the segment width never changes,
        so the readout no longer slides or clips (#179 and the side-by-side alignment work).

        A non-finite value means "this stat is enabled but nothing has measured it" - the widget seeds
        its usage fields with NaN and only replaces them when a real sample arrives. That distinction
        matters: under RDP the GPU poll is skipped entirely (monitor_thread.run), so no sample ever
        arrives, and seeding with 0.0 made the widget display a confident "GPU 0%" that was really
        just the initialiser. N/A is the same idiom _build_hw_suffix already uses for an unavailable
        temperature or wattage.
        """
        try:
            if not math.isfinite(float(val)):
                return self.i18n.DEFAULT_TEXT
        except (TypeError, ValueError):
            return self.i18n.DEFAULT_TEXT
        return f"{int(val)}%"

    def draw_hardware_stats(self, painter: QPainter, cpu_usage: Optional[float], gpu_usage: Optional[float],
                           width: int, height: int, config: RenderConfig,
                           cpu_temp: Optional[float] = None, gpu_temp: Optional[float] = None,
                           ram_info: Optional[Tuple[float, float]] = None,
                           vram_info: Optional[Tuple[float, float]] = None,
                           layout_mode: str = 'vertical', x_offset: int = 0, fixed_width: Optional[int] = None,
                           cpu_power: Optional[float] = None, gpu_power: Optional[float] = None,
                           y_offset: int = 0, memory_below: bool = False) -> None:
        """Draws CPU and/or GPU utilization statistics with optional temperature, power, and memory.

        y_offset: top of the band (`height` tall) the rows are centered in (see draw_network_speeds).
        memory_below: give each RAM/VRAM value a row of its own under its stat even when CPU and GPU
            share the segment (where it is otherwise inline); a side taskbar is too narrow for it inline."""
        try:
            order = getattr(config, 'widget_display_order', ["network", "cpu", "gpu"])
            cpu_idx = order.index("cpu") if "cpu" in order else 999
            gpu_idx = order.index("gpu") if "gpu" in order else 999

            cpu_color, gpu_color = self.hardware_label_colors(config)

            items = []
            if cpu_usage is not None:
                items.append((cpu_idx, ('CPU', cpu_usage, cpu_temp, ram_info, cpu_color, cpu_power)))
            if gpu_usage is not None:
                items.append((gpu_idx, ('GPU', gpu_usage, gpu_temp, vram_info, gpu_color, gpu_power)))

            items.sort(key=lambda x: x[0])
            enabled_stats = [x[1] for x in items]

            if not enabled_stats: return

            painter.setFont(self.font)

            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            total_height = line_height * len(enabled_stats)
            top_y = int((height - total_height) / 2 + ascent)

            margin = constants.renderer.TEXT_MARGIN
            current_x = x_offset + margin

            is_compact = getattr(config, 'widget_display_mode', 'network_only') == "compact_stack" or len(enabled_stats) > 1

            show_temps = getattr(config, "show_hardware_temps", False)
            show_power = getattr(config, "show_hardware_power", False)

            style = getattr(config, 'hardware_label_style', constants.config.defaults.DEFAULT_HARDWARE_LABEL_STYLE)
            sp =self.metrics.horizontalAdvance(" ")

            # Keep percent / suffix / memory as SEPARATE cells (not one concatenated string) so each sits
            # in its own fixed-width column. That lines the rows up (the memory values align across CPU
            # and GPU even when one has a temp sensor and the other doesn't) AND makes the segment width
            # CONSTANT as values cross digit boundaries - so the whole readout stops sliding and never
            # clips. Worst-case column widths are computed once and are identical for every row.
            rows = []
            for (label, val, temp, mem_info, color_hex, power) in enabled_stats:
                mem_text, total = "", 0.0
                if mem_info and mem_info[0] is not None:
                    used, total = mem_info[0], (mem_info[1] or 0.0)
                    # An iGPU has no dedicated VRAM and PDH reports 0.0 for it. Draw nothing rather
                    # than spend width on a "0.0G" that reads like a measurement. RAM is unaffected:
                    # it always has a real total. Matches the Monitor's Overview tile, which has
                    # always hidden itself in this case.
                    if total <= 0 and not has_dedicated_vram(used):
                        mem_text = ""
                    else:
                        mem_text = f"{used:.1f}/{total:.1f}G" if total > 0 else f"{used:.1f}G"
                mem_label = (constants.renderer.MEMORY_LABEL_RAM if label == 'CPU'
                             else constants.renderer.MEMORY_LABEL_VRAM)
                rows.append({'label': label, 'color': color_hex, 'total': total,
                             'pct': self._fmt_hw_percent(val),
                             'suffix': self._build_hw_suffix(temp, power, show_temps, show_power),
                             'mem': mem_text, 'mem_label': mem_label if mem_text else ''})

            label_col = (max(self.metrics.horizontalAdvance(r['label']) for r in rows) + 4) if style == "text" else 14
            pct_col = self.metrics.horizontalAdvance("100%")   # the >3d percent is already a fixed 4-char field
            if show_temps and show_power:
                suffix_ref = "(99°C, 250.0W)"
            elif show_power:
                suffix_ref = "(250.0W)"
            elif show_temps:
                suffix_ref = "(99°C)"
            else:
                suffix_ref = ""
            suffix_col = (sp + self.metrics.horizontalAdvance(suffix_ref)) if suffix_ref else 0

            any_mem = any(r['mem'] for r in rows)
            totals = [r['total'] for r in rows if r['total'] > 0]
            t = max(totals) if totals else 0.0
            mem_num_col = self.metrics.horizontalAdvance(f"{t:.1f}/{t:.1f}G" if t > 0 else "9999G") if any_mem else 0
            # A gap, not a glyph. The memory value used to be introduced by " | ", which read as
            # clutter on a readout that is only a few characters wide (#250). Whitespace separates
            # it just as well, and drops a few pixels of reserved width while it is at it.
            sep_col = self.metrics.horizontalAdvance("  ")
            # #250: an optional "RAM"/"VRAM" cell in front of the number, sized for the widest label
            # so the numbers stay aligned across rows. Zero when off: the widget keeps its width.
            show_mem_labels = bool(getattr(config, 'show_memory_labels', False))
            mem_label_col = memory_label_width(self.metrics) if (show_mem_labels and any_mem) else 0
            inline_mem = is_compact and not memory_below
            mem_col = (sep_col + mem_label_col + mem_num_col) if (any_mem and inline_mem) else 0

            extra_rows = 0 if inline_mem else sum(1 for r in rows if r['mem'])
            total_height = line_height * (len(rows) + extra_rows)
            top_y = y_offset + int((height - total_height) / 2 + ascent)
            current_x = x_offset + margin

            stat_col = label_col + pct_col + suffix_col + mem_col
            seg_w = max(stat_col, (label_col + mem_label_col + mem_num_col) if (any_mem and not inline_mem) else 0)

            y = top_y
            for r in rows:
                if style == "text":
                    painter.setPen(QPen(QColor(r['color'])))
                    painter.drawText(current_x, y, r['label'])
                else:
                    self._draw_icon(painter, r['label'], current_x, y, QColor(r['color']))
                vx = current_x + label_col
                painter.setPen(self.default_color)
                # Right-align the percent in its fixed "100%" column whenever something trails it on the
                # same row (a suffix, or inline memory) so "8% (48C)" / "8% | 11.8/15.7G" stay tight and
                # line up across rows. Also right-align when the percent is the row's ONLY content (no
                # suffix, no memory anywhere) so a sub-100 value hugs the tray instead of leaving the
                # fixed-column surplus as a gap (#106). Left-align ONLY when it sits alone above a stacked
                # memory row (CPU+RAM), so it lines up with the memory beneath it (#179).
                has_trailing = (inline_mem and mem_col and r['mem']) or bool(suffix_col and r['suffix'])
                has_mem_below = (not inline_mem) and bool(r['mem'])
                right_align_pct = has_trailing or not has_mem_below
                px = (vx + pct_col - self.metrics.horizontalAdvance(r['pct'])) if right_align_pct else vx
                painter.drawText(px, y, r['pct'])
                if suffix_col and r['suffix']:
                    painter.drawText(vx + pct_col + sp, y, r['suffix'])  # live suffix in its worst-case column
                if inline_mem and mem_col and r['mem']:
                    mx = vx + pct_col + suffix_col
                    label_x = mx + sep_col
                    number_col_x = label_x + mem_label_col
                    # Right-align the number in its fixed-width column so the trailing 'G'
                    # lines up across rows while RAM/VRAM stay anchored in their own cell.
                    num_x = number_col_x + mem_num_col - self.metrics.horizontalAdvance(r['mem'])
                    if mem_label_col:
                        self._draw_memory_label(painter, label_x, y, r)
                    painter.drawText(num_x, y, r['mem'])
                y += line_height
                if not inline_mem and r['mem']:
                    # Right-align memory to the segment's right edge while keeping the
                    # RAM/VRAM label anchored in its fixed-width label cell.
                    mem_right = current_x + seg_w
                    number_col_x = mem_right - mem_num_col
                    label_x = number_col_x - mem_label_col
                    num_x = mem_right - self.metrics.horizontalAdvance(r['mem'])

                    if mem_label_col:
                        self._draw_memory_label(painter, label_x, y, r)

                    painter.drawText(num_x, y, r['mem'])
                    y += line_height

            self._last_text_rect = QRect(x_offset, top_y, seg_w + margin, total_height)
            self._extend_content_bounds(self._last_text_rect)

        except Exception as e:
            self.logger.error("Failed to draw hardware stats: %s", e)


    @staticmethod
    def hardware_label_colors(config: RenderConfig) -> Tuple[str, str]:
        """(cpu_hex, gpu_hex) for the CPU/GPU labels - icons, text labels and the RAM/VRAM tags alike.

        - match_text: the widget's text colour, so the labels follow it onto a light taskbar (it turns
          black there) instead of the hard-coded white that pre-2.2 "monochrome" painted, invisibly.
        - custom: the two picked colours (an unparsable one falls back to the text colour).
        - brand (default branch): the vendor palette in hardware_vendors. The taskbar is judged light
          or dark from the text colour - the widget background is usually transparent, and the text
          colour is exactly what the theme manager flips per taskbar theme (or what the user chose to
          be legible on theirs), so it is the honest proxy and costs no registry read per paint.
        """
        text = str(getattr(config, 'default_color', constants.config.defaults.DEFAULT_COLOR))
        mode = getattr(config, 'hardware_label_color', constants.config.defaults.DEFAULT_HARDWARE_LABEL_COLOR)
        if mode == "match_text":
            return text, text
        if mode == "custom":
            pair = []
            for key in ('cpu_label_color', 'gpu_label_color'):
                value = str(getattr(config, key, '') or '')
                pair.append(value if QColor.isValidColorName(value) else text)
            return pair[0], pair[1]
        text_qc = QColor(text)
        light_taskbar = text_qc.isValid() and text_qc.lightnessF() < 0.5
        return (hardware_vendors.widget_label_color("cpu", light_taskbar),
                hardware_vendors.widget_label_color("gpu", light_taskbar))

    def _draw_memory_label(self, painter: QPainter, label_x: int, y: int, row: Dict[str, Any]) -> None:
        """Paint a row's RAM/VRAM label at the fixed start of its reserved label cell,
        in the row's CPU/GPU colour, then restore the value pen."""
        painter.setPen(QPen(QColor(row['color'])))
        painter.drawText(label_x, y, row['mem_label'])
        painter.setPen(self.default_color)

    def _draw_icon(self, painter: QPainter, icon_type: str, x: int, y_ascent: int, color: Optional[QColor] = None) -> None:
        """Draws a tiny symbolic icon for CPU or GPU."""
        painter.save()

        # Icon box size
        size = constants.renderer.HW_ICON_SIZE_PX
        rect = QRect(x, y_ascent - size + 1, size, size)
        
        draw_color = color if color else self.default_color
        pen = QPen(draw_color, 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        if icon_type == 'CPU':
            # Microchip with visible legs extending from PCB edge
            for dx in [3, 5, 7]:
                painter.drawLine(rect.left() + dx, rect.top(), rect.left() + dx, rect.top() + 1)
                painter.drawLine(rect.left() + dx, rect.bottom() - 1, rect.left() + dx, rect.bottom())
            for dy in [3, 5, 7]:
                painter.drawLine(rect.left(), rect.top() + dy, rect.left() + 1, rect.top() + dy)
                painter.drawLine(rect.right() - 1, rect.top() + dy, rect.right(), rect.top() + dy)

            # Draw PCB package (Outer outline)
            painter.drawRect(rect)
                
            # Draw Integrated Heatspreader (Inner filled package)
            ihs_rect = rect.adjusted(2, 2, -2, -2)
            painter.setBrush(painter.pen().color())
            painter.drawRect(ihs_rect) # Solid-filled IHS
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # Silicon Die Core (Inner dark center)
            core_rect = rect.adjusted(4, 4, -4, -4) 
            # The die is a contrasting dot: near-black inside a light package, near-white inside a dark
            # one - a black (match-text, light taskbar) icon would otherwise lose it entirely.
            painter.fillRect(core_rect, QColor("#121212" if draw_color.lightnessF() >= 0.5 else "#F0F0F0"))

        elif icon_type == 'GPU':
            # Graphics card with 'G' and Fan
            card_rect = rect.adjusted(0, 3, 0, -1)
            painter.drawRect(card_rect)
            # Bracket on left
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            # Fan circle
            fan_size = 5
            fan_rect = QRect(rect.center().x() - 1, rect.center().y() + 1, fan_size, fan_size)
            painter.drawEllipse(fan_rect.adjusted(-2, -1, -2, -1))
            
            # Tiny 'G'
            small_font = QFont(self.font.family(), 6)
            painter.setFont(small_font)
            painter.drawText(card_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "G")
            
        painter.restore()


    def _build_hw_suffix(self, temp: Optional[float], power: Optional[float],
                        show_temps: bool, show_power: bool) -> str:
        """Builds a parenthetical suffix string from available hardware extras.

        Examples: "(43°C, 7.8W)", "(43°C)", "(7.8W)", "(N/A)", or "" if nothing enabled.
        """
        if not show_temps and not show_power:
            return ""

        parts = []
        has_any_data = False

        if show_temps:
            try:
                temp_ok = temp is not None and math.isfinite(float(temp))
            except Exception:
                temp_ok = False
            if temp_ok:
                # Round, don't truncate. int() floors, so 27.9 rendered as "27" on the taskbar
                # while the Monitor window - which uses :.0f everywhere - said "28" for the same
                # sample, and users reasonably read the disagreement as a bug (#237).
                parts.append(f"{float(temp):.0f}°C")
                has_any_data = True
            else:
                parts.append(None)  # placeholder - will be replaced with N/A if nothing else has data

        if show_power:
            try:
                power_ok = power is not None and math.isfinite(float(power))
            except Exception:
                power_ok = False
            if power_ok:
                parts.append(f"{float(power):.1f}W")
                has_any_data = True
            else:
                parts.append(None)

        if not has_any_data:
            return f"({self.i18n.DEFAULT_TEXT})"

        # Filter out None placeholders (partial data is fine - just show what we have)
        valid_parts = [p for p in parts if p is not None]
        return f"({', '.join(valid_parts)})"

    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig,
                        history: List[Any], layout_mode: str = 'vertical',
                        is_hardware: bool = False, hardware_color: str = "#FFFFFF",
                        series: str = "both") -> None:
        """Draws a mini graph of history (speed or hardware utilization).

        ``series`` picks the network line(s): "both" (one shared axis), or "upload" / "download" alone
        on their own axis - the panels of the split view."""
        if not config.graph_enabled or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        # Honor the configured graph timespan: the series buffers more than the visible window (so a
        # longer timespan reveals already-recorded samples at once), so show only the last `max_samples`
        # points (= history_minutes worth). Without this the graph plotted the whole buffer and 3 min
        # looked identical to 20 min.
        if config.max_samples and len(history) > config.max_samples:
            history = history[-config.max_samples:]

        try:
            side_margin = constants.renderer.GRAPH_LEFT_PADDING
            top_margin = constants.renderer.GRAPH_MARGIN
            bottom_margin = constants.renderer.GRAPH_BOTTOM_PADDING
            
            graph_rect = QRect(side_margin, top_margin, width - (side_margin * 2), height - (top_margin + bottom_margin))
            if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

            if config.graph_style == "trend":
                self._draw_trend_graph(painter, graph_rect, width, height, config, history,
                                       is_hardware, hardware_color, series)
                return

            # Cache key for the (expensive) polyline recompute below. The history is an
            # append-only, time-ordered sliding window, so (first, last, length) uniquely
            # identifies its contents - an O(1) key instead of hashing all ~N points every
            # paint. (len >= MIN_GRAPH_POINTS here, so [0]/[-1] are safe.)
            current_hash = hash((history[0], history[-1], len(history), is_hardware))
            cache_key = (is_hardware, series, "detailed")
            cached = self._graph_cache.get(cache_key)

            if cached is None or cached[0] != (width, height) or cached[1] != current_hash:
                if is_hardware:
                    # Hardware is 0-100%
                    max_y, ceiling = 100.0, None
                else:
                    max_y, ceiling = self._network_graph_scale(history, config, series)
                # A fixed axis can be outrun (a burst above plan + headroom), so the curve is capped at its
                # top edge instead of running off the top of the graph. The auto-fit is sized to the data,
                # so it is left exactly as before.
                y_cap = max_y if ceiling is not None else math.inf
                
                num_points = len(history)
                step_x = graph_rect.width() / (num_points - 1) if num_points > 1 else graph_rect.width()
                right_edge = float(graph_rect.right())
                base_y = float(graph_rect.bottom())
                h = float(graph_rect.height())

                raw_x = [right_edge - (num_points - 1 - i) * step_x for i in range(num_points)]
                
                def make_smooth_polyline(accessor):
                    raw_y = [accessor(d) for d in history]
                    cx, cy = calculate_monotone_cubic_interpolation(raw_x, raw_y, density=5)
                    points = [QPointF(x, base_y - (min(max(0, y), y_cap) / max_y) * h) for x, y in zip(cx, cy)]
                    return points

                # Dotted line at the plan speed - only with headroom above 0%: at 0% the plan speed IS the
                # graph's top edge, so a line there would add nothing. Snapped to a pixel center so the
                # antialiased 1px dots stay crisp instead of smearing across two rows.
                if ceiling is not None and config.graph_show_ceiling and config.graph_scale_headroom > 0:
                    ceiling_y = math.floor(base_y - (ceiling / max_y) * h) + 0.5
                else:
                    ceiling_y = None

                if is_hardware:
                    upload_points = make_smooth_polyline(lambda d: d.value)
                    download_points = []
                else:
                    upload_points = (make_smooth_polyline(lambda d: d.upload)
                                     if series in ("both", "upload") else [])
                    download_points = (make_smooth_polyline(lambda d: d.download)
                                       if series in ("both", "download") else [])

                cached = ((width, height), current_hash, upload_points, download_points, ceiling_y)
                self._graph_cache[cache_key] = cached

            _size, _key, upload_points, download_points, ceiling_y = cached

            painter.save()
            painter.setOpacity(config.graph_opacity)

            # Behind the data, in the text color (theme-aware, so it reads on light and dark taskbars).
            if ceiling_y is not None:
                ceiling_pen = QPen(QColor(config.default_color), 1)
                ceiling_pen.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(ceiling_pen)
                painter.drawLine(QPointF(float(graph_rect.left()), ceiling_y),
                                 QPointF(float(graph_rect.right()), ceiling_y))

            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

            from PyQt6.QtGui import QLinearGradient, QBrush, QPolygonF

            def draw_area(points, color_hex):
                if not points: return
                poly_points = [QPointF(points[0].x(), float(graph_rect.bottom()))]
                poly_points.extend(points)
                poly_points.append(QPointF(points[-1].x(), float(graph_rect.bottom())))
                
                grad = QLinearGradient(0, graph_rect.top(), 0, graph_rect.bottom())
                c = QColor(color_hex)
                c.setAlpha(120)
                grad.setColorAt(0.0, c)
                c.setAlpha(0)
                grad.setColorAt(1.0, c)
                
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPolygon(QPolygonF(poly_points))

            if is_hardware:
                draw_area(upload_points, hardware_color)
            else:
                draw_area(upload_points, constants.graph.UPLOAD_LINE_COLOR)
                draw_area(download_points, constants.graph.DOWNLOAD_LINE_COLOR)

            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            stroke_width = 1.5

            if is_hardware:
                hw_pen = QPen(QColor(hardware_color), stroke_width)
                hw_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(hw_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(upload_points)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if upload_points:
                    upload_pen = QPen(QColor(constants.graph.UPLOAD_LINE_COLOR), stroke_width)
                    upload_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(upload_pen)
                    painter.drawPolyline(upload_points)

                if download_points:
                    download_pen = QPen(QColor(constants.graph.DOWNLOAD_LINE_COLOR), stroke_width)
                    download_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(download_pen)
                    painter.drawPolyline(download_points)

            painter.restore()
        except Exception as e:
            self.logger.error("Failed to draw mini graph: %s", e)

    def _draw_trend_graph(self, painter: QPainter, graph_rect: QRect, width: int, height: int,
                          config: RenderConfig, history: List[Any], is_hardware: bool,
                          hardware_color: str, series: str) -> None:
        """The "trend" mini graph style: each series smoothed (trend_smooth) and drawn as a clean line
        (no fill unless TREND_FILL_ALPHA is raised), instead of every per-second sample. Same axis rules as Detailed - a fixed
        scale is unchanged; the auto-fit is sized to the SMOOTHED data, so the trend fills the height
        instead of leaving room for spikes it no longer draws. Points are cached like Detailed's."""
        current_hash = hash((history[0], history[-1], len(history), is_hardware))
        cache_key = (is_hardware, series, "trend")
        cached = self._graph_cache.get(cache_key)

        if cached is None or cached[0] != (width, height) or cached[1] != current_hash:
            num_points = len(history)
            window = trend_window(num_points)
            if is_hardware:
                ups, downs = trend_smooth([d.value for d in history], window), []
                max_y, ceiling = 100.0, None
            else:
                ups = (trend_smooth([d.upload for d in history], window)
                       if series in ("both", "upload") else [])
                downs = (trend_smooth([d.download for d in history], window)
                         if series in ("both", "download") else [])
                # No outlier clip: smoothing already took out the 1 s spikes it exists for, and on
                # smoothed data it fired on real (smoothed) peaks and set the axis below them.
                max_y, ceiling = self._network_graph_scale(history, config, series, trend_scale_values(ups),
                                                           trend_scale_values(downs), outlier_clip=False)
            # Always inside the graph: the auto-fit reads the edge damped (trend_scale_values) and a
            # fixed axis can be outrun, so cap at the top edge either way - never draw off the rect.
            y_cap = max_y

            step_x = graph_rect.width() / (num_points - 1) if num_points > 1 else graph_rect.width()
            right_edge = float(graph_rect.right())
            base_y = float(graph_rect.bottom())
            # QRect.bottom() is top + height - 1, so span height - 1: max_y lands ON the top row, not above it.
            h = float(max(1, graph_rect.height() - 1))
            xs = [right_edge - (num_points - 1 - i) * step_x for i in range(num_points)]
            # Fewer samples than pixels (a short history stretched across the graph): interpolate so
            # the line stays a curve, not a chain of segments. Denser than that (the usual case): the
            # smoothed series has no detail finer than a pixel, so ~2 points per pixel are plenty.
            sparse = num_points < graph_rect.width()
            stride = max(1, num_points // (2 * max(1, graph_rect.width())))

            def polyline(values):
                if not values:
                    return []
                if sparse:
                    px, py = calculate_monotone_cubic_interpolation(xs, values, density=5)
                    px, py = list(px), list(py)
                    if not px or px[-1] != xs[-1]:   # end exactly on "now"
                        px.append(xs[-1])
                        py.append(values[-1])
                else:
                    idx = list(range(0, num_points, stride))
                    if idx[-1] != num_points - 1:
                        idx.append(num_points - 1)
                    px, py = [xs[i] for i in idx], [values[i] for i in idx]
                return [QPointF(x, base_y - (min(max(0.0, y), y_cap) / max_y) * h) for x, y in zip(px, py)]

            if ceiling is not None and config.graph_show_ceiling and config.graph_scale_headroom > 0:
                ceiling_y = math.floor(base_y - (ceiling / max_y) * h) + 0.5
            else:
                ceiling_y = None
            cached = ((width, height), current_hash, polyline(ups), polyline(downs), ceiling_y)
            self._graph_cache[cache_key] = cached

        _size, _key, upload_points, download_points, ceiling_y = cached
        from PyQt6.QtGui import QLinearGradient, QBrush, QPolygonF

        painter.save()
        painter.setOpacity(config.graph_opacity)
        if ceiling_y is not None:
            ceiling_pen = QPen(QColor(config.default_color), 1)
            ceiling_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(ceiling_pen)
            painter.drawLine(QPointF(float(graph_rect.left()), ceiling_y),
                             QPointF(float(graph_rect.right()), ceiling_y))

        if is_hardware:
            lines = [(upload_points, hardware_color)]
        else:
            lines = [(upload_points, constants.graph.UPLOAD_LINE_COLOR),
                     (download_points, constants.graph.DOWNLOAD_LINE_COLOR)]
        lines = [(pts, c) for pts, c in lines if pts]

        fill_alpha = constants.renderer.TREND_FILL_ALPHA
        if fill_alpha > 0:
            # A faint wash in plain SourceOver (not Detailed's additive Plus, which all but vanishes on
            # a light taskbar): it only says "area under the line"; the line carries the data.
            bottom = float(graph_rect.bottom())
            painter.setPen(Qt.PenStyle.NoPen)
            for pts, color_hex in lines:
                grad = QLinearGradient(0, graph_rect.top(), 0, graph_rect.bottom())
                c = QColor(color_hex)
                c.setAlpha(fill_alpha)
                grad.setColorAt(0.0, c)
                c.setAlpha(0)
                grad.setColorAt(1.0, c)
                painter.setBrush(QBrush(grad))
                painter.drawPolygon(QPolygonF([QPointF(pts[0].x(), bottom), *pts, QPointF(pts[-1].x(), bottom)]))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for pts, color_hex in lines:
            pen = QPen(QColor(color_hex), constants.renderer.TREND_LINE_WIDTH)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(pts)
        painter.restore()

    @staticmethod
    def _network_graph_scale(history: List[Any], config: RenderConfig,
                             series: str = "both", uploads: Optional[List[float]] = None,
                             downloads: Optional[List[float]] = None,
                             outlier_clip: bool = True) -> Tuple[float, Optional[float]]:
        """Top of the network mini graph's y-axis in bytes/sec, plus the fixed ceiling it was built from
        (None while auto-scaling).

        Auto-scaling re-fits the axis to the busiest sample in the window, so once a download scrolls out
        the axis shrinks and idle background traffic fills the graph as tall as the download did - the two
        are only told apart by reading the numbers. A fixed scale (graph_fixed_scale with a plan speed
        set) tops out at the plan speed plus the headroom instead, so a given height always means the
        same speed. A single-direction panel ("upload" / "download") scales to that direction's own
        samples and plan speed, so a 30 Mbps upload isn't flattened by a 300 Mbps download.
        History is non-empty here (len >= MIN_GRAPH_POINTS). ``uploads`` / ``downloads`` override the
        samples the auto-fit reads (the Trend style's smoothed series); None reads them from history."""
        ceiling = {"upload": config.graph_scale_ceiling_up,
                   "download": config.graph_scale_ceiling_down}.get(series, config.graph_scale_ceiling)
        if config.graph_fixed_scale and ceiling > 0:
            return max(ceiling * (1.0 + config.graph_scale_headroom), constants.renderer.MIN_Y_SCALE), ceiling

        # The Trend style passes its smoothed series in, so the auto-fit is sized to what is drawn.
        if uploads is None:
            uploads = [d.upload for d in history] if series in ("both", "upload") else []
        if downloads is None:
            downloads = [d.download for d in history] if series in ("both", "download") else []
        max_speed_val = max(uploads + downloads)

        if outlier_clip and len(history) > 10:
            all_speeds = uploads + downloads
            all_speeds_sorted = sorted(all_speeds)
            percentile_95 = all_speeds_sorted[int(len(all_speeds_sorted) * 0.95)]
            if percentile_95 > 0 and max_speed_val > percentile_95 * 3.0:
                max_speed_val = percentile_95

        padded_max_speed = max_speed_val * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
        return max(padded_max_speed, constants.renderer.MIN_Y_SCALE), None


    def update_config(self, config_dict: Dict[str, Any]) -> None:
        """Updates rendering configuration."""
        try:
            self.config = RenderConfig.from_dict(config_dict)
            # Rebuild colors, fonts, metrics AND the pen cache from the new config.
            # update_config previously re-derived colors/fonts inline but never
            # rebuilt self._cached_pens - which is what the paint loop actually uses -
            # so saved color/threshold edits did nothing until the next restart
            # (issue #153). Delegating to _refresh_resource_cache() is the single
            # source of truth and makes edits apply live.
            self._refresh_resource_cache()

            self._graph_cache.clear()
            self.logger.debug("Renderer config updated.")
        except Exception as e:
            self.logger.error("Failed to update config: %s", e)


    def get_last_text_rect(self) -> QRect:
        """Returns last text bounding rect."""
        return self._last_text_rect

    def reset_content_bounds(self) -> None:
        """Clear the per-paint content union; render_widget calls this before drawing the segments."""
        self._content_bounds = QRect()
        self._pause_slots = []

    def _extend_content_bounds(self, rect: QRect) -> None:
        """Grow _content_bounds to include ``rect`` (each drawn segment)."""
        if rect is None or not rect.isValid() or rect.isEmpty():
            return
        if self._content_bounds.isNull() or self._content_bounds.isEmpty():
            self._content_bounds = QRect(rect)
        else:
            self._content_bounds = self._content_bounds.united(rect)

    def get_content_bounds(self) -> QRect:
        """Bounding rect of ALL segments drawn this paint (network + CPU/GPU), for centering the
        context menu over the whole widget. Falls back to the last segment if nothing accumulated."""
        if self._content_bounds.isValid() and not self._content_bounds.isEmpty():
            return self._content_bounds
        return self._last_text_rect


    def pause(self) -> None:
        """Pauses graph updates."""
        self.paused = True
        self.logger.debug("Renderer paused.")


    def resume(self) -> None:
        """Resumes graph updates."""
        self.paused = False
        self.logger.debug("Renderer resumed.")
