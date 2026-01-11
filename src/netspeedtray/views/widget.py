from __future__ import annotations

# --- Standard Library Imports ---
import logging
import math
import os
import sys
import time
import winreg
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

# --- Third-Party Imports ---
import win32api
import win32con
import win32gui
from win32con import MONITOR_DEFAULTTONEAREST
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Qt
from PyQt6.QtGui import (
    QCloseEvent, QColor, QContextMenuEvent, QFont, QFontMetrics, QHideEvent,
    QIcon, QMouseEvent, QPaintEvent, QPainter, QShowEvent
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

# --- First-Party (Local) Imports ---
from netspeedtray import constants
from ..core.controller import NetworkController as CoreController
from ..core.timer_manager import SpeedTimerManager
from ..core.widget_state import WidgetState as CoreWidgetState
from ..utils.config import ConfigManager as CoreConfigManager
from ..utils.position_utils import PositionManager as CorePositionManager, WindowState as CoreWindowState
from ..utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, is_taskbar_visible,
    is_small_taskbar, get_process_name_from_hwnd
)

from ..utils.visibility_manager import VisibilityManager
from ..utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig
from ..utils.win_event_hook import (
    EVENT_OBJECT_LOCATIONCHANGE,
    EVENT_SYSTEM_FOREGROUND,
    EVENT_SYSTEM_MOVESIZEEND,
    WinEventHook
)

