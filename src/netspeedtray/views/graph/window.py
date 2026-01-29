"""
Graph module for NetSpeedTray.

This module defines the `GraphWindow` class, which provides a graphical interface for visualizing
network speed history and per-app bandwidth usage using Matplotlib and PyQt6.
"""

# --- Standard Library Imports ---
import logging
import os
import time
import warnings
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

# Suppress Matplotlib AutoDateLocator interval warnings globally
# This warning is harmless in our context and clutters the terminal.
warnings.filterwarnings("ignore", "AutoDateLocator was unable to pick an appropriate interval")

# --- Third-Party Imports (Centralized) ---
# NOTE: matplotlib backend is set in monitor.py entry point to ensure it's set before ANY matplotlib import
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from matplotlib.ticker import ScalarFormatter, FixedLocator
import numpy as np


# --- PyQt6 Imports ---
from PyQt6.QtCore import Qt, QPoint, QThread, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui import QResizeEvent, QCloseEvent, QIcon, QShowEvent
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QTabWidget, QVBoxLayout, QWidget
)

# --- Custom Application Imports ---
from netspeedtray import constants
from netspeedtray.constants import styles as style_constants
from netspeedtray.utils import helpers
from netspeedtray.utils.components import Win11Slider, Win11Toggle
from netspeedtray.core.position_manager import ScreenUtils
from netspeedtray.utils import styles
from netspeedtray.utils import styles as style_utils



from netspeedtray.views.graph.controls import GraphSettingsPanel
from netspeedtray.views.graph.interaction import GraphInteractionHandler
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.graph.worker import GraphDataWorker

