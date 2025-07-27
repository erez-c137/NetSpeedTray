"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration.
"""

import logging
import time
from typing import Tuple, List, Optional, Dict, Any
from dataclasses import dataclass, field
from collections import deque

from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PyQt6.QtCore import Qt, QPointF, QRect

from ..constants.constants import RendererConstants, ConfigConstants, UnitConstants, GraphConstants
from ..core.widget_state import AggregatedSpeedData, PerInterfaceSpeedData
from ..utils.helpers import format_speed
from ..core.model import CoreSpeedData


logger = logging.getLogger("NetSpeedTray.WidgetRenderer")


@dataclass
class RenderConfig:
    """
    Configuration for rendering network speeds and mini graph.

    Attributes:
        use_megabytes: Use MB/s (True) or Mbps (False).
        color_coding: Enable speed-based color changes.
        graph_enabled: Show mini graph.
        graph_opacity: Graph opacity (0.0-1.0).
        high_speed_threshold: High speed threshold (Mbps or MB/s).
        low_speed_threshold: Low speed threshold (Mbps or MB/s).
        arrow_width: Width for arrow characters (pixels).
        font_family: Font family name.
        font_size: Font size (points).
        font_weight: Font weight (QFont.Weight).
        default_color: Default text color (hex).
        high_speed_color: High speed color (hex).
        low_speed_color: Low speed color (hex).
    """
    use_megabytes: bool
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

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """
        Creates RenderConfig from config dict with validation.

        Args:
            config: Configuration dictionary from the application.

        Returns:
            RenderConfig: A new RenderConfig instance with validated settings.

        Raises:
            ValueError: If the configuration is invalid.
        """
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
                use_megabytes=config.get('use_megabytes', ConfigConstants.DEFAULT_USE_MEGABYTES),
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
                graph_opacity=max(0.0, min(1.0, opacity))
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig: %s", e)
            raise ValueError("Invalid rendering configuration") from e


class WidgetRenderer:
    """
    Renders network speeds and optional mini graph for NetworkSpeedWidget.

    Uses RenderConfig to style the display, supports dynamic color coding and graph history.

    Attributes:
        logger: Logger instance for tracking renderer operations and errors.
        config: RenderConfig object with rendering settings.
        default_color: Default QColor for text rendering.
        high_color: QColor for high-speed text.
        low_color: QColor for low-speed text.
        font: QFont object for text rendering.
        metrics: QFontMetrics object for text layout calculations.
        speed_history: Deque storing AggregatedSpeedData for the mini graph.
        _last_text_rect: QRect of the last rendered text area.
        _max_speed_cache: Cached maximum speed for graph scaling.
        _last_widget_size: Last widget size for graph caching.
        _cached_upload_points: Cached points for upload speed graph.
        _cached_download_points: Cached points for download speed graph.
        _last_history_hash: Hash of the last speed history for caching.
        _last_speeds: Stored speeds and unit for rendering.
        paused: Flag indicating if graph updates are paused.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initializes renderer with config, handling setup errors.

        Args:
            config: Configuration dictionary from the application.

        Raises:
            RuntimeError: If renderer initialization fails.
        """
        self.logger = logger
        try:
            self.config = RenderConfig.from_dict(config)
            self.default_color = QColor(self.config.default_color)
            self.high_color = QColor(self.config.high_speed_color)
            self.low_color = QColor(self.config.low_speed_color)
            self.font = QFont(self.config.font_family, self.config.font_size, self.config.font_weight)
            self.metrics = QFontMetrics(self.font)
            self.speed_history = deque(maxlen=300)  # Stores AggregatedSpeedData
            self._last_text_rect = QRect()
            self._max_speed_cache: Optional[float] = None
            self._last_widget_size: Tuple[int, int] = (0, 0)
            self._cached_upload_points: List[QPointF] = []
            self._cached_download_points: List[QPointF] = []
            self._last_history_hash: int = 0
            self._last_speeds: Tuple[float, float, str] = (0.0, 0.0, "KBps")  # Upload, Download, Unit
            self.paused = False
            self.logger.info("WidgetRenderer initialized.")
        except Exception as e:
            self.logger.error("Failed to initialize WidgetRenderer: %s", e)
            self.config = None
            self.font = QFont()
            self.metrics = QFontMetrics(self.font)
            self.speed_history = deque(maxlen=1)
            raise RuntimeError("Renderer initialization failed") from e

    def set_speeds(self, upload: float, download: float, unit: str) -> None:
        """
        Stores the latest upload and download speeds and triggers a repaint.

        Args:
            upload: Upload speed (in KBps or MBps, based on unit).
            download: Download speed (in KBps or MBps, based on unit).
            unit: Unit of speed (e.g., "KBps", "MBps").
        """
        try:
            # Convert speeds to bytes/sec for internal use
            factor = 1000 if unit == "MBps" else 1
            upload_bytes = upload * factor * 1000 / 8  # KBps or MBps to bytes/sec
            download_bytes = download * factor * 1000 / 8  # KBps or MBps to bytes/sec
            self._last_speeds = (upload, download, unit)
            self.logger.debug("Speeds set - Upload: %.2f %s, Download: %.2f %s", upload, unit, download, unit)

            # Update speed history for mini graph
            speed_data = AggregatedSpeedData(
                upload=upload_bytes,
                download=download_bytes,
                timestamp=time.time()
            )
            self.update_speed_history(speed_data)
        except Exception as e:
            self.logger.error("Error setting speeds: %s", e, exc_info=True)
            self._last_speeds = (0.0, 0.0, "KBps")

    def render(
        self,
        painter: QPainter,
        speed_data: CoreSpeedData,
        rect: QRect,
        metrics: QFontMetrics,
        config: Dict[str, Any],
        default_color: QColor,
        high_color: QColor,
        low_color: QColor
    ) -> None:
        """
        Renders the network speed data onto the widget using the provided painter.

        Formats and displays upload/download speeds with appropriate colors based on
        speed thresholds. Positions the text within the given rectangle, ensuring proper
        alignment and spacing.

        Args:
            painter: The QPainter object used for rendering.
            speed_data: The CoreSpeedData object containing upload/download speeds and timestamp.
            rect: The QRect defining the widget's rendering area.
            metrics: The QFontMetrics object for text layout calculations.
            config: Application configuration dictionary.
            default_color: Default color for text rendering.
            high_color: Color for high-speed values.
            low_color: Color for low-speed values (e.g., zero speed).

        Raises:
            ValueError: If critical rendering parameters (e.g., painter, rect) are invalid.
        """
        if self.paused:
            self.logger.debug("Rendering skipped: WidgetRenderer is paused")
            return

        if not painter.isActive():
            raise ValueError("Cannot render: QPainter is not active")
        if rect.isEmpty():
            raise ValueError("Cannot render: Rendering rectangle is empty")

        try:
            # Extract configuration settings
            use_megabytes = config.get('speed_unit', 'Mbps') == 'MB/s'
            color_coding = config.get('color_coding', ConfigConstants.DEFAULT_COLOR_CODING)
            high_speed_threshold = config.get('high_speed_threshold', ConfigConstants.DEFAULT_HIGH_SPEED_THRESHOLD)
            low_speed_threshold = config.get('low_speed_threshold', ConfigConstants.DEFAULT_LOW_SPEED_THRESHOLD)

            # Convert speeds to display units (Mbps or MB/s)
            divisor = UnitConstants.MEGA_DIVISOR if use_megabytes else UnitConstants.MEGA_DIVISOR / UnitConstants.BITS_PER_BYTE
            unit_label = UnitConstants.MBPS_LABEL if use_megabytes else UnitConstants.MBITS_LABEL

            upload_speed = speed_data.upload / divisor
            download_speed = speed_data.download / divisor

            # Apply color coding if enabled
            if color_coding:
                speed = max(upload_speed, download_speed)
                if speed >= high_speed_threshold:
                    painter.setPen(high_color)
                elif speed >= low_speed_threshold:
                    painter.setPen(low_color)
                else:
                    painter.setPen(default_color)
            else:
                painter.setPen(default_color)

            # Format the speed strings
            upload_text = f"{RendererConstants.UPLOAD_ARROW} {format_speed(speed_data.upload, use_megabytes)}"
            download_text = f"{RendererConstants.DOWNLOAD_ARROW} {format_speed(speed_data.download, use_megabytes)}"

            # Calculate positions using the provided font metrics
            upload_width = metrics.horizontalAdvance(upload_text)
            download_width = metrics.horizontalAdvance(download_text)
            text_height = metrics.height()

            # Center the text horizontally in the widget
            x = rect.left() + RendererConstants.TEXT_MARGIN
            max_width = max(upload_width, download_width)
            if max_width < rect.width() - 2 * RendererConstants.TEXT_MARGIN:
                x += (rect.width() - 2 * RendererConstants.TEXT_MARGIN - max_width) // 2

            # Text is already vertically centered by the rect passed from paintEvent
            y_upload = rect.top()
            y_download = y_upload + text_height

            # Update the last text rectangle for reference
            self._last_text_rect = QRect(x, y_upload, max_width, 2 * text_height)

            # Draw the upload speed
            painter.drawText(x, y_upload + metrics.ascent(), upload_text)

            # Draw the download speed
            painter.drawText(x, y_download + metrics.ascent(), download_text)

        except Exception as e:
            self.logger.error("Error rendering speeds: %s", e)
            self._draw_error(painter, rect, "Render Error")

    def _draw_error(self, painter: QPainter, rect: QRect, message: str) -> None:
        """
        Draws an error message on the widget.

        Args:
            painter: The QPainter object used for rendering.
            rect: The QRect defining the widget's rendering area.
            message: The error message to display.
        """
        painter.save()
        painter.fillRect(rect, QColor(150, 0, 0, 200))
        painter.setPen(Qt.GlobalColor.white)
        error_font = QFont(self.font)
        error_font.setPointSize(max(6, self.config.font_size - 2))
        painter.setFont(error_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()

    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int, height: int, config: RenderConfig) -> None:
        """
        Draws upload/download speeds with arrows.

        Args:
            painter: The QPainter object used for rendering.
            upload: Upload speed in bytes/sec.
            download: Download speed in bytes/sec.
            width: Widget width in pixels.
            height: Widget height in pixels.
            config: RenderConfig object with rendering settings.
        """
        try:
            # Calculate metrics for layout
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            total_height = line_height * 2

            # Calculate vertical position to center the text
            top_y = (height - total_height) // 2 + ascent
            bottom_y = top_y + line_height

            # Format speed values
            upload_text = format_speed(upload, config.use_megabytes)
            download_text = format_speed(download, config.use_megabytes)
            up_num, up_unit = upload_text.split(" ", 1) if " " in upload_text else (upload_text, "")
            down_num, down_unit = download_text.split(" ", 1) if " " in download_text else (download_text, "")

            # Calculate widths for horizontal centering
            arrow_width = self.metrics.horizontalAdvance(RendererConstants.UPLOAD_ARROW)
            max_num_width = max(self.metrics.horizontalAdvance(up_num), self.metrics.horizontalAdvance(down_num))
            max_unit_width = max(self.metrics.horizontalAdvance(up_unit), self.metrics.horizontalAdvance(down_unit))

            # Calculate total content width
            content_width = arrow_width + RendererConstants.ARROW_NUMBER_GAP + max_num_width + RendererConstants.VALUE_UNIT_GAP + max_unit_width

            # Calculate left margin to center the content horizontally
            margin = max((width - content_width) // 2, RendererConstants.TEXT_MARGIN)

            # Calculate positions for text elements
            number_x = margin + arrow_width + RendererConstants.ARROW_NUMBER_GAP
            unit_x = number_x + max_num_width + RendererConstants.VALUE_UNIT_GAP

            painter.setPen(self._get_speed_color(upload, config) if config.color_coding else self.default_color)
            painter.drawText(margin, top_y, RendererConstants.UPLOAD_ARROW)
            painter.drawText(number_x, top_y, up_num)
            painter.drawText(unit_x, top_y, up_unit)

            painter.setPen(self._get_speed_color(download, config) if config.color_coding else self.default_color)
            painter.drawText(margin, bottom_y, RendererConstants.DOWNLOAD_ARROW)
            painter.drawText(number_x, bottom_y, down_num)
            painter.drawText(unit_x, bottom_y, down_unit)

            self._last_text_rect = QRect(margin, top_y - ascent, unit_x + self.metrics.horizontalAdvance(up_unit), total_height)
        except Exception as e:
            self.logger.error("Failed to draw speeds: %s", e)
            self._last_text_rect = QRect()

    def _get_speed_color(self, speed: float, config: RenderConfig) -> QColor:
        """
        Returns color based on speed thresholds or default if color coding is disabled.

        Args:
            speed: Speed value in bytes/sec.
            config: RenderConfig object with rendering settings.

        Returns:
            QColor: Color to use for rendering the speed.
        """
        # Always return default_color if color_coding is disabled
        if not config.color_coding:
            return self.default_color

        # Apply color coding if enabled
        divisor = UnitConstants.MEGA_DIVISOR  # 1,000,000 for MBps or Mbps
        factor = 1 if config.use_megabytes else UnitConstants.BITS_PER_BYTE  # 1 for MBps, 8 for Mbps
        high = config.high_speed_threshold * divisor / factor
        low = config.low_speed_threshold * divisor / factor
        return self.high_color if speed >= high else self.low_color if speed >= low else self.default_color

    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig) -> None:
        """
        Draws mini graph of speed history.

        Args:
            painter: The QPainter object used for rendering.
            width: Widget width in pixels.
            height: Widget height in pixels.
            config: RenderConfig object with rendering settings.
        """
        if len(self.speed_history) < RendererConstants.MIN_GRAPH_POINTS:
            return

        try:
            margin = RendererConstants.GRAPH_MARGIN
            graph_rect = QRect(margin, margin, width - 2 * margin, height - 2 * margin)
            if graph_rect.width() <= 0 or graph_rect.height() <= 0:
                return

            current_hash = hash(tuple((data.upload, data.download) for data in self.speed_history))
            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                # Calculate the actual maximum speed from the data
                actual_max_speed = max(max(data.upload for data in self.speed_history),
                                      max(data.download for data in self.speed_history), 1.0)

                # Set a minimum threshold for the Y-axis scale (defined in RendererConstants.MIN_Y_SCALE)
                # Default is 100 Kbps = 12.5 KB/s = 12500 bytes/sec
                min_y_scale = RendererConstants.MIN_Y_SCALE

                # Use the larger of actual max speed or minimum scale for the Y-axis
                max_speed = max(actual_max_speed, min_y_scale)

                # Set a minimum threshold below which speeds will be treated as zero (defined in RendererConstants.MIN_SPEED_THRESHOLD)
                # Now using the same threshold as the activity display (10,000 bytes/sec = 80 Kbps)
                min_speed_threshold = RendererConstants.MIN_SPEED_THRESHOLD

                step_x = graph_rect.width() / (len(self.speed_history) - 1)
                self._cached_upload_points = [
                    QPointF(margin + i * step_x,
                            # If speed is below threshold, draw at bottom (flat line)
                            graph_rect.bottom() if data.upload < min_speed_threshold else
                            # Otherwise, scale normally
                            graph_rect.bottom() - min(1.0, data.upload / max_speed) * graph_rect.height())
                    for i, data in enumerate(self.speed_history)
                ]
                self._cached_download_points = [
                    QPointF(margin + i * step_x,
                            # If speed is below threshold, draw at bottom (flat line)
                            graph_rect.bottom() if data.download < min_speed_threshold else
                            # Otherwise, scale normally
                            graph_rect.bottom() - min(1.0, data.download / max_speed) * graph_rect.height())
                    for i, data in enumerate(self.speed_history)
                ]
                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash
                self.logger.debug("Recalculated graph points.")

            painter.save()
            painter.setOpacity(config.graph_opacity)
            # Use the same colors as the main graph
            upload_color = QColor(GraphConstants.UPLOAD_LINE_COLOR)
            pen = QPen(upload_color, RendererConstants.LINE_WIDTH, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(*self._cached_upload_points)
            # Use the same colors as the main graph
            download_color = QColor(GraphConstants.DOWNLOAD_LINE_COLOR)
            pen.setColor(download_color)
            painter.setPen(pen)
            painter.drawPolyline(*self._cached_download_points)
            painter.restore()
        except Exception as e:
            self.logger.error("Failed to draw mini graph: %s", e)

    def update_config(self, config_dict: Dict[str, Any]) -> None:
        """
        Updates rendering configuration.

        Args:
            config_dict: New configuration dictionary.
        """
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

    def update_speed_history(self, speed_data: AggregatedSpeedData) -> None:
        """
        Updates graph history unless paused.

        Args:
            speed_data: AggregatedSpeedData object to add to history.
        """
        if not self.paused:
            self.speed_history.append(speed_data)
            self._cached_upload_points = []
            self._cached_download_points = []
            self._last_history_hash = 0

    def get_last_text_rect(self) -> QRect:
        """
        Returns last text bounding rect.

        Returns:
            QRect: The bounding rectangle of the last rendered text.
        """
        return self._last_text_rect

    def pause(self) -> None:
        """
        Pauses graph updates.
        """
        self.paused = True
        self.logger.debug("Renderer paused.")

    def resume(self) -> None:
        """
        Resumes graph updates.
        """
        self.paused = False
        self.logger.debug("Renderer resumed.")