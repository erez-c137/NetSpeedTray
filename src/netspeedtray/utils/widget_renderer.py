"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration. This renderer
supports multiple layouts (e.g., vertical, horizontal) to adapt to different UI constraints.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

from netspeedtray.core.widget_state import SpeedDataSnapshot, AggregatedSpeedData
from netspeedtray.utils.helpers import format_speed, calculate_monotone_cubic_interpolation
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPainterPath
from PyQt6.QtCore import Qt, QPointF, QRect
from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.WidgetRenderer")


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
    use_separate_arrow_font: bool = False
    arrow_font_family: str = constants.config.defaults.DEFAULT_FONT_FAMILY
    arrow_font_size: int = 9
    arrow_font_weight: int = constants.fonts.WEIGHT_DEMIBOLD
    
    # New: Hardware Monitoring Toggles
    monitor_cpu_enabled: bool = False
    monitor_gpu_enabled: bool = False
    monitor_ram_enabled: bool = False
    monitor_vram_enabled: bool = False
    widget_display_mode: str = "network_only"
    widget_display_order: List[str] = field(default_factory=lambda: ["network", "cpu", "gpu"])


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            opacity_raw = config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)
            opacity = float(opacity_raw) / 100.0 if opacity_raw is not None else (constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
            
            hist_mins = int(config.get('history_minutes', constants.config.defaults.DEFAULT_HISTORY_MINUTES))
            rate = float(config.get('update_rate', constants.config.defaults.DEFAULT_UPDATE_RATE))
            if rate <= 0: rate = 1.0
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
                short_unit_labels=bool(config.get('short_unit_labels', constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)),
                max_samples=max_samples,
                use_separate_arrow_font=bool(config.get('use_separate_arrow_font', False)),
                arrow_font_family=str(config.get('arrow_font_family', constants.config.defaults.DEFAULT_FONT_FAMILY)),
                arrow_font_size=int(config.get('arrow_font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                arrow_font_weight=int(config.get('arrow_font_weight', constants.fonts.WEIGHT_DEMIBOLD)),
                
                # New
                monitor_cpu_enabled=bool(config.get('monitor_cpu_enabled', False)),
                monitor_gpu_enabled=bool(config.get('monitor_gpu_enabled', False)),
                monitor_ram_enabled=bool(config.get('monitor_ram_enabled', False)),
                monitor_vram_enabled=bool(config.get('monitor_vram_enabled', False)),
                widget_display_mode=str(config.get('widget_display_mode', 'network_only')),
                widget_display_order=list(config.get('widget_display_order', ["network", "cpu", "gpu"]))
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
            try:
                self.paused = False
                
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
            'gpu': QPen(QColor(constants.renderer.GPU_LINE_COLOR))
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

    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, layout_mode: str = 'vertical', x_offset: int = 0, fixed_width: Optional[int] = None) -> None:
        """Draws current upload and download speeds."""
        try:
            # Format speeds
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

            painter.setFont(self.font)
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            
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

            # 3. Arrow Width (use max of UP/DW arrows)
            arrow_up = self.i18n.UPLOAD_ARROW
            arrow_dw = self.i18n.DOWNLOAD_ARROW
            max_arrow_width = max(
                self.arrow_metrics.horizontalAdvance(arrow_up),
                self.arrow_metrics.horizontalAdvance(arrow_dw)
            ) if not config.hide_arrows else 0
            
            # Constants for gaps
            arrow_gap = constants.renderer.ARROW_NUMBER_GAP if not config.hide_arrows else 0
            unit_gap = constants.renderer.VALUE_UNIT_GAP if not config.hide_unit_suffix else 0
            vertical_gap = 1
            margin = constants.renderer.TEXT_MARGIN
            
            # Fixed Offsets (Arrow and Number start are fixed)
            arrow_x = x_offset + margin
            number_x = arrow_x + max_arrow_width + arrow_gap
            
            # Unit offset is relative to the (potentially expanded) number area
            unit_x = number_x + number_area_width + unit_gap
            
            # Default Vertical Layout (Stack UP over DW)
            total_height = (line_height * 2) + vertical_gap
            top_y = int((height - total_height) / 2 + ascent)
            
            # Draw UP
            self._draw_speed_line_fixed(painter, config.swap_upload_download, up_val, up_unit, arrow_x, number_x, unit_x, top_y, config, number_area_width)
            
            # Draw DW
            dw_y = top_y + line_height + vertical_gap
            self._draw_speed_line(painter, not config.swap_upload_download, dw_val, dw_unit, arrow_x, number_x, unit_x, dw_y, config, number_area_width)

        except Exception as e:
            self.logger.error("Failed to draw network speeds: %s", e)

    def _draw_speed_line(self, painter: QPainter, is_upload: bool, val: str, unit: str, arrow_x: int, number_x: int, unit_x: int, y: int, config: RenderConfig, number_area_width: int) -> None:
        """Unified helper to draw a single speed line (Arrow + Value + Unit) with stable alignment."""
        # Color coding
        if config.color_coding:
            try:
                # Clean up commas and spaces for float conversion
                clean_val = val.replace(',', '').replace(' ', '').strip()
                f_val = float(clean_val)
                if f_val >= config.high_speed_threshold: 
                    painter.setPen(self._cached_pens['high'])
                elif f_val <= config.low_speed_threshold: 
                    painter.setPen(self._cached_pens['low'])
                else: 
                    painter.setPen(self._cached_pens['default'])
            except:
                painter.setPen(self._cached_pens['default'])
        else:
            painter.setPen(self._cached_pens['default'])

        # 1. Draw Arrow
        if not config.hide_arrows:
            painter.setFont(self.arrow_font)
            arrow = self.i18n.UPLOAD_ARROW if is_upload else self.i18n.DOWNLOAD_ARROW
            painter.drawText(arrow_x, y, arrow)

        # 2. Draw Value (Right-aligned within fixed/expanded number area)
        painter.setFont(self.font)
        val_width = self.metrics.horizontalAdvance(val)
        aligned_number_x = number_x + (number_area_width - val_width)
        painter.drawText(int(aligned_number_x), y, val)
        
        # 3. Draw Unit
        if not config.hide_unit_suffix:
            painter.drawText(unit_x, y, unit)

    def _draw_speed_line_fixed(self, *args, **kwargs):
        """Deprecated alias for _draw_speed_line."""
        return self._draw_speed_line(*args, **kwargs)




    def draw_hardware_stats(self, painter: QPainter, cpu_usage: Optional[float], gpu_usage: Optional[float], 
                           width: int, height: int, config: RenderConfig, 
                           cpu_temp: Optional[float] = None, gpu_temp: Optional[float] = None,
                           ram_info: Optional[Tuple[float, float]] = None, 
                           vram_info: Optional[Tuple[float, float]] = None,
                           layout_mode: str = 'vertical', x_offset: int = 0, fixed_width: Optional[int] = None) -> None:
        """Draws CPU and/or GPU utilization statistics with optional temperature and memory."""
        try:
            enabled_stats = []
            if cpu_usage is not None: 
                enabled_stats.append(('CPU', cpu_usage, cpu_temp, ram_info, constants.renderer.CPU_LINE_COLOR))
            if gpu_usage is not None: 
                enabled_stats.append(('GPU', gpu_usage, gpu_temp, vram_info, constants.renderer.GPU_LINE_COLOR))
            
            if not enabled_stats: return
            
            painter.setFont(self.font)
            
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            total_height = line_height * len(enabled_stats)
            top_y = int((height - total_height) / 2 + ascent)
            
            margin = constants.renderer.TEXT_MARGIN
            current_x = x_offset + margin
            
            # Use color differentiator but icons are the primary identifier now
            for i, (label, val, temp, mem_info, color_hex) in enumerate(enabled_stats):
                y_pos = top_y + (i * line_height)
                icon_color = QColor(color_hex)
                self._draw_icon(painter, label, current_x, y_pos, icon_color)
                
                # Format: "Usage% (Temp) | Used/Total"
                val_str = f"{int(val)}%"
                if temp is not None:
                    val_str += f" ({int(temp)}°C)"
                
                if mem_info and mem_info[0] is not None:
                    used, total = mem_info
                    if total is not None:
                        val_str += f" | {used:.1f}/{total:.1f}G"
                    else:
                        # Fallback for universal PDH where total might be unknown
                        val_str += f" | {used:.1f}G"
                    
                val_x = current_x + 14 # Icon width + gap
                # Stick to default color for text to keep it clean, or use color if it helps
                # User said "not loving color as differentiator", so primary focus is icons.
                # However, subtle color helps grouping. I'll use default color for text for maximum cleanliness.
                painter.setPen(self.default_color)
                painter.drawText(val_x, y_pos, val_str)

        except Exception as e:
            self.logger.error("Failed to draw hardware stats: %s", e)


    def _draw_icon(self, painter: QPainter, icon_type: str, x: int, y_ascent: int, color: Optional[QColor] = None) -> None:
        """Draws a tiny symbolic icon for CPU or GPU."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Icon box size
        size = 11
        rect = QRect(x, y_ascent - size + 1, size, size)
        
        draw_color = color if color else self.default_color
        pen = QPen(draw_color, 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        if icon_type == 'CPU':
            # Microchip with 'C'
            inner_rect = rect.adjusted(2, 2, -2, -2)
            painter.drawRect(inner_rect)
            # "Pins"
            for offset in [0, 5, 9]: # top/bottom
                painter.drawLine(rect.left() + offset, rect.top(), rect.left() + offset, rect.top() + 1)
                painter.drawLine(rect.left() + offset, rect.bottom() - 1, rect.left() + offset, rect.bottom())
            for offset in [2, 6, 8]: # left/right
                painter.drawLine(rect.left(), rect.top() + offset, rect.left() + 1, rect.top() + offset)
                painter.drawLine(rect.right() - 1, rect.top() + offset, rect.right(), rect.top() + offset)
            
            # Tiny 'C'
            small_font = QFont(self.font.family(), 6)
            painter.setFont(small_font)
            painter.drawText(inner_rect, Qt.AlignmentFlag.AlignCenter, "C")

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


    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig, 
                        history: List[Any], layout_mode: str = 'vertical', 
                        is_hardware: bool = False, hardware_color: str = "#FFFFFF") -> None:
        """Draws a mini graph of history (speed or hardware utilization)."""
        if not config.graph_enabled or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        try:
            side_margin = constants.renderer.GRAPH_LEFT_PADDING
            top_margin = constants.renderer.GRAPH_MARGIN
            bottom_margin = constants.renderer.GRAPH_BOTTOM_PADDING
            
            graph_rect = QRect(side_margin, top_margin, width - (side_margin * 2), height - (top_margin + bottom_margin))
            if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

            # Hash check for caching
            current_hash = hash((tuple(history), is_hardware))

            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(history)
                
                if is_hardware:
                    from netspeedtray.core.widget_state import HardwareStatSnapshot
                    # Hardware is 0-100%
                    max_y = 100.0
                else:
                    # Speed history
                    max_speed_val = max(
                        max(d.upload for d in history),
                        max(d.download for d in history)
                    ) if history else 0
                    
                    if len(history) > 10:
                        all_speeds = [d.upload for d in history] + [d.download for d in history]
                        all_speeds_sorted = sorted(all_speeds)
                        percentile_95 = all_speeds_sorted[int(len(all_speeds_sorted) * 0.95)]
                        if percentile_95 > 0 and max_speed_val > percentile_95 * 3.0:
                            max_speed_val = percentile_95

                    padded_max_speed = max_speed_val * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
                    max_y = max(padded_max_speed, constants.renderer.MIN_Y_SCALE)
                
                max_samples = max(2, config.max_samples)
                step_x = graph_rect.width() / (max_samples - 1)
                right_edge = float(graph_rect.right())
                base_y = float(graph_rect.bottom())
                h = float(graph_rect.height())

                raw_x = [right_edge - (num_points - 1 - i) * step_x for i in range(num_points)]
                
                def make_smooth_polyline(accessor):
                    raw_y = [accessor(d) for d in history]
                    cx, cy = calculate_monotone_cubic_interpolation(raw_x, raw_y, density=5)
                    points = [QPointF(x, base_y - (max(0, y) / max_y) * h) for x, y in zip(cx, cy)]
                    return points

                if is_hardware:
                    self._cached_upload_points = make_smooth_polyline(lambda d: d.value)
                    self._cached_download_points = []
                else:
                    self._cached_upload_points = make_smooth_polyline(lambda d: d.upload)
                    self._cached_download_points = make_smooth_polyline(lambda d: d.download)

                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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
                draw_area(self._cached_upload_points, hardware_color)
            else:
                draw_area(self._cached_upload_points, constants.graph.UPLOAD_LINE_COLOR)
                draw_area(self._cached_download_points, constants.graph.DOWNLOAD_LINE_COLOR)
            
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            stroke_width = 1.5 

            if is_hardware:
                hw_pen = QPen(QColor(hardware_color), stroke_width)
                hw_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(hw_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(self._cached_upload_points)
            else:
                upload_pen = QPen(QColor(constants.graph.UPLOAD_LINE_COLOR), stroke_width)
                upload_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(upload_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(self._cached_upload_points)

                download_pen = QPen(QColor(constants.graph.DOWNLOAD_LINE_COLOR), stroke_width)
                download_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(download_pen)
                painter.drawPolyline(self._cached_download_points)

            painter.restore()
        except Exception as e:
            self.logger.error("Failed to draw mini graph: %s", e)


    def update_config(self, config_dict: Dict[str, Any]) -> None:
        """Updates rendering configuration."""
        try:
            self.config = RenderConfig.from_dict(config_dict)
            self.default_color = QColor(self.config.default_color)
            self.high_color = QColor(self.config.high_speed_color)
            self.low_color = QColor(self.config.low_speed_color)
            self.font = QFont(self.config.font_family, self.config.font_size, self.config.font_weight)
            self.metrics = QFontMetrics(self.font)
            
            # Update Arrow Font
            if self.config.use_separate_arrow_font:
                self.arrow_font = QFont(self.config.arrow_font_family, self.config.arrow_font_size, int(self.config.arrow_font_weight))
            else:
                self.arrow_font = self.font
            self.arrow_metrics = QFontMetrics(self.arrow_font)

            self._cached_upload_points = []
            self._cached_download_points = []
            self._last_history_hash = 0
            self.logger.debug("Renderer config updated.")
        except Exception as e:
            self.logger.error("Failed to update config: %s", e)


    def get_last_text_rect(self) -> QRect:
        """Returns last text bounding rect."""
        return self._last_text_rect


    def pause(self) -> None:
        """Pauses graph updates."""
        self.paused = True
        self.logger.debug("Renderer paused.")


    def resume(self) -> None:
        """Resumes graph updates."""
        self.paused = False
        self.logger.debug("Renderer resumed.")