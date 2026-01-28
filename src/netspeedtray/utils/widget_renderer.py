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

from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PyQt6.QtCore import Qt, QPointF, QRect

from netspeedtray import constants

from netspeedtray.core.widget_state import SpeedDataSnapshot, AggregatedSpeedData
from netspeedtray.utils.helpers import format_speed

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


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            # Use constants for all default fallbacks
            opacity = float(config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)) / 100.0
            
            return cls(
                color_coding=config.get('color_coding', constants.config.defaults.DEFAULT_COLOR_CODING),
                graph_enabled=config.get('graph_enabled', constants.config.defaults.DEFAULT_GRAPH_ENABLED),
                high_speed_threshold=float(config.get('high_speed_threshold', constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=constants.renderer.DEFAULT_ARROW_WIDTH, # No longer in config
                font_family=config.get('font_family', constants.config.defaults.DEFAULT_FONT_FAMILY),
                font_size=int(config.get('font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                font_weight=int(config.get('font_weight', constants.config.defaults.DEFAULT_FONT_WEIGHT)),
                default_color=config.get('default_color', constants.config.defaults.DEFAULT_COLOR),
                high_speed_color=config.get('high_speed_color', constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR),
                low_speed_color=config.get('low_speed_color', constants.config.defaults.DEFAULT_LOW_SPEED_COLOR),
                background_color=config.get('background_color', constants.config.defaults.DEFAULT_BACKGROUND_COLOR),
                background_opacity=max(0.0, min(1.0, float(config.get('background_opacity', constants.config.defaults.DEFAULT_BACKGROUND_OPACITY)) / 100.0)),
                graph_opacity=max(0.0, min(1.0, opacity)),
                speed_display_mode=config.get('speed_display_mode', constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE),
                decimal_places=int(config.get('decimal_places', constants.config.defaults.DEFAULT_DECIMAL_PLACES)),
                text_alignment=config.get('text_alignment', constants.config.defaults.DEFAULT_TEXT_ALIGNMENT),
                force_decimals=config.get('force_decimals', constants.config.defaults.DEFAULT_FORCE_DECIMALS),
                unit_type=config.get('unit_type', constants.config.defaults.DEFAULT_UNIT_TYPE),
                swap_upload_download=config.get('swap_upload_download', constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD),
                hide_arrows=config.get('hide_arrows', constants.config.defaults.DEFAULT_HIDE_ARROWS),
                hide_unit_suffix=config.get('hide_unit_suffix', constants.config.defaults.DEFAULT_HIDE_UNIT_SUFFIX),
                short_unit_labels=config.get('short_unit_labels', constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)
            )
        except (ValueError, TypeError) as e:
            logger.error("Failed to create RenderConfig from dict: %s", e)
            raise ValueError("Invalid rendering configuration") from e


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
                self.font = QFont(self.config.font_family, self.config.font_size, self.config.font_weight)
                self.metrics = QFontMetrics(self.font)
                
                self._last_text_rect = QRect()
                self._last_widget_size: Tuple[int, int] = (0, 0)
                self._cached_upload_points: List[QPointF] = []
                self._cached_download_points: List[QPointF] = []
                self._last_history_hash: int = 0
                
                self.paused = False
                self.logger.info("WidgetRenderer initialized.")
            except Exception as e:
                self.logger.error("Failed to initialize WidgetRenderer: %s", e)
                self.config = None
                self.font = QFont()
                self.metrics = QFontMetrics(self.font)
                raise RuntimeError("Renderer initialization failed") from e


    def _draw_error(self, painter: QPainter, rect: QRect, message: str) -> None:
        """Draws an error message on the widget."""
        painter.save()
        painter.fillRect(rect, QColor(150, 0, 0, 200))
        painter.setPen(Qt.GlobalColor.white)
        error_font = QFont(self.font)
        error_font.setPointSize(max(6, self.config.font_size - 2))
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
            always_mbps = config.speed_display_mode == "always_mbps"
            decimal_places = max(0, min(2, config.decimal_places))
            force_decimals = config.force_decimals
            unit_type = config.unit_type
            swap_order = config.swap_upload_download
            
            if layout_mode == 'horizontal':
                self._draw_horizontal_layout(painter, upload, download, width, height, config, always_mbps, decimal_places, force_decimals, unit_type, swap_order, config.short_unit_labels)
            else: # Default to vertical
                self._draw_vertical_layout(painter, upload, download, width, height, config, always_mbps, decimal_places, force_decimals, unit_type, swap_order, config.short_unit_labels)

        except Exception as e:
            self.logger.error("Failed to draw speeds: %s", e, exc_info=True)
            self._last_text_rect = QRect()


    def _draw_vertical_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, always_mbps: bool, decimal_places: int, force_decimals: bool, unit_type: str, swap_order: bool, short_labels: bool) -> None:
        """Draws the standard two-line vertical layout with correct compact centering."""
        line_height = self.metrics.height()
        ascent = self.metrics.ascent()
        
        # Calculate the height of ONLY the text itself to create a compact block.
        total_text_height = line_height * 2
        # Center this compact block vertically.
        top_y = int((height - total_text_height) / 2 + ascent)
        # The second line is positioned exactly one line_height below the first.
        bottom_y = top_y + line_height

        upload_text, download_text = self._format_speed_texts(upload, download, always_mbps, decimal_places, force_decimals, unit_type, short_labels=short_labels)
        up_val_str, up_unit = upload_text
        down_val_str, down_unit = download_text
        
        # Calculate widths, conditionally including arrows and units
        hide_arrows = config.hide_arrows
        hide_unit = config.hide_unit_suffix
        
        arrow_width = 0 if hide_arrows else self.metrics.horizontalAdvance(self.i18n.UPLOAD_ARROW)
        arrow_gap = 0 if hide_arrows else constants.renderer.ARROW_NUMBER_GAP
        
        # Use helpers for reference strings and labels
        from netspeedtray.utils.helpers import get_reference_value_string, get_unit_labels_for_type
        
        ref_val = get_reference_value_string(always_mbps, decimal_places)
        max_number_width = self.metrics.horizontalAdvance(ref_val)
        
        # For units: determine the widest possible unit label for the current unit_type
        # This prevents jumping when unit changes from "B" to "KiB" to "MiB" etc.
        unit_gap = 0 if hide_unit else constants.renderer.VALUE_UNIT_GAP
        
        if hide_unit:
            max_unit_width = 0
        else:
            # Get all possible unit labels for the current unit type and find the widest
            possible_units = get_unit_labels_for_type(self.i18n, unit_type, short_labels)
            
            max_unit_width = max(self.metrics.horizontalAdvance(u) for u in possible_units)
        
        content_width = arrow_width + arrow_gap + max_number_width + unit_gap + max_unit_width

        margin = self._calculate_margin(width, content_width, config.text_alignment)
        number_starting_x_base = margin + arrow_width + arrow_gap
        # The unit starts after the allocated number width
        unit_x = number_starting_x_base + max_number_width + unit_gap

        def draw_line(y_pos: int, arrow_char: str, val_str: str, unit_str: str, color: QColor):
            painter.setPen(color)
            if not hide_arrows:
                painter.drawText(margin, y_pos, arrow_char)
            
            # Right-align numbers within the fixed-width number column
            # This keeps digits stable regardless of value magnitude
            val_width = self.metrics.horizontalAdvance(val_str)
            val_x = number_starting_x_base + max_number_width - val_width

            painter.drawText(int(val_x), y_pos, val_str)
            
            # Units are left-aligned in their fixed-width column
            if not hide_unit:
                painter.drawText(unit_x, y_pos, unit_str)

        # Handle swap order: if swapped, draw download on top
        if swap_order:
            draw_line(top_y, self.i18n.DOWNLOAD_ARROW, down_val_str, down_unit, self._get_speed_color(download, config))
            draw_line(bottom_y, self.i18n.UPLOAD_ARROW, up_val_str, up_unit, self._get_speed_color(upload, config))
        else:
            draw_line(top_y, self.i18n.UPLOAD_ARROW, up_val_str, up_unit, self._get_speed_color(upload, config))
            draw_line(bottom_y, self.i18n.DOWNLOAD_ARROW, down_val_str, down_unit, self._get_speed_color(download, config))
        
        self._last_text_rect = QRect(margin, int(top_y - ascent), int(content_width), int(total_text_height))



    def _draw_horizontal_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, always_mbps: bool, decimal_places: int, force_decimals: bool, unit_type: str, swap_order: bool, short_labels: bool) -> None:
        """Draws the compact single-line horizontal layout."""
        upload_text_full, download_text_full = self._format_speed_texts(upload, download, always_mbps, decimal_places, force_decimals, unit_type, short_labels=short_labels, full_string=True)
        
        # Handle swap order
        if swap_order:
            first_arrow = self.i18n.DOWNLOAD_ARROW
            first_text = download_text_full
            first_speed = download
            second_arrow = self.i18n.UPLOAD_ARROW
            second_text = upload_text_full
            second_speed = upload
        else:
            first_arrow = self.i18n.UPLOAD_ARROW
            first_text = upload_text_full
            first_speed = upload
            second_arrow = self.i18n.DOWNLOAD_ARROW
            second_text = download_text_full
            second_speed = download
        
        first_str = f"{first_arrow} {first_text}"
        second_str = f"{second_arrow} {second_text}"
        separator = constants.layout.HORIZONTAL_LAYOUT_SEPARATOR
        
        first_width = self.metrics.horizontalAdvance(first_str)
        second_width = self.metrics.horizontalAdvance(second_str)
        separator_width = self.metrics.horizontalAdvance(separator)
        content_width = first_width + separator_width + second_width

        y_pos = int((height - self.metrics.height()) / 2 + self.metrics.ascent())
        margin = self._calculate_margin(width, content_width, config.text_alignment)
        
        first_x = margin
        separator_x = first_x + first_width
        second_x = separator_x + separator_width

        painter.setPen(self._get_speed_color(first_speed, config))
        painter.drawText(first_x, y_pos, first_str)
        
        painter.setPen(self.default_color)
        painter.drawText(separator_x, y_pos, separator)
        
        painter.setPen(self._get_speed_color(second_speed, config))
        painter.drawText(second_x, y_pos, second_str)

        self._last_text_rect = QRect(margin, (height - self.metrics.height()) // 2, int(content_width), self.metrics.height())


    def _format_speed_texts(self, upload: float, download: float, always_mbps: bool, decimal_places: int, force_decimals: bool, unit_type: str = "bits_decimal", short_labels: bool = False, full_string: bool = False) -> Tuple[Any, Any]:
        """Helper to format speed values into final strings or tuples using centralized logic."""
        up_formatted = format_speed(
            upload, self.i18n, always_mbps=always_mbps, decimal_places=decimal_places, 
            unit_type=unit_type, split_unit=not full_string, short_labels=short_labels
        )
        down_formatted = format_speed(
            download, self.i18n, always_mbps=always_mbps, decimal_places=decimal_places, 
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
        """Draws a mini graph of speed history, adapting to the current layout mode."""
        if not config.graph_enabled or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        try:
            if layout_mode == 'horizontal':
                graph_width = constants.layout.MINI_GRAPH_HORIZONTAL_WIDTH
                v_margin = constants.layout.DEFAULT_PADDING
                graph_rect = QRect(width - graph_width - constants.renderer.GRAPH_RIGHT_PADDING, v_margin, graph_width, height - (v_margin * 2))
            else:
                text_rect = self.get_last_text_rect()
                if not text_rect.isValid(): return
                v_padding = constants.renderer.GRAPH_MARGIN * 2
                graph_rect = text_rect.adjusted(0, -v_padding, 0, v_padding)

            if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

            # Hash the history data to prevent unnecessary recalculations
            # AggregatedSpeedData is a frozen dataclass, so it's hashable.
            current_hash = hash(tuple(history))

            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(history)
                # Gracefully handle empty history to prevent max() error
                if not history:
                    return

                max_speed_val = max(
                    max(d.upload for d in history),
                    max(d.download for d in history)
                ) if history else 0

                padded_max_speed = max_speed_val * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
                max_y = max(padded_max_speed, constants.renderer.MIN_Y_SCALE)
                step_x = graph_rect.width() / (num_points - 1) if num_points > 1 else 0

                self._cached_upload_points = [
                    QPointF(graph_rect.left() + i * step_x, graph_rect.bottom() - (d.upload / max_y) * graph_rect.height())
                    for i, d in enumerate(history)
                ]
                self._cached_download_points = [
                    QPointF(graph_rect.left() + i * step_x, graph_rect.bottom() - (d.download / max_y) * graph_rect.height())
                    for i, d in enumerate(history)
                ]
                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            upload_pen = QPen(QColor(constants.graph.UPLOAD_LINE_COLOR), constants.renderer.LINE_WIDTH)
            painter.setPen(upload_pen)
            painter.drawPolyline(self._cached_upload_points)

            download_pen = QPen(QColor(constants.graph.DOWNLOAD_LINE_COLOR), constants.renderer.LINE_WIDTH)
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