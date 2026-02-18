from __future__ import annotations

# --- Standard Library Imports ---
import logging
import math
import os
import sys
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
from netspeedtray.core.controller import NetworkController as CoreController
from netspeedtray.core.timer_manager import SpeedTimerManager
from netspeedtray.core.monitor_thread import NetworkMonitorThread
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.core.widget_state import WidgetState as CoreWidgetState
from netspeedtray.utils.config import ConfigManager as CoreConfigManager
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, is_taskbar_visible,
    is_small_taskbar, get_process_name_from_hwnd
)

from netspeedtray.utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.views.widget.theme import WidgetThemeManager
from netspeedtray.core.startup_manager import StartupManager
from netspeedtray.core.config_controller import ConfigController

# --- Type Checking ---
if TYPE_CHECKING:
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.graph import GraphWindow
    from netspeedtray.views.settings import SettingsDialog


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""

    MIN_UPDATE_INTERVAL = constants.config.defaults.MINIMUM_UPDATE_RATE


    def __init__(self, taskbar_height: int = constants.taskbar.taskbar.DEFAULT_HEIGHT, config: Optional[Dict[str, Any]] = None, i18n: Optional[constants.i18n.I18nStrings] = None, parent: QObject | None = None) -> None:
        """Initialize the NetworkSpeedTray with core components and UI setup."""
        super().__init__(parent)
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.{self.__class__.__name__}")
        self.logger.debug("Initializing NetworkSpeedWidget...")
        self.settings_dialog: Optional[SettingsDialog] = None

        # --- Core Application State ---
        self.session_start_time = datetime.now()
        self.config_manager = CoreConfigManager()
        
        # Initialize ConfigController
        # NOTE: We pass 'self' (the widget) to the controller. This requires careful handling
        # in the controller to avoid circular discrepancies, but allows it to orchestrate updates.
        self.config_controller = ConfigController(self, self.config_manager)
        
        self.config: Dict[str, Any] = config or self.config_controller.load_initial_config(taskbar_height)
        
        if i18n is None:
            raise ValueError("An i18n instance must be provided to NetworkSpeedWidget.")
        self.i18n = i18n
        
        # These MUST be initialized before _init_managers() because it checks self.current_metrics
        self.current_font: QFont = None
        self.current_metrics: QFontMetrics = None

        self._init_managers() # Initialize managers first
        self.theme_manager.apply_theme_aware_defaults()

        # --- Declare all instance attributes for clarity ---
        self.widget_state: CoreWidgetState
        self.timer_manager: SpeedTimerManager
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: PositionManager
        self.input_handler: InputHandler
        self.layout_manager: WidgetLayoutManager
        self.theme_manager: WidgetThemeManager
        self.tray_manager: TrayIconManager
        self.monitor_thread: NetworkMonitorThread
        self.graph_window: Optional[GraphWindow] = None
        self.app_icon: QIcon
        # Note: self.current_font and self.current_metrics are initialized earlier before _init_managers()
        
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        self.taskbar_height: int = taskbar_height
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()
        self.startup_manager: StartupManager
        self.is_paused: bool = False
        self._last_immediate_hide_time: float = 0.0 # For the race condition fix
        self._is_context_menu_visible: bool = False
        self.last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._taskbar_lost_count: int = 0
        self._will_quit_app: bool = False # Flag to distinguish hide vs exit
        
        # Hooks for system events
        self.system_event_handler: SystemEventHandler

        
        # Timers for periodic checks
        # self._tray_watcher_timer moved to PositionManager
        self._state_watcher_timer = QTimer(self) # The "Safety Net" timer
        
        self.setVisible(False)
        self.logger.debug("Widget initially hidden to stabilize position and size.")

        # --- Initialization Steps ---
        try:
            self.layout_manager.setup_window_properties()
            self._init_ui_components()
            self._init_core_components()
            
            # Now that all components are initialized, perform the initial resize.
            self.layout_manager.resize_widget_for_font()
            
            self._setup_connections()
            self._setup_timers()
            self.position_manager.update_position()
            self._synchronize_startup_task()
            
            QTimer.singleShot(0, self._delayed_initial_show)

            self.logger.debug("NetworkSpeedWidget initialized successfully.")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
            raise RuntimeError(f"Failed to initialize NetworkSpeedWidget: {e}") from e


    def _setup_timers(self) -> None:
        """Configures all application timers."""
        # PositionManager handles tray monitoring
        if hasattr(self, 'position_manager'):
            self.position_manager.start_monitoring()
        self.logger.debug("PositionManager monitoring started.")

        # This is the "Safety Net" timer. It runs to catch states missed by events.
        self._state_watcher_timer.setInterval(constants.timeouts.STATE_WATCHER_INTERVAL_MS)
        self._state_watcher_timer.timeout.connect(self._execute_refresh)
        self._state_watcher_timer.start()
        self.logger.debug(f"Safety net state watcher timer started ({constants.timeouts.STATE_WATCHER_INTERVAL_MS}ms).")


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
            
            # Background Monitoring Thread
            update_rate = self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)
            self.monitor_thread = NetworkMonitorThread(interval=update_rate)
            
            self.controller = CoreController(config=self.config, widget_state=self.widget_state)
            self.controller.set_view(self)
            self.renderer = CoreWidgetRenderer(self.config, self.i18n)
            
            # Note: We no longer start timer_manager for speeds; monitor_thread drives them.
            # self.timer_manager.start_timer() 
            self.logger.debug("Core components initialized; monitor thread ready.")
        except Exception as e:
            self.logger.error("Failed to initialize core components: %s", e, exc_info=True)
            raise RuntimeError("Failed to initialize core application components") from e


    def _init_managers(self) -> None:
        """Initialize all helper managers."""
        self.logger.debug("Initializing Managers...")
        
        # 1. Layout & Theme Managers
        self.layout_manager = WidgetLayoutManager(self)
        self.theme_manager = WidgetThemeManager(self)
        self.startup_manager = StartupManager()
        
        if not self.current_metrics:
            self.layout_manager.init_font()

        try:
            # Position Manager
            taskbar_info = get_taskbar_info()
            window_state = WindowState(
                config=self.config,
                widget=self,
                taskbar_info=taskbar_info,
                font_metrics=self.current_metrics
            )
            self.position_manager = PositionManager(window_state, parent=self)
            # Note: InputHandler is initialized in _init_ui_components() after tray_manager is created
            
            self.logger.debug("Managers initialized successfully.")

        except Exception as e:
            self.logger.critical(f"Failed to initialize managers: {e}", exc_info=True)
            raise RuntimeError("Manager initialization failed") from e











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

            # Allow user override to keep widget visible even when a fullscreen window is present
            keep_visible = self.config.get("keep_visible_fullscreen", False)
            should_be_visible = is_taskbar_visible(taskbar_info) and (keep_visible or not is_taskbar_obstructed(taskbar_info, hwnd))

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)
            
            # Only update position if we are supposed to be visible.
            if self.isVisible():
                if not self.config.get("free_move", False):
                    self.position_manager.update_position(fresh_taskbar_info=taskbar_info)
                
                # Always re-assert topmost status when visible to prevent falling behind taskbar (#77)
                self._ensure_win32_topmost()

        except Exception as e:
            self.logger.error(f"Critical error in _execute_refresh (failure count: {self._taskbar_lost_count}): {e}")
            # If we've had many consecutive failures, only then hide as a last resort.
            # Otherwise, keep it visible and hope for recovery on next tick.
            if self._taskbar_lost_count > 30 and self.isVisible():
                 self.logger.warning("Hiding widget as a last resort after sustained detection failure.")
                 self.setVisible(False)


    def _delayed_initial_show(self) -> None:
        """Triggers the initial authoritative visibility check."""
        self.logger.debug("Executing delayed initial show...")
        try:
            # Replace the call to the old manager with the proven, authoritative function.
            self._execute_refresh()
            
            if self.isVisible():
                self.logger.debug("Widget shown after stabilization")
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





    # _load_initial_config removed as it is now handled by ConfigController class


    def _on_theme_changed(self) -> None:
        """Delegates theme change handling."""
        self.theme_manager.on_theme_changed()





    def _init_ui_components(self) -> None:
        """Initialize UI-related elements: icon, tray, event handler."""
        self.logger.debug("Initializing UI components...")

        self.tray_manager = TrayIconManager(self, self.i18n)
        self.tray_manager.initialize()
        
        # Input Handler must be initialized here, after tray_manager and position_manager exist
        self.input_handler = InputHandler(
            widget=self,
            position_manager=self.position_manager,
            tray_manager=self.tray_manager
        )
        
        self.system_event_handler = SystemEventHandler(self)






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
            # self.timer_manager.stats_updated.connect(self.controller.update_speeds) # Deprecated
            self.monitor_thread.counters_ready.connect(self.controller.handle_network_counters)
            self.controller.display_speed_updated.connect(self.update_display_speeds)
            
            # Start the monitoring thread
            self.monitor_thread.start()

            # 1. System Event Handler (replaces manual WinEventHooks)
            self.system_event_handler.foreground_app_changed.connect(self._execute_refresh)
            self.system_event_handler.taskbar_changed.connect(self.update_position)
            self.system_event_handler.theme_changed.connect(self._on_theme_changed)
            
            # For immediate hide, we just connect to a lambda that hides self
            self.system_event_handler.immediate_hide_requested.connect(lambda: self.setVisible(False))
            
            # Handle taskbar restarts
            self.system_event_handler.taskbar_restarted.connect(lambda: [QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS, self._execute_refresh) for i in range(constants.timeouts.TASKBAR_RESTART_RETRIES)])
            
            self.system_event_handler.start()

            
            self.logger.debug("Signal connections and WinEventHooks established successfully.")
        except Exception as e:
            self.logger.error("Error setting up signal connections: %s", e, exc_info=True)
            raise RuntimeError("Failed to establish critical signal connections") from e

        
    def _validate_lazy_imports(self) -> None:
        """Validates lazy imports to catch potential issues early."""
        self.logger.debug("Validating lazy imports...")
        try:
            from netspeedtray.views.settings import SettingsDialog
            from netspeedtray.views.graph import GraphWindow
            self.logger.debug("Lazy imports validated successfully.")
        except ImportError as e:
            self.logger.error("Lazy import validation failed: %s", e, exc_info=True)



    def paintEvent(self, event: QPaintEvent) -> None:
        """
        Handles all painting for the widget by delegating to the renderer.
        """
        if not self.isVisible():
            return
        
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # painter.fillRect(self.rect(), QColor(0, 0, 0, 1)) # Removed hardcoded fill

            render_config = RenderConfig.from_dict(self.config)
            
            # Draw user-configurable background
            self.renderer.draw_background(painter, self.rect(), render_config)

            if not self.renderer or not self.current_metrics:
                self.logger.error("Renderer or metrics not initialized during paintEvent")
                self._draw_paint_error(painter, "Render Error")
                return
            
            # Detect layout mode and pass to renderer
            taskbar_info = get_taskbar_info()
            layout_mode = 'horizontal' if is_small_taskbar(taskbar_info) else 'vertical'
            
            # Draw Mini-Graph (Background Layer)
            # Must be drawn BEFORE text to prevent obscuring readability
            if render_config.graph_enabled:
                history = self.widget_state.get_aggregated_speed_history()
                self.renderer.draw_mini_graph(
                    painter=painter,
                    width=self.width(),
                    height=self.height(),
                    config=render_config,
                    history=history,
                    layout_mode=layout_mode
                )

            painter.setFont(self.current_font)

            upload_bytes_sec = (self.upload_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            download_bytes_sec = (self.download_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            
            self.renderer.draw_network_speeds(
                painter=painter,
                upload=upload_bytes_sec,
                download=download_bytes_sec,
                width=self.width(),
                height=self.height(),
                config=render_config,
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
            if self.current_font:
                p.setFont(self.current_font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

            if created_painter:
                p.end()

        except Exception as paint_err:
            self.logger.critical(f"CRITICAL: Failed to draw paint error indicator: {paint_err}", exc_info=True)






    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse press events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_press(event)


    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse move events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_move(event)


    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse release events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_release(event)


    def changeEvent(self, event: QEvent) -> None:
        """
        This event is handled for proper superclass behavior, but all custom
        logic is now managed by the debounced WinEventHooks to prevent blinking.
        """
        super().changeEvent(event)


    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Delegates double-click events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_double_click(event)


    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """
        Shows the context menu. This handler is the primary mechanism for
        keyboard-invoked context menus and a fallback for mouse events.
        """
        try:
            if self.tray_manager:
                self.tray_manager.show_context_menu()
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
        """
        Handles widget closure.
        By default, closing the widget just hides it (standard Tray behavior).
        The application only exits if _will_quit_app is set to True.
        """
        if self._will_quit_app:
            self.logger.info("Application exit requested. Cleaning up...")
            try:
                self.cleanup()
                event.accept()
                
                # Explicitly ensure the app quits loop
                app = QApplication.instance()
                if app:
                    app.quit()
                    
            except Exception as e:
                self.logger.error(f"Error during shutdown cleanup: {e}", exc_info=True)
                event.accept()
        else:
            self.logger.debug("Close event received but not quitting app. Hiding widget.")
            self.setVisible(False)
            event.ignore() # Prevent destruction of the widget

    def fully_exit_application(self) -> None:
        """Helper to cleanly exit the entire application."""
        self.logger.info("Fully exiting application...")
        self._will_quit_app = True
        self.close()






 

    @property
    def position_manager_property(self):
        # Expose position manager for binding if needed, though self.position_manager exists
        return self.position_manager

    def _ensure_win32_topmost(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.ensure_topmost()

    def _enforce_topmost_status(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.enforce_topmost_status()

    def reset_to_default_position(self) -> None:
        """
        Resets the widget to its default position using PositionManager.
        """
        self.logger.info("Resetting widget position to default.")
        self.position_manager.reset_to_default()
        
        # Save the cleared config state
        self.update_config({'position_x': None, 'position_y': None})


    def apply_all_settings(self) -> None:
        """Delegates to ConfigController."""
        self.config_controller.apply_all_settings()


    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.handle_settings_changed(updated_config, save_to_disk)


    def show_settings(self) -> None:
        """Creates and displays the settings dialog as a normal, non-modal window."""
        self.logger.debug("Showing settings dialog...")
        try:
            from netspeedtray.views.settings import SettingsDialog

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
                # Connect signal for live preview updates (don't save to disk during preview)
                self.settings_dialog.settings_changed.connect(
                    lambda cfg: self.handle_settings_changed(cfg, save_to_disk=False)
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



    def _rollback_config(self, old_config: Dict[str, Any]) -> None:
        """Delegates to ConfigController."""
        self.config_controller.rollback_config(old_config)


    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.update_config(updates, save_to_disk)


    def handle_graph_settings_update(self, updates: Dict[str, Any]) -> None:
        """
        Public method called by the GraphWindow to update and save configuration.
        This centralizes the saving logic and prevents race conditions.
        """
        self.logger.debug(f"Received settings update from graph window: {updates}")
        # The update_config method already updates the in-memory config and saves to disk.
        # We can just call it directly.
        self.update_config(updates)





    def open_graph_window(self) -> None:
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
            from netspeedtray.views.graph import GraphWindow

            if self.graph_window is None or not self.graph_window.isVisible():
                self.logger.debug("Creating new GraphWindow instance.")
                
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
        QTimer.singleShot(constants.timeouts.GRAPH_CLOSE_REFRESH_DELAY_MS, self._execute_refresh)


    # update_config (redundant definition) removed


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
        """Checks if startup is enabled via StartupManager."""
        return self.startup_manager.is_startup_enabled(force_check)


    def toggle_startup(self, enable: bool) -> None:
        """Toggles startup via StartupManager."""
        try:
            self.startup_manager.toggle_startup(enable)
            self.config['start_with_windows'] = enable
            self.update_config({'start_with_windows': enable})
            self.logger.info(f"Application startup successfully {'enabled' if enable else 'disabled'}.")
        except Exception as e:
            self.logger.error(f"Failed to {'enable' if enable else 'disable'} startup: {e}", exc_info=True)
            QMessageBox.warning(
                self,
                "Startup Error",
                f"Could not {'enable' if enable else 'disable'} automatic startup.\n\n{e}"
            )


    def _synchronize_startup_task(self) -> None:
        """Synchronizes startup state using StartupManager."""
        should_be_enabled = self.config.get("start_with_windows", constants.config.defaults.DEFAULT_START_WITH_WINDOWS)
        self.startup_manager.synchronize_startup_task(should_be_enabled)


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
        self.logger.debug("Performing widget cleanup...")
        try:
            # --- Stop all external event listeners and timers ---
            # self.foreground_hook and movesize_hook are likely legacy, but keeping check is harmless
            if hasattr(self, 'system_event_handler') and self.system_event_handler:
                self.system_event_handler.stop()
            elif hasattr(self, 'foreground_hook') and self.foreground_hook: 
                self.foreground_hook.stop()
            
            # Stop PositionManager monitoring
            if self.position_manager:
                self.position_manager.stop_monitoring()
            
            if self._state_watcher_timer.isActive(): self._state_watcher_timer.stop()
            
            # --- Stop the background monitor thread ---
            if hasattr(self, 'monitor_thread') and self.monitor_thread:
                self.logger.debug("Stopping NetworkMonitorThread...")
                self.monitor_thread.stop()

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
            
            self.logger.debug("Performing final configuration save...")
            self.config_manager.save(self.config)

            self.logger.debug("Widget cleanup finished successfully.")
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: %s", e, exc_info=True)