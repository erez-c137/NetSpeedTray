
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
        self.arrow_metrics: Optional[QFontMetrics] = None

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
        
        if isinstance(font_weight_val, int):
             # FIX for #89: Directly use integer weight (e.g., 400, 700)
             font_weight = font_weight_val
        elif isinstance(font_weight_val, str):
            font_weight = {
                "normal": QFont.Weight.Normal, 
                "bold": QFont.Weight.Bold
            }.get(font_weight_val.lower(), QFont.Weight.Normal)
        else:
            font_weight = QFont.Weight.Normal

        font = QFont(font_family, font_size, font_weight)
        self.widget.setFont(font)
        self.widget.current_font = font # Update public attribute
        
        self.metrics = QFontMetrics(font)
        self.widget.current_metrics = self.metrics # Update public attribute
        
        # Arrow font handling
        if config.get("use_separate_arrow_font", False):
            arrow_family = config.get("arrow_font_family", font_family)
            arrow_size = config.get("arrow_font_size", font_size)
            arrow_weight_raw = config.get("arrow_font_weight", font_weight_val)
            
            # Simplified weight logic for layout manager (mirrors main font logic above if needed)
            if isinstance(arrow_weight_raw, int):
                arrow_weight = arrow_weight_raw
            else:
                arrow_weight = {
                    "normal": QFont.Weight.Normal, 
                    "bold": QFont.Weight.Bold
                }.get(str(arrow_weight_raw).lower(), QFont.Weight.Normal)
                
            arrow_font = QFont(arrow_family, arrow_size, arrow_weight)
        else:
            arrow_font = font
            
        self.arrow_metrics = QFontMetrics(arrow_font)
        
        self.logger.debug(f"Font set: {font_family}, {font_size}px, Weight: {font_weight}")
        
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
            edge = taskbar_info.get_edge_position()
            is_horizontal = edge in (constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM)
            dpi_scale = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0
            
            is_small = is_small_taskbar(taskbar_info)
            self.logger.debug(f"Taskbar edge: {edge}, Small: {is_small}")

            precision = self.widget.config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES)
            margin = constants.renderer.TEXT_MARGIN
            hide_arrows = self.widget.config.get("hide_arrows", False)
            hide_units = self.widget.config.get("hide_unit_suffix", False)
            # FIX for Issue #106: Read short_unit_labels config to ensure consistency
            # Bug: Layout width calculation wasn't using same label format as render-time formatter
            #      This caused text truncation when layout predicted smaller width than actual render
            # Solution: Pass short_labels consistently to both layout calculation and actual rendering
            short_labels = self.widget.config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)
            
            if is_horizontal:
                # --- HORIZONTAL TASKBAR (TOP/BOTTOM) ---
                if is_small:
                    # Small Horizontal Layout Width Calculation
                    unit_type = self.widget.config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE)
                    # FIX for #106: Pass short_labels to formatter to match render-time behavior
                    # This ensures layout calculation predicts the same width as actual rendering
                    # Example: short_labels=True → "9.99 Mb/s" (shorter)
                    #         short_labels=False → "9.99 Megabits/s" (longer, needs more width)
                    upload_pair, download_pair = self.widget.renderer._format_speed_texts(9.99, 99.99, False, precision, True, unit_type, short_labels=short_labels, full_string=False)
                    
                    up_val, up_unit = upload_pair
                    down_val, down_unit = download_pair
                    
                    def get_part_width(arrow_char, val, unit):
                        w = 0
                        if not hide_arrows:
                            w += self.arrow_metrics.horizontalAdvance(arrow_char) + self.arrow_metrics.horizontalAdvance(" ")
                        w += self.metrics.horizontalAdvance(val)
                        if not hide_units:
                            w += self.metrics.horizontalAdvance(" ") + self.metrics.horizontalAdvance(unit)
                        return w

                    up_width = get_part_width(self.widget.i18n.UPLOAD_ARROW, up_val, up_unit)
                    down_width = get_part_width(self.widget.i18n.DOWNLOAD_ARROW, down_val, down_unit)
                    sep_width = self.metrics.horizontalAdvance(constants.layout.HORIZONTAL_LAYOUT_SEPARATOR)
                    
                    calculated_width = up_width + sep_width + down_width + (margin * 2)
                else:
                    # Large Horizontal Layout Width Calculation (Vertical Mode)
                    always_mbps = self.widget.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE) == "always_mbps"
                    
                    from netspeedtray.utils.helpers import get_all_possible_unit_labels, get_reference_value_string
                    
                    max_number_str = get_reference_value_string(always_mbps, precision)
                    max_number_width = self.metrics.horizontalAdvance(max_number_str)
                    
                    possible_units = get_all_possible_unit_labels(self.widget.i18n)
                    max_unit_width = max(self.metrics.horizontalAdvance(unit) for unit in possible_units) if not hide_units else 0
                    
                    arrow_width = self.metrics.horizontalAdvance(self.widget.i18n.UPLOAD_ARROW) if not hide_arrows else 0
                    arrow_gap = constants.renderer.ARROW_NUMBER_GAP if not hide_arrows else 0
                    unit_gap = constants.renderer.VALUE_UNIT_GAP if not hide_units else 0

                    calculated_width = (margin + arrow_width + arrow_gap +
                                        max_number_width + unit_gap +
                                        max_unit_width + margin)
                
                # Height: use visible taskbar area (availableGeometry), not Shell_TrayWnd
                # which can include invisible padding on high-DPI Windows 11 displays.
                screen_obj = taskbar_info.get_screen()
                if screen_obj:
                    avail = screen_obj.availableGeometry()
                    full = screen_obj.geometry()
                    visible_height = full.height() - avail.height()
                    if visible_height > 0:
                        calculated_height = visible_height
                    else:
                        calculated_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale
                else:
                    calculated_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale

            else:
                # --- VERTICAL TASKBAR (LEFT/RIGHT) ---
                # Width is determined by the taskbar width
                calculated_width = (taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale
                # Height is calculated to fit exactly two lines of text + small padding
                # This prevents the widget from stretching the full length of the screen.
                calculated_height = self.metrics.height() * 2 + (margin * 4)

            # FIX for #88: Ensure minimum size to prevents widget from disappearing
            final_width = max(calculated_width, 50) 
            final_height = max(calculated_height, 20)

            self.widget.setFixedSize(math.ceil(final_width), math.ceil(final_height))
            self.logger.debug(f"Widget resized to: {self.widget.width()}x{self.widget.height()}px")

        except Exception as e:
            self.logger.error(f"Failed to resize widget: {e}", exc_info=True)
            self.widget.setFixedSize(150, 40)
            self.logger.warning("Applied fallback widget size: 150x40px")
