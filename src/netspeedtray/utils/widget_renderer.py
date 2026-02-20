"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration. This renderer
supports multiple layouts (e.g., vertical, horizontal) to adapt to different UI constraints.
"""

import logging
import math
from typing import Tuple, List, Optional, Dict, Any
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


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            # Use constants for all default fallbacks
            opacity_raw = config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)
            opacity = float(opacity_raw) / 100.0 if opacity_raw is not None else (constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
            
            # Calculate max samples for graph scaling (#91)
            hist_mins = int(config.get('history_minutes', constants.config.defaults.DEFAULT_HISTORY_MINUTES))
            rate = float(config.get('update_rate', constants.config.defaults.DEFAULT_UPDATE_RATE))
            if rate <= 0: rate = 1.0
            max_samples = int((hist_mins * 60) / rate)

            # Font Weight handling (robust against legacy string values)
            weight_raw = config.get('font_weight', constants.fonts.WEIGHT_DEMIBOLD)
            if isinstance(weight_raw, str):
                weight_val = {
                    "normal": constants.fonts.WEIGHT_NORMAL, 
                    "bold": constants.fonts.WEIGHT_BOLD
                }.get(weight_raw.lower(), constants.fonts.WEIGHT_NORMAL)
            else:
                try:
                    weight_val = int(weight_raw)
                except (ValueError, TypeError):
                    weight_val = constants.fonts.WEIGHT_DEMIBOLD

            return cls(
                color_coding=bool(config.get('color_coding', constants.config.defaults.DEFAULT_COLOR_CODING)),
                graph_enabled=bool(config.get('graph_enabled', constants.config.defaults.DEFAULT_GRAPH_ENABLED)),
                high_speed_threshold=float(config.get('high_speed_threshold', constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=constants.renderer.DEFAULT_ARROW_WIDTH, # No longer in config
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
                arrow_font_weight=int(config.get('arrow_font_weight', constants.fonts.WEIGHT_DEMIBOLD))
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig from dict: %s", e, exc_info=True)
            # Re-raise as ValueError with context to allow upstream handling
            raise ValueError(f"Invalid rendering configuration: {e}") from e


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
                self.config = RenderConfig.from_dict(config)
                self.default_color = QColor(self.config.default_color)
                self.high_color = QColor(self.config.high_speed_color)
                self.low_color = QColor(self.config.low_speed_color)
                # FIX for #89: Ensure font weight is int
                weight = int(self.config.font_weight)
                self.font = QFont(self.config.font_family, self.config.font_size, weight)
                self.metrics = QFontMetrics(self.font)
                
                # Arrow font
                if self.config.use_separate_arrow_font:
                    self.arrow_font = QFont(self.config.arrow_font_family, self.config.arrow_font_size, int(self.config.arrow_font_weight))
                else:
                    self.arrow_font = self.font
                self.arrow_metrics = QFontMetrics(self.arrow_font)
                
                self._last_text_rect = QRect()
                self._last_widget_size: Tuple[int, int] = (0, 0)
                self._cached_upload_points: List[QPointF] = []
                self._cached_download_points: List[QPointF] = []
                self._last_history_hash: int = 0
                
                self.paused = False
                self.logger.debug("WidgetRenderer initialized.")
            except Exception as e:
                self.logger.error("Failed to initialize WidgetRenderer: %s", e)
                # Fail gracefully
                self.config = None
                self.font = QFont()
                self.metrics = QFontMetrics(self.font)
                raise RuntimeError("Renderer initialization failed") from e


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
        painter.save()
        bg_color = QColor(config.background_color)
        
        # CRITICAL FIX: Ensure minimum opacity of ~0.004 (1/255) so the window system
        # treats the window as "hit-testable" even if visually transparent.
        # This prevents clicks from falling through or causing undefined behavior.
        min_opacity = 1.0 / 255.0
        effective_opacity = max(config.background_opacity, min_opacity)
        
        bg_color.setAlphaF(effective_opacity)
        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4) # Rounded corners for polish
        painter.restore()


    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, layout_mode: str = 'vertical') -> None:
        """Draws upload/download speeds, adapting to vertical or horizontal layouts."""
        try:
            force_mega_unit = config.speed_display_mode == "always_mbps"
            decimal_places = max(0, min(2, config.decimal_places))
            force_decimals = config.force_decimals
            unit_type = config.unit_type
            swap_order = config.swap_upload_download
            
            if layout_mode == 'horizontal':
                self._draw_horizontal_layout(painter, upload, download, width, height, config, force_mega_unit, decimal_places, force_decimals, unit_type, swap_order, config.short_unit_labels)
            else: # Default to vertical
                self._draw_vertical_layout(painter, upload, download, width, height, config, force_mega_unit, decimal_places, force_decimals, unit_type, swap_order, config.short_unit_labels)

        except Exception as e:
            self.logger.error("Failed to draw speeds: %s", e, exc_info=True)
            self._last_text_rect = QRect()


    # ... (skipping _draw_vertical_layout and _draw_horizontal_layout and helpers as they are unchanged) ...
    # Wait, I need to keep them or the tool will delete them if I don't include them in the range or chunk.
    # The user asked for "ReplacementContent" for the replaced range.
    # I should target the Class RenderConfig and WidgetRenderer.__init__ and draw_mini_graph?
    # No, I should replace blocks.
    
    # Let's replace the whole top part of file including RenderConfig.
    # And then a second chunk for draw_mini_graph?
    # Ah, the tool `replace_file_content` is for single contiguous block.
    # `multi_replace_file_content` is better here.
    pass

    def _draw_vertical_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, force_mega_unit: bool, decimal_places: int, force_decimals: bool, unit_type: str, swap_order: bool, short_labels: bool) -> None:
        """
        Draws the standard two-line vertical layout.
        Includes INTELLIGENT SCALING to fit narrow/vertical taskbars (#99).
        """
        # --- Preparation & Helpers ---
        # Helper to get current metrics
        def get_width_metrics(current_metrics, current_arrow_metrics):
            arrow_w = 0 if hide_arrows else current_arrow_metrics.horizontalAdvance(self.i18n.UPLOAD_ARROW)
            arrow_g = 0 if hide_arrows else constants.renderer.ARROW_NUMBER_GAP
            
            ref_v = get_reference_value_string(force_mega_unit, decimal_places, unit_type=unit_type)
            max_num_w = current_metrics.horizontalAdvance(ref_v)
            
            unit_g = 0 if hide_unit else constants.renderer.VALUE_UNIT_GAP
            if hide_unit:
                max_u_w = 0
            else:
                p_units = get_unit_labels_for_type(self.i18n, unit_type, short_labels)
                max_u_w = max(current_metrics.horizontalAdvance(u) for u in p_units)
                
            total_w = arrow_w + arrow_g + max_num_w + unit_g + max_u_w
            return total_w, arrow_w, arrow_g, max_num_w, unit_g, max_u_w

        from netspeedtray.utils.helpers import get_reference_value_string, get_unit_labels_for_type

        upload_text, download_text = self._format_speed_texts(upload, download, force_mega_unit, decimal_places, force_decimals, unit_type, short_labels=short_labels)
        up_val_str, up_unit = upload_text
        down_val_str, down_unit = download_text
        
        hide_arrows = config.hide_arrows
        hide_unit = config.hide_unit_suffix
        
        # --- Font Scaling Logic ---
        # Start with configured fonts
        draw_font = QFont(self.font)
        draw_arrow_font = QFont(self.arrow_font)
        
        # Iteratively reduce size if too wide
        min_font_size = 6
        current_size = self.config.font_size
        
        # Safety margin to prevent edge-touching
        # Narrow taskbars (e.g. 60px) need strict margins
        safe_width = width - (constants.renderer.TEXT_MARGIN * 2) 
        if safe_width < 10: safe_width = 10 # Sanity check
        
        metrics = QFontMetrics(draw_font)
        arrow_metrics = QFontMetrics(draw_arrow_font)
        
        content_width, arrow_width, arrow_gap, max_number_width, unit_gap, max_unit_width = get_width_metrics(metrics, arrow_metrics)
        
        # Scaling Loop
        while content_width > safe_width and current_size > min_font_size:
            current_size -= 1
            draw_font.setPointSize(current_size)
            metrics = QFontMetrics(draw_font)
            
            # Scale arrow font proportionally if it was separate, or keep in sync if same
            if self.config.use_separate_arrow_font:
                # Naive scaling: reduce by 1 as well? Or keep ratio?
                # Let's just reduce by 1 to keep it simple and safe.
                af_size = max(min_font_size, draw_arrow_font.pointSize() - 1)
                draw_arrow_font.setPointSize(af_size)
            else:
                draw_arrow_font.setPointSize(current_size)
                
            arrow_metrics = QFontMetrics(draw_arrow_font)
            content_width, arrow_width, arrow_gap, max_number_width, unit_gap, max_unit_width = get_width_metrics(metrics, arrow_metrics)

        # --- Draw Calculation ---
        line_height = metrics.height()
        ascent = metrics.ascent()
        total_text_height = line_height * 2
        top_y = int((height - total_text_height) / 2 + ascent)
        bottom_y = top_y + line_height

        margin = self._calculate_margin(width, content_width, config.text_alignment)
        
        # If we scaled down heavily, force centering to look better than left-aligned cramming
        if current_size < self.config.font_size:
             # Recalculate margin for true center in limited space
             margin = int((width - content_width) / 2)

        number_starting_x_base = margin + arrow_width + arrow_gap
        unit_x = number_starting_x_base + max_number_width + unit_gap

        def draw_line(y_pos: int, arrow_char: str, val_str: str, unit_str: str, color: QColor):
            painter.setPen(color)
            if not hide_arrows:
                painter.setFont(draw_arrow_font)
                painter.drawText(margin, y_pos, arrow_char)
            
            painter.setFont(draw_font)
            
            val_width = metrics.horizontalAdvance(val_str)
            val_x = number_starting_x_base + max_number_width - val_width
            painter.drawText(int(val_x), y_pos, val_str)
            
            if not hide_unit:
                painter.drawText(unit_x, y_pos, unit_str)

        if swap_order:
            draw_line(top_y, self.i18n.DOWNLOAD_ARROW, down_val_str, down_unit, self._get_speed_color(download, config))
            draw_line(bottom_y, self.i18n.UPLOAD_ARROW, up_val_str, up_unit, self._get_speed_color(upload, config))
        else:
            draw_line(top_y, self.i18n.UPLOAD_ARROW, up_val_str, up_unit, self._get_speed_color(upload, config))
            draw_line(bottom_y, self.i18n.DOWNLOAD_ARROW, down_val_str, down_unit, self._get_speed_color(download, config))
        
        self._last_text_rect = QRect(margin, int(top_y - ascent), int(content_width), int(total_text_height))



    def _draw_horizontal_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, force_mega_unit: bool, decimal_places: int, force_decimals: bool, unit_type: str, swap_order: bool, short_labels: bool) -> None:
        """Draws the compact single-line horizontal layout."""
        # Get split value/unit pairs
        upload_pair, download_pair = self._format_speed_texts(upload, download, force_mega_unit, decimal_places, force_decimals, unit_type, short_labels=short_labels, full_string=False)
        
        up_val, up_unit = upload_pair
        down_val, down_unit = download_pair

        hide_arrows = config.hide_arrows
        hide_unit = config.hide_unit_suffix

        def build_string(arrow_char: str, val_str: str, unit_str: str) -> str:
            parts = []
            if not hide_arrows:
                parts.append(arrow_char)
            parts.append(val_str)
            if not hide_unit:
                parts.append(unit_str)
            # Note: For horizontal layout, we still use full string drawing usually,
            # but if using separate fonts we must draw parts manually.
            pass

        def draw_part_h(x_pos: int, arrow_char: str, val_str: str, unit_str: str, color: QColor) -> int:
            painter.setPen(color)
            current_x = x_pos
            if not hide_arrows:
                painter.setFont(self.arrow_font)
                painter.drawText(current_x, y_pos, arrow_char)
                current_x += self.arrow_metrics.horizontalAdvance(arrow_char) + self.arrow_metrics.horizontalAdvance(" ")
                painter.setFont(self.font)
            
            painter.drawText(current_x, y_pos, val_str)
            current_x += self.metrics.horizontalAdvance(val_str)
            
            if not hide_unit:
                painter.drawText(current_x, y_pos, " " + unit_str)
                current_x += self.metrics.horizontalAdvance(" " + unit_str)
            
            return current_x

        y_pos = int((height - self.metrics.height()) / 2 + self.metrics.ascent())
        
        # Calculate totals for alignment
        def get_width(arrow_char, val, unit):
            w = 0
            if not hide_arrows:
                w += self.arrow_metrics.horizontalAdvance(arrow_char) + self.arrow_metrics.horizontalAdvance(" ")
            w += self.metrics.horizontalAdvance(val)
            if not hide_unit:
                w += self.metrics.horizontalAdvance(" " + unit)
            return w

        up_width = get_width(self.i18n.UPLOAD_ARROW, up_val, up_unit)
        down_width = get_width(self.i18n.DOWNLOAD_ARROW, down_val, down_unit)
        sep = constants.layout.HORIZONTAL_LAYOUT_SEPARATOR
        sep_width = self.metrics.horizontalAdvance(sep)
        
        total_width = up_width + sep_width + down_width
        start_x = self._calculate_margin(width, total_width, config.text_alignment)
        
        painter.setFont(self.font) # Default starting font

        if swap_order:
            next_x = draw_part_h(start_x, self.i18n.DOWNLOAD_ARROW, down_val, down_unit, self._get_speed_color(download, config))
            painter.setPen(self.default_color)
            painter.drawText(next_x, y_pos, sep)
            draw_part_h(next_x + sep_width, self.i18n.UPLOAD_ARROW, up_val, up_unit, self._get_speed_color(upload, config))
        else:
            next_x = draw_part_h(start_x, self.i18n.UPLOAD_ARROW, up_val, up_unit, self._get_speed_color(upload, config))
            painter.setPen(self.default_color)
            painter.drawText(next_x, y_pos, sep)
            draw_part_h(next_x + sep_width, self.i18n.DOWNLOAD_ARROW, down_val, down_unit, self._get_speed_color(download, config))
            
        self._last_text_rect = QRect(start_x, int(y_pos - self.metrics.ascent()), int(total_width), self.metrics.height())


    def _format_speed_texts(self, upload: float, download: float, force_mega_unit: bool, decimal_places: int, force_decimals: bool, unit_type: str = "bits_decimal", short_labels: bool = False, full_string: bool = False) -> Tuple[Any, Any]:
        """Helper to format speed values into final strings or tuples using centralized logic."""
        up_formatted = format_speed(
            upload, self.i18n, force_mega_unit=force_mega_unit, decimal_places=decimal_places, 
            unit_type=unit_type, split_unit=not full_string, short_labels=short_labels
        )
        down_formatted = format_speed(
            download, self.i18n, force_mega_unit=force_mega_unit, decimal_places=decimal_places, 
            unit_type=unit_type, split_unit=not full_string, short_labels=short_labels
        )
        
        # If full_string is True, format_speed returns a string.
        # If full_string is False (split_unit is True), format_speed returns Tuple[str, str].
        return up_formatted, down_formatted


    def _calculate_margin(self, width: int, content_width: float, alignment_str: str) -> int:
        """Calculates the starting X coordinate as an integer based on text alignment."""
        align_map = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}
        alignment = align_map.get(alignment_str, Qt.AlignmentFlag.AlignHCenter)
        
        margin_px = constants.renderer.TEXT_MARGIN

        if alignment == Qt.AlignmentFlag.AlignLeft:
            return margin_px
        elif alignment == Qt.AlignmentFlag.AlignRight:
            return int(max(width - content_width - margin_px, margin_px))
        else: # Center
            return int(max((width - content_width) / 2, margin_px))


    def _get_speed_color(self, speed: float, config: RenderConfig) -> QColor:
        """Returns color based on speed thresholds."""
        if not config.color_coding:
            return self.default_color
        
        # Thresholds are in Mbps, speed is in bytes/sec. Convert for comparison.
        speed_mbps = (speed * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
        
        if speed_mbps >= config.high_speed_threshold:
            return self.high_color
        if speed_mbps >= config.low_speed_threshold:
            return self.low_color
        return self.default_color


    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig, history: List[AggregatedSpeedData], layout_mode: str = 'vertical') -> None:
        """Draws a mini graph of speed history with gradient area fill."""
        if not config.graph_enabled or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        try:
            # Layout Logic: Use full widget width/height minus margins, 
            # instead of relying on potentially uninitialized text rects.
            side_margin = constants.renderer.GRAPH_LEFT_PADDING
            top_margin = constants.renderer.GRAPH_MARGIN
            bottom_margin = constants.renderer.GRAPH_BOTTOM_PADDING
            
            # Reduce height slightly to avoid text overlap if needed, but since it's background, 
            # full coverage looks better (Area Chart style).
            graph_rect = QRect(
                side_margin, 
                top_margin, 
                width - (side_margin * 2), 
                height - (top_margin + bottom_margin)
            )

            if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

            # Hash check for caching
            current_hash = hash(tuple(history))

            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(history)
                if not history: return

                max_speed_val = max(
                    max(d.upload for d in history),
                    max(d.download for d in history)
                ) if history else 0

                # Filter extreme outliers that would distort Y-axis scaling
                # Use 95th percentile if max is significantly higher than typical range
                if len(history) > 10:
                    all_speeds = [d.upload for d in history] + [d.download for d in history]
                    all_speeds_sorted = sorted(all_speeds)
                    percentile_95 = all_speeds_sorted[int(len(all_speeds_sorted) * 0.95)]
                    
                    # If max is >3x the 95th percentile, likely a phantom spike. Use 95th instead.
                    if percentile_95 > 0 and max_speed_val > percentile_95 * 3.0:
                        max_speed_val = percentile_95

                padded_max_speed = max_speed_val * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
                max_y = max(padded_max_speed, constants.renderer.MIN_Y_SCALE)
                
                max_samples = max(2, config.max_samples)
                step_x = graph_rect.width() / (max_samples - 1)

                right_edge = float(graph_rect.right())
                base_y = float(graph_rect.bottom())
                h = float(graph_rect.height())

                # Generate Points
                # Note: We create Polygons for the fill (start at bottom, go up, follow line, go down)
                
                # Generate Points with Spline Interpolation for "Fluid Motion"
                # X coordinates are relative to the right edge (0 = latest/rightmost)
                raw_x = [right_edge - (num_points - 1 - i) * step_x for i in range(num_points)]
                
                def make_smooth_polyline(accessor):
                    raw_y = [accessor(d) for d in history]
                    
                    # Interpolate (density=5 gives nice smoothness without costing too much CPU)
                    cx, cy = calculate_monotone_cubic_interpolation(raw_x, raw_y, density=5)
                    
                    # Map to Screen Coordinates
                    points = []
                    for x, y in zip(cx, cy):
                         # Clip y to 0 (spline shouldn't go below 0 due to monotonic property, but floats can drift)
                         y = max(0, y)
                         screen_y = base_y - (y / max_y) * h
                         points.append(QPointF(x, screen_y))
                    return points

                self._cached_upload_points = make_smooth_polyline(lambda d: d.upload)
                self._cached_download_points = make_smooth_polyline(lambda d: d.download)

                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # --- Draw Gradients (Fill) with "Neon" Additive Blending ---
            # Helper to draw area
            from PyQt6.QtGui import QLinearGradient, QBrush, QPolygonF
            
            # Use Additive blending for the glow effect if opacity is low enough to benefit
            # (CompositionMode_Plus adds colors: overlapping areas become brighter)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

            def draw_area(points, color_hex):
                if not points: return
                poly_points = [QPointF(points[0].x(), float(graph_rect.bottom()))] # Start bottom-left
                poly_points.extend(points)
                poly_points.append(QPointF(points[-1].x(), float(graph_rect.bottom()))) # End bottom-right
                
                grad = QLinearGradient(0, graph_rect.top(), 0, graph_rect.bottom())
                c = QColor(color_hex)
                c.setAlpha(120) # Start semi-opaque
                grad.setColorAt(0.0, c)
                c.setAlpha(0)   # Fade to transparent
                grad.setColorAt(1.0, c)
                
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPolygon(QPolygonF(poly_points))

            draw_area(self._cached_upload_points, constants.graph.UPLOAD_LINE_COLOR)
            draw_area(self._cached_download_points, constants.graph.DOWNLOAD_LINE_COLOR)
            
            # Restore Normal blending for Lines (strokes needs to be crisp on top)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            # --- Draw Lines (Stroke) ---
            # Thicker lines for visibility
            stroke_width = 1.5 

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
            self.logger.error("Failed to draw mini graph: %s", e, exc_info=True)


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