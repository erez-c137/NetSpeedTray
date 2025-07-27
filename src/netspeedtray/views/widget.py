"""
Network Speed Widget (NetSpeedTray Taskbar Element)

This module defines the `NetworkSpeedWidget`, the primary visual component of NetSpeedTray.
It displays real-time network upload and download speeds in a frameless, always-on-top widget
positioned near the Windows system tray area.
"""

from __future__ import annotations

import logging
import os
import sys
import math
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from ..views.graph import GraphWindow
    from ..views.settings import SettingsDialog

from PyQt6.QtCore import (
    QPoint, QRect, QEvent, QObject, QSize, QTimer, Qt, pyqtSignal, QCoreApplication
)
from PyQt6.QtGui import (
    QCloseEvent, QColor, QContextMenuEvent, QFont, QFontMetrics, QHideEvent,
    QIcon, QMouseEvent, QPaintEvent, QPainter, QScreen, QShowEvent
)
from PyQt6.QtWidgets import (
    QApplication, QMenu, QMessageBox, QWidget, QDialog
)

import win32api
import win32con
import win32gui
import win32com.client

from ..constants.constants import (
    AppConstants, ConfigConstants, HelperConstants, PositionConstants,
    RendererConstants, TimerConstants, TaskbarEdge, UIConstants, LayoutConstants,
    FontConstants
)
from ..constants.i18n_strings import I18nStrings as CoreI18nStrings
from ..core.controller import NetworkController as CoreController
from ..core.timer_manager import SpeedTimerManager
from ..core.widget_state import WidgetState as CoreWidgetState, AggregatedSpeedData as CoreSpeedData
from ..utils.config import ConfigManager as CoreConfigManager
from ..utils.helpers import format_speed
from ..utils.position_utils import PositionManager as CorePositionManager, WindowState as CoreWindowState, PositionCalculator
from ..utils.taskbar_utils import TaskbarEdge, TaskbarInfo, get_taskbar_info, is_fullscreen_active, is_taskbar_visible
from ..utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""

    font_updated = pyqtSignal()
    MIN_UPDATE_INTERVAL = 0.5  # Minimum seconds between updates

    def __init__(self, taskbar_height: int = 40, config: Optional[Dict[str, Any]] = None, parent: QObject | None = None) -> None:
        """Initialize the NetworkSpeedWidget with core components and UI setup.

        Args:
            taskbar_height: Height of the taskbar in logical pixels (default: 40).
            config: Application configuration dictionary (optional; loads from ConfigManager if None).
            parent: Parent object in the Qt hierarchy (optional).

        Raises:
            RuntimeError: If initialization of core components fails.
        """
        super().__init__(parent)
        self.logger = logging.getLogger(f"{AppConstants.APP_NAME}.{self.__class__.__name__}")
        self.logger.info("Initializing NetworkSpeedWidget...")
        self._last_update_time: float = 0.0

        # Instance Attributes
        self.i18n: CoreI18nStrings
        self.config_manager: CoreConfigManager
        self.config: Dict[str, Any] = ConfigConstants.DEFAULT_CONFIG.copy()
        self.widget_state: CoreWidgetState
        self.timer_manager: SpeedTimerManager
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: CorePositionManager
        self.graph_window: Optional[GraphWindow] = None
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        self.taskbar_height: int = taskbar_height
        self.app_icon: QIcon
        self.context_menu: QMenu
        self.font: QFont
        self.metrics: QFontMetrics = None
        self.default_color: QColor
        self.high_color: QColor
        self.low_color: QColor
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()
        self.is_paused: bool = False
        self.visibility_timer = QTimer(self)
        self.position_timer = QTimer(self)
        self._context_menu_shown: bool = False

        # Ensure the attribute is initialized
        self._context_menu_shown = False
        self._startup_time = None

        # Keep the widget hidden initially
        self.setVisible(False)
        self.logger.debug("Widget initially hidden to stabilize position and size")

        # Initialization Steps
        try:
            # Step 1: Setup i18n and config manager
            self.i18n = CoreI18nStrings()
            self.config_manager = CoreConfigManager()
            self.logger.debug("I18n and ConfigManager initialized")

            # Step 2: Load configuration
            if config is not None:
                self.config = config
                self.logger.debug("Using provided configuration")
            else:
                self.config = self._load_initial_config(taskbar_height)

            # Step 3: Configure window properties
            self._setup_window_properties()

            # Step 4: Initialize UI components
            self._init_ui_components()

            # Step 5: Initialize core logic components
            self._init_core_components()

            # Step 6: Setup position manager
            self._init_position_manager()

            # Step 7: Connect signals and slots
            self._setup_connections()

            # Step 8: Initialize context menu
            self._init_context_menu()

            # Step 9: Configure timers
            self.visibility_timer.timeout.connect(self._check_visibility)
            self.visibility_timer.start(TimerConstants.VISIBILITY_CHECK_INTERVAL_MS)
            self.logger.debug(f"Visibility timer started (interval: {TimerConstants.VISIBILITY_CHECK_INTERVAL_MS} ms)")

            self.position_timer.timeout.connect(self._check_position)
            self.position_check_interval_ms = getattr(TimerConstants, 'POSITION_CHECK_INTERVAL_MS', 1500)
            if self.position_check_interval_ms > 0:
                self.position_timer.start(self.position_check_interval_ms)
                self.logger.debug("Position timer started")
            else:
                self.logger.debug("Position timer disabled")

            # Step 10: Set initial position
            self._initialize_position()

            # Step 11: Delay showing the widget until position and size are stable
            QTimer.singleShot(1000, self._delayed_initial_show)

            # Step 12: Validate lazy imports
            self._validate_lazy_imports()

            self.logger.info("NetworkSpeedWidget initialized successfully")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
            raise RuntimeError(f"Failed to initialize NetworkSpeedWidget: {e}") from e

    def _init_core_components(self) -> None:
        """
        Initialize non-UI core logic components.

        Sets up WidgetState, SpeedTimerManager, NetworkController, and WidgetRenderer.
        """
        self.logger.debug("Initializing core components...")
        if not self.config:
            raise RuntimeError("Cannot initialize core: Config missing")
        try:
            self.widget_state = CoreWidgetState(self.config)
            self.timer_manager = SpeedTimerManager(self.config, parent=self)
            self.controller = CoreController(config=self.config, widget_state=self.widget_state)
            self.controller.set_view(self)
            self.renderer = CoreWidgetRenderer(self.config)
            self.timer_manager.start_timer()
            self.logger.debug("Core components initialized and timers started")
        except Exception as e:
            self.logger.error("Failed to initialize core components: %s", e, exc_info=True)
            raise RuntimeError("Failed to initialize core application components") from e

    def _init_position_manager(self) -> None:
        """
        Initialize the PositionManager for widget placement.

        Raises:
            RuntimeError: If configuration is missing, taskbar info cannot be retrieved,
                        or PositionManager initialization fails.
        """
        self.logger.debug("Initializing PositionManager...")
        if not self.config:
            raise RuntimeError("Cannot init PositionManager: Config missing")
        if not self.metrics:
            self.logger.warning("Metrics not set, initializing default font")
            self._init_font()
        try:
            taskbar_info = get_taskbar_info()
            if not taskbar_info:
                raise RuntimeError("Failed to get taskbar info")
            window_state = CoreWindowState(
                config=self.config,
                widget=self,
                taskbar_info=taskbar_info,
                font_metrics=self.metrics
            )
            self.position_manager = CorePositionManager(window_state)
            self.logger.debug("PositionManager initialized")
        except Exception as e:
            self.logger.error("Failed to initialize PositionManager: %s", e, exc_info=True)
            raise RuntimeError("PositionManager initialization failed") from e

    def _initialize_position(self) -> None:
        """Set the initial widget position using saved coordinates or default placement."""
        self.logger.debug("Setting initial widget position...")
        if not hasattr(self, 'position_manager') or not self.position_manager:
            self.logger.error("PositionManager not initialized")
            self._init_position_manager()
        try:
            # Try to use saved position
            pos_x = self.config.get("position_x")
            pos_y = self.config.get("position_y")
            if isinstance(pos_x, int) and isinstance(pos_y, int) and pos_x >= 0 and pos_y >= 0:
                self.move(pos_x, pos_y)
                self.logger.info(f"Restored saved position: ({pos_x}, {pos_y})")
            else:
                # Fallback to calculated position
                self.position_manager.update_position()
                self._adjust_position_with_margin()
            self.logger.info(f"Initial position set to: ({self.pos().x()}, {self.pos().y()})")
        except Exception as e:
            self.logger.error("Failed to set initial position: %s", e, exc_info=True)
            raise RuntimeError("Initial position setup failed") from e

    def _adjust_position_with_margin(self) -> None:
        """
        Adjust widget position to include a margin from the tray overflow menu.
        """
        self.logger.debug("Adjusting position with margin...")
        try:
            current_pos = self.pos()
            taskbar_info = get_taskbar_info()
            if not taskbar_info:
                self.logger.warning("Cannot adjust position: Taskbar info unavailable")
                return
            edge = taskbar_info.get_edge_position()
            margin = getattr(UIConstants, 'WIDGET_MARGIN_FROM_TRAY', 5)

            if edge == TaskbarEdge.BOTTOM or edge == TaskbarEdge.TOP:
                adjusted_x = current_pos.x() - margin
                self.move(max(0, adjusted_x), current_pos.y())
            elif edge == TaskbarEdge.RIGHT:
                adjusted_x = current_pos.x() - margin
                self.move(max(0, adjusted_x), current_pos.y())
            elif edge == TaskbarEdge.LEFT:
                adjusted_x = current_pos.x() + margin
                self.move(adjusted_x, current_pos.y())

            self.save_position()
            self.logger.debug(f"Position adjusted with {margin}px margin: {self.pos()}")
        except Exception as e:
            self.logger.error(f"Error adjusting position: {e}", exc_info=True)

    def _delayed_initial_show(self) -> None:
        """
        Delays showing the widget until its position and size are stabilized.
        """
        self.logger.debug("Executing delayed initial show...")
        try:
            if self.position_manager:
                self.position_manager.update_position()
                self._adjust_position_with_margin()
                self.logger.debug(f"Position updated: {self.pos()}")

            if self.upload_speed == 0.0 and self.download_speed == 0.0:
                self.upload_speed = 0.0
                self.download_speed = 0.0
                self.logger.debug("Set placeholder speeds for initial rendering")

            self._check_visibility()
            if self.isVisible():
                self.logger.info("Widget shown after stabilization")
            else:
                self.logger.debug("Widget remains hidden due to visibility conditions")
        except Exception as e:
            self.logger.error(f"Error in delayed initial show: {e}", exc_info=True)
            self.setVisible(False)

    def pause(self) -> None:
        """Pause widget updates (for future use, not active by default)."""
        if self.is_paused:
            self.logger.debug("Widget already paused")
            return
        self.logger.info("Pausing widget updates")
        self.is_paused = True
        if self.controller:
            self.controller.pause()
        if self.timer_manager:
            self.timer_manager.stop_timer()
        if self.renderer:
            self.renderer.pause()
        self.update_config({'paused': True})
        self.update()

    def resume(self) -> None:
        """Resume widget updates (for future use, not active by default)."""
        if not self.is_paused:
            self.logger.debug("Widget already running")
            return
        self.logger.info("Resuming widget updates")
        self.is_paused = False
        if self.controller:
            self.controller.resume()
        if self.timer_manager:
            self.timer_manager.start_timer()
        if self.renderer:
            self.renderer.resume()
        self.update_config({'paused': False})
        self.update()

    def update_stats(self, upload_mbps: float, download_mbps: float) -> None:
        """
        Slot to receive raw network speeds from NetworkController in Mbps.

        Applies debouncing to prevent updates faster than MIN_UPDATE_INTERVAL.

        Args:
            upload_mbps: Raw upload speed in Mbps.
            download_mbps: Raw download speed in Mbps.
        """
        current_time = time.monotonic()
        if current_time - self._last_update_time < self.MIN_UPDATE_INTERVAL:
            self.logger.debug(
                "Skipping update_stats due to debouncing (last update %.3fs ago)",
                current_time - self._last_update_time
            )
            return
        self._last_update_time = current_time

        upload_bytes_sec = upload_mbps * 1_000_000 / 8
        download_bytes_sec = download_mbps * 1_000_000 / 8
        self.logger.debug(f"Converted to bytes/s - Upload: {upload_bytes_sec:.2f}, Download: {download_bytes_sec:.2f}")
        self.widget_state.add_speed_data(upload_bytes_sec, download_bytes_sec)
        self._update_display_speeds()

    def _update_display_speeds(self) -> None:
        """
        Updates the displayed speeds, applying smoothing and formatting.
        Handles MBps, Mbps, KBps, and Kbps robustly based on config.
        Fallbacks to KBps if config is missing or invalid.
        """
        upload_speed, download_speed = self.widget_state.get_smoothed_speeds()  # bytes/sec
        self.logger.debug(f"Smoothed speeds - Upload: {upload_speed:.2f} B/s, Download: {download_speed:.2f} B/s")
        self.upload_speed = upload_speed
        self.download_speed = download_speed

        # Robust unit selection
        unit_pref = self.config.get("speed_unit")
        if not unit_pref:
            # Backward compatibility: use use_megabytes if present
            if self.config.get("use_megabytes", False):
                unit_pref = "MBps"
            else:
                unit_pref = "KBps"  # Original default

        if unit_pref == "MBps":
            upload_display = upload_speed / 1_000_000
            download_display = download_speed / 1_000_000
            unit = "MB/s"
        elif unit_pref == "Mbps":
            upload_display = upload_speed * 8 / 1_000_000
            download_display = download_speed * 8 / 1_000_000
            unit = "Mbps"
        elif unit_pref == "Kbps":
            upload_display = upload_speed * 8 / 1_000
            download_display = download_speed * 8 / 1_000
            unit = "Kbps"
        elif unit_pref == "KBps":
            upload_display = upload_speed / 1_000
            download_display = download_speed / 1_000
            unit = "KB/s"
        else:
            self.logger.warning(f"Unknown speed_unit '{unit_pref}' in config, defaulting to KBps.")
            upload_display = upload_speed / 1_000
            download_display = download_speed / 1_000
            unit = "KB/s"

        # Always show at least two decimals for MB/s and Mbps for small values
        if unit in ("MB/s", "Mbps"):
            upload_display = round(upload_display, 2)
            download_display = round(download_display, 2)
        else:
            upload_display = round(upload_display)
            download_display = round(download_display)

        # Avoid showing 0 if there is a small value
        if upload_speed > 0 and upload_display == 0:
            upload_display = max(upload_display, 0.01)
        if download_speed > 0 and download_display == 0:
            download_display = max(download_display, 0.01)

        self.logger.debug(f"Display speeds - Upload: {upload_display} {unit}, Download: {download_display} {unit}")
        self.renderer.set_speeds(upload_display, download_display, unit)
        self.update()

    def _load_initial_config(self, taskbar_height: int) -> Dict[str, Any]:
        """Load configuration and inject taskbar height."""
        self.logger.debug("Loading initial configuration...")
        config = self.config_manager.load()
        config["taskbar_height"] = taskbar_height
        return config

    def _setup_window_properties(self) -> None:
        """
        Set Qt window flags and attributes for proper Windows integration.
        """
        self.logger.debug("Setting window properties...")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.logger.debug("Window properties set")

    def _init_ui_components(self) -> None:
        """Initialize UI-related elements: icon, colors, font."""
        self.logger.debug("Initializing UI components...")
        if not self.config:
            raise RuntimeError("Cannot initialize UI: Config missing")
        self._load_icon()
        self.default_color = QColor(self.config.get("default_color", ConfigConstants.DEFAULT_COLOR))
        self.high_color = QColor(self.config.get("high_speed_color", ConfigConstants.DEFAULT_HIGH_SPEED_COLOR))
        self.low_color = QColor(self.config.get("low_speed_color", ConfigConstants.DEFAULT_LOW_SPEED_COLOR))
        self.logger.debug(f"Colors initialized - Default: {self.default_color.name()}, High: {self.high_color.name()}, Low: {self.low_color.name()}")
        self._init_font()
        self.logger.debug("UI components initialized")

    def _init_font(self) -> None:
        """Initialize the font and set initial widget size."""
        self.logger.debug("Initializing font...")
        self._set_font(resize=True)

    def _set_font(self, resize: bool = True) -> None:
        """Apply font settings from config."""
        self.logger.debug("Setting font...")
        font_family = self.config.get("font_family", HelperConstants.DEFAULT_FONT)
        font_size = max(5, min(int(self.config.get("font_size", ConfigConstants.DEFAULT_FONT_SIZE)), 72))
        font_weight = self.config.get("font_weight", ConfigConstants.DEFAULT_FONT_WEIGHT)
        if isinstance(font_weight, str):
            font_weight = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}.get(font_weight.lower(), QFont.Weight.Normal)
        self.font = QFont(font_family, font_size, font_weight)
        self.setFont(self.font)
        self.metrics = QFontMetrics(self.font)
        self.logger.debug(f"Font set: {font_family}, {font_size}px")
        if resize:
            self._resize_widget_for_font()

    def _init_context_menu(self) -> None:
        """Creates the right-click context menu."""
        self.logger.debug("Initializing context menu...")
        try:
            self.context_menu = QMenu(self)
            settings_action = self.context_menu.addAction(self.i18n.SETTINGS_MENU_ITEM)
            settings_action.triggered.connect(self.show_settings)
            self.context_menu.addSeparator()
            exit_action = self.context_menu.addAction(self.i18n.EXIT_MENU_ITEM)
            app_instance = QApplication.instance()
            if app_instance:
                exit_action.triggered.connect(self.close)
            else:
                exit_action.setEnabled(False)
                self.logger.warning("Could not connect Exit menu item: No QApplication instance.")
            self.logger.debug("Context menu initialized successfully.")
        except Exception as e:
            self.logger.error("Error initializing context menu: %s", e, exc_info=True)

    def _toggle_pause_resume(self) -> None:
        """Toggles between pause and resume states."""
        if self.is_paused:
            self.resume()
            self.pause_action.setText(self.i18n.PAUSE_MENU_ITEM)
        else:
            self.pause()
            self.pause_action.setText(self.i18n.RESUME_MENU_ITEM)

    def _setup_connections(self) -> None:
        """
        Connects signals from core components to widget slots.

        Raises:
            RuntimeError: If core components are missing during setup.
        """
        self.logger.debug("Setting up signal connections...")
        if not all([self.widget_state, self.timer_manager, self.controller]):
            raise RuntimeError("Core components missing during signal connection setup.")
        try:
            if hasattr(self.timer_manager, 'stats_updated') and hasattr(self.controller, 'update_speeds'):
                self.timer_manager.stats_updated.connect(self.controller.update_speeds)
            else:
                self.logger.warning("Cannot connect stats_updated to update_speeds: Signal or slot missing.")

            if hasattr(self.controller, 'speeds_updated'):
                self.controller.speeds_updated.connect(self.update_stats)
            else:
                self.logger.warning("speeds_updated signal unavailable; speed updates may not work.")

            self.font_updated.connect(self._update_font)

            self.logger.debug("Signal connections established successfully.")
        except Exception as e:
            self.logger.error("Error setting up signal connections: %s", e, exc_info=True)
            raise RuntimeError("Failed to establish critical signal connections") from e

    def _validate_lazy_imports(self) -> None:
        """Validates lazy imports to catch potential issues early."""
        self.logger.debug("Validating lazy imports...")
        try:
            from .settings import SettingsDialog
            from .graph import GraphWindow
            self.logger.debug("Lazy imports validated successfully.")
        except ImportError as e:
            self.logger.error("Lazy import validation failed: %s", e, exc_info=True)

    def _resize_widget_for_font(self) -> None:
        """
        Calculates and sets the widget's fixed dimensions based on current font metrics.

        Raises:
            RuntimeError: If FontMetrics or screen data is unavailable.
        """
        self.logger.debug("Resizing widget based on renderer layout...")
        if not self.metrics:
            self.logger.error("Cannot resize widget: FontMetrics not available.")
            raise RuntimeError("FontMetrics not initialized.")

        try:
            self.logger.info("Calculating widget size...")
            screen = self.screen() or QApplication.primaryScreen()
            if not screen:
                self.logger.warning("No screen available; assuming DPI scaling of 1.0")
                dpi_scale = 1.0
            else:
                dpi_scale = screen.devicePixelRatio()
                self.logger.debug(f"DPI scaling factor: {dpi_scale}")

            precision = self.config.get("speed_precision", 2)
            max_number_str = f"{999.99:.{precision}f}"

            margin = int(RendererConstants.TEXT_MARGIN * dpi_scale)
            arrow_num_gap = int(RendererConstants.ARROW_NUMBER_GAP * dpi_scale)
            value_unit_gap = int(RendererConstants.VALUE_UNIT_GAP * dpi_scale)

            arrow_char = RendererConstants.UPLOAD_ARROW
            arrow_width = self.metrics.horizontalAdvance(arrow_char)
            max_number_width = self.metrics.horizontalAdvance(max_number_str)

            possible_units = [" Kbps", " Mbps", " Gbps", " KB/s", " MB/s", " GB/s"]
            max_unit_width = max(self.metrics.horizontalAdvance(unit) for unit in possible_units)

            calculated_width = (
                margin +
                arrow_width +
                arrow_num_gap +
                max_number_width +
                value_unit_gap +
                max_unit_width +
                margin
            )
            calculated_width = math.ceil(calculated_width)

            self.logger.debug(
                f"Width calc: margin({margin}) + arrow({arrow_width}) + "
                f"gap1({arrow_num_gap}) + number({max_number_width}) + "
                f"gap2({value_unit_gap}) + unit({max_unit_width}) + margin({margin}) "
                f"= {calculated_width}"
            )

            logical_taskbar_height = self.taskbar_height
            v_padding = self.config.get("vertical_padding", 0)
            calculated_height = max(logical_taskbar_height + v_padding, 10)

            self.setFixedSize(int(calculated_width), int(calculated_height))
            self.logger.info(f"Widget resized to: {calculated_width}x{calculated_height}px")

            if self.isVisible() and hasattr(self, 'position_manager') and self.position_manager:
                self.position_manager.update_position()
                self._adjust_position_with_margin()
                self.logger.debug("Position updated after resize.")
        except Exception as e:
            self.logger.error(f"Failed to resize widget: {e}", exc_info=True)
            raise RuntimeError("Failed to resize widget based on font") from e

    def _update_font(self) -> None:
        """Slot to update the font and resize the widget when font settings change."""
        self.logger.debug("Updating font and resizing widget due to settings change...")
        try:
            self._set_font(resize=True)
        except Exception as e:
            self.logger.error(f"Runtime font update failed: {e}", exc_info=True)

    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Handles painting the widget by rendering network speeds using WidgetRenderer.

        Uses WidgetRenderer's draw_network_speeds to display upload and download speeds,
        and optionally draws a mini graph if enabled in the configuration.
        """
        self.logger.debug("paintEvent triggered")
        if not self.isVisible():
            self.logger.debug("Skipping paintEvent: widget is not visible")
            return

        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            self.logger.debug("Background filled with nearly transparent color (0, 0, 0, 1)")

            if not self.renderer:
                self.logger.error("Renderer not initialized during paintEvent")
                self._draw_paint_error(painter, "Renderer Error")
                return

            if not self.metrics:
                self.logger.warning("Font metrics not available during paintEvent, initializing default font")
                self._init_font()

            painter.setFont(self.font)

            # Get speeds and unit from renderer (set by set_speeds)
            upload, download, unit = self.renderer._last_speeds

            # Convert back to bytes/sec for draw_network_speeds if needed
            if unit == "MB/s":
                upload_bytes = upload * 1_000_000
                download_bytes = download * 1_000_000
            elif unit == "Mbps":
                upload_bytes = upload * 1_000_000 / 8
                download_bytes = download * 1_000_000 / 8
            elif unit == "KB/s":
                upload_bytes = upload * 1_000
                download_bytes = download * 1_000
            elif unit == "Kbps":
                upload_bytes = upload * 1_000 / 8
                download_bytes = download * 1_000 / 8
            else:
                upload_bytes = upload
                download_bytes = download

            # Create RenderConfig for rendering
            render_config = RenderConfig.from_dict(self.config)

            # Draw network speeds
            self.renderer.draw_network_speeds(
                painter=painter,
                upload=upload_bytes,
                download=download_bytes,
                width=self.width(),
                height=self.height(),
                config=render_config
            )
            self.logger.debug(f"Speeds rendered - Upload: {upload:.2f} {unit}, Download: {download:.2f} {unit}")

            # Draw mini graph if enabled
            if render_config.graph_enabled:
                self.renderer.draw_mini_graph(
                    painter=painter,
                    width=self.width(),
                    height=self.height(),
                    config=render_config
                )
                self.logger.debug("Mini graph rendered")

            painter.end()
            self.logger.debug("paintEvent completed successfully")
        except Exception as e:
            self.logger.error(f"Error in paintEvent: {e}", exc_info=True)
            self._draw_paint_error(painter, "Paint Error")
            if not painter.isActive():
                painter.begin(self)
                painter.end()

    def _draw_paint_error(self, painter: Optional[QPainter], text: str) -> None:
        """Draws a visual error indicator on the widget background."""
        try:
            if painter is None or not painter.isActive():
                p = QPainter(self)
                created_painter = True
            else:
                p = painter
                created_painter = False

            p.fillRect(self.rect(), QColor(200, 0, 0, 200))
            p.setPen(Qt.GlobalColor.white)
            if self.font:
                p.setFont(self.font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

            if created_painter:
                p.end()

        except Exception as paint_err:
            self.logger.critical(f"CRITICAL: Failed to draw paint error indicator: {paint_err}", exc_info=True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        Handles mouse press events for dragging and context menu triggering.
        """
        self.logger.debug("Mouse press event: button=%s, pos=%s", event.button(), event.pos())
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_offset = event.position().toPoint()
                self.logger.debug(f"Drag started. Offset: ({self._drag_offset.x()}, {self._drag_offset.y()})")
                event.accept()
            elif event.button() == Qt.MouseButton.RightButton:
                self.logger.info("Right-click detected in mousePressEvent")
                self._context_menu_shown = False
                if self.context_menu:
                    text_rect_local = self.renderer.get_last_text_rect() if self.renderer else QRect()
                    if not text_rect_local.isValid() or text_rect_local.isEmpty():
                        self.logger.warning("Renderer text rect invalid. Falling back to widget center.")
                        widget_global_pos = self.mapToGlobal(self.rect().topLeft())
                        center_global_x = widget_global_pos.x() + self.width() // 2
                        ref_top_global_y = widget_global_pos.y()
                    else:
                        text_center_local = text_rect_local.center()
                        text_top_left_local = text_rect_local.topLeft()
                        center_global = self.mapToGlobal(text_center_local)
                        top_left_global = self.mapToGlobal(text_top_left_local)
                        center_global_x = center_global.x()
                        ref_top_global_y = top_left_global.y()

                    menu_size = self.context_menu.sizeHint()
                    menu_width = menu_size.width() if menu_size.width() > 0 else getattr(UIConstants, 'ESTIMATED_MENU_WIDTH', 150)
                    menu_height = menu_size.height() if menu_size.height() > 0 else 100

                    target_x = center_global_x - menu_width // 2
                    padding_above = getattr(UIConstants, 'MENU_PADDING_ABOVE', 5)
                    target_y = ref_top_global_y - menu_height - padding_above
                    target_pos = QPoint(int(round(target_x)), int(round(target_y)))

                    screen = self.screen() or QApplication.primaryScreen()
                    if screen:
                        screen_rect = screen.availableGeometry()
                        validated_x = max(screen_rect.left(), min(target_pos.x(), screen_rect.right() - menu_width + 1))
                        validated_y = max(screen_rect.top(), min(target_pos.y(), screen_rect.bottom() - menu_height + 1))
                        target_pos.setX(validated_x)
                        target_pos.setY(validated_y)

                    self.context_menu.exec(target_pos)
                    self._context_menu_shown = True
                    self.logger.info("Context menu shown via mousePressEvent at position: %s", target_pos)
                    event.accept()
                else:
                    self.logger.warning("Context menu not initialized")
                    event.ignore()
            else:
                super().mousePressEvent(event)
        except Exception as e:
            self.logger.error(f"Error in mousePressEvent: {e}", exc_info=True)
            event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Moves the widget if dragging is active."""
        if not self._dragging:
            super().mouseMoveEvent(event)
            return

        if not self.position_manager or not hasattr(self.position_manager, '_calculator'):
            self.logger.warning("Cannot process drag move: PositionManager or calculator unavailable.")
            return

        try:
            new_global_pos = event.globalPosition().toPoint() - self._drag_offset
            taskbar_info = get_taskbar_info()
            if not taskbar_info:
                self.logger.warning("Cannot constrain drag move: Taskbar info unavailable.")
                self.move(new_global_pos)
                event.accept()
                return

            calculator = self.position_manager._calculator
            constrained_pos = calculator.constrain_drag_position(new_global_pos, taskbar_info, self.size())

            if constrained_pos:
                self.move(constrained_pos)
            event.accept()

        except Exception as e:
            self.logger.error(f"Error processing mouseMoveEvent: {e}", exc_info=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Stops dragging on left mouse button release and saves the position."""
        try:
            if event.button() == Qt.MouseButton.LeftButton and self._dragging:
                self._dragging = False
                self.logger.debug(f"Drag finished. Final position: {self.pos().x()},{self.pos().y()}")
                self.save_position()
                event.accept()
            else:
                super().mouseReleaseEvent(event)
        except Exception as e:
            self.logger.error(f"Error in mouseReleaseEvent: {e}", exc_info=True)

    def changeEvent(self, event: QEvent) -> None:
        """Detects window activation/deactivation to ensure topmost status."""
        super().changeEvent(event)
        event_type = event.type()
        if event_type in (QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate):
            self.logger.debug(f"{'WindowActivate' if event_type == QEvent.Type.WindowActivate else 'WindowDeactivate'} event, ensuring topmost.")
            QTimer.singleShot(10, self._ensure_win32_topmost)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Shows the speed history graph on left double-click."""
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self.logger.debug("Left double-click detected, showing graph window.")
                self._show_graph_window()
                event.accept()
            else:
                super().mouseDoubleClickEvent(event)
        except Exception as e:
            self.logger.error(f"Error in mouseDoubleClickEvent: {e}", exc_info=True)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """
        Shows the context menu centered on the rendered text as a fallback.
        """
        self.logger.info("Context menu event triggered at position: global=%s, local=%s",
                        event.globalPos(), event.pos())
        if self._context_menu_shown:
            self.logger.debug("Context menu already shown via mousePressEvent, skipping")
            event.accept()
            return

        if not hasattr(self, 'context_menu') or not self.context_menu:
            self.logger.error("Context menu not initialized.")
            event.ignore()
            return
        if not hasattr(self, 'renderer') or not self.renderer:
            self.logger.error("Renderer not available for text position calculation.")
            event.ignore()
            return

        try:
            text_rect_local = self.renderer.get_last_text_rect()
            if not text_rect_local.isValid() or text_rect_local.isEmpty():
                self.logger.warning("Renderer text rect invalid. Falling back to widget center.")
                widget_global_pos = self.mapToGlobal(self.rect().topLeft())
                center_global_x = widget_global_pos.x() + self.width() // 2
                ref_top_global_y = widget_global_pos.y()
            else:
                text_center_local = text_rect_local.center()
                text_top_left_local = text_rect_local.topLeft()
                center_global = self.mapToGlobal(text_center_local)
                top_left_global = self.mapToGlobal(text_top_left_local)
                center_global_x = center_global.x()
                ref_top_global_y = top_left_global.y()

            menu_size = self.context_menu.sizeHint()
            menu_width = menu_size.width() if menu_size.width() > 0 else getattr(UIConstants, 'ESTIMATED_MENU_WIDTH', 150)
            menu_height = menu_size.height() if menu_size.height() > 0 else 100

            target_x = center_global_x - menu_width // 2
            padding_above = getattr(UIConstants, 'MENU_PADDING_ABOVE', 5)
            target_y = ref_top_global_y - menu_height - padding_above
            target_pos = QPoint(int(round(target_x)), int(round(target_y)))

            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                screen_rect = screen.availableGeometry()
                validated_x = max(screen_rect.left(), min(target_pos.x(), screen_rect.right() - menu_width + 1))
                validated_y = max(screen_rect.top(), min(target_pos.y(), screen_rect.bottom() - menu_height + 1))
                target_pos.setX(validated_x)
                target_pos.setY(validated_y)

            self.context_menu.exec(target_pos)
            self._context_menu_shown = True
            self.logger.info("Context menu shown successfully at position: %s", target_pos)
            event.accept()

        except Exception as e:
            self.logger.error(f"Error showing context menu: {e}", exc_info=True)
            try:
                self.context_menu.exec(event.globalPos())
                self._context_menu_shown = True
                self.logger.debug("Context menu shown at fallback position: %s", event.globalPos())
                event.accept()
            except Exception as fallback_e:
                self.logger.critical(f"Failed to show context menu at fallback position: {fallback_e}", exc_info=True)
                event.ignore()

    def showEvent(self, event: QShowEvent) -> None:
        self.logger.debug(f"Widget showEvent triggered. New visibility: {self.isVisible()}")
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.logger.debug("Widget hideEvent triggered.")
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.logger.info("Close event received. Initiating cleanup...")
        try:
            self.cleanup()
            event.accept()
            self.logger.info("Widget cleanup complete. Proceeding to close.")
            # Ensure the QApplication event loop is quit so all processes terminate
            app = QApplication.instance()
            if app:
                app.quit()
        except Exception as e:
            self.logger.error(f"Error during closeEvent cleanup: {e}", exc_info=True)
            event.accept()
            app = QApplication.instance()
            if app:
                app.quit()

    def _check_visibility(self) -> None:
        """
        Checks if the widget should be visible based on fullscreen state and taskbar visibility.
        """
        try:
            taskbar_info = get_taskbar_info()
            if not taskbar_info:
                self._visibility_fail_count = getattr(self, '_visibility_fail_count', 0) + 1
                if self._visibility_fail_count >= 3:
                    self.logger.warning("Repeated taskbar info failures; hiding widget as fallback.")
                    self.setVisible(False)
                return

            self._visibility_fail_count = 0
            fullscreen_active = is_fullscreen_active(taskbar_info)
            taskbar_visible_flag = is_taskbar_visible(taskbar_info)
            should_be_visible = taskbar_visible_flag and not fullscreen_active
            is_currently_visible = self.isVisible()

            if not should_be_visible and is_currently_visible:
                startup_time = getattr(self, '_startup_time', None)
                if startup_time is None:
                    self._startup_time = datetime.now()
                    startup_time = self._startup_time
                time_since_startup = (datetime.now() - startup_time).total_seconds()
                grace_period = 5.0
                if time_since_startup < grace_period:
                    self.logger.debug(
                        f"Deferring visibility change: within grace period ({time_since_startup:.1f}/{grace_period}s)"
                    )
                    return

            if should_be_visible != is_currently_visible:
                self.logger.info(
                    f"Visibility change: Fullscreen={fullscreen_active}, "
                    f"TaskbarVisible={taskbar_visible_flag} -> ShouldBeVisible={should_be_visible}"
                )
                self.setVisible(should_be_visible)
            else:
                self.logger.debug(
                    f"Visibility unchanged: Fullscreen={fullscreen_active}, "
                    f"TaskbarVisible={taskbar_visible_flag}, Visible={is_currently_visible}"
                )
        except Exception as e:
            self.logger.error(f"Error in visibility check: {e}", exc_info=True)
            self._visibility_fail_count = getattr(self, '_visibility_fail_count', 0) + 1
            if self._visibility_fail_count >= 3:
                self.setVisible(False)
                self.logger.warning("Hiding widget due to persistent visibility check errors.")

    def _ensure_win32_topmost(self) -> None:
        """Uses the Windows API to assert the widget's topmost status."""
        widget_hwnd_ptr = self.winId()
        if not widget_hwnd_ptr:
            return

        try:
            widget_hwnd = int(widget_hwnd_ptr)
            if not win32gui.IsWindow(widget_hwnd):
                if widget_hwnd != 0:
                    self.logger.warning("Cannot ensure Win32 topmost: Widget HWND %d is invalid.", widget_hwnd)
                return

            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            result = win32gui.SetWindowPos(widget_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            if result == 0:
                error_code = win32api.GetLastError()
                if error_code != 0:
                    self.logger.error("SetWindowPos HWND_TOPMOST failed for HWND %d. Error: %d", widget_hwnd, error_code)
        except Exception as e:
            self.logger.error("Error in _ensure_win32_topmost for HWND %s: %s", widget_hwnd_ptr, e, exc_info=True)

    def _check_position(self) -> None:
        """
        Ensures the widget's topmost status without resetting its position.
        """
        if not self.isVisible():
            return
        if not self.position_manager:
            if hasattr(self, 'position_manager'):
                self.logger.warning("Position check skipped: PositionManager not available.")
            return

        try:
            # Only ensure topmost status, do not reset position
            self._ensure_win32_topmost()
            self.logger.debug("Topmost status ensured")
        except Exception as e:
            self.logger.error(f"Error in _check_position: {e}", exc_info=True)

    def apply_all_settings(self) -> None:
        """
        Applies all relevant settings from the current config to the widget and its components.

        Raises:
            RuntimeError: If configuration is missing or applying settings fails.
        """
        self.logger.info("Applying all settings from current configuration...")
        if not self.config:
            raise RuntimeError("Configuration not loaded.")

        try:
            if self.renderer:
                try:
                    self.renderer.update_config(self.config)
                    self.logger.debug("Renderer configuration updated.")
                except Exception as e:
                    self.logger.error(f"Failed to update renderer config: {e}", exc_info=True)

            if self.controller:
                try:
                    self.controller.update_interface_settings(
                        mode=self.config.get("interface_mode", ConfigConstants.DEFAULT_INTERFACE_MODE),
                        selected=self.config.get("selected_interfaces", []),
                    )
                    self.logger.debug("Controller interface settings updated.")
                except Exception as e:
                    self.logger.error(f"Failed to update controller settings: {e}", exc_info=True)

            if self.timer_manager:
                try:
                    update_rate = self.config.get("update_rate", ConfigConstants.DEFAULT_UPDATE_RATE)
                    self.timer_manager.update_speed_rate(update_rate)
                    self.logger.debug(f"TimerManager speed update rate set to {update_rate}s")
                except Exception as e:
                    self.logger.error(f"Failed to update timer rates: {e}", exc_info=True)

            if self.widget_state:
                try:
                    self.widget_state.apply_config(self.config)
                    self.logger.debug("WidgetState configuration updated.")
                except Exception as e:
                    self.logger.error(f"Failed to update WidgetState config: {e}", exc_info=True)

            self.font_updated.emit()
            self.update()
            self.logger.info("All settings applied successfully.")
        except Exception as e:
            self.logger.error(f"Error applying settings to components: {e}", exc_info=True)
            raise RuntimeError(f"Failed to apply settings: {e}") from e

    def handle_settings_changed(self, updated_config: Dict[str, Any]) -> None:
        """Handles configuration changes from the settings dialog."""
        self.logger.info("Handling settings change request...")
        old_config = self.config.copy()
        try:
            self.update_config(updated_config)
            try:
                self.apply_all_settings()
                self.logger.info("Settings successfully changed and applied.")
            except Exception as apply_err:
                self.logger.error(f"Failed to APPLY settings: {apply_err}", exc_info=True)
                self._rollback_config(old_config)
                raise RuntimeError(f"Failed to apply settings: {apply_err}") from apply_err
        except Exception as e:
            self.logger.error(f"Failed to handle settings change: {e}", exc_info=True)
            raise

    def show_settings(self) -> None:
        """Creates and displays the modal settings dialog."""
        self.logger.debug("Showing settings dialog...")
        try:
            from .settings import SettingsDialog
            config_copy = self.config.copy()
            interfaces = self.get_available_interfaces()
            startup_status = self.is_startup_enabled()
            app_version = getattr(self, 'app_version', "Unknown")

            dialog = SettingsDialog(
                parent=self,
                config=config_copy,
                version=app_version,
                i18n=self.i18n,
                available_interfaces=interfaces,
                is_startup_enabled=startup_status
            )

            dialog.adjustSize()
            accepted = dialog.exec()
            if accepted:
                self.logger.info("Settings dialog accepted by user.")
                try:
                    new_config = dialog.get_config()
                    if not new_config:
                        raise RuntimeError("Failed to retrieve settings from dialog UI.")
                    self.handle_settings_changed(new_config)
                    if dialog.should_update_startup_task():
                        requested_startup_state = dialog.is_startup_requested()
                        self.logger.info(f"Startup setting changed to: {requested_startup_state}")
                        self.toggle_startup(requested_startup_state)
                except Exception as e:
                    self.logger.error(f"Error applying settings: {e}", exc_info=True)
                    QMessageBox.warning(
                        self,
                        self.i18n.ERROR_TITLE,
                        f"Could not apply the new settings:\n\n{e}"
                    )
            else:
                self.logger.info("Settings dialog cancelled by user.")

            QTimer.singleShot(0, self._check_position)

        except Exception as e:
            self.logger.error(f"Error showing settings: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Could not open settings:\n\n{str(e)}")

    def _load_icon(self) -> None:
        """Loads the application icon from the assets directory."""
        self.logger.debug("Loading application icon...")
        self.app_icon = QIcon()
        try:
            if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                base_path = sys._MEIPASS
            else:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
                base_path = project_root

            icon_filename = getattr(AppConstants, 'ICON_FILENAME', 'NetSpeedTray.ico')
            icon_path = os.path.join(base_path, "assets", icon_filename)
            icon_path = os.path.normpath(icon_path)

            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.setWindowIcon(self.app_icon)
                self.logger.debug("Application icon loaded and set successfully.")
            else:
                self.logger.warning(f"Application icon not found at '{icon_path}'. Using default system icon.")
        except Exception as e:
            self.logger.error(f"Error loading application icon: {e}", exc_info=True)

    def _rollback_config(self, old_config: Dict[str, Any]) -> None:
        """Restores a previous configuration state."""
        self.logger.warning("Rolling back configuration changes due to apply failure.")
        self.config = old_config
        try:
            self.config_manager.save(self.config)
            self.logger.info("Configuration rolled back and saved successfully.")
        except Exception as e:
            self.logger.error(f"CRITICAL: Error saving rolled-back configuration: {e}", exc_info=True)

    def update_config(self, updates: Dict[str, Any]) -> None:
        """Updates the internal configuration and saves it to disk."""
        self.logger.debug(f"Updating configuration with {len(updates)} items...")
        if not self.config or not self.config_manager:
            raise RuntimeError("Configuration or ConfigManager not initialized.")
        try:
            self.config.update(updates)
            self.config_manager.save(self.config)
            self.logger.info("Configuration updated and saved successfully.")
        except Exception as e:
            self.logger.error(f"Error updating/saving configuration: {e}", exc_info=True)
            raise RuntimeError(f"Failed to save configuration: {e}") from e

    def save_position(self) -> None:
        """Saves the widget's current position to the configuration."""
        if not self.position_manager:
            self.logger.warning("Cannot save position: PositionManager not available.")
            return
        try:
            current_pos = self.pos()
            pos_dict = {"position_x": current_pos.x(), "position_y": current_pos.y()}
            self.update_config(pos_dict)
            self.logger.debug(f"Widget position saved: ({current_pos.x()}, {current_pos.y()})")
        except Exception as e:
            self.logger.error(f"Error saving widget position: %s", e, exc_info=True)

    def _show_graph_window(self) -> None:
        """Creates and displays the speed history graph window."""
        self.logger.debug("Request to show graph window.")
        if not self.i18n or not self.config or not self.widget_state:
            self.logger.error("Cannot show graph: Required components missing.")
            QMessageBox.critical(self, "Error", "Internal error: Required components not available.")
            return

        try:
            from .graph import GraphWindow
            if self.graph_window is None or not self.graph_window.isVisible():
                self.logger.info("Creating new GraphWindow instance.")
                self.graph_window = GraphWindow(parent=self, i18n=self.i18n)
                self.graph_window.show()
            else:
                self.logger.debug("Graph window already exists. Activating.")
                self.graph_window.show()
                self.graph_window.raise_()
                self.graph_window.activateWindow()
        except Exception as e:
            self.logger.error(f"Error showing graph window: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open the graph window:\n\n{str(e)}")

    def get_config(self) -> Dict[str, Any]:
        return self.config.copy() if self.config else {}

    def get_widget_size(self) -> QSize:
        return self.size()

    def set_app_version(self, version: str) -> None:
        self.app_version = version
        self.logger.debug(f"Application version set to: {version}")

    def update_position(self) -> None:
        self.logger.debug("External request to update widget position.")
        if self.position_manager:
            try:
                self.position_manager.update_position()
                self._adjust_position_with_margin()
            except Exception as e:
                self.logger.error(f"Error during position update: {e}", exc_info=True)

    def is_startup_enabled(self) -> bool:
        self.logger.debug("Checking system startup configuration...")
        try:
            return self._check_startup_registry()
        except Exception as e:
            self.logger.error(f"Error checking startup status: {e}", exc_info=True)
            return False

    def toggle_startup(self, enable: bool) -> None:
        self.logger.info(f"Request to {'enable' if enable else 'disable'} application startup.")
        try:
            self._set_startup_registry(enable)
            self.logger.info(f"Application startup successfully {'enabled' if enable else 'disabled'}.")
        except Exception as e:
            self.logger.error(f"Failed to {'enable' if enable else 'disable'} startup: {e}", exc_info=True)
            QMessageBox.warning(
                self,
                "Startup Error",
                f"Could not {'enable' if enable else 'disable'} automatic startup.\n\n{e}"
            )

    def _get_executable_path(self) -> str:
        if getattr(sys, 'frozen', False):
            return sys.executable
        else:
            return os.path.abspath(sys.argv[0])

    def _check_startup_registry(self) -> bool:
        if sys.platform != 'win32':
            return False
        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = AppConstants.APP_NAME
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_READ)
            with key:
                winreg.QueryValueEx(key, app_name)
                self.logger.debug(f"Startup entry '{app_name}' found in HKCU\\{registry_path}.")
                return True
        except FileNotFoundError:
            self.logger.debug(f"Startup entry '{app_name}' not found in HKCU\\{registry_path}.")
            return False
        except Exception as e:
            self.logger.error(f"Error checking startup registry: {e}")
            return False

    def _set_startup_registry(self, enable: bool) -> None:
        if sys.platform != 'win32':
            raise NotImplementedError("Startup registry modification only implemented for Windows.")
        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = AppConstants.APP_NAME
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_WRITE)
            with key:
                if enable:
                    executable_path = self._get_executable_path()
                    quoted_path = f'"{executable_path}"'
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, quoted_path)
                    self.logger.info(f"Added startup entry '{app_name}' pointing to {quoted_path}.")
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                        self.logger.info(f"Removed startup entry '{app_name}'.")
                    except FileNotFoundError:
                        self.logger.debug(f"Startup entry '{app_name}' not found, nothing to remove.")
        except Exception as e:
            raise RuntimeError(f"Failed to modify startup registry: {e}") from e

    def get_available_interfaces(self) -> List[str]:
        if self.controller:
            try:
                return self.controller.get_available_interfaces()
            except Exception as e:
                self.logger.error(f"Error getting available interfaces: {e}")
                return []
        else:
            self.logger.warning("Cannot get interfaces: Controller not initialized.")
            return []

    def cleanup(self) -> None:
        """
        Performs necessary cleanup actions before the widget is destroyed.
        """
        self.logger.info("Performing widget cleanup...")
        try:
            self.logger.debug("Stopping timers...")
            for timer, name in [(self.visibility_timer, "visibility"), (self.position_timer, "position")]:
                if timer and timer.isActive():
                    try:
                        timer.stop()
                        self.logger.debug(f"{name.capitalize()} timer stopped.")
                    except Exception as e:
                        self.logger.error(f"Failed to stop {name} timer: {e}", exc_info=True)

            if self.timer_manager and hasattr(self.timer_manager, 'cleanup'):
                try:
                    self.timer_manager.cleanup()
                    self.logger.debug("SpeedTimerManager cleaned up.")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup SpeedTimerManager: {e}", exc_info=True)

            if self.controller and hasattr(self.controller, 'cleanup'):
                try:
                    self.controller.cleanup()
                    self.logger.debug("Controller cleaned up.")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup Controller: {e}", exc_info=True)

            if self.widget_state and hasattr(self.widget_state, 'cleanup'):
                try:
                    self.widget_state.cleanup()
                    self.logger.debug("WidgetState cleaned up.")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup WidgetState: {e}", exc_info=True)

            try:
                self.save_position()
                if self.config_manager:
                    self.config_manager.save(self.config)
                    self.logger.debug("Final configuration saved.")
            except Exception as e:
                self.logger.error(f"Error saving final config/position: %s", e, exc_info=True)

            if self.graph_window:
                self.logger.debug("Closing graph window...")
                try:
                    if hasattr(self.graph_window, '_realtime_timer'):
                        self.graph_window._realtime_timer.stop()
                    if hasattr(self.graph_window, '_update_timer'):
                        self.graph_window._update_timer.stop()
                    if hasattr(self.graph_window, 'closing_normally'):
                        self.graph_window.closing_normally = True
                    self.graph_window.close()
                    self.graph_window = None
                    self.logger.debug("Graph window closed.")
                except Exception as e:
                    self.logger.error(f"Error closing graph window: %s", e, exc_info=True)

            self.upload_speed = 0.0
            self.download_speed = 0.0
            self._dragging = False
            self._drag_offset = QPoint()

            self.logger.info("Widget cleanup finished successfully.")
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: %s", e, exc_info=True)