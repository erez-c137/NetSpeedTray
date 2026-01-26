
import logging
import math
from typing import TYPE_CHECKING, Dict, Any, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QWidget

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import is_small_taskbar, get_taskbar_info

if TYPE_CHECKING:
    from netspeedtray.views.widget.main import NetworkSpeedWidget

class WidgetLayoutManager:
    """
    Manages layout, font, and window properties for the NetworkSpeedWidget.
    Extracts sizing and property logic from the main widget class.
    """

    def __init__(self, widget: "NetworkSpeedWidget"):
        self.widget = widget
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.LayoutManager")
        self.metrics: Optional[QFontMetrics] = None

    def setup_window_properties(self) -> None:
        """Set Qt window flags and attributes for proper Windows integration."""
        self.logger.debug("Setting window properties...")
        self.widget.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.widget.setMouseTracking(True)
        self.logger.debug("Window properties set")

    def init_font(self) -> None:
        """Initialize the font and set initial widget size."""
        self.logger.debug("Initializing font...")
        self.set_font(resize=False)

    def set_font(self, resize: bool = True) -> None:
        """Apply font settings from config."""
        self.logger.debug("Setting font...")
        config = self.widget.config
        
        font_family = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        font_size = config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
        font_weight_val = config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
        
        if isinstance(font_weight_val, str):
            font_weight = {
                "normal": QFont.Weight.Normal, 
                "bold": QFont.Weight.Bold
            }.get(font_weight_val.lower(), QFont.Weight.Normal)
        else:
            font_weight = QFont.Weight.Normal

        font = QFont(font_family, font_size, font_weight)
        self.widget.setFont(font)
        self.widget.font = font # Update public attribute
        
        self.metrics = QFontMetrics(font)
        self.widget.metrics = self.metrics # Update public attribute
        
        self.logger.debug(f"Font set: {font_family}, {font_size}px")
        
        if resize:
            self.resize_widget_for_font()

    def resize_widget_for_font(self) -> None:
        """Calculates and sets the widget's fixed dimensions."""
        self.logger.debug("Resizing widget based on layout...")
        if not self.metrics:
            raise RuntimeError("FontMetrics not initialized.")
        if not hasattr(self.widget, 'renderer'):
            raise RuntimeError("Renderer not initialized before resizing.")

        try:
            taskbar_info = get_taskbar_info()
            is_small = is_small_taskbar(taskbar_info)
            self.logger.debug(f"Small taskbar detected: {is_small}")

            precision = self.widget.config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES)
            margin = constants.renderer.TEXT_MARGIN
            
            if is_small:
                # Horizontal Layout Calculation
                upload_text, download_text = self.widget.renderer._format_speed_texts(9.99, 99.99, False, precision, True)
                up_str = f"{self.widget.i18n.UPLOAD_ARROW} {upload_text}"
                down_str = f"{self.widget.i18n.DOWNLOAD_ARROW} {download_text}"
                separator = constants.layout.HORIZONTAL_LAYOUT_SEPARATOR
                
                calculated_width = (self.metrics.horizontalAdvance(up_str) +
                                    self.metrics.horizontalAdvance(separator) +
                                    self.metrics.horizontalAdvance(down_str) + (margin * 2))
                calculated_height = self.widget.taskbar_height
            else:
                # Vertical Layout Calculation
                # Use same fixed-width reference pattern as the renderer
                # "888" for auto mode (covers up to 999), "8888" for always_mbps mode
                always_mbps = self.widget.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE) == "always_mbps"
                
                # Use helpers for reference strings and labels
                from netspeedtray.utils.helpers import get_all_possible_unit_labels, get_reference_value_string
                
                max_number_str = get_reference_value_string(always_mbps, precision)
                max_number_width = self.metrics.horizontalAdvance(max_number_str)
                
                possible_units = get_all_possible_unit_labels(self.widget.i18n)
                max_unit_width = max(self.metrics.horizontalAdvance(unit) for unit in possible_units)
                
                arrow_width = self.metrics.horizontalAdvance(self.widget.i18n.UPLOAD_ARROW)

                calculated_width = (margin + arrow_width + constants.renderer.ARROW_NUMBER_GAP +
                                    max_number_width + constants.renderer.VALUE_UNIT_GAP +
                                    max_unit_width + margin)
                calculated_height = self.widget.taskbar_height

            self.widget.setFixedSize(math.ceil(calculated_width), math.ceil(calculated_height))
            self.logger.debug(f"Widget resized to: {self.widget.width()}x{self.widget.height()}px")

        except Exception as e:
            self.logger.error(f"Failed to resize widget: {e}", exc_info=True)
            # Set a safe fallback size to prevent widget from being unusable
            self.widget.setFixedSize(150, 40)
            self.logger.warning("Applied fallback widget size: 150x40px")
