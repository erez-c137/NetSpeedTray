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

from ..core.widget_state import SpeedDataSnapshot
from ..utils.helpers import format_speed

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
    graph_opacity: float = field(default_factory=lambda: constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
    speed_display_mode: str = constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE
    decimal_places: int = constants.config.defaults.DEFAULT_DECIMAL_PLACES
    text_alignment: str = constants.config.defaults.DEFAULT_TEXT_ALIGNMENT
    force_decimals: bool = False


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            opacity = float(config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)) / 100.0
            weight = config.get('font_weight', constants.config.defaults.DEFAULT_FONT_WEIGHT)
            if isinstance(weight, str):
                weights = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}
                weight = weights.get(weight.lower(), constants.config.defaults.DEFAULT_FONT_WEIGHT)
            weight = int(weight)
            if not 1 <= weight <= 1000:
                logger.warning("Invalid font_weight %s, using default.", weight)
                weight = constants.config.defaults.DEFAULT_FONT_WEIGHT

            return cls(
                color_coding=config.get('color_coding', constants.config.defaults.DEFAULT_COLOR_CODING),
                graph_enabled=config.get('graph_enabled', constants.config.defaults.DEFAULT_GRAPH_ENABLED),
                high_speed_threshold=float(config.get('high_speed_threshold', constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=int(config.get('arrow_width', constants.renderer.DEFAULT_ARROW_WIDTH)),
                font_family=config.get('font_family', constants.config.defaults.DEFAULT_FONT_FAMILY),
                font_size=int(config.get('font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                font_weight=weight,
                default_color=config.get('default_color', constants.config.defaults.DEFAULT_COLOR),
                high_speed_color=config.get('high_speed_color', constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR),
                low_speed_color=config.get('low_speed_color', constants.config.defaults.DEFAULT_LOW_SPEED_COLOR),
                graph_opacity=max(0.0, min(1.0, opacity)),
                speed_display_mode=config.get('speed_display_mode', constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE),
                decimal_places=int(config.get('decimal_places', constants.config.defaults.DEFAULT_DECIMAL_PLACES)),
                text_alignment=config.get('text_alignment', constants.config.defaults.DEFAULT_TEXT_ALIGNMENT),
                force_decimals=config.get('force_decimals', False)
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig: %s", e)
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


    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, layout_mode: str = 'vertical') -> None:
        """Draws upload/download speeds, adapting to vertical or horizontal layouts."""
        try:
            always_mbps = config.speed_display_mode == "always_mbps"
            decimal_places = max(0, min(2, config.decimal_places))
            force_decimals = config.force_decimals
            
            if layout_mode == 'horizontal':
                self._draw_horizontal_layout(painter, upload, download, width, height, config, always_mbps, decimal_places, force_decimals)
            else: # Default to vertical
                self._draw_vertical_layout(painter, upload, download, width, height, config, always_mbps, decimal_places, force_decimals)

        except Exception as e:
            self.logger.error("Failed to draw speeds: %s", e, exc_info=True)
            self._last_text_rect = QRect()


    def _draw_vertical_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, always_mbps: bool, decimal_places: int, force_decimals: bool) -> None:
        """Draws the standard two-line vertical layout."""
        line_height = self.metrics.height()
        ascent = self.metrics.ascent()
        total_text_height = line_height * 2
        # Ensure all coordinates are integers before drawing
        top_y = int((height - total_text_height) / 2 + ascent)
        bottom_y = top_y + line_height

        upload_text, download_text = self._format_speed_texts(upload, download, always_mbps, decimal_places, force_decimals)
        up_val_str, up_unit = upload_text
        down_val_str, down_unit = download_text
        
        arrow_width = self.metrics.horizontalAdvance(self.i18n.UPLOAD_ARROW)
        max_num_width = max(self.metrics.horizontalAdvance(up_val_str), self.metrics.horizontalAdvance(down_val_str))
        max_unit_width = max(self.metrics.horizontalAdvance(up_unit), self.metrics.horizontalAdvance(down_unit))
        content_width = arrow_width + constants.renderer.ARROW_NUMBER_GAP + max_num_width + constants.renderer.VALUE_UNIT_GAP + max_unit_width

        margin = self._calculate_margin(width, content_width, config.text_alignment)
        number_x = margin + arrow_width + constants.renderer.ARROW_NUMBER_GAP
        unit_x = number_x + max_num_width + constants.renderer.VALUE_UNIT_GAP

        painter.setPen(self._get_speed_color(upload, config))
        painter.drawText(margin, top_y, self.i18n.UPLOAD_ARROW)
        painter.drawText(number_x, top_y, up_val_str)
        painter.drawText(unit_x, top_y, up_unit)

        painter.setPen(self._get_speed_color(download, config))
        painter.drawText(margin, bottom_y, self.i18n.DOWNLOAD_ARROW)
        painter.drawText(number_x, bottom_y, down_val_str)
        painter.drawText(unit_x, bottom_y, down_unit)
        
        self._last_text_rect = QRect(margin, int(top_y - ascent), int(content_width), int(total_text_height))

    def _draw_horizontal_layout(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig, always_mbps: bool, decimal_places: int, force_decimals: bool) -> None:
        """Draws the compact single-line horizontal layout."""
        upload_text, download_text = self._format_speed_texts(upload, download, always_mbps, decimal_places, force_decimals, full_string=True)
        
        up_str = f"{constants.renderer.UPLOAD_ARROW} {upload_text}"
        down_str = f"{constants.renderer.DOWNLOAD_ARROW} {download_text}"
        separator = constants.layout.HORIZONTAL_LAYOUT_SEPARATOR
        
        up_width = self.metrics.horizontalAdvance(up_str)
        down_width = self.metrics.horizontalAdvance(down_str)
        separator_width = self.metrics.horizontalAdvance(separator)
        content_width = up_width + separator_width + down_width

        # Ensure all coordinates are integers before drawing
        y_pos = int((height - self.metrics.height()) / 2 + self.metrics.ascent())
        margin = self._calculate_margin(width, content_width, config.text_alignment)
        
        up_x = margin
        separator_x = up_x + up_width
        down_x = separator_x + separator_width

        painter.setPen(self._get_speed_color(upload, config))
        painter.drawText(up_x, y_pos, up_str)
        
        painter.setPen(self.default_color)
        painter.drawText(separator_x, y_pos, separator)
        
        painter.setPen(self._get_speed_color(download, config))
        painter.drawText(down_x, y_pos, down_str)

        self._last_text_rect = QRect(margin, (height - self.metrics.height()) // 2, int(content_width), self.metrics.height())


    def _format_speed_texts(self, upload: float, download: float, always_mbps: bool, decimal_places: int, force_decimals: bool, full_string: bool = False) -> Tuple[Any, Any]:
        """Helper to format speed values into final strings or tuples."""
        upload_full = format_speed(upload, self.i18n, False, always_mbps=always_mbps, decimal_places=decimal_places)
        download_full = format_speed(download, self.i18n, False, always_mbps=always_mbps, decimal_places=decimal_places)
        
        up_val_str, up_unit = upload_full.split(" ", 1)
        down_val_str, down_unit = download_full.split(" ", 1)

        up_val_float = float(up_val_str)
        down_val_float = float(down_val_str)

        def format_value(value: float) -> str:
            """Formats a float value based on the force_decimals setting."""
            if force_decimals:
                return f"{value:.{decimal_places}f}"
            else:
                epsilon = 1e-9
                if abs(value - round(value)) < epsilon:
                    # If the value is very close to a whole number, show no decimals.
                    return f"{round(value)}"
                else:
                    # Otherwise, show the configured number of decimal places.
                    return f"{value:.{decimal_places}f}"

        up_num_final = format_value(up_val_float)
        down_num_final = format_value(down_val_float)

        if full_string:
            return f"{up_num_final} {up_unit}", f"{down_num_final} {down_unit}"
        else:
            return (up_num_final, up_unit), (down_num_final, down_unit)


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
        speed_mbps = (speed * 8) / 1_000_000
        
        if speed_mbps >= config.high_speed_threshold:
            return self.high_color
        if speed_mbps >= config.low_speed_threshold:
            return self.low_color
        return self.default_color


    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig, history: List[SpeedDataSnapshot], layout_mode: str = 'vertical') -> None:
        """Draws a mini graph of speed history, skipping if in horizontal mode."""
        if layout_mode == 'horizontal' or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        try:
            text_rect = self.get_last_text_rect()
            if not text_rect.isValid():
                self.logger.debug("Cannot draw mini-graph: text rect is invalid.")
                return

            vertical_padding = 10
            graph_rect = text_rect.adjusted(0, -vertical_padding, 0, vertical_padding)
            if graph_rect.width() <= 0 or graph_rect.height() <= 0:
                return

            # The mini-graph always shows the total, so we must aggregate the speeds first.
            aggregated_history = []
            for snapshot in history:
                total_upload = sum(up for up, down in snapshot.speeds.values())
                total_download = sum(down for up, down in snapshot.speeds.values())
                aggregated_history.append({'upload': total_upload, 'download': total_download})

            current_hash = hash(tuple((d['upload'], d['download']) for d in aggregated_history))

            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(aggregated_history)
                if num_points < 2: return

                max_upload = max(d['upload'] for d in aggregated_history)
                max_download = max(d['download'] for d in aggregated_history)
                actual_max_speed = max(max_upload, max_download, 1.0)
                padded_max_speed = actual_max_speed * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
                max_speed = max(padded_max_speed, constants.renderer.MIN_Y_SCALE)
                min_speed_threshold = constants.renderer.MIN_SPEED_THRESHOLD
                step_x = graph_rect.width() / (num_points - 1)

                self._cached_upload_points = [
                    QPointF(graph_rect.left() + i * step_x,
                            graph_rect.bottom() if data['upload'] < min_speed_threshold else
                            graph_rect.bottom() - min(1.0, data['upload'] / max_speed) * graph_rect.height())
                    for i, data in enumerate(aggregated_history)
                ]
                self._cached_download_points = [
                    QPointF(graph_rect.left() + i * step_x,
                            graph_rect.bottom() if data['download'] < min_speed_threshold else
                            graph_rect.bottom() - min(1.0, data['download'] / max_speed) * graph_rect.height())
                    for i, data in enumerate(aggregated_history)
                ]
                
                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            upload_color = QColor(constants.graph.UPLOAD_LINE_COLOR)
            pen = QPen(upload_color, constants.renderer.LINE_WIDTH, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(self._cached_upload_points)
            
            download_color = QColor(constants.graph.DOWNLOAD_LINE_COLOR)
            pen.setColor(download_color)
            painter.setPen(pen)
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
            self.logger.info("Renderer config updated.")
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