class GraphWindow(QWidget):
    """
    A window for displaying network speed history and per-app bandwidth usage using PyQt6.
    """
    # Signal for background thread processing
    # Args: (start_time, end_time, interface_name, is_session_view, sequence_id)
    request_data_processing = pyqtSignal(object, object, str, bool, int)
    window_closed = pyqtSignal()

    def __init__(self, main_widget, parent=None, logger=None, i18n=None, session_start_time: Optional[datetime] = None):
        """ Initialize the GraphWindow with its UI components. """
        super().__init__(parent)
        
        # Ensure the window is destroyed on close so 'destroyed' signal fires
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            
        self._main_widget = main_widget
        self.logger = logger or logging.getLogger(__name__)
        self.i18n = i18n
        self.session_start_time = session_start_time or datetime.now()
        
        # --- State variables ---
        self._is_closing = False
        self._initial_load_done = False
        self._is_dark_mode = self._main_widget.config.get("dark_mode", True)
        self._is_live_update_enabled = self._main_widget.config.get("live_update", True)
        self._history_period_value = self._main_widget.config.get('history_period_slider_value', 0)
        self._data_cache: Dict[Tuple[str, str], List[Tuple[datetime, float, float]]] = {}
        self.interface_filter = None # Initialized when settings panel is created

        # --- Setup UI and Timers ---
        self.setupUi()
        self.setWindowTitle(constants.graph.WINDOW_TITLE)
        try:
            icon_path = helpers.get_app_asset_path(constants.app.ICON_FILENAME)
            if icon_path.exists(): self.setWindowIcon(QIcon(str(icon_path)))
        except Exception as e: self.logger.error(f"Error setting window icon: {e}", exc_info=True)

        # --- Initialize Core Components in Order ---
        # Initialize overlay elements (stats bar, hamburger menu)
        self._init_overlay_elements()

        # Add stats bar to the top of the layout
        self.graph_layout.addWidget(self.stats_bar)
        
        # Initialize Renderer (Matplotlib)
        # It needs the container to embed the canvas.
        self.renderer = GraphRenderer(self.graph_widget, self.i18n, self.logger)
        
        # Apply theme immediately after renderer init to ensure correct colors on first draw
        self.renderer.apply_theme(self._is_dark_mode)
        
        # Expose renderer components to window for event connection and logic
        self.canvas = self.renderer.canvas
        self.figure = self.renderer.figure
        self.axes = self.renderer.axes
        self.ax_download = self.renderer.ax_download
        self.ax_upload = self.renderer.ax_upload

        
        # Initialize Interaction Handler
        # Pass self (window) so it can access renderer via window.renderer
        self.interaction = GraphInteractionHandler(self)

        # The stats bar and hamburger menu were initialized and added to the layout 
        # at the start of __init__ to ensure they appear on top.
        # Canvas is added by GraphRenderer to graph_widget's layout automatically.

        # Now that the UI is fully built, initialize the background worker
        self._init_worker_thread(self._main_widget.widget_state)
        
        # Finally, connect all signals
        self._connect_signals()


    def setupUi(self, parent=None):
        """Constructs the main layout and widgets for the GraphWindow (manual, not Qt Designer)."""
        # Main vertical layout
        self.main_layout = QVBoxLayout(self)

        # Tab widget (App Usage feature temporarily disabled)
        self.tab_widget = QTabWidget(self)
        self.main_layout.addWidget(self.tab_widget)

        # Graph tab
        self.graph_widget = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_widget)
        # Note: Layout is automatically set by passing parent to constructor - do NOT call setLayout again
        self.tab_widget.addTab(self.graph_widget, self.i18n.SPEED_GRAPH_TAB_LABEL)
        
        # Hide the tab bar as it's not needed for a single tab
        self.tab_widget.tabBar().setVisible(False)

        # Connect tab change signal
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        # Set minimum size for usability
        self.setMinimumSize(constants.graph.GRAPH_WIDGET_WIDTH, constants.graph.GRAPH_WIDGET_HEIGHT)

        # Placeholders for overlay elements (created in _init_overlay_elements)
        self.stats_bar = None
        self.hamburger_icon = None

        # Placeholders for other dynamic widgets
        self._no_app_data_label = None
        self._app_entry_widgets = {}
        self._app_entry_labels_progress = {}

        # Placeholders for matplotlib canvas, set in _init_matplotlib
        self.figure = None
        self.canvas = None
        self.ax = None

        # Placeholders for settings panel, set in _init_settings_panel
        self.settings_widget = None

        # Timers and state
        self._realtime_timer = QTimer(self)
        self._db_size_update_timer = QTimer(self)
        self._config_debounce_timer = QTimer(self)
        self._config_debounce_timer.setSingleShot(True)
        self._config_debounce_timer.setInterval(500) # 500ms debounce
        self._config_debounce_timer.timeout.connect(self._process_pending_config_save)
        self._pending_config = {}

        # Data Update Debounce (Protects DB and UI from slider hammering)
        self._data_update_debounce_timer = QTimer(self)
        self._data_update_debounce_timer.setSingleShot(True)
        self._data_update_debounce_timer.setInterval(150) # 150ms debounce
        self._data_update_debounce_timer.timeout.connect(self._execute_debounced_data_update)
        self._pending_data_update = False
        
        # Performance & Sync State
        self._current_request_id = 0
        self._last_processed_id = -1
        self._cached_boot_time = None
        self._cached_earliest_db = None
        self._last_cache_refresh = 0
        
        self._no_data_text_obj = None
        self._current_date_formatter_type = None

        # Call window positioning after UI is set up
        self._position_window()





    def _init_overlay_elements(self):
        """ Initialize stats bar and hamburger menu """
        try:
            # Stats Bar
            self.stats_bar = QLabel(self)
            initial_stats_text = getattr(constants.graph, 'INITIAL_STATS_TEXT', "")
            self.stats_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stats_bar.setText(initial_stats_text)
            self.stats_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.stats_bar.show()

            # Hamburger Menu
            hamburger_size = getattr(constants.graph, 'HAMBURGER_ICON_SIZE', 24)
            self.hamburger_icon = QPushButton(self.graph_widget)
            self.hamburger_icon.setFixedSize(hamburger_size, hamburger_size)
            self.hamburger_icon.setCursor(Qt.CursorShape.PointingHandCursor)
            self.hamburger_icon.setText("☰")
            font = self.hamburger_icon.font()
            font.setPointSize(14)
            self.hamburger_icon.setFont(font)
            
            # Apply centralized overlay style using the correct alias
            self.hamburger_icon.setStyleSheet(style_utils.graph_overlay_style())

            self.hamburger_icon.show()
            self.hamburger_icon.clicked.connect(self._toggle_settings_panel_visibility)
            
            self.stats_bar.raise_()
            
        except Exception as e:
            self.logger.error(f"Error initializing overlay elements: {e}", exc_info=True)





    def _toggle_settings_panel_visibility(self):
        """
        Shows or hides the settings panel, ensuring it is perfectly aligned with the stats bar.
        """
        try:
            if self.settings_widget is None:
                self.logger.debug("First use: Creating settings panel.")
                initial_state = {
                    'history_period_value': self._history_period_value,
                    'is_dark_mode': self._is_dark_mode,
                    'is_live_update_enabled': self._is_live_update_enabled,
                    'retention_days': self._main_widget.config.get("keep_data", 30) if self._main_widget else 30,
                    'show_legend': self._main_widget.config.get("show_legend", True) if self._main_widget else True
                }
                self.settings_widget = GraphSettingsPanel(self, i18n=self.i18n, initial_state=initial_state)
                self.interface_filter = self.settings_widget.interface_filter
                self.settings_widget.hide()
                QTimer.singleShot(0, self._populate_settings_panel)

            if not hasattr(self, '_original_window_size'):
                self._original_window_size = self.geometry().size()
                self._original_graph_size = self.graph_widget.size()

            if self.settings_widget.isVisible():
                self.settings_widget.hide()
                self.resize(self._original_window_size)
                self.graph_widget.setFixedSize(self._original_graph_size)
                self._reposition_overlay_elements()
                self.logger.debug("Settings panel hidden.")
                return

            self.graph_widget.setFixedSize(self._original_graph_size)

            hamburger_right_global = self.hamburger_icon.mapToGlobal(QPoint(self.hamburger_icon.width(), 0))
            hamburger_right_window = self.mapFromGlobal(hamburger_right_global)
            panel_x = hamburger_right_window.x() + 8

            # Use coordinate mapping for robust vertical alignment
            stats_bar_top_left_global = self.stats_bar.mapToGlobal(QPoint(0, 0))
            stats_bar_top_left_window = self.mapFromGlobal(stats_bar_top_left_global)
            panel_y = stats_bar_top_left_window.y()

            panel_width = 300
            panel_height = self.graph_widget.height() - panel_y

            self.settings_widget.setFixedSize(panel_width, panel_height)

            window_geometry = self.geometry()
            required_width = panel_x + panel_width + 16
            if window_geometry.width() < required_width:
                self.resize(required_width, window_geometry.height())

            self.settings_widget.move(panel_x, panel_y)
            self.settings_widget.raise_()
            self.settings_widget.show()
            self.logger.debug(f"Settings panel shown at ({panel_x}, {panel_y}). Population deferred.")
        except Exception as e:
            self.logger.error(f"Error in _toggle_settings_panel_visibility: {e}", exc_info=True)


    def _populate_settings_panel(self):
        """
        Finishes layout and connection of the settings panel.
        """
        if not self.settings_widget: return
            
        self.logger.debug("Populating settings panel widgets.")
        try:
            self._connect_settings_signals()
            
            # Populate interfaces directly via the widget method
            if self._main_widget:
                interfaces = self._main_widget.get_unified_interface_list()
                self.settings_widget.populate_interfaces(interfaces)
            
            # Ensure the populated widget is visible if the panel is still open
            if self.settings_widget.isVisible():
                self.settings_widget.show()
        except Exception as e:
            self.logger.error(f"Error populating settings panel: {e}", exc_info=True)


    def _populate_interface_filter(self):
        """
        Called when the database is updated to refresh the interface list in the filter.
        """
        self._populate_settings_panel()




    def _init_worker_thread(self, widget_state):
        """Initializes the background worker thread for data processing."""
        self.worker_thread = QThread()
        self.data_worker = GraphDataWorker(widget_state)
        self.data_worker.moveToThread(self.worker_thread)
        
        self.data_worker.data_ready.connect(self._on_data_ready)
        self.data_worker.error.connect(self._show_graph_error)
        
        self.request_data_processing.connect(self.data_worker.process_data)
        
        # NOTE: We do NOT connect database_updated here because it causes an infinite recursion
        # (update_graph flushes the batch, which triggers database_updated, which triggers update_graph).
        # We rely on periodic timers and manual refreshes for UI updates.
            
        self.worker_thread.start()









            



            



    def _on_interface_filter_changed(self, interface_name: str) -> None:
        """
        Slot that is triggered when the user selects a new interface from the filter.
        """
        if self._is_closing:
            return
            
        self.logger.info("User selected interface: %s", interface_name)
        
        # Debounce the update call to prevent rapid switching flickers
        self._pending_data_update_show_loading = True
        self._data_update_debounce_timer.start()


    def _on_retention_changed(self, days: int = None) -> None:
        """
        Slot triggered when the Data Retention slider is released.
        Arguments:
            days: The number of days selected (passed from signal).
        """
        if self._is_closing or not self._main_widget:
            return

        try:
            # If called manually without args (shouldn't happen with new signals, but safety)
            if days is None: 
                 if not hasattr(self.settings_widget, 'keep_data_slider'): return
                 slider_value = self.settings_widget.keep_data_slider.value()
                 days = constants.data.retention.DAYS_MAP.get(slider_value, 30)

            self.logger.info("Data Retention changed. New period: %d days.", days)

            # Call the new method on the parent to trigger the grace period logic
            if hasattr(self._main_widget, 'update_retention_period'):
                self._main_widget.update_retention_period(days)
            else:
                self.logger.warning("Parent widget does not have 'update_retention_period' method.")
                
        except Exception as e:
            self.logger.error("Error in _on_retention_changed: %s", e, exc_info=True)


    def _get_time_range_from_ui(self) -> Tuple[Optional[datetime], datetime]:
        """
        Determines the start and end time for a query based on the
        current state of the 'Timeline' slider.
        """
        import psutil

        now = datetime.now()
        period_value = self._history_period_value
        period_key = constants.data.history_period.PERIOD_MAP.get(period_value, constants.data.history_period.DEFAULT_PERIOD)
        
        # Cache boot_time and earliest_db to avoid expensive calls on every update
        if period_key == "TIMELINE_SYSTEM_UPTIME":
            import time as pytime
            curr_time = pytime.time()
            if not self._cached_boot_time or (curr_time - self._last_cache_refresh) > 60:
                self.logger.debug("Refreshing boot time and earliest DB timestamp cache.")
                self._cached_boot_time = datetime.fromtimestamp(psutil.boot_time())
                self._cached_earliest_db = self._main_widget.widget_state.get_earliest_data_timestamp()
                self._last_cache_refresh = curr_time

        start_time = constants.data.history_period.get_start_time(
            period_key, 
            now, 
            session_start=self.session_start_time,
            boot_time=self._cached_boot_time,
            earliest_db=self._cached_earliest_db
        )

        return start_time, now


    def _perform_initial_update(self):
        """
        Performs the initial graph update by triggering the standard
        history period update logic, which respects the initial slider state.
        """
        try:
            # The history_period widget may not exist, so use the state variable
            self.update_history_period(self._history_period_value, initial_setup=True)

            self._reposition_overlay_elements()
            if hasattr(self, 'canvas'):
                self.canvas.draw()
        except Exception as e:
            self.logger.error(f"Error in initial update: {e}", exc_info=True)


    def _position_window(self) -> None:
        """Position the window centered on the primary screen or to saved position."""
        try:
            pos_x, pos_y = -1, -1
            if self._main_widget and "graph_window_pos" in self._main_widget.config:
                saved_pos = self._main_widget.config["graph_window_pos"]
                if isinstance(saved_pos, dict) and "x" in saved_pos and "y" in saved_pos:
                    pos_x, pos_y = saved_pos["x"], saved_pos["y"]

            screen = QApplication.primaryScreen()
            if not screen:
                self.logger.warning("No primary screen found for positioning.")
                self.move(100,100) # Fallback
                return
            
            screen_geometry = screen.geometry()
            window_width = self.width() 
            window_height = self.height()

            if pos_x != -1 and pos_y != -1: # Found saved position
                # Validate saved position to ensure it's (mostly) on a screen
                validated_pos = ScreenUtils.validate_position(pos_x, pos_y, (window_width, window_height), screen)
                final_x, final_y = validated_pos.x, validated_pos.y
            else: # Center on primary screen
                final_x = (screen_geometry.width() - window_width) // 2 + screen_geometry.x()
                final_y = (screen_geometry.height() - window_height) // 2 + screen_geometry.y()
            
            self.move(final_x, final_y)
            self.logger.debug(f"GraphWindow positioned at ({final_x}, {final_y})")
        except Exception as e:
            self.logger.error(f"Error positioning window: {e}", exc_info=True)


    def _on_tab_changed(self, index: int) -> None:
        """Handle tab changes to update UI elements and data."""
        if self._is_closing: return
        try:
            if index == 0:  # Speed Graph
                self.update_graph()

            # The tab widget no longer has other tabs, but this ensures
            # the overlays are correctly positioned if tabs are added later.
            QTimer.singleShot(0, self._reposition_overlay_elements)

            self.logger.debug(f"Switched to tab {index}")
        except Exception as e:
            self.logger.error(f"Error handling tab change: {e}", exc_info=True)


    def _show_graph_message(self, message: str, is_error: bool = True) -> None:
        """
        Displays a message overlayed on the Matplotlib graph area, clearing both axes.
        """
        if is_error:
            self.logger.error(f"Displaying graph error: {message}")
        try:
            if not hasattr(self, 'axes') or not hasattr(self, 'canvas'):
                return

            is_dark = self._is_dark_mode
            facecolor = constants.styles.GRAPH_BG_DARK if is_dark else constants.styles.GRAPH_BG_LIGHT
            text_color = constants.color.RED if is_error else (constants.styles.DARK_MODE_TEXT_COLOR if is_dark else constants.styles.LIGHT_MODE_TEXT_COLOR)

            # Clear any previously plotted data from both axes
            for ax in self.axes:
                ax.clear()
                ax.set_xticks([])
                ax.set_yticks([])
                ax.set_xlabel("")
                ax.set_ylabel("")
                for spine in ax.spines.values():
                    spine.set_visible(False)
                ax.set_facecolor(facecolor)
                ax.grid(False) # Turn off grid for message display

            # Place the text message in the center of the top (download) plot
            self.ax_download.text(0.5, 0.5, message,
                                ha='center', va='center', transform=self.ax_download.transAxes,
                                color=text_color, fontsize=constants.graph.ERROR_MESSAGE_FONTSIZE)

            self.figure.patch.set_facecolor(facecolor)
            self.canvas.draw_idle()
        except Exception as e_draw:
            self.logger.error(f"Failed to display graph message on canvas: {e_draw}", exc_info=True)


    def _show_graph_error(self, message: str) -> None:
        """ A convenience wrapper to display an error message on the graph. """
        self._show_graph_message(message, is_error=True)

            
    def toggle_settings(self) -> None:
        """Toggle the settings dialog."""
        if self._is_closing or not self._main_widget or not hasattr(self._main_widget, "config_manager"): 
            return
            
        try:
            from netspeedtray.views.settings import SettingsDialog
            settings_dialog = SettingsDialog(
                parent=self._main_widget,  # Keep parent reference for config access
                config=self._main_widget.config.copy(),
                version=self._main_widget.version,
                i18n=self.i18n,
                available_interfaces=self._main_widget.get_available_interfaces() if hasattr(self._main_widget, "get_available_interfaces") else None,
                is_startup_enabled=self._main_widget.startup_task_exists() if hasattr(self._main_widget, "startup_task_exists") else False
            )
            
            # Center the dialog relative to this window
            if settings_dialog.windowHandle():
                settings_dialog.windowHandle().setTransientParent(self.windowHandle())
            
            # Show the dialog modally
            if settings_dialog.exec() == QDialog.DialogCode.Accepted:
                # Settings were saved, apply them
                if hasattr(self._main_widget, 'apply_all_settings'):
                    self._main_widget.apply_all_settings() 
                    # Update the graph and UI elements with new settings
                    self._reposition_overlay_elements()
                    
                    # Call update_graph without unnecessary parameters
                    self.update_graph()
                else:
                    self.logger.warning("Parent widget lacks 'apply_all_settings' method.")

            self.logger.debug("Settings dialog complete")

        except Exception as e:
            self.logger.error(f"Error toggling settings: {e}", exc_info=True)


    def toggle_dark_mode(self, checked: bool) -> None:
        """ Applies dark or light mode theme to the GraphWindow and its components. """
        if self._is_closing: return
        try:
            self._is_dark_mode = checked
            self.logger.debug(f"Applying graph dark mode theme: {checked}")

            # Sync toggle state if changed externally
            if self.settings_widget and hasattr(self.settings_widget, 'dark_mode_toggle'):
                toggle = self.settings_widget.dark_mode_toggle
                if toggle.isChecked() != checked:
                    toggle.blockSignals(True)
                    toggle.setChecked(checked)
                    toggle.blockSignals(False)

            if self._main_widget and hasattr(self._main_widget, "config_manager"):
                self._main_widget.config["dark_mode"] = checked
                self._main_widget.config_manager.save(self._main_widget.config)

            # Delegate to Renderer
            if hasattr(self, 'renderer'):
                self.renderer.apply_theme(checked)

            # Update CSS-based overlay elements
            self.stats_bar.setStyleSheet(style_utils.graph_overlay_style())
            self.stats_bar.raise_()
            self.hamburger_icon.raise_()
            self._reposition_overlay_elements()

        except Exception as e:
            self.logger.error(f"Error in toggle_dark_mode: {e}", exc_info=True)


    def _connect_signals(self):
        """Connect all relevant signals for UI interactivity."""
        self._realtime_timer.timeout.connect(self._update_realtime)
        # NOTE: Canvas mouse/legend events are connected by GraphInteractionHandler._connect_mpl_events()


    def _connect_settings_signals(self):
        """Connect signals for the settings panel after it has been created."""
        if not self.settings_widget: return
        
        self.settings_widget.dark_mode_toggled.connect(self.toggle_dark_mode)
        self.settings_widget.live_update_toggled.connect(self.toggle_live_update)
        self.settings_widget.interface_filter_changed.connect(self._on_interface_filter_changed)
        
        # History connects
        self.settings_widget.history_period_changed.connect(self._on_history_slider_released)
        self.settings_widget.history_period_changing.connect(self._update_history_period_text)
        
        # Retention connects
        self.settings_widget.retention_changed.connect(self._on_retention_changed)
        self.settings_widget.retention_changing.connect(self._update_keep_data_text)
        
        self.settings_widget.show_legend_toggled.connect(self._on_legend_toggled)
        
        # Initialize text via the changing signal handlers (manual call)
        if hasattr(self.settings_widget, 'history_period_slider'):
             val = self.settings_widget.history_period_slider.value()
             self._update_history_period_text(val)
        
        if hasattr(self.settings_widget, 'keep_data_slider'):
             val_ret = self.settings_widget.keep_data_slider.value()
             self._update_keep_data_text(val_ret)


    def _pause_realtime_updates(self) -> None:
        """Stops the real-time timer to prevent UI lag during interactions."""
        if self._realtime_timer.isActive():
            self.logger.debug("Pausing real-time graph updates for UI interaction.")
            self._realtime_timer.stop()


    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """
        Intercepts events from watched objects. We use this to detect when the
        interface dropdown's popup is shown or hidden, to pause background updates.
        """
        # We only care about events from the combo box's popup widget.
        if watched == self.interface_filter.view().parent():
            if event.type() == QEvent.Type.Show:
                # The popup is about to be shown, so pause the timer.
                self._pause_realtime_updates()
            elif event.type() == QEvent.Type.Hide:
                # The popup has been hidden, so resume the timer.
                self._resume_realtime_updates()
        
        # Pass the event on to the parent class for default processing.
        return super().eventFilter(watched, event)


    def _resume_realtime_updates(self) -> None:
        """Resumes the real-time timer after a UI interaction is complete."""
        # Only restart the timer if the user still has "Live Update" enabled.
        if self.realtime.isChecked() and not self._realtime_timer.isActive():
            self.logger.debug("Resuming real-time graph updates.")
            self._realtime_timer.start(constants.graph.REALTIME_UPDATE_INTERVAL_MS)


    def _on_history_slider_released(self, value: int = None) -> None:
        """
        Handles the timeline slider release event.
        Args:
            value: The slider value (index) passed from the signal.
        """
        if self.logger.isEnabledFor(logging.DEBUG):
            start = time.perf_counter()
            self.logger.debug(f"[PERF-{start}] _on_history_slider_released START")
        
        # Get the current value from the argument or fallback to widget
        if value is None:
             if hasattr(self.settings_widget, 'history_period_slider'):
                 value = self.settings_widget.history_period_slider.value()
             else:
                 return

        current_value = value
        self._history_period_value = current_value # UPDATE STATE VARIABLE

        # Determine if we should show the loading message (suppress for Session)
        period_key = constants.data.history_period.PERIOD_MAP.get(current_value, "")
        show_loading = (period_key != "TIMELINE_SESSION")

        # Instead of updating immediately, we start the debounce timer.
        # This prevents "freezing" when someone is rapidly switching timelines.
        self._pending_data_update_show_loading = show_loading
        self._data_update_debounce_timer.start()
        
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f"[PERF-{start}] _on_history_slider_released QUEUED (150ms debounce)")


    def _notify_parent_of_setting_change(self, settings_dict: dict) -> None:
        """Helper method to pass a dictionary of configuration updates to the parent widget."""
        if self._main_widget and hasattr(self._main_widget, 'handle_graph_settings_update'):
            self.logger.debug(f"Notifying parent of setting change: {settings_dict}")
            self._main_widget.handle_graph_settings_update(settings_dict)
        else:
            self.logger.warning(f"Cannot notify parent of setting change: Method not found.")

    def _execute_debounced_data_update(self) -> None:
        """Actually triggers the graph and config update after the debounce period."""
        try:
            val = self._history_period_value
            show_loading = getattr(self, '_pending_data_update_show_loading', True)
            
            self.logger.debug(f"Executing debounced data update for period index {val}")
            
            # 1. Trigger Graph Update
            self.update_history_period(val, show_loading=show_loading)
            
            # 2. Queue Config Save
            self._pending_config.update({'history_period_slider_value': val})
            self._config_debounce_timer.start()
            
        except Exception as e:
            self.logger.error(f"Error in debounced data update: {e}", exc_info=True)


    def _process_pending_config_save(self) -> None:
        """Slot for debounce timer to save accumulated config changes."""
        if not self._pending_config: return
        try:
            self.logger.debug(f"Executing debounced config save: {self._pending_config}")
            self._notify_parent_of_setting_change(self._pending_config)
            self._pending_config = {}
        except Exception as e:
            self.logger.error(f"Error in debounced config save: {e}", exc_info=True)


    def _save_slider_value_to_config(self, config_key: str, value: int) -> None:
        """Helper method to save a slider's integer value to the configuration."""
        if self._main_widget and hasattr(self._main_widget, 'config') and hasattr(self._main_widget, 'config_manager'):
            self.logger.debug(f"Saving slider value: {config_key} = {value}")
            self._main_widget.config[config_key] = value
            try:
                self._main_widget.config_manager.save(self._main_widget.config)
            except Exception as e:
                self.logger.error(f"Failed to save config after slider value change for {config_key}: {e}")
        else:
            self.logger.warning(f"Cannot save slider value for {config_key}: parent or config components missing.")


    def showEvent(self, event: QShowEvent) -> None:
        """
        Override the show event to trigger the initial data load after the window
        is visible, preventing UI blocking on startup.
        """
        super().showEvent(event)

        if self._is_live_update_enabled and not self._realtime_timer.isActive():
            self.logger.debug("Window shown with live update enabled. Starting timer.")
            self._realtime_timer.start(constants.graph.REALTIME_UPDATE_INTERVAL_MS)

        if not self._initial_load_done:
            self._initial_load_done = True
            # Display a "collecting data" message immediately for better UX
            self._show_graph_message(
                getattr(self.i18n, 'COLLECTING_DATA_MESSAGE', "Collecting data..."),
                is_error=False
            )
            # Schedule the actual data fetch and graph update.
            # A 50ms delay gives the UI time to fully paint itself.
            QTimer.singleShot(50, lambda: self.update_graph(show_loading=True))
            self.logger.debug("Scheduled initial graph update after window became visible.")


    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        """ Handles window resize events. Repositions overlay elements. """
        if event: super().resizeEvent(event)
        if self._is_closing or not hasattr(self, 'tab_widget'): return # Not fully initialized

        try:
            QTimer.singleShot(0, self._reposition_overlay_elements)

        except Exception as e:
            self.logger.error(f"Error in resizeEvent: {e}", exc_info=True)


    def _reposition_overlay_elements(self) -> None:
        """Reposition the hamburger icon to be vertically centered on the stats bar."""
        if self._is_closing or not all(hasattr(self, attr) for attr in ['tab_widget', 'hamburger_icon', 'stats_bar']):
            return

        try:
            if self.tab_widget.currentWidget() == self.graph_widget:
                hamburger_x = self.graph_widget.width() - self.hamburger_icon.width() - constants.graph.HAMBURGER_ICON_OFFSET_X
                
                # This logic is now correct due to the shared parentage
                stats_bar_center_y = self.stats_bar.y() + self.stats_bar.height() // 2
                hamburger_y = stats_bar_center_y - self.hamburger_icon.height() // 2
                
                self.hamburger_icon.move(hamburger_x, hamburger_y)
                self.hamburger_icon.raise_()
        except Exception as e:
            self.logger.error(f"Error repositioning overlay elements: {e}", exc_info=True)


    def toggle_live_update(self, checked: bool) -> None:
        """ Starts or stops the real-time update timer based on the toggle. """
        if self._is_closing: return
        self._is_live_update_enabled = checked
        try:
            if self._is_live_update_enabled:
                # Set the interval and start the timer.
                self._realtime_timer.start(constants.graph.REALTIME_UPDATE_INTERVAL_MS)
                self.logger.info("Live updates enabled.")
                # Trigger an immediate update when the user turns it on.
                self._update_realtime()
            else:
                self._realtime_timer.stop()
                self.logger.info("Live updates disabled.")

        except Exception as e:
            self.logger.error(f"Error toggling live update: {e}", exc_info=True)


    def _update_realtime(self) -> None:
        """
        Slot for the real-time timer. Triggers a live graph update ONLY if
        the user is currently viewing the "Session" timeline.
        """
        if not self.isActiveWindow() or self._is_closing or not self.isVisible() or not self._is_live_update_enabled:
            return

        try:
            # Only perform a live update if the user is on the "Session" timeline.
            period_key = constants.data.history_period.PERIOD_MAP.get(self._history_period_value, "")
            if period_key == "TIMELINE_SESSION":
                self.logger.debug("Live update tick: Refreshing session data.")
                # Trigger the standard, worker-based update. The worker will
                # fetch the latest in-memory session data, which is very fast.
                # PASS show_loading=False to avoid flickering the "collecting data" overlay
                self.update_graph(show_loading=False)
            # If not on session view, do nothing.

        except Exception as e:
            self.logger.error(f"Error in real-time update: {e}", exc_info=True)


    def _update_history_period_text(self, value: int) -> None:
        """Updates the text label for the history period slider in the settings panel."""
        try:
            # This function should ONLY ever interact with the settings panel widgets.
            if hasattr(self.settings_widget, 'history_period_slider'):
                period_key = constants.data.history_period.PERIOD_MAP.get(value, constants.data.history_period.DEFAULT_PERIOD)
                translated_period = getattr(self.i18n, period_key, period_key)
                self.settings_widget.history_period_slider.setValueText(translated_period)
        except Exception as e:
            self.logger.error(f"Error updating history period text: {e}", exc_info=True)


    def update_history_period(self, value: int, show_loading: bool = True) -> None:
        """Triggers a graph update based on the selected history period."""
        if self._is_closing:
            return
        try:
            # The new architecture is simple: just trigger a full update.
            self.update_graph(show_loading=show_loading)
            self.logger.debug("History period update triggered for a full refresh.")
        except Exception as e:
            self.logger.error(f"Error updating history period: {e}", exc_info=True)


    def export_history(self) -> None:
        """Export network speed history to CSV, respecting the current UI filters."""
        import csv

        if self._is_closing: return
        try:
            if not self._main_widget or not self._main_widget.widget_state:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, self.i18n.EXPORT_DATA_ACCESS_ERROR_MESSAGE)
                return

            # Get filters from UI to export exactly what the user is seeing
            start_time, _ = self._get_time_range_from_ui()
            selected_interface_key = self.interface_filter.currentData()
            interface_to_query = None if selected_interface_key == "all" else selected_interface_key

            history_tuples = self._main_widget.widget_state.get_speed_history(
                start_time=start_time,
                interface_name=interface_to_query
            )

            if not history_tuples:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, self.i18n.NO_HISTORY_DATA_MESSAGE)
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = constants.export.CSV_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            file_path, _ = QFileDialog.getSaveFileName(
                self, self.i18n.EXPORT_CSV_TITLE, os.path.join(constants.export.DEFAULT_EXPORT_PATH, suggested_name), self.i18n.CSV_FILE_FILTER
            )
            if file_path:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", newline="", encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([self.i18n.CSV_HEADER_TIMESTAMP, self.i18n.CSV_HEADER_UPLOAD_MBPS, self.i18n.CSV_HEADER_DOWNLOAD_MBPS])
                    for ts, up_bytes_sec, down_bytes_sec in history_tuples:
                        writer.writerow([
                            ts.isoformat(),
                            f"{(up_bytes_sec * 8 / 1_000_000):.4f}",
                            f"{(down_bytes_sec * 8 / 1_000_000):.4f}"
                        ])
                QMessageBox.information(
                    self, self.i18n.SUCCESS_TITLE, self.i18n.EXPORT_SUCCESS_MESSAGE.format(file_path=file_path)
                )
                self.logger.info(f"History exported to {file_path}")
        except PermissionError:
            self.logger.error(f"Permission denied exporting history to {file_path}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.PERMISSION_DENIED_MESSAGE)
        except Exception as e:
            self.logger.error(f"Error exporting history: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.EXPORT_ERROR_MESSAGE.format(error=str(e)))


    def save_figure(self) -> None:
        """Save the current graph as a PNG image."""
        if self._is_closing:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = constants.export.IMAGE_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            file_path, _ = QFileDialog.getSaveFileName(
                self, self.i18n.EXPORT_GRAPH_IMAGE_TITLE, os.path.join(constants.export.DEFAULT_EXPORT_PATH, suggested_name), self.i18n.PNG_FILE_FILTER
            )
            if file_path:
                os.makedirs(os.path.dirname(file_path), exist_ok=True) # Ensure dir exists
                # Use figure's current facecolor for saved image background
                self.figure.savefig(file_path, bbox_inches='tight', dpi=constants.export.IMAGE_DPI, facecolor=self.figure.get_facecolor())
                QMessageBox.information(
                    self, self.i18n.SUCCESS_TITLE, self.i18n.EXPORT_SUCCESS_MESSAGE.format(file_path=file_path)
                )
                self.logger.info(f"Graph image saved to {file_path}")

        except PermissionError:
            self.logger.error(f"Permission denied saving graph image to {file_path}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.PERMISSION_DENIED_MESSAGE)
        except Exception as e:
            self.logger.error(f"Error saving graph image: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.EXPORT_ERROR_MESSAGE.format(error=str(e)))


    def closeEvent(self, event: QCloseEvent) -> None:
        """Handles window closure by hiding or performing a full cleanup."""
        try:
            # This flag is set by the parent widget during a full application shutdown.
            if getattr(self, '_is_closing', False):
                self.logger.debug("Performing full cleanup for graph window on app exit...")
                
                # Stop all timers and the worker thread
                self._realtime_timer.stop()
                self._db_size_update_timer.stop()
                if hasattr(self, 'worker_thread') and self.worker_thread.isRunning():
                    self.worker_thread.quit()
                    self.worker_thread.wait(1000) # Wait up to 1 second
                
                # Clear figure without using pyplot to avoid popup windows
                if hasattr(self, 'figure'): 
                    self.figure.clear()
                if hasattr(self, 'canvas'): self.canvas.deleteLater()
                
                event.accept()
            else:
                # If the user is just closing the window, stop the thread
                # and clean up resources so it can be created fresh next time.
                self.logger.debug("User closing graph window. Stopping worker and hiding.")
                
                self._realtime_timer.stop()
                if hasattr(self, 'worker_thread') and self.worker_thread.isRunning():
                    self.worker_thread.quit()
                    self.worker_thread.wait(1000)
                
                # Save final state before hiding
                final_graph_settings = {
                    "graph_window_pos": {"x": self.pos().x(), "y": self.pos().y()},
                    "history_period_slider_value": self._history_period_value
                }
                self._notify_parent_of_setting_change(final_graph_settings)
                
                self.window_closed.emit()
                
                # IMPORTANT: Use accept() so WA_DeleteOnClose cleans up the widget.
                # Previously hide() + ignore() caused a memory leak with ghost windows.
                event.accept()
                
                # Tell the parent widget that this instance is "dead"
                # so that it will create a fresh one next time.
                if self._main_widget:
                    self._main_widget.graph_window = None
                
        except Exception as e:
            self.logger.error(f"Error in closeEvent: {e}", exc_info=True)            
            event.ignore()

    def _get_db_size_mb(self) -> float:
        """Get the size of the database file in megabytes."""
        try:
            # Access the db_path via the db_worker attribute on widget_state.
            if (hasattr(self._main_widget, "widget_state") and
                    hasattr(self._main_widget.widget_state, "db_worker") and
                    hasattr(self._main_widget.widget_state.db_worker, "db_path")):
                db_path = self._main_widget.widget_state.db_worker.db_path
                if db_path and os.path.exists(db_path):
                    return os.path.getsize(db_path) / (1024 * 1024)
        except Exception as e:
            self.logger.error(f"Error getting DB size: {e}", exc_info=True)
        return 0.0


    def _update_db_size(self) -> None:
        """
        The single, authoritative function to update the database size display.
        This is now the only place where the label text for the "Year" setting is formatted.
        """
        try:
            if hasattr(self.settings_widget, 'keep_data_slider'):
                # Check if the slider is still set to "Year" before updating.
                slider = self.settings_widget.keep_data_slider
                days = constants.data.retention.DAYS_MAP.get(slider.value(), 30)
                if days == 365:
                    db_size_mb = self._get_db_size_mb()
                    # Pass the raw float `db_size_mb` directly.
                    slider.setValueText(self.i18n.YEAR_WITH_DB_SIZE_LABEL.format(size_mb=db_size_mb))
        except Exception as e:
            self.logger.error(f"Error updating keep data label: {e}", exc_info=True)


    def _init_db_size_timer(self) -> None:
        """Initialize DB size update timer and immediately update the display."""
        if not hasattr(self, "_db_size_update_timer"):
            self._db_size_update_timer = QTimer(self)
            self._db_size_update_timer.timeout.connect(self._update_db_size)
            self._db_size_update_timer.setInterval(10000)  # 10 second update interval
        
        # Start timer and do initial update if using 1-year retention
        if hasattr(self, 'keep_data') and self.keep_data.value() in constants.data.retention.DAYS_MAP:
            days = constants.data.retention.DAYS_MAP[self.keep_data.value()]
            if days == 365:
                self._update_db_size()  # Update size immediately
                if not self._db_size_update_timer.isActive():
                    self._db_size_update_timer.start()    
        

    def _update_keep_data_text(self, value: int) -> None:
        """
        Update the keep data slider's value text and manage the DB size refresh timer.
        """
        if not hasattr(self.settings_widget, 'keep_data_slider'):
            return

        days = constants.data.retention.DAYS_MAP.get(value, constants.data.retention.DAYS_MAP[3])

        if days == 365:
            # Call the single authoritative update function.
            self._update_db_size()
            if not self._db_size_update_timer.isActive():
                # Start the timer with the correct 10-second interval.
                self._db_size_update_timer.start(10000)
        else:
            # For other periods, just show the number of days.
            self.settings_widget.keep_data_slider.setValueText(self.i18n.DAYS_TEMPLATE.format(days=days))
            if self._db_size_update_timer.isActive():
                self._db_size_update_timer.stop()


    def _on_legend_toggled(self, checked: bool) -> None:
        """Handles the Show Legend toggle, saves the state, and updates the graph."""
        if self._is_closing or not self._main_widget:
            return
        
        # 1. Update the application's central configuration
        self._notify_parent_of_setting_change({'show_legend': checked})
        
        # 2. Trigger a graph redraw to show/hide the legend
        self.update_graph()


    def _days_to_slider_value(self, days: int) -> int:
        """Convert a number of days to the corresponding slider value (0-6).
        
        Args:
            days: The number of days to convert.
            
        Returns:
            The slider value (0-6) that corresponds to the given number of days.
        """
        for slider_value, mapped_days in constants.data.retention.DAYS_MAP.items():
            if mapped_days == days:
                return slider_value
        # If not found, default to 30 days (slider value 3)
        return 3


    def update_graph(self, show_loading: bool = True):
        """
        Triggers a data refresh by emitting a signal to the worker thread.
        This is the primary entry point for all graph updates.
        """
        if self._is_closing or not hasattr(self, 'request_data_processing'):
            return
        
        # Only perform updates if the window is actually visible to save resources,
        # unless it's the very first load.
        if not self.isVisible() and self._initial_load_done:
            return

        if show_loading:
            # Show a loading message immediately for a responsive feel.
            self._show_graph_message(self.i18n.COLLECTING_DATA_MESSAGE, is_error=False)

        # Get the current filter settings from the UI.
        start_time, end_time = self._get_time_range_from_ui()
        # Get the current filter settings from the UI safely.
        interface_to_query = None
        if hasattr(self, 'interface_filter') and self.interface_filter:
            data = self.interface_filter.currentData()
            interface_to_query = data if data != "all" else None

        interface_to_query = self.interface_filter.currentData() if self.interface_filter else "all"
        period_key = constants.data.history_period.PERIOD_MAP.get(self._history_period_value, "")
        is_session_view = period_key == "TIMELINE_SESSION"

        # 4. Emit the processing request with a Sequence ID
        self._current_request_id += 1
        # self.logger.debug(f"[PERF] update_graph: Emitting request {self._current_request_id}")
        self.request_data_processing.emit(start_time, end_time, interface_to_query, is_session_view, self._current_request_id)


    def _on_data_ready(self, data: List[Tuple[float, float, float]], total_up: float, total_down: float, sequence_id: int):
        """Slot to receive processed data from the worker and render it."""
        if self._is_closing: return

        # PERFORMANCE: Only render if this is the LATEST request result
        if sequence_id < self._last_processed_id:
            # self.logger.debug(f"Skipping obsolete result (SID {sequence_id} < {self._last_processed_id})")
            return
            
        self._last_processed_id = sequence_id
        # start = time.perf_counter()
        # self.logger.debug(f"[PERF-{start}] _on_data_ready START (Received from Worker)")
        
        if self._is_closing: return
        if not data or len(data) < 2:
            self._show_graph_message(self.i18n.NO_DATA_MESSAGE, is_error=False)
            # self.logger.debug(f"[PERF-{start}] _on_data_ready END (No Data) (dur={time.perf_counter() - start:.4f}s)")
            return
        
        # Call the new, dedicated rendering function
        try:
            self._render_graph(data, total_up, total_down)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error rendering graph: {e}", exc_info=True)
            self._show_graph_message(self.i18n.GRAPH_UPDATE_ERROR_TEMPLATE.format(error=str(e)), is_error=True)
        
        # self.logger.debug(f"[PERF-{start}] _on_data_ready END (Render Complete) (dur={time.perf_counter() - start:.4f}s)")

    def _render_graph(self, history_data: List[Tuple[float, float, float]], total_up: float = 0.0, total_down: float = 0.0):
        """
        Delegates rendering to GraphRenderer with optimization for live updates.
        """
        try:
            self.logger.debug(f"_render_graph called with {len(history_data)} data points")
            start_time, end_time = self._get_time_range_from_ui()
            period_key = constants.data.history_period.PERIOD_MAP.get(self._history_period_value, "")
            self.logger.debug(f"Rendering with period_key={period_key}, start={start_time}, end={end_time}")
            
            # Prepare data common to both paths (logic duplicated from renderer to ensure consistency?)
            # No, better to let renderer handle it. But update_data needs pre-processed data?
            # To avoid duplication, let's rely on renderer.
            
            # ATTEMPT OPTIMIZED UPDATE
            # Only try optimized update if we are in High Res mode (e.g. Session or < 6h)
            # because aggregated modes might change bins.
            # Ideally renderer.update_data handles this, but for safety, we assume update_data 
            # only works for high-res continuous lines.
            
            update_success = False
            
            # Determine if we should try update (simple heuristic: Session view is prime candidate)
            is_session = (period_key == "TIMELINE_SESSION")
            
            if is_session:
                 # We need to prep data for update_data
                 raw_data = np.array(history_data, dtype=float)
                 if len(raw_data) > 0:
                     timestamps = raw_data[:, 0]
                     ups = raw_data[:, 1]
                     downs = raw_data[:, 2]
                     
                     upload_mbps = (ups * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
                     download_mbps = (downs * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
                     upload_mbps = np.maximum(upload_mbps, 0)
                     download_mbps = np.maximum(download_mbps, 0)
                     
                     plot_dates = [datetime.fromtimestamp(t) for t in timestamps]
                     
                     if self.renderer.update_data(plot_dates, upload_mbps, download_mbps, start_time, end_time):
                         update_success = True
                         # Construct result for interaction cache manually
                         # (timestamps, x_coords (None implies recalc), up_mbps, down_mbps)
                         from matplotlib.dates import date2num
                         # Re-convert to bytes for cache (as expected by interaction) - wait interaction expects raw bytes?
                         # renderer.render returns PLOTTED data (Mbps?).
                         # interaction.update_data_cache docs say: "Bytes/s = (Mbps...)"
                         # Wait, renderer.render conversion (lines 215):
                         # if plotted_up is not None: plotted_up = (plotted_up * ...) / ...
                         # Interaction expects BYTES/SEC.
                         # But internally cache stores raw bytes.
                         # Let's check interaction.py line 87: self._graph_data_ups = upload_speeds * 8
                         # It expects input in Mbps?
                         # renderer line 215: up = up_mbps * 1M / 8.
                         # So render returns BYTES/SEC.
                         
                         # So here we use ups/downs directly (they are bytes/sec).
                         result = (timestamps, None, ups, downs)

            if not update_success:
                # Delegate to Renderer for full render
                result = self.renderer.render(history_data, start_time, end_time, period_key)
                # If full render occurred, previous crosshairs are gone. Restore them.
                if hasattr(self.interaction, 'refresh_overlays'):
                    self.interaction.refresh_overlays()
            
            if result:
                processed_timestamps, x_coords, processed_ups, processed_downs = result
                # Note: processed_ups/downs from render() are already converted to bytes/sec if they were mbps.
                # If we did the manual update, 'ups' and 'downs' from history_data are ALREADY bytes/sec.
                self.interaction.update_data_cache(processed_timestamps, processed_ups, processed_downs, x_coords=x_coords)
                self.logger.debug(f"Updated interaction cache with {len(processed_timestamps)} points")
            else:
                self.interaction.update_data_cache(np.array([]), np.array([]), np.array([]))

            # Apply Theme (Color synchronization)
            if hasattr(self, 'renderer'):
                self.renderer.apply_theme(self._is_dark_mode)

            # Update Stats Bar with pre-calculated totals
            self._update_stats_bar(history_data, total_up, total_down)
            
            # Force canvas visibility
            self.canvas.setVisible(True)
            self.logger.debug(f"Canvas visible={self.canvas.isVisible()}, size={self.canvas.size()}")
            
        except Exception as e:
            self.logger.error(f"Error rendering graph: {e}", exc_info=True)
            self._show_graph_error(f"Render Error: {e}")

    def _update_stats_bar(self, history_data: List[Tuple[float, float, float]], total_upload_bytes: float = 0.0, total_download_bytes: float = 0.0) -> None:
        """
        Update the stats bar using the pre-calculated totals from the worker.
        """
        try:
            if not history_data:
                self.stats_bar.setText(self.i18n.NO_DATA_MESSAGE)
                return

            upload_bytes_sec = [up for _, up, _ in history_data if up is not None]
            download_bytes_sec = [down for _, _, down in history_data if down is not None]
            max_upload_mbps = (max(upload_bytes_sec) * 8 / 1_000_000) if upload_bytes_sec else 0.0
            max_download_mbps = (max(download_bytes_sec) * 8 / 1_000_000) if download_bytes_sec else 0.0

            total_upload_display, total_upload_unit = helpers.format_data_size(total_upload_bytes, self.i18n)
            total_download_display, total_download_unit = helpers.format_data_size(total_download_bytes, self.i18n)

            stats_text = self.i18n.DEFAULT_STATS_TEXT_TEMPLATE.format(
                max_up=max_upload_mbps, max_up_unit="Mbps",
                max_down=max_download_mbps, max_down_unit="Mbps",
                up_total=total_upload_display, up_unit=total_upload_unit,
                down_total=total_download_display, down_unit=total_download_unit
            )
            self.stats_bar.setText(stats_text)
            
        except Exception as e:
            self.logger.error(f"Error updating stats bar: {e}", exc_info=True)
            self.stats_bar.setText(self.i18n.STATS_CALCULATION_ERROR)
