"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration.
"""

import logging
from typing import Tuple, List, Optional, Dict, Any
from dataclasses import dataclass, field

from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PyQt6.QtCore import Qt, QPointF, QRect

from ..constants.constants import RendererConstants, ConfigConstants, UnitConstants, GraphConstants
from ..core.widget_state import AggregatedSpeedData
from ..utils.helpers import format_speed

logger = logging.getLogger("NetSpeedTray.WidgetRenderer")


@dataclass
class RenderConfig:
    # ... (This class remains unchanged) ...
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
    graph_opacity: float = field(default_factory=lambda: ConfigConstants.DEFAULT_GRAPH_OPACITY / 100.0)
    speed_display_mode: str = ConfigConstants.DEFAULT_SPEED_DISPLAY_MODE
    decimal_places: int = ConfigConstants.DEFAULT_DECIMAL_PLACES
    text_alignment: str = ConfigConstants.DEFAULT_TEXT_ALIGNMENT
    force_decimals: bool = False

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        # ... (This method remains unchanged) ...
        try:
            opacity = float(config.get('graph_opacity', ConfigConstants.DEFAULT_GRAPH_OPACITY)) / 100.0
            weight = config.get('font_weight', ConfigConstants.DEFAULT_FONT_WEIGHT)
            if isinstance(weight, str):
                weights = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}
                weight = weights.get(weight.lower(), ConfigConstants.DEFAULT_FONT_WEIGHT)
            weight = int(weight)
            if not 1 <= weight <= 1000:
                logger.warning("Invalid font_weight %s, using default.", weight)
                weight = ConfigConstants.DEFAULT_FONT_WEIGHT

            return cls(
                color_coding=config.get('color_coding', ConfigConstants.DEFAULT_COLOR_CODING),
                graph_enabled=config.get('graph_enabled', ConfigConstants.DEFAULT_GRAPH_ENABLED),
                high_speed_threshold=float(config.get('high_speed_threshold', ConfigConstants.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', ConfigConstants.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=int(config.get('arrow_width', RendererConstants.DEFAULT_ARROW_WIDTH)),
                font_family=config.get('font_family', ConfigConstants.DEFAULT_FONT_FAMILY),
                font_size=int(config.get('font_size', ConfigConstants.DEFAULT_FONT_SIZE)),
                font_weight=weight,
                default_color=config.get('default_color', ConfigConstants.DEFAULT_COLOR),
                high_speed_color=config.get('high_speed_color', ConfigConstants.DEFAULT_HIGH_SPEED_COLOR),
                low_speed_color=config.get('low_speed_color', ConfigConstants.DEFAULT_LOW_SPEED_COLOR),
                graph_opacity=max(0.0, min(1.0, opacity)),
                speed_display_mode=config.get('speed_display_mode', ConfigConstants.DEFAULT_SPEED_DISPLAY_MODE),
                decimal_places=int(config.get('decimal_places', ConfigConstants.DEFAULT_DECIMAL_PLACES)),
                text_alignment=config.get('text_alignment', ConfigConstants.DEFAULT_TEXT_ALIGNMENT),
                force_decimals=config.get('force_decimals', False)
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig: %s", e)
            raise ValueError("Invalid rendering configuration") from e


class WidgetRenderer:
    """
    Renders network speeds and optional mini graph for NetworkSpeedWidget.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
            """
            Initializes renderer with config, handling setup errors.
            """
            self.logger = logger
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
                # Simplified error state
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

    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig) -> None:
        """Draws upload/download speeds with arrows."""
        try:
            # ... (This method's implementation remains unchanged) ...
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            total_height = line_height * 2
            top_y = (height - total_height) // 2 + ascent
            bottom_y = top_y + line_height
            always_mbps = config.speed_display_mode == "always_mbps"
            decimal_places = max(0, min(2, config.decimal_places))
            force_decimals = config.force_decimals
            align_map = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}
            alignment = align_map.get(config.text_alignment, Qt.AlignmentFlag.AlignHCenter)
            upload_full_text = format_speed(upload, False, always_mbps=always_mbps, decimal_places=decimal_places)
            download_full_text = format_speed(download, False, always_mbps=always_mbps, decimal_places=decimal_places)
            up_val_str, up_unit = upload_full_text.split(" ", 1)
            down_val_str, down_unit = download_full_text.split(" ", 1)
            up_val_num = float(up_val_str)
            down_val_num = float(down_val_str)
            if force_decimals:
                up_num_final = f"{up_val_num:.{decimal_places}f}"
                down_num_final = f"{down_val_num:.{decimal_places}f}"
            else:
                up_num_final = f"{up_val_num:g}"
                if up_val_num == 0.0: up_num_final = "0"
                down_num_final = f"{down_val_num:g}"
                if down_val_num == 0.0: down_num_final = "0"
            
            arrow_width = self.metrics.horizontalAdvance(RendererConstants.UPLOAD_ARROW)
            max_num_width = max(self.metrics.horizontalAdvance(up_num_final), self.metrics.horizontalAdvance(down_num_final))
            max_unit_width = max(self.metrics.horizontalAdvance(up_unit), self.metrics.horizontalAdvance(down_unit))
            content_width = arrow_width + RendererConstants.ARROW_NUMBER_GAP + max_num_width + RendererConstants.VALUE_UNIT_GAP + max_unit_width

            if alignment == Qt.AlignmentFlag.AlignLeft:
                margin = RendererConstants.TEXT_MARGIN
            elif alignment == Qt.AlignmentFlag.AlignRight:
                margin = max(width - content_width - RendererConstants.TEXT_MARGIN, RendererConstants.TEXT_MARGIN)
            else:  # Center
                margin = max((width - content_width) // 2, RendererConstants.TEXT_MARGIN)

            number_x = margin + arrow_width + RendererConstants.ARROW_NUMBER_GAP
            unit_x = number_x + max_num_width + RendererConstants.VALUE_UNIT_GAP

            painter.setPen(self._get_speed_color(upload, config) if config.color_coding else self.default_color)
            painter.drawText(margin, top_y, RendererConstants.UPLOAD_ARROW)
            painter.drawText(number_x, top_y, up_num_final)
            painter.drawText(unit_x, top_y, up_unit)

            painter.setPen(self._get_speed_color(download, config) if config.color_coding else self.default_color)
            painter.drawText(margin, bottom_y, RendererConstants.DOWNLOAD_ARROW)
            painter.drawText(number_x, bottom_y, down_num_final)
            painter.drawText(unit_x, bottom_y, down_unit)
            
            self.logger.debug(f"[DEBUG] Creating text rect with: x={margin}, y={int(top_y - ascent)}, w={int(content_width)}, h={int(total_height)}")
            self._last_text_rect = QRect(margin, int(top_y - ascent), int(content_width), int(total_height))

        except Exception as e:
            self.logger.error("Failed to draw speeds: %s", e)
            self._last_text_rect = QRect()

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

    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig, history: List[AggregatedSpeedData]) -> None:
        """Draws a mini graph of speed history behind the text."""
        # ... (This method is now correct and remains as you implemented it) ...
        if len(history) < RendererConstants.MIN_GRAPH_POINTS:
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

            current_hash = hash(tuple((data.upload, data.download) for data in history))
            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(history)
                if num_points < 2: return

                actual_max_speed = max(max(d.upload for d in history), max(d.download for d in history), 1.0)
                max_speed = max(actual_max_speed, RendererConstants.MIN_Y_SCALE)
                min_speed_threshold = RendererConstants.MIN_SPEED_THRESHOLD
                step_x = graph_rect.width() / (num_points - 1)

                self._cached_upload_points = [
                    QPointF(graph_rect.left() + i * step_x,
                            graph_rect.bottom() if data.upload < min_speed_threshold else
                            graph_rect.bottom() - min(1.0, data.upload / max_speed) * graph_rect.height())
                    for i, data in enumerate(history)
                ]
                self._cached_download_points = [
                    QPointF(graph_rect.left() + i * step_x,
                            graph_rect.bottom() if data.download < min_speed_threshold else
                            graph_rect.bottom() - min(1.0, data.download / max_speed) * graph_rect.height())
                    for i, data in enumerate(history)
                ]
                
                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            upload_color = QColor(GraphConstants.UPLOAD_LINE_COLOR)
            pen = QPen(upload_color, RendererConstants.LINE_WIDTH, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(self._cached_upload_points)
            
            download_color = QColor(GraphConstants.DOWNLOAD_LINE_COLOR)
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