# --- Type Checking ---
if TYPE_CHECKING:
    from ..constants.i18n import I18nStrings
    from ..views.graph import GraphWindow
    from ..views.settings import SettingsDialog


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""

    MIN_UPDATE_INTERVAL = constants.config.defaults.MINIMUM_UPDATE_RATE


    def __init__(self, taskbar_height: int = constants.taskbar.taskbar.DEFAULT_HEIGHT, config: Optional[Dict[str, Any]] = None, i18n: Optional[constants.i18n.I18nStrings] = None, parent: QObject | None = None) -> None:
        """Initialize the NetworkSpeedTray with core components and UI setup."""
        super().__init__(parent)
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.{self.__class__.__name__}")
        self.logger.info("Initializing NetworkSpeedWidget...")
        self.settings_dialog: Optional[SettingsDialog] = None

        # --- Core Application State ---
        self.session_start_time = datetime.now()
        self.config_manager = CoreConfigManager()
        self.config: Dict[str, Any] = config or self._load_initial_config(taskbar_height)
        
        if i18n is None:
            raise ValueError("An i18n instance must be provided to NetworkSpeedWidget.")
        self.i18n = i18n
        
        self._apply_theme_aware_defaults()

        # --- Declare all instance attributes for clarity ---
        self.widget_state: CoreWidgetState
        self.timer_manager: SpeedTimerManager
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: CorePositionManager
        self.graph_window: Optional[GraphWindow] = None
        self.app_icon: QIcon
        self.context_menu: QMenu
        self.font: QFont
        self.metrics: QFontMetrics = None
        self.default_color: QColor
        self.high_color: QColor
        self.low_color: QColor
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        self.taskbar_height: int = taskbar_height
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()
        self.is_paused: bool = False
        self._last_immediate_hide_time: float = 0.0 # For the race condition fix
        self._is_context_menu_visible: bool = False
        self.last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._taskbar_lost_count: int = 0
        
        # Hooks for system events
        self.foreground_hook: Optional[WinEventHook] = None
        self.taskbar_hook: Optional[WinEventHook] = None
        self.movesize_hook: Optional[WinEventHook] = None
        
        # Timers for periodic checks
        self._tray_watcher_timer = QTimer(self)
        self._state_watcher_timer = QTimer(self) # The "Safety Net" timer
        
        self.setVisible(False)
        self.logger.debug("Widget initially hidden to stabilize position and size.")

        # --- Initialization Steps ---
        try:
            self._setup_window_properties()
            self._init_ui_components()
            self._init_core_components()
            
            # Now that all components are initialized, perform the initial resize.
            self._resize_widget_for_font()
            
            self._init_position_manager()
            self._init_context_menu()
            self._setup_connections()
            self._setup_timers()
            self._initialize_position()
            self._synchronize_startup_task()
            
            QTimer.singleShot(0, self._delayed_initial_show)

            self.logger.debug("NetworkSpeedWidget initialized successfully.")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
            raise RuntimeError(f"Failed to initialize NetworkSpeedWidget: {e}") from e


    def _setup_timers(self) -> None:
        """Configures all application timers."""
        # This timer periodically checks if icons were added/removed from the tray.
        self._tray_watcher_timer.timeout.connect(self._check_for_tray_changes)
        self._tray_watcher_timer.start(10000)
        self.logger.debug("Smart tray watcher timer started (10000ms).")

        # This is the "Safety Net" timer. It runs to catch states missed by events.
        self._state_watcher_timer.setInterval(1000)
        self._state_watcher_timer.timeout.connect(self._execute_refresh)
        self._state_watcher_timer.start()
        self.logger.debug("Safety net state watcher timer started (1000ms).")

        # Add a dedicated timer to watch for explorer.exe restarts
        self._taskbar_validity_timer = QTimer(self)
        self._taskbar_validity_timer.timeout.connect(self._check_taskbar_validity)
        self._taskbar_validity_timer.start(3000) # Check every 3 seconds
        self.logger.debug("Taskbar validity watcher timer started (3000ms).")


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
            self.renderer = CoreWidgetRenderer(self.config, self.i18n)
            self.timer_manager.start_timer()
            self.logger.debug("Core components initialized and timers started")
        except Exception as e:
            self.logger.error("Failed to initialize core components: %s", e, exc_info=True)
            raise RuntimeError("Failed to initialize core application components") from e


    def _init_position_manager(self) -> None:
        """
        Initialize the PositionManager for widget placement.
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

            # The taskbar_hook has been removed from here. The new VisibilityManager
            # now handles all necessary system events, including those that affect position.

        except Exception as e:
            self.logger.error(f"Failed to initialize PositionManager: %s", e, exc_info=True)
            raise RuntimeError("PositionManager initialization failed") from e


    def _initialize_position(self) -> None:
        """Set the initial widget position using saved coordinates or default placement."""
        self.logger.debug("Setting initial widget position...")
        if not hasattr(self, 'position_manager') or not self.position_manager:
            self.logger.error("PositionManager not initialized, cannot set position.")
            return

        try:
            use_saved_position = self.config.get("free_move", False)
            pos_x = self.config.get("position_x")
            pos_y = self.config.get("position_y")

            if use_saved_position and isinstance(pos_x, int) and isinstance(pos_y, int):
                # Restore the exact saved position if free_move is ON.
                self.move(pos_x, pos_y)
                self.logger.info(f"Restored saved 'Free Move' position: ({pos_x}, {pos_y})")
            else:
                # Calculate and apply the position without saving the config
                self.logger.debug("Placing widget at default taskbar-snapped position.")
                self.position_manager.update_position()

        except Exception as e:
            self.logger.error(f"Failed to set initial position: {e}", exc_info=True)
            raise RuntimeError("Initial position setup failed") from e


    def _check_and_update_position(self) -> None:
        """
        Periodically checks if the widget's position needs correction and updates it
        ONLY if it has drifted from the calculated target position.
        """
        if self.config.get("free_move", False) or not self.isVisible() or self._dragging:
            return

        if not self.position_manager:
            return

        try:
            # 1. Get the widget's current position
            current_pos = self.pos()
            
            # 2. Calculate where the widget SHOULD be
            target_pos = self.position_manager.get_calculated_position()

            # 3. Only trigger an update if the position is actually incorrect
            if target_pos and current_pos != target_pos:
                self.logger.debug(
                    f"Position drift detected. Current: {current_pos}, Target: {target_pos}. Correcting."
                )
                # The position manager's update function will perform the move
                self.position_manager.update_position()
            else:
                self.logger.debug(f"Position check OK. Current: {current_pos}, Target: {target_pos}.")

        except Exception as e:
            self.logger.error(f"Error during periodic position check: {e}", exc_info=True)


    def _check_for_tray_changes(self) -> None:
        """
        Periodically checks if the system tray's geometry has changed, which
        indicates icons have been added or removed. Triggers a reposition only
        if a change is detected. This is the 'smart' polling solution.
        """
        if self.config.get("free_move", False) or not self.isVisible():
            return

        try:
            taskbar_info = get_taskbar_info()
            if not taskbar_info:
                return

            current_tray_rect = taskbar_info.get_tray_rect()

            # If this is the first run, just store the rect and exit.
            if self.last_tray_rect is None:
                self.last_tray_rect = current_tray_rect
                return

            # If the tray's rectangle has changed, an icon was added or removed.
            if self.last_tray_rect != current_tray_rect:
                self.logger.debug("System tray change detected. Updating widget position.")
                self.update_position()
                # Store the new rect for the next comparison.
                self.last_tray_rect = current_tray_rect

        except Exception as e:
            self.logger.error(f"Error during tray change check: {e}", exc_info=True)


    def _check_taskbar_validity(self) -> None:
        """
        Periodically checks if the stored taskbar handle is still valid. If not,
        it triggers a persistent refresh cycle to recover from an explorer.exe restart.
        """
        try:
            if not self.position_manager or not hasattr(self.position_manager, 'taskbar_info'):
                return

            taskbar_hwnd = self.position_manager.taskbar_info.hwnd
            
            if taskbar_hwnd != 0 and not win32gui.IsWindow(taskbar_hwnd):
                self.logger.warning(
                    f"Taskbar handle {taskbar_hwnd} is no longer valid. Explorer likely restarted. "
                    "Initiating recovery refresh cycle."
                )
                # Instead of one refresh, trigger a burst of attempts
                # This gives explorer.exe several seconds to fully initialize.
                for i in range(5): # Attempt to refresh 5 times over 5 seconds
                    QTimer.singleShot(i * 1000, self._execute_refresh)
        except Exception as e:
            self.logger.error(f"Error during taskbar validity check: {e}", exc_info=True)


    def _on_foreground_change_immediate(self, hwnd: int) -> None:
        """
        Handles the raw event for an "emergency hide" on unambiguous fullscreen windows.
        This handler's ONLY job is to hide the widget; it never shows it.
        """
        try:
            if not self.isVisible() or not hwnd or not win32gui.IsWindow(hwnd):
                return

            taskbar_info = get_taskbar_info()
            if is_taskbar_obstructed(taskbar_info, hwnd):
                window_rect = win32gui.GetWindowRect(hwnd)
                monitor_info = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd))
                monitor_rect = monitor_info.get('Monitor')
                
                # This check for true fullscreen is what provides the instant hide.
                if window_rect == monitor_rect:
                    self.logger.debug(f"Immediate check: Fullscreen detected (HWND: {hwnd}). Hiding widget.")
                    self.setVisible(False)
                    # Set the cooldown timestamp to prevent the debounced handler from undoing this.
                    self._last_immediate_hide_time = time.monotonic()

        except (win32gui.error, AttributeError):
            pass # Fail silently; the authoritative check will fix any errors.
        except Exception as e:
            self.logger.error(f"Error in immediate foreground change handler: {e}", exc_info=True)


    def _execute_refresh(self, hwnd: int = 0) -> None:
        """
        The AUTHORITATIVE refresh trigger. This version includes a grace period
        to handle temporary taskbar detection failures (e.g., during shell restarts).
        """
        if self._is_context_menu_visible or self._dragging:
            return
        
        try:
            taskbar_info = get_taskbar_info()

            # Implement the "coasting" logic for taskbar detection failures.
            # Implement the "coasting" logic for taskbar detection failures.
            if taskbar_info.hwnd == 0: # hwnd=0 signifies a fallback object from get_taskbar_info
                self._taskbar_lost_count += 1
                if self._taskbar_lost_count % 10 == 0: # Log warning every 10 seconds
                    self.logger.warning(
                        f"Taskbar detection failing. Coasting on fallback/safe mode. "
                        f"Failure count: {self._taskbar_lost_count}"
                    )
                # Removed logic that hides widget after 5 failures. 
                # We now rely on 'safe fallback position' (bottom-right of screen) instead.
            else:
                # If we successfully found a real taskbar, reset the counter.
                self._taskbar_lost_count = 0

            if hwnd == 0:
                hwnd = win32gui.GetForegroundWindow()

            should_be_visible = is_taskbar_visible(taskbar_info) and not is_taskbar_obstructed(taskbar_info, hwnd)

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)
            
            # Only update position if we are supposed to be visible.
            if self.isVisible() and not self.config.get("free_move", False):
                self.position_manager.update_position(fresh_taskbar_info=taskbar_info)
                self._ensure_win32_topmost()

        except Exception as e:
            self.logger.error(f"Critical error in _execute_refresh: {e}", exc_info=True)
            if self.isVisible(): self.setVisible(False)


    def _delayed_initial_show(self) -> None:
        """Triggers the initial authoritative visibility check."""
        self.logger.debug("Executing delayed initial show...")
        try:
            # Replace the call to the old manager with the proven, authoritative function.
            self._execute_refresh()
            
            if self.isVisible():
                self.logger.info("Widget shown after stabilization")
        except Exception as e:
            self.logger.error(f"Error in delayed initial show: {e}", exc_info=True)
            # Ensure widget is hidden if an error occurs during the initial check.
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


    def update_display_speeds(self, upload_mbps: float, download_mbps: float) -> None:
        """
        Slot for the controller's `display_speed_updated` signal.
        Receives aggregated speeds in Mbps and schedules a repaint of the widget.
        """
        self.upload_speed = upload_mbps
        self.download_speed = download_mbps
        self.update() # Trigger a repaint


    def _update_display_speeds(self) -> None:
        """
        This method is now deprecated and kept to prevent crashes if called by old code.
        All logic has been moved to update_stats and paintEvent.
        """
        self.update() # Just trigger a repaint.


    def _load_initial_config(self, taskbar_height: int) -> Dict[str, Any]:
        """Load configuration and inject taskbar height."""
        self.logger.debug("Loading initial configuration...")
        config = self.config_manager.load()
        config["taskbar_height"] = taskbar_height
        return config


    def _apply_theme_aware_defaults(self) -> None:
        """
        Synchronizes the widget's text color with the Windows theme on startup,
        but ONLY if the user has not manually set a color.
        """
        try:
            # Defaults to True if the key doesn't exist (for backward compatibility).
            is_automatic = self.config.get("color_is_automatic", True)

            if not is_automatic:
                self.logger.info("User has set a manual color. Skipping auto-theme sync.")
                return

            # If the color is automatic, proceed with the theme check.
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            system_uses_light_theme, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            winreg.CloseKey(key)

            correct_color = constants.color.BLACK if system_uses_light_theme == 1 else constants.color.WHITE
            
            # Get the color currently stored in the config to compare against.
            current_color = self.config.get("default_color", "")

            # If the current color is wrong for the theme, correct it.
            if current_color.upper() != correct_color.upper():
                theme_name = "Light" if system_uses_light_theme == 1 else "Dark"
                self.logger.info(
                    f"Windows theme is {theme_name}, but auto-color is wrong. Correcting to {correct_color}."
                )
                # Update the in-memory config and save it.
                updates = {
                    "default_color": correct_color,
                    "color_is_automatic": True # Re-affirm it's automatic
                }
                self.config.update(updates)
                self.update_config(updates)

        except Exception as e:
            self.logger.warning(f"Could not perform theme-aware color sync: {e}")


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
        
        # Make the window's transparent areas ignore mouse events
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        
        self.setMouseTracking(True)
        self.logger.debug("Window properties set")


    def _init_ui_components(self) -> None:
        """Initialize UI-related elements: icon, colors, font."""
        self.settings_dialog: Optional[SettingsDialog] = None
        self.logger.debug("Initializing UI components...")

        self._load_icon()
        self.default_color = QColor(self.config.get("default_color", constants.config.defaults.DEFAULT_COLOR))
        self.high_color = QColor(self.config.get("high_speed_color", constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR))
        self.low_color = QColor(self.config.get("low_speed_color", constants.config.defaults.DEFAULT_LOW_SPEED_COLOR))
        
        self.logger.debug(f"Colors initialized - Default: {self.default_color.name()}, High: {self.high_color.name()}, Low: {self.low_color.name()}")
        self._init_font()
        self.logger.debug("UI components initialized")


    def _init_font(self) -> None:
        """Initialize the font and set initial widget size."""
        self.logger.debug("Initializing font...")
        self._set_font(resize=False)


    def _set_font(self, resize: bool = True) -> None:
        """Apply font settings from config."""
        self.logger.debug("Setting font...")
        font_family = self.config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        font_size = self.config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
        font_weight = self.config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
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
        Connects signals from core components and initializes the WinEventHooks for
        stable, debounced visibility management.
        """
        self.logger.debug("Setting up signal connections and WinEventHooks...")
        if not all([self.widget_state, self.timer_manager, self.controller]):
            raise RuntimeError("Core components missing during signal connection setup.")
        try:
            # Connect core component signals
            self.timer_manager.stats_updated.connect(self.controller.update_speeds)
            self.controller.display_speed_updated.connect(self.update_display_speeds)

            # 1. Foreground window change hook (for showing/hiding)
            self.foreground_hook = WinEventHook(EVENT_SYSTEM_FOREGROUND, debounce_ms=250)
            self.foreground_hook.event_triggered.connect(self._on_foreground_change_immediate)
            self.foreground_hook.event_triggered_debounced.connect(self._execute_refresh)
            self.foreground_hook.start()

            # 2. Taskbar move/resize hook (for repositioning)
            taskbar_info = get_taskbar_info()
            self.movesize_hook = WinEventHook(EVENT_SYSTEM_MOVESIZEEND, hwnd_to_watch=taskbar_info.hwnd)
            self.movesize_hook.event_triggered.connect(self.update_position)
            self.movesize_hook.start()
            
            self.logger.debug("Signal connections and WinEventHooks established successfully.")
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
        Calculates and sets the widget's fixed dimensions, adapting for small taskbars.
        """
        self.logger.debug("Resizing widget based on renderer layout...")
        if not self.metrics:
            raise RuntimeError("FontMetrics not initialized.")
        if not hasattr(self, 'renderer'):
            raise RuntimeError("Renderer not initialized before resizing.")

        try:
            taskbar_info = get_taskbar_info()
            is_small = is_small_taskbar(taskbar_info)
            self.logger.debug(f"Small taskbar detected: {is_small}")

            precision = self.config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES)
            margin = constants.renderer.TEXT_MARGIN
            
            if is_small:
                # Horizontal Layout Calculation
                upload_text, download_text = self.renderer._format_speed_texts(9.99, 99.99, False, precision, True)
                up_str = f"{self.i18n.UPLOAD_ARROW} {upload_text}"
                down_str = f"{self.i18n.DOWNLOAD_ARROW} {download_text}"
                separator = constants.layout.HORIZONTAL_LAYOUT_SEPARATOR
                
                calculated_width = (self.metrics.horizontalAdvance(up_str) +
                                    self.metrics.horizontalAdvance(separator) +
                                    self.metrics.horizontalAdvance(down_str) + (margin * 2))
                calculated_height = self.taskbar_height
            else:
                # Vertical Layout Calculation
                max_number_str = f"{999.99:.{precision}f}"
                arrow_width = self.metrics.horizontalAdvance(self.i18n.UPLOAD_ARROW)
                max_number_width = self.metrics.horizontalAdvance(max_number_str)
                possible_units = [
                    f" {self.i18n.KBITS_LABEL}", f" {self.i18n.MBITS_LABEL}", f" {self.i18n.GBITS_LABEL}",
                    f" {self.i18n.KBPS_LABEL}", f" {self.i18n.MBPS_LABEL}", f" {self.i18n.GBPS_LABEL}"
                ]
                max_unit_width = max(self.metrics.horizontalAdvance(unit) for unit in possible_units)

                calculated_width = (margin + arrow_width + constants.renderer.ARROW_NUMBER_GAP +
                                    max_number_width + constants.renderer.VALUE_UNIT_GAP +
                                    max_unit_width + margin)
                calculated_height = self.taskbar_height

            self.setFixedSize(math.ceil(calculated_width), math.ceil(calculated_height))
            self.logger.debug(f"Widget resized to: {self.width()}x{self.height()}px (Layout: {'Horizontal' if is_small else 'Vertical'})")

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


    def _update_color_for_theme(self) -> None:
        """
        Checks the current Windows shell (taskbar) theme and updates the widget's
        text color in real-time, but ONLY if the color is set to automatic.
        This is an in-memory change and does not write to the config file.
        """
        self.logger.debug("Executing live theme color update check...")
        try:
            if not self.config.get("color_is_automatic", True):
                self.logger.info("User has a manual color set. Live theme update is disabled.")
                return

            # Query the registry for the current shell theme.
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            # This value specifically controls the Windows shell (taskbar) theme. 1 = Light, 0 = Dark.
            system_uses_light_theme, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            winreg.CloseKey(key)

            # Determine the target color based on the shell theme.
            if system_uses_light_theme == 1:  # Light Taskbar
                target_color_hex = constants.color.BLACK
                theme_name = "Light"
            else:  # Dark Taskbar
                target_color_hex = constants.config.defaults.DEFAULT_COLOR
                theme_name = "Dark"
            
            # Only perform an update if the color actually needs to change.
            if self.default_color.name().upper() != target_color_hex.upper():
                self.logger.info(f"Windows shell theme changed to {theme_name}. Updating text color to {target_color_hex}.")
                
                # Apply the change to the in-memory QColor object used for painting.
                self.default_color = QColor(target_color_hex)
                
                # Trigger a repaint of the widget to reflect the new color.
                self.update()
            else:
                self.logger.debug(f"Shell theme changed to {theme_name}, but color is already correct.")

        except FileNotFoundError:
            self.logger.warning("Could not find theme registry key for live update. Feature may not work.")
        except Exception as e:
            self.logger.error(f"Failed to perform live theme color update: {e}", exc_info=True)


    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Handles all painting for the widget by delegating to the renderer.
        """
        if not self.isVisible():
            return
        
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

            if not self.renderer or not self.metrics:
                self.logger.error("Renderer or metrics not initialized during paintEvent")
                self._draw_paint_error(painter, "Render Error")
                return

            painter.setFont(self.font)

            upload_bytes_sec = (self.upload_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            download_bytes_sec = (self.download_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            render_config = RenderConfig.from_dict(self.config)
            
            # Detect layout mode and pass to renderer
            taskbar_info = get_taskbar_info()
            layout_mode = 'horizontal' if is_small_taskbar(taskbar_info) else 'vertical'
            
            self.renderer.draw_network_speeds(
                painter=painter,
                upload=upload_bytes_sec,
                download=download_bytes_sec,
                width=self.width(),
                height=self.height(),
                config=render_config,
                layout_mode=layout_mode
            )

            # Pass the corrected layout_mode to the graph renderer as well
            if render_config.graph_enabled:
                history = self.widget_state.get_in_memory_speed_history()
                self.renderer.draw_mini_graph(
                    painter=painter,
                    width=self.width(),
                    height=self.height(),
                    config=render_config,
                    history=history,
                    layout_mode=layout_mode
                )
            
        except Exception as e:
            self.logger.error(f"Error in paintEvent: {e}", exc_info=True)
        finally:
            if painter.isActive():
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

            error_color = QColor(constants.color.RED)
            error_color.setAlpha(200) # Keep alpha for translucency
            p.fillRect(self.rect(), error_color)
            p.setPen(Qt.GlobalColor.white)
            if self.font:
                p.setFont(self.font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

            if created_painter:
                p.end()

        except Exception as paint_err:
            self.logger.critical(f"CRITICAL: Failed to draw paint error indicator: {paint_err}", exc_info=True)


    def enterEvent(self, event: QEvent) -> None:
        """
        When the mouse enters the widget's rectangle, make it tangible
        so it can be clicked and dragged.
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        super().enterEvent(event)


    def leaveEvent(self, event: QEvent) -> None:
        """
        When the mouse leaves the widget's rectangle, make it click-through
        again so it doesn't block other applications.
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        super().leaveEvent(event)


    def _calculate_menu_position(self) -> QPoint:
        """
        Calculates the optimal global position for the context menu.
        Positions the menu above the rendered text, ensuring it stays on-screen.
        """
        try:
            text_rect_local = self.renderer.get_last_text_rect() if self.renderer else QRect()

            # Determine a reference point for positioning
            if not text_rect_local.isValid() or text_rect_local.isEmpty():
                self.logger.warning("Renderer text rect invalid. Falling back to widget center for menu.")
                ref_global_pos = self.mapToGlobal(self.rect().center())
                ref_top_global_y = self.mapToGlobal(self.rect().topLeft()).y()
            else:
                ref_global_pos = self.mapToGlobal(text_rect_local.center())
                ref_top_global_y = self.mapToGlobal(text_rect_local.topLeft()).y()

            menu_size = self.context_menu.sizeHint()
            menu_width = menu_size.width() if menu_size.width() > 0 else constants.ui.general.ESTIMATED_MENU_WIDTH
            menu_height = menu_size.height()

            # Position menu above the text, centered horizontally
            target_x = ref_global_pos.x() - menu_width // 2
            target_y = ref_top_global_y - menu_height - constants.ui.general.MENU_PADDING_ABOVE
            target_pos = QPoint(int(round(target_x)), int(round(target_y)))

            # Validate position to ensure it's on-screen
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                screen_rect = screen.availableGeometry()
                validated_x = max(screen_rect.left(), min(target_pos.x(), screen_rect.right() - menu_width + 1))
                validated_y = max(screen_rect.top(), min(target_pos.y(), screen_rect.bottom() - menu_height + 1))
                target_pos.setX(validated_x)
                target_pos.setY(validated_y)
            
            return target_pos
        except Exception as e:
            self.logger.error(f"Error calculating menu position: {e}", exc_info=True)
            return self.mapToGlobal(self.rect().center()) # Safe fallback


    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handles mouse press events for dragging and context menu."""
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_offset = event.position().toPoint()
                event.accept()
            elif event.button() == Qt.MouseButton.RightButton:
                if self.context_menu:
                    menu_pos = self._calculate_menu_position()
                    self._is_context_menu_visible = True
                    self.context_menu.exec(menu_pos)
                    self._is_context_menu_visible = False
                    # Replace the call to the old manager with the proven, authoritative function.
                    self._execute_refresh()
                event.accept()
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

        if not self.position_manager or not hasattr(self.position_manager, 'calculator'):
            self.logger.warning("Cannot process drag move: PositionManager or calculator unavailable.")
            return

        try:
            new_global_pos = event.globalPosition().toPoint() - self._drag_offset
            free_move_enabled = self.config.get("free_move", False)

            if free_move_enabled:
                self.move(new_global_pos)
            else:
                taskbar_info = get_taskbar_info()
                if not taskbar_info:
                    self.logger.warning("Cannot constrain drag move: Taskbar info unavailable.")
                    self.move(new_global_pos)
                    event.accept()
                    return

                calculator = self.position_manager.calculator
                constrained_pos = calculator.constrain_drag_position(new_global_pos, taskbar_info, self.size(), self.config)
                if constrained_pos:
                    self.move(constrained_pos)

        except Exception as e:
            self.logger.error(f"Error processing mouseMoveEvent: {e}", exc_info=True)


    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """
        Stops dragging and saves the new position to the configuration.
        """
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.save_position() # This is the only action needed.
            event.accept()
        else:
            super().mouseReleaseEvent(event)


    def changeEvent(self, event: QEvent) -> None:
        """
        This event is handled for proper superclass behavior, but all custom
        logic is now managed by the debounced WinEventHooks to prevent blinking.
        """
        super().changeEvent(event)


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
        Shows the context menu. This handler is the primary mechanism for
        keyboard-invoked context menus and a fallback for mouse events.
        """
        try:
            if self.context_menu:
                menu_pos = self._calculate_menu_position()
                # --- ADDED STATE MANAGEMENT ---
                self._is_context_menu_visible = True
                self.context_menu.exec(menu_pos)
                self._is_context_menu_visible = False
                # Manually trigger a refresh after the menu closes to restore state.
                self.visibility_manager.refresh_visibility()
            event.accept()
        except Exception as e:
            self.logger.error(f"Error showing context menu: {e}", exc_info=True)
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
            app = QApplication.instance()
            if app:
                app.quit()
        except Exception as e:
            self.logger.error(f"Error during closeEvent cleanup: {e}", exc_info=True)
            event.accept()
            app = QApplication.instance()
            if app:
                app.quit()


    def _handle_visibility_change(self, should_be_visible: bool) -> None:
        """Slot to handle the visibility signal from the VisibilityManager."""
        if self._dragging or self.config.get("free_move", False):
            return

        if self.isVisible() != should_be_visible:
            self.setVisible(should_be_visible)
            if should_be_visible:
                self.update_position()
                self._ensure_win32_topmost()


    def _update_widget_state(self, hwnd: int) -> None:
        """
        The single authoritative method to control widget visibility.
        Now includes a check to prevent flicker if the state isn't changing.
        """
        try:
            taskbar_info = get_taskbar_info()
            
            should_be_visible = is_taskbar_visible(taskbar_info) and not is_taskbar_obstructed(taskbar_info, hwnd)

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)
                if should_be_visible:
                    self.position_manager.update_position()

        except Exception as e:
            self.logger.error(f"Error in _update_widget_state: {e}", exc_info=True)
 

    def _ensure_win32_topmost(self) -> None:
        """
        Uses the Windows API to forcefully re-assert the widget's topmost status.

        This uses the "re-promotion" technique (setting NOTOPMOST then TOPMOST)
        to handle edge cases where the window manager's Z-order gets "stuck,"
        for instance after the Start Menu or Action Center is closed.
        """
        widget_hwnd_ptr = self.winId()
        if not widget_hwnd_ptr:
            return

        try:
            widget_hwnd = int(widget_hwnd_ptr)
            if not win32gui.IsWindow(widget_hwnd):
                if widget_hwnd != 0:
                    self.logger.warning("Cannot ensure Win32 topmost: Widget HWND %d is invalid.", widget_hwnd)
                return

            # Define the flags to prevent moving, resizing, or activating the window.
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE

            # --- The "Re-Promotion" Technique ---
            # 1. Briefly drop the window from the topmost layer.
            win32gui.SetWindowPos(widget_hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            # 2. Immediately promote it back to the topmost layer.
            win32gui.SetWindowPos(widget_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            
            self.logger.debug("Forcefully re-asserted HWND_TOPMOST status for HWND %d.", widget_hwnd)

        except Exception as e:
            self.logger.error("Error in _ensure_win32_topmost for HWND %s: %s", widget_hwnd_ptr, e, exc_info=True)


    def _enforce_topmost_status(self) -> None:
        """
        Periodically ensures the widget's topmost status. This is the primary
        mechanism for keeping the widget visible over the taskbar after shell
        UI elements like the Start Menu are closed.
        """
        # Do not run this logic if the user wants to move the widget freely.
        if self.config.get("free_move", False):
            return

        try:
            self._ensure_win32_topmost()
        except Exception as e:
            self.logger.error(f"Error in periodic topmost enforcement: {e}", exc_info=True)


    def reset_to_default_position(self) -> None:
        """
        Resets the widget to its default position by updating the config and then
        triggering a reposition.
        """
        self.logger.info("Resetting widget to default auto-detected position.")
        if not self.position_manager:
            self.logger.warning("Cannot reset position, PositionManager is not available.")
            return

        self.update_config({
            "position_x": None,
            "position_y": None,
            "tray_offset_x": constants.config.defaults.DEFAULT_TRAY_OFFSET_X
        })
        self.update_position()


    def apply_all_settings(self) -> None:
        """
        Applies all settings from the current config in a specific, synchronous order
        to prevent race conditions: update components -> update colors -> set font & resize -> reposition -> repaint.
        """
        self.logger.debug("Applying all settings from current configuration...")
        if not self.config:
            raise RuntimeError("Configuration not loaded.")

        try:
            # 1. Update all non-visual components.
            if self.renderer:
                self.renderer.update_config(self.config)
            if self.controller:
                self.controller.apply_config(self.config)
            if self.timer_manager:
                update_rate = self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)
                self.timer_manager.update_speed_rate(update_rate)
            if self.widget_state:
                self.widget_state.apply_config(self.config)

            self.logger.debug("Synchronizing internal QColor objects with current config.")
            self.default_color = QColor(self.config.get("default_color", constants.config.defaults.DEFAULT_COLOR))
            self.high_color = QColor(self.config.get("high_speed_color", constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR))
            self.low_color = QColor(self.config.get("low_speed_color", constants.config.defaults.DEFAULT_LOW_SPEED_COLOR))

            # 2. Directly set the font, which also triggers the resize.
            self._set_font(resize=True)
            
            # 3. AFTER resizing, update the position.
            self.update_position()
            
            # 4. Schedule a repaint to reflect all changes.
            self.update()
            
            self.logger.debug("All settings applied successfully.")
        except Exception as e:
            self.logger.error(f"Error applying settings to components: {e}", exc_info=True)
            raise RuntimeError(f"Failed to apply settings: {e}") from e


    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """
        Handles configuration changes. Applies them to the widget and optionally saves them.
        """
        self.logger.debug(f"Handling settings change request... (Save to disk: {save_to_disk})")
        
        old_config = self.config.copy()

        try:
            free_move_was_enabled = old_config.get('free_move', False)
            free_move_is_now_enabled = updated_config.get('free_move', False)

            if free_move_was_enabled and not free_move_is_now_enabled:
                self.logger.debug("Free Move was disabled. Clearing saved coordinates.")
                updated_config['position_x'] = None
                updated_config['position_y'] = None
            
            # Capture current position when Free Move is active upon saving
            if free_move_is_now_enabled:
                current_pos = self.pos()
                updated_config['position_x'] = current_pos.x()
                updated_config['position_y'] = current_pos.y()
                self.logger.debug(f"Free Move is active. Capturing current position ({current_pos.x()}, {current_pos.y()}) for save.")

            if save_to_disk:
                self.update_config(updated_config)
            else:
                self.config.update(updated_config)
            
            # apply_all_settings now handles the entire visual update transaction correctly.
            self.apply_all_settings()

            self.logger.debug("Settings successfully handled and applied.")

        except Exception as e:
            self.logger.error(f"Failed to handle settings change: {e}", exc_info=True)
            self.config = old_config # Rollback in-memory config
            raise


    def show_settings(self) -> None:
        """Creates and displays the settings dialog as a normal, non-modal window."""
        self.logger.debug("Showing settings dialog...")
        try:
            from .settings import SettingsDialog

            if self.settings_dialog is None:
                self.logger.debug("Creating new SettingsDialog instance.")
                # Create the dialog as a top-level window (parent=None)
                self.settings_dialog = SettingsDialog(
                    main_widget=self,
                    config=self.config.copy(),
                    version=constants.app.VERSION,
                    i18n=self.i18n,
                    available_interfaces=self.get_unified_interface_list(),
                    is_startup_enabled=self.is_startup_enabled()
                )

            if not self.settings_dialog.isVisible():
                # Also update the interface list when showing an existing dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
                self.settings_dialog.reset_with_config(
                    config=self.config.copy(),
                    is_startup_enabled=self.is_startup_enabled()
                )
                self.settings_dialog.show()
            else:
                self.logger.debug("Settings dialog already visible. Activating.")
                # Also update the interface list when re-activating the dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
                self.settings_dialog.raise_()
                self.settings_dialog.activateWindow()

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

            icon_filename = constants.app.ICON_FILENAME
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


    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Updates the internal configuration and optionally saves it to disk."""
        self.logger.debug(f"Updating configuration with {len(updates)} items... (Save: {save_to_disk})")
        if not self.config or not self.config_manager:
            raise RuntimeError("Configuration or ConfigManager not initialized.")
        try:
            self.config.update(updates)
            if save_to_disk:
                self.config_manager.save(self.config)
                self.logger.debug("Configuration updated and saved successfully.")
        except Exception as e:
            self.logger.error(f"Error updating/saving configuration: {e}", exc_info=True)
            raise RuntimeError(f"Failed to save configuration: {e}") from e


    def handle_graph_settings_update(self, updates: Dict[str, Any]) -> None:
        """
        Public method called by the GraphWindow to update and save configuration.
        This centralizes the saving logic and prevents race conditions.
        """
        self.logger.debug(f"Received settings update from graph window: {updates}")
        # The update_config method already updates the in-memory config and saves to disk.
        # We can just call it directly.
        self.update_config(updates)


    def save_position(self) -> None:
        """
        Instantly saves the widget's current X and Y coordinates to the configuration.
        This provides a "live save" experience when the user moves the widget,
        ensuring its position is remembered immediately after a drag, even before
        the main settings are saved.
        """
        current_pos = self.pos()
        position_updates = {
            "position_x": current_pos.x(),
            "position_y": current_pos.y()
        }
        self.logger.debug(f"Live-saving position after drag: ({current_pos.x()}, {current_pos.y()})")
        self.update_config(position_updates)


    def _show_graph_window(self) -> None:
        """Creates and displays the speed history graph window."""
        self.logger.debug("Request to show graph window.")
        if not self.i18n or not self.config or not self.widget_state:
            self.logger.error("Cannot show graph: Required components missing.")
            QMessageBox.critical(self, "Error", "Internal error: Required components not available.")
            return

        try:
            # --- Optimization: Lazy import ---
            # By placing the import here, matplotlib is only loaded when the user
            # requests the graph, speeding up initial application startup.
            from .graph import GraphWindow

            if self.graph_window is None or not self.graph_window.isVisible():
                self.logger.info("Creating new GraphWindow instance.")
                
                self.graph_window = GraphWindow(
                    main_widget=self, # Pass self as the main_widget reference
                    parent=None,      # Set the Qt parent to None to decouple
                    i18n=self.i18n,
                    session_start_time=self.session_start_time
                )
                
                # Connect the signal AFTER the instance exists.
                self.widget_state.db_worker.database_updated.connect(
                    self.graph_window._populate_interface_filter
                )
                
                # CLEAN FIX: Listen for destruction to restore Z-order/Visibility
                # This decouples the child from the parent's implementation details.
                self.graph_window.window_closed.connect(self._on_graph_window_closed)

                # Show the window.
                self.graph_window.show()

            else:
                self.logger.debug("Graph window already exists. Activating.")
                self.graph_window.show()
                self.graph_window.raise_()
                self.graph_window.activateWindow()
        except Exception as e:
            self.logger.error(f"Error showing graph window: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open the graph window:\n\n{str(e)}")


    def _on_graph_window_closed(self) -> None:
        """
        Handles the destruction of the graph window.
        Triggers a delayed refresh to restore the main widget's Z-order and visibility
        after the focus transition completes.
        """
        self.logger.debug("Graph window destroyed. Restoring main widget state.")
        self.graph_window = None
        
        # Explicitly force visibility first to prevent "disappeared" state
        # The subsequent refresh will handle obstruction logic, but we assume
        # the user wants to see the widget after closing the graph.
        self.show()
        self.raise_()
        self.activateWindow()

        # Use singleShot with a slightly longer delay to allow Windows focus settling
        QTimer.singleShot(300, self._execute_refresh)


    def get_config(self) -> Dict[str, Any]:
        return self.config.copy() if self.config else {}


    def get_widget_size(self) -> QSize:
        return self.size()


    def set_app_version(self, version: str) -> None:
        self.app_version = version
        self.logger.debug(f"Application version set to: {version}")


    def update_position(self) -> None:
        """
        The single, authoritative method to reposition the widget based on its current state.
        """
        self.logger.debug("Authoritative request to update widget position.")
        if self.position_manager:
            try:
                self.position_manager.update_position()
            except Exception as e:
                self.logger.error(f"Error during position update: {e}", exc_info=True)


    def is_startup_enabled(self, force_check: bool = False) -> bool:
        """
        Checks if the application is configured to start with Windows.
        By default, this uses the improved 'correctness' check.
        `force_check=True` can be used to do a simple existence check.
        """
        self.logger.debug("Checking system startup configuration...")
        try:
            return self._check_startup_registry(check_for_correctness=(not force_check))
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


    def _synchronize_startup_task(self) -> None:
        """
        Ensures the Windows startup task state matches the setting in the config file.
        This runs once on application startup to correct any mismatches.
        """
        try:
            # 1. Read the user's desired state from the config file.
            should_be_enabled = self.config.get("start_with_windows", True)
            
            # 2. Check the actual current state from the Windows Registry (using our improved function).
            is_currently_correct = self._check_startup_registry()

            # 3. If the user wants it enabled, but the key is incorrect or missing, fix it.
            if should_be_enabled and not is_currently_correct:
                self.logger.info(
                    "Startup task is enabled in config but missing or incorrect in registry. Creating it..."
                )
                self._set_startup_registry(enable=True)
            
            # 4. If the user wants it disabled, but a key exists (even an incorrect one), remove it.
            elif not should_be_enabled and self.is_startup_enabled(force_check=True):
                self.logger.info(
                    "Startup task is disabled in config but exists in registry. Removing it..."
                )
                self._set_startup_registry(enable=False)
                
            else:
                self.logger.debug("Startup task state is already synchronized with configuration.")
        except Exception as e:
            self.logger.error(f"Failed during startup task synchronization: {e}", exc_info=True)


    def update_retention_period(self, days: int) -> None:
        """
        Public method called by child windows (like GraphWindow) to update
        the data retention period and trigger the necessary backend logic.
        
        Args:
            days: The new retention period in days.
        """
        self.logger.info("Request received to update data retention period to %d days.", days)
        if not self.widget_state:
            self.logger.error("Cannot update retention period: WidgetState is not available.")
            return
        
        # 1. Update the in-memory config dictionary.
        self.config["keep_data"] = days
        
        # 2. Persist the change immediately to the config file.
        self.update_config(self.config)
        
        # 3. Notify the WidgetState, which will trigger the grace period logic.
        self.widget_state.update_retention_period()


    def _get_executable_path(self) -> str:
        """Gets the correct, quoted executable path or command for the registry."""        
        if getattr(sys, 'frozen', False):
            # Production: The executable path itself.
            return f'"{sys.executable}"'
        else:
            # Development: The python interpreter followed by the script path.
            python_executable = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            return f'"{python_executable}" "{script_path}"'


    def _check_startup_registry(self, check_for_correctness: bool = True) -> bool:
        if sys.platform != 'win32':
            return False
        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = constants.app.APP_NAME
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_READ)
            with key:
                stored_path, _ = winreg.QueryValueEx(key, app_name)
                if not check_for_correctness:
                    return True

                expected_path = self._get_executable_path()

                # When frozen, compare canonical file paths.
                # When running from source, compare the command strings directly.
                is_frozen = getattr(sys, 'frozen', False)
                paths_match = False
                try:
                    if is_frozen:
                        # For the .exe, compare the actual files on disk.
                        paths_match = os.path.samefile(stored_path.strip('"'), expected_path.strip('"'))
                    else:
                        # For dev mode, compare the command strings after normalizing.
                        paths_match = os.path.normcase(stored_path) == os.path.normcase(expected_path)
                except FileNotFoundError:
                    # Stored path points to a non-existent file.
                    paths_match = False

                if paths_match:
                    self.logger.debug("Correct startup entry found in registry.")
                    return True
                else:
                    self.logger.warning(
                        "Startup entry found but path is incorrect. Expected: %s, Found: %s.",
                        expected_path, stored_path
                    )
                    return False

        except FileNotFoundError:
            self.logger.debug(f"Startup entry '{app_name}' not found.")
            return False
        except Exception as e:
            self.logger.error(f"Error checking startup registry: {e}", exc_info=True)
            return False


    def _set_startup_registry(self, enable: bool) -> None:
        if sys.platform != 'win32':  # pragma: no cover
            raise NotImplementedError("Startup registry modification only implemented for Windows.")
        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = constants.app.APP_NAME
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_WRITE)
            with key:
                if enable:
                    executable_command = self._get_executable_path()
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, executable_command)
                    self.logger.info(f"Added startup entry '{app_name}' pointing to: {executable_command}")
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                        self.logger.info(f"Removed startup entry '{app_name}'.")
                    except FileNotFoundError:
                        self.logger.debug(f"Startup entry '{app_name}' not found, nothing to remove.")
        except Exception as e:
            raise RuntimeError(f"Failed to modify startup registry: {e}") from e


    def get_unified_interface_list(self) -> List[str]:
        """
        Returns a comprehensive, sorted list of network interfaces by combining
        currently active interfaces with all interfaces found in the history database.
        This serves as the single source of truth for all UI elements.
        """
        if not self.controller or not self.widget_state:
            self.logger.warning("Cannot get unified interface list: core components not initialized.")
            return []
        
        try:
            # Call the controller directly, as it is the true source of the live list.
            live_interfaces = set(self.controller.get_available_interfaces())
            
            # Get interfaces from the database history
            historical_interfaces = set(self.widget_state.get_distinct_interfaces())
            
            # Combine them, which automatically handles duplicates, then sort for a consistent UI.
            unified_list = sorted(list(live_interfaces.union(historical_interfaces)))
            
            self.logger.debug(f"Unified interface list created with {len(unified_list)} items.")
            return unified_list
        except Exception as e:
            self.logger.error(f"Error creating unified interface list: {e}", exc_info=True)
            return [] # Return an empty list on error
        

    def get_active_interfaces(self) -> List[str]:
        """
        Provides a passthrough to the controller's method for getting a list
        of currently active network interfaces.
        """
        if self.controller:
            return self.controller.get_active_interfaces()
        return []


    def cleanup(self) -> None:
        """Performs necessary cleanup and a single, final save of the configuration."""
        self.logger.info("Performing widget cleanup...")
        try:
            # --- Stop all external event listeners and timers ---
            if self.foreground_hook: self.foreground_hook.stop()
            if self.movesize_hook: self.movesize_hook.stop()
            if self._tray_watcher_timer.isActive(): self._tray_watcher_timer.stop()
            if self._state_watcher_timer.isActive(): self._state_watcher_timer.stop()

            # --- Clean up core components ---
            if self.timer_manager: self.timer_manager.cleanup()
            if self.controller: self.controller.cleanup()
            if self.widget_state: self.widget_state.cleanup()

            # (The rest of the cleanup method for saving config remains the same)
            if self.graph_window:
                final_graph_settings = {
                    "graph_window_pos": {"x": self.graph_window.pos().x(), "y": self.graph_window.pos().y()},
                    "dark_mode": self.graph_window._is_dark_mode,
                    "history_period_slider_value": self.graph_window._history_period_value,
                }
                self.update_config(final_graph_settings, save_to_disk=False)
                self.graph_window._is_closing = True
                self.graph_window.close()
                self.graph_window = None

            if self.config.get("free_move", False):
                pos = self.pos()
                self.update_config({"position_x": pos.x(), "position_y": pos.y()}, save_to_disk=False)
            else:
                self.update_config({"position_x": None, "position_y": None}, save_to_disk=False)
            
            self.logger.info("Performing final configuration save...")
            self.config_manager.save(self.config)

            self.logger.info("Widget cleanup finished successfully.")
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: %s", e, exc_info=True)