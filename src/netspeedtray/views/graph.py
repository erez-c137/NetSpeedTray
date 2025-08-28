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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# --- Third-Party Imports ---
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtGui import QResizeEvent, QCloseEvent, QIcon
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QTabWidget, QVBoxLayout, QWidget
)

# --- Custom Application Imports ---
from netspeedtray import constants
from netspeedtray.utils import helpers
from netspeedtray.utils.components import Win11Slider, Win11Toggle
from netspeedtray.utils.position_utils import ScreenUtils
from netspeedtray.utils.styles import get_accent_color


class GraphWindow(QWidget):
    """
    A window for displaying network speed history and per-app bandwidth usage using PyQt6.
    (Docstring remains largely the same as provided)
    """

    def __init__(self, parent=None, logger=None, i18n=None, session_start_time: Optional[datetime] = None):
            """ Initialize the GraphWindow with its UI components. """
            super().__init__()
            self._parent = parent
            self.logger = logger or logging.getLogger(__name__)
            if i18n is not None:
                self.i18n = i18n
            elif parent is not None and hasattr(parent, 'i18n'):
                self.i18n = parent.i18n
            else:
                self.i18n = None
            self._is_closing = False
            self._current_data = None
            self._last_stats_update = time.monotonic()
            self._stats_update_interval = constants.graph.STATS_UPDATE_INTERVAL
            self._cached_stats = {}

            # The graph window now uses the time passed from its parent, with a fallback.
            self.session_start_time = session_start_time or datetime.now()

            # Setup timers
            self._realtime_timer = QTimer(self)
            self._update_timer = QTimer(self)
            self._db_size_update_timer = QTimer(self)
            self._db_size_update_timer.timeout.connect(self._update_db_size)
            
            # Setup UI
            self.setupUi(self)
            self.setWindowTitle(constants.graph.WINDOW_TITLE)

            # Set window icon
            try:
                icon_filename = getattr(constants.app, 'ICON_FILENAME', 'NetSpeedTray.ico')
                icon_path = helpers.get_app_asset_path(icon_filename)
                if icon_path.exists():
                    self.setWindowIcon(QIcon(str(icon_path)))
                else:
                    self.logger.warning(f"Icon file not found at {icon_path}")
            except Exception as e:
                self.logger.error(f"Error setting window icon: {e}", exc_info=True)
            
            # Initialize matplotlib components first
            self._init_matplotlib()
            
            # Initialize overlay elements
            self._init_overlay_elements()
            
            # Initialize settings panel
            self._init_settings_panel()
            
            # Apply initial theme
            initial_dark_mode = self._parent.config.get("dark_mode", False) if self._parent else False
            self.toggle_dark_mode(initial_dark_mode)
            
            # Connect signals
            self._connect_signals()
            
            # Initial update
            self._perform_initial_update()

            # Start the live update timer by default
            self.toggle_live_update(True)
    
    def setupUi(self, parent=None):
        """Constructs the main layout and widgets for the GraphWindow (manual, not Qt Designer)."""
        # Main vertical layout
        self.main_layout = QVBoxLayout(self)
        self.setLayout(self.main_layout)


        # Tab widget (App Usage feature temporarily disabled)
        self.tab_widget = QTabWidget(self)
        self.main_layout.addWidget(self.tab_widget)

        # Graph tab
        self.graph_widget = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_widget)
        self.graph_widget.setLayout(self.graph_layout)
        self.tab_widget.addTab(self.graph_widget, self.i18n.SPEED_GRAPH_TAB_LABEL)

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
        self.history_period_label = None
        self.keep_data_label = None
        self.dark_mode = None
        self.realtime = None
        self.history_period = None
        self.keep_data = None

        # Timers and state
        self._realtime_timer = QTimer(self)
        self._update_timer = QTimer(self)
        self._db_size_update_timer = QTimer(self)
        self._graph_update_pending = False
        self._last_history = None
        self._no_data_text_obj = None
        self._current_date_formatter_type = None

        # Call window positioning after UI is set up
        self._position_window()

    def _init_matplotlib(self):
        """ Initialize matplotlib figure and canvas """
        try:
            # Use FIGURE_SIZE tuple (width, height)
            fig_size = getattr(constants.graph, 'FIGURE_SIZE', (8, 6))
            self.figure = Figure(figsize=fig_size)
            self.canvas = FigureCanvas(self.figure)
            self.ax = self.figure.add_subplot(111)
            # Use i18n or fallback for axis labels
            self.ax.set_ylabel('Speed')
            self.ax.grid(True, linestyle=getattr(constants.graph, 'GRID_LINESTYLE', '--'), alpha=getattr(constants.graph, 'GRID_ALPHA', 0.5))

            # Initialize the upload and download lines
            self.upload_line, = self.ax.plot([], [], 
                color=constants.graph.UPLOAD_LINE_COLOR, 
                linewidth=constants.graph.LINE_WIDTH,
                label=self.i18n.UPLOAD_LABEL if hasattr(self, 'i18n') else 'Upload'
            )
            self.download_line, = self.ax.plot([], [], 
                color=constants.graph.DOWNLOAD_LINE_COLOR, 
                linewidth=constants.graph.LINE_WIDTH,
                label=self.i18n.DOWNLOAD_LABEL if hasattr(self, 'i18n') else 'Download'
            )
            
            # Initialize axis limits and "no data" text object
            self._no_data_text_obj = None
            self.ax.set_xlim(0, 1)
            self.ax.set_ylim(0, constants.graph.MIN_Y_AXIS_LIMIT)
            
            # Add canvas to layout
            if hasattr(self, 'graph_layout'):
                self.graph_layout.addWidget(self.canvas)
            else:
                self.logger.error("graph_layout not found")
                
            # Ensure initial draw
            self.canvas.draw_idle()
        except Exception as e:
            self.logger.error(f"Error initializing matplotlib: {e}", exc_info=True)
    
    def _init_overlay_elements(self):
        """ Initialize stats bar and hamburger menu """
        try:
            # Stats Bar
            self.stats_bar = QLabel(self)
            # Use INITIAL_STATS_TEXT if present, else fallback
            initial_stats_text = getattr(constants.graph, 'INITIAL_STATS_TEXT', "")
            self.stats_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stats_bar.setText(initial_stats_text)
            # Make the stats bar always fill the width of the graph area
            self.stats_bar.setMinimumWidth(0)
            self.stats_bar.setMaximumWidth(16777215)  # Max possible width
            self.stats_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.stats_bar.show()
              # Hamburger Menu
            hamburger_size = getattr(constants.graph, 'HAMBURGER_ICON_SIZE', 24)  # Fallback to 24px if missing
            self.hamburger_icon = QPushButton(self)
            self.hamburger_icon.setFixedSize(hamburger_size, hamburger_size)
            self.hamburger_icon.setCursor(Qt.CursorShape.PointingHandCursor)
            # Set hamburger icon as Unicode text
            self.hamburger_icon.setText("☰")
            font = self.hamburger_icon.font()
            font.setPointSize(14)  # Slightly smaller for better appearance
            self.hamburger_icon.setFont(font)
            # Apply a semi-transparent dark background with white text for better visibility
            base_style = """
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.7);
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.85);
                }
                QPushButton:pressed {
                    background-color: rgba(0, 0, 0, 1.0);
                }
            """
            self.hamburger_icon.setStyleSheet(base_style)
            self.hamburger_icon.show()
            # Connect to toggle settings panel
            self.hamburger_icon.clicked.connect(self._toggle_settings_panel_visibility)
            
            # Ensure visibility
            self.stats_bar.raise_()
            self.hamburger_icon.raise_()
            
        except Exception as e:
            self.logger.error(f"Error initializing overlay elements: {e}", exc_info=True)


    def _toggle_settings_panel_visibility(self):
        """Show or hide the settings panel, expanding to the right without resizing the graph or moving the hamburger icon."""
        if not self.settings_widget:
            return

        try:
            # Store original window and graph widget sizes before any changes
            if not hasattr(self, '_original_window_size'):
                self._original_window_size = self.geometry().size()
                self._original_graph_size = self.graph_widget.size()

            if self.settings_widget.isVisible():
                self.settings_widget.hide()
                # Restore original window and graph widget sizes
                self.resize(self._original_window_size)
                self.graph_widget.setFixedSize(self._original_graph_size)
                self._reposition_overlay_elements()  # Adjust stats bar
                self.logger.debug("Settings panel hidden, window and graph sizes restored")
                return

            # Fix graph widget size to prevent resizing
            self.graph_widget.setFixedSize(self._original_graph_size)

            # Get hamburger icon's global position (right edge)
            hamburger_right_global = self.hamburger_icon.mapToGlobal(QPoint(self.hamburger_icon.width(), 0))
            # Convert to window coordinates
            hamburger_right_window = self.mapFromGlobal(hamburger_right_global)

            # Set panel position to the right of the hamburger icon with a small gap
            panel_x = hamburger_right_window.x() + 8
            panel_y = hamburger_right_window.y()

            # Set fixed size for the settings panel - increased width to accommodate sliders
            panel_width = 300
            panel_height = self._original_graph_size.height()
            self.settings_widget.setFixedSize(panel_width, panel_height)

            # Ensure window is wide enough to accommodate the panel
            window_geometry = self.geometry()
            required_width = panel_x + panel_width + 16  # 16px padding on the right
            if window_geometry.width() < required_width:
                self.resize(required_width, window_geometry.height())

            # Move and show the settings panel
            self.settings_widget.move(panel_x, panel_y)
            self.settings_widget.raise_()
            self.settings_widget.show()
            self.logger.debug(f"Settings panel positioned at ({panel_x}, {panel_y})")
        except Exception as e:
            self.logger.error(f"Error positioning settings panel: {e}", exc_info=True)


    def _init_settings_panel(self):
        """Initialize settings panel widgets with a fixed dark theme."""
        from netspeedtray.utils.styles import always_dark_panel_style
        from netspeedtray.utils.components import Win11Slider, Win11Toggle

        panel_styles = always_dark_panel_style()
        explicit_label_style = (
            f"color: {constants.styles.SETTINGS_PANEL_TEXT_DARK}; "
            "background-color: transparent; border: none; outline: none; "
            "padding: 0px; margin: 0px; font-size: 13px; font-family: 'Segoe UI Variable';"
        )
        PANEL_TEXT_COLOR_FOR_SLIDER_VALUES = constants.styles.GRAPH_TEXT_DARK

        self.settings_widget = QWidget(self)
        self.settings_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.settings_widget.setStyleSheet(
            f"QWidget {{ background-color: {constants.styles.GRAPH_BG_DARK}; border: none; outline: none; }}"
        )

        settings_main_layout = QVBoxLayout(self.settings_widget)
        settings_main_layout.setContentsMargins(12, 12, 12, 12)
        settings_main_layout.setSpacing(15)
        self.settings_widget.setLayout(settings_main_layout)

        group_box = QGroupBox(self.settings_widget)
        group_box.setTitle(getattr(self.i18n, 'GRAPH_SETTINGS_LABEL', 'Graph Settings'))
        group_box.setStyleSheet(panel_styles.get("QGroupBox_PanelDark"))
        
        group_content_layout = QGridLayout(group_box)
        group_content_layout.setVerticalSpacing(10)
        group_content_layout.setHorizontalSpacing(8)
        group_content_layout.setContentsMargins(10, 15, 10, 10)

        current_row = 0

        # --- Interface Filter ---
        interface_label = QLabel(self.i18n.INTERFACE_LABEL)
        interface_label.setStyleSheet(explicit_label_style)
        self.interface_filter = QComboBox()
        self.interface_filter.setStyleSheet(panel_styles.get("QComboBox_PanelDark"))
        # Use the i18n string for display and a fixed key 'all' for logic
        self.interface_filter.addItem(self.i18n.ALL_INTERFACES_AGGREGATED_LABEL, "all")

        group_content_layout.addWidget(interface_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.interface_filter, current_row, 0, 1, 2)
        current_row += 1

        # --- History period Slider ---
        self.history_period_label = QLabel(getattr(self.i18n, 'HISTORY_PERIOD_LABEL_NO_VALUE', 'Timeline'))
        self.history_period_label.setStyleSheet(explicit_label_style)

        current_history_period_str = self._parent.config.get('history_period', constants.data.history_period.DEFAULT_PERIOD)
        initial_history_slider_val = self._parent.config.get('history_period_slider_value', 
            next((k for k, v in constants.data.history_period.PERIOD_MAP.items() if v == current_history_period_str), 0))
        self.history_period = Win11Slider(
            min_value=0, max_value=len(constants.data.history_period.PERIOD_MAP) - 1,
            value=initial_history_slider_val, page_step=1, has_ticks=True, parent=group_box,
            value_label_text_color=PANEL_TEXT_COLOR_FOR_SLIDER_VALUES
        )
        group_content_layout.addWidget(self.history_period_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.history_period, current_row, 0, 1, 2)
        current_row += 1

        # --- Data Retention Slider ---
        self.keep_data_label = QLabel(getattr(self.i18n, 'DATA_RETENTION_LABEL_NO_VALUE', 'Data Retention'))
        self.keep_data_label.setStyleSheet(explicit_label_style)

        config_days = self._parent.config.get("keep_data", constants.config.defaults.DEFAULT_HISTORY_PERIOD_DAYS)
        initial_slider_value = self._days_to_slider_value(config_days)
        self.keep_data = Win11Slider(
            min_value=0, max_value=len(constants.data.retention.DAYS_MAP) - 1,
            value=initial_slider_value, page_step=1, has_ticks=True, parent=group_box,
            value_label_text_color=PANEL_TEXT_COLOR_FOR_SLIDER_VALUES
        )
        group_content_layout.addWidget(self.keep_data_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.keep_data, current_row, 0, 1, 2)
        current_row += 1

        # --- Toggles ---
        dm_label = QLabel(getattr(self.i18n, 'DARK_MODE_LABEL', 'Dark Mode'))
        dm_label.setStyleSheet(explicit_label_style)
        self.dark_mode = Win11Toggle(
            initial_state=self._parent.config.get('dark_mode', constants.config.defaults.DEFAULT_DARK_MODE),
            parent=group_box
        )
        group_content_layout.addWidget(dm_label, current_row, 0)
        group_content_layout.addWidget(self.dark_mode, current_row, 1, Qt.AlignmentFlag.AlignLeft)
        current_row += 1
        
        lu_label = QLabel(getattr(self.i18n, 'LIVE_UPDATE_LABEL', 'Live Update'))
        lu_label.setStyleSheet(explicit_label_style)
        self.realtime = Win11Toggle(initial_state=True, parent=group_box)
        group_content_layout.addWidget(lu_label, current_row, 0)
        group_content_layout.addWidget(self.realtime, current_row, 1, Qt.AlignmentFlag.AlignLeft)

        settings_main_layout.addWidget(group_box)
        settings_main_layout.addStretch(1)
        self.settings_widget.hide()


    def _populate_interface_filter(self) -> None:
        """
        Fetches the list of distinct interfaces from the database and populates
        the interface filter QComboBox.
        """
        self.logger.debug("Populating interface filter...")
        try:
            if not self._parent or not hasattr(self._parent, 'widget_state'):
                self.logger.warning("Cannot populate interfaces: parent or widget_state missing.")
                return

            # Block signals to prevent triggering updates while we modify the list
            self.interface_filter.blockSignals(True)
            
            current_selection = self.interface_filter.currentText()
            self.interface_filter.clear()
            
            # Add the default aggregate view
            # Use the i18n string for display and a fixed key 'all' for logic
            self.interface_filter.addItem(self.i18n.ALL_INTERFACES_AGGREGATED_LABEL, "all")
            
            # Fetch and add the distinct interfaces from the database
            distinct_interfaces = self._parent.widget_state.get_distinct_interfaces()
            if distinct_interfaces:
                self.interface_filter.addItems(sorted(distinct_interfaces))
            
            # Restore previous selection if it still exists
            index = self.interface_filter.findText(current_selection)
            if index != -1:
                self.interface_filter.setCurrentIndex(index)
            
            self.logger.info("Interface filter populated with %d interfaces.", len(distinct_interfaces))

        except Exception as e:
            self.logger.error("Failed to populate interface filter: %s", e, exc_info=True)
            # Ensure there's at least the default option on error
            if self.interface_filter.count() == 0:
                self.interface_filter.addItem("All (Aggregated)")
        finally:
            # Always unblock signals
            self.interface_filter.blockSignals(False)


    def _on_interface_filter_changed(self, interface_name: str) -> None:
        """
        Slot that is triggered when the user selects a new interface from the filter.
        """
        if self._is_closing:
            return
            
        self.logger.info("User selected interface: %s", interface_name)
        # The main update method will automatically fetch data for the currently
        # selected period and interface. We just need to trigger it.
        self.update_graph()


    def _on_retention_changed(self) -> None:
        """
        Slot triggered when the Data Retention slider is released. It notifies
        the main widget to update the retention period in the backend.
        """
        if self._is_closing or not self._parent:
            return

        try:
            slider_value = self.keep_data.value()
            days = constants.data.retention.DAYS_MAP.get(slider_value, 30)
            self.logger.info("Data Retention slider released. New period: %d days.", days)

            # Call the new method on the parent to trigger the grace period logic
            if hasattr(self._parent, 'update_retention_period'):
                self._parent.update_retention_period(days)
            else:
                self.logger.warning("Parent widget does not have 'update_retention_period' method.")
                
        except Exception as e:
            self.logger.error("Error in _on_retention_changed: %s", e, exc_info=True)


    def _get_time_range_from_ui(self) -> Tuple[Optional[datetime], datetime]:
        """
        Determines the start and end time for a query based on the
        current state of the 'Timeline' slider.

        Returns:
            A tuple of (start_time, end_time). start_time can be None for 'All'.
        """
        import psutil

        now = datetime.now()
        period_value = self.history_period.value()
        # Get the non-translated KEY from the map
        period_key = constants.data.history_period.PERIOD_MAP.get(period_value, constants.data.history_period.DEFAULT_PERIOD)
        
        start_time: Optional[datetime] = None
        # Compare against the KEY, not the displayed text
        if period_key == "TIMELINE_SYSTEM_UPTIME":
            start_time = datetime.fromtimestamp(psutil.boot_time())
        elif period_key == "TIMELINE_SESSION":
            start_time = self.session_start_time
        elif period_key == "TIMELINE_3_HOURS":
            start_time = now - timedelta(hours=3)
        elif period_key == "TIMELINE_6_HOURS":
            start_time = now - timedelta(hours=6)
        elif period_key == "TIMELINE_12_HOURS":
            start_time = now - timedelta(hours=12)
        elif period_key == "TIMELINE_24_HOURS":
            start_time = now - timedelta(days=1)
        elif period_key == "TIMELINE_WEEK":
            start_time = now - timedelta(weeks=1)
        elif period_key == "TIMELINE_MONTH":
            start_time = now - timedelta(days=30)
        # Note: "TIMELINE_ALL" correctly results in start_time = None
        
        return start_time, now


    def _perform_initial_update(self):
        """
        Performs the initial graph update by triggering the standard
        history period update logic, which respects the initial slider state.
        """
        try:
            if hasattr(self, 'history_period'):
                self.update_history_period(self.history_period.value(), initial_setup=True)

            self._reposition_overlay_elements()
            if hasattr(self, 'canvas'):
                self.canvas.draw()
        except Exception as e:
            self.logger.error(f"Error in initial update: {e}", exc_info=True)

    def _position_window(self) -> None:
        """Position the window centered on the primary screen or to saved position."""
        try:
            pos_x, pos_y = -1, -1
            if self._parent and "graph_window_pos" in self._parent.config:
                saved_pos = self._parent.config["graph_window_pos"]
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
        Displays a message overlayed on the Matplotlib graph area.
        Can be used for errors or informational states like 'collecting data'.
        """
        if is_error:
            self.logger.error(f"Displaying graph error: {message}")
        try:
            if not hasattr(self, 'ax') or not hasattr(self, 'canvas'): return

            # Clear any plotted data
            self.upload_line.set_data([],[])
            self.download_line.set_data([],[])

            is_dark = self.dark_mode.isChecked()
            facecolor = constants.styles.GRAPH_BG_DARK if is_dark else constants.styles.GRAPH_BG_LIGHT
            
            # Use red for errors, and standard theme text color for info messages
            if is_error:
                text_color = constants.color.RED
            else:
                text_color = constants.styles.DARK_MODE_TEXT_COLOR if is_dark else constants.styles.LIGHT_MODE_TEXT_COLOR

            if self._no_data_text_obj: # Reuse if it exists
                self._no_data_text_obj.set_text(message)
                self._no_data_text_obj.set_color(text_color)
                self._no_data_text_obj.set_visible(True)
            else:
                self._no_data_text_obj = self.ax.text(0.5, 0.5, message,
                            ha='center', va='center', transform=self.ax.transAxes,
                            color=text_color, fontsize=constants.graph.ERROR_MESSAGE_FONTSIZE, visible=True)

            # Hide all axis elements for a clean message display
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.ax.set_xlabel("")
            self.ax.set_ylabel("")
            for spine in self.ax.spines.values(): spine.set_visible(False)

            self.ax.set_facecolor(facecolor)
            self.figure.patch.set_facecolor(facecolor)
            self.canvas.draw_idle()
        except Exception as e_draw:
            self.logger.error(f"Failed to display graph message on canvas: {e_draw}", exc_info=True)


    def _show_graph_error(self, message: str) -> None:
        """ A convenience wrapper to display an error message on the graph. """
        self._show_graph_message(message, is_error=True)

            
    def toggle_settings(self) -> None:
        """Toggle the settings dialog."""
        if self._is_closing or not self._parent or not hasattr(self._parent, "config_manager"): 
            return
            
        try:
            from .settings import SettingsDialog
            settings_dialog = SettingsDialog(
                parent=self._parent,  # Keep parent as self._parent for config access
                config=self._parent.config.copy(),
                version=self._parent.version,
                i18n=self.i18n,
                available_interfaces=self._parent.get_available_interfaces() if hasattr(self._parent, "get_available_interfaces") else None,
                is_startup_enabled=self._parent.startup_task_exists() if hasattr(self._parent, "startup_task_exists") else False
            )
            
            # Center the dialog relative to this window
            if settings_dialog.windowHandle():
                settings_dialog.windowHandle().setTransientParent(self.windowHandle())
            
            # Show the dialog modally
            if settings_dialog.exec() == QDialog.DialogCode.Accepted:
                # Settings were saved, apply them
                if hasattr(self._parent, 'apply_all_settings'):
                    self._parent.apply_all_settings() 
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
        """ Applies dark or light mode theme to the GraphWindow and its components.
            The settings panel itself remains always dark.
        """
        if self._is_closing: return
        try:
            is_dark = checked
            self.logger.debug(f"Applying graph dark mode theme: {is_dark}")

            if self.dark_mode.isChecked() != is_dark: # Sync toggle if called programmatically
                self.dark_mode.blockSignals(True)
                self.dark_mode.setChecked(is_dark) # This refers to the Win11Toggle for the *graph's* dark mode
                self.dark_mode.blockSignals(False)

            if self._parent and hasattr(self._parent, "config_manager"):
                self._parent.config["dark_mode"] = is_dark # This is the main graph dark mode setting
                self._parent.config_manager.save(self._parent.config)

            # Hamburger icon - keep consistent dark background style regardless of theme
            self.hamburger_icon.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 0, 0, 0.7);
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.85);
                }
                QPushButton:pressed {
                    background-color: rgba(0, 0, 0, 1.0);
                }
            """) # Make sure this is the full intended style from your original working code

            # Matplotlib Graph Theme (This is the main purpose of this method)
            if not hasattr(self, 'figure') or not hasattr(self, 'ax') or not hasattr(self, 'canvas'):
                self.logger.error("Graph components not initialized for dark mode toggle")
                return # Added return to prevent further errors if components missing

            graph_bg = constants.styles.GRAPH_BG_DARK if is_dark else constants.styles.GRAPH_BG_LIGHT
            text_color = constants.styles.DARK_MODE_TEXT_COLOR if is_dark else constants.styles.LIGHT_MODE_TEXT_COLOR
            grid_color = getattr(constants.styles, 'GRID_COLOR_DARK', '#444444') if is_dark else getattr(constants.styles, 'GRID_COLOR_LIGHT', '#CCCCCC')

            self.figure.patch.set_facecolor(graph_bg)
            self.ax.set_facecolor(graph_bg)
            self.ax.xaxis.label.set_color(text_color)
            self.ax.yaxis.label.set_color(text_color)
            self.ax.tick_params(colors=text_color) # Styles tick numbers and lines
            
            # Grid styling
            self.ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=grid_color)
            
            # Spines (axis borders) styling
            for spine in self.ax.spines.values(): 
                spine.set_color(grid_color) # Use grid_color or text_color as appropriate for spines
            
            # Legend styling
            if self.ax.get_legend():
                leg = self.ax.get_legend()
                for text_obj in leg.get_texts():
                    text_obj.set_color(text_color)
                leg.get_frame().set_facecolor(graph_bg)
                leg.get_frame().set_edgecolor(grid_color) # Or a specific legend border color
            
            # The code block that was causing the error:
            # Ensure this draw_idle is properly handled. The original code had it inside another try/except for e_mpl.
            # Let's assume for now it's part of the main try block of this method.
            # The earlier `return` if components are missing should protect this.
            # self.canvas.draw_idle() # This line was the one without a proper try/except structure previously.
                                    # Now it's part of the main try.

            # Overlay Elements for the main graph
            self.stats_bar.setStyleSheet(constants.styles.STATS_DARK_STYLE if is_dark else constants.styles.STATS_LIGHT_STYLE)
            self.stats_bar.raise_()
            self.hamburger_icon.raise_() # Ensure it's on top after potential redraws
            self._reposition_overlay_elements() # Reposition after theme changes might affect sizes

            # Redraw canvas for main graph changes
            # It's generally good practice to have draw_idle in a try-except if it can fail.
            try:
                self.canvas.draw_idle()
            except Exception as e_draw_idle:
                self.logger.error(f"Error during canvas.draw_idle() in toggle_dark_mode: {e_draw_idle}", exc_info=True)
                try:
                    self.canvas.draw() # Fallback to draw() if draw_idle() fails
                except Exception as e_draw_fallback:
                    self.logger.error(f"Error during canvas.draw() fallback in toggle_dark_mode: {e_draw_fallback}", exc_info=True)


        except Exception as e: # This is the main try's except
            self.logger.error(f"Error in toggle_dark_mode: {e}", exc_info=True)


    def _connect_signals(self):
        """Connect all relevant signals for UI interactivity."""
        self._realtime_timer.timeout.connect(self._update_realtime)
        if hasattr(self, 'dark_mode') and self.dark_mode:
            self.dark_mode.toggled.connect(self.toggle_dark_mode)
        if hasattr(self, 'realtime') and self.realtime:
            self.realtime.toggled.connect(self.toggle_live_update)
        
        if hasattr(self, 'interface_filter') and self.interface_filter:
            self.interface_filter.currentTextChanged.connect(self._on_interface_filter_changed)

        if hasattr(self, 'history_period') and self.history_period:
            self.history_period.sliderReleased.connect(self._on_history_slider_released)
            self.history_period.valueChanged.connect(self._update_history_period_text)
        
        if hasattr(self, 'keep_data') and self.keep_data:
            self.keep_data.sliderReleased.connect(self._on_retention_changed)
            self.keep_data.valueChanged.connect(self._update_keep_data_text)

        # Set initial text values for sliders
        if hasattr(self, 'history_period'):
            self._update_history_period_text(self.history_period.value())
        if hasattr(self, 'keep_data'):
            self._update_keep_data_text(self.keep_data.value())


    def _on_history_slider_released(self) -> None:
        """
        Handles the timeline slider release event. This triggers an immediate graph
        update and notifies the parent to save the new setting for persistence.
        """
        # Get the current value from the slider
        current_value = self.history_period.value()

        # 1. Trigger the graph to update itself with the new time period.
        self.update_history_period(current_value)

        # 2. Notify the parent widget to save this new setting to the config file.
        self._notify_parent_of_setting_change({'history_period_slider_value': current_value})


    def _notify_parent_of_setting_change(self, settings_dict: dict) -> None:
        """Helper method to pass a dictionary of configuration updates to the parent widget."""
        if self._parent and hasattr(self._parent, 'handle_graph_settings_update'):
            self.logger.debug(f"Notifying parent of setting change: {settings_dict}")
            self._parent.handle_graph_settings_update(settings_dict)
        else:
            self.logger.warning(f"Cannot notify parent of setting change: Method not found.")


    def _save_slider_value_to_config(self, config_key: str, value: int) -> None:
        """Helper method to save a slider's integer value to the configuration."""
        if self._parent and hasattr(self._parent, 'config') and hasattr(self._parent, 'config_manager'):
            self.logger.debug(f"Saving slider value: {config_key} = {value}")
            self._parent.config[config_key] = value
            try:
                self._parent.config_manager.save(self._parent.config)
            except Exception as e:
                self.logger.error(f"Failed to save config after slider value change for {config_key}: {e}")
        else:
            self.logger.warning(f"Cannot save slider value for {config_key}: parent or config components missing.")

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        """ Handles window resize events. Repositions overlay elements. """
        if event: super().resizeEvent(event)
        if self._is_closing or not hasattr(self, 'tab_widget'): return # Not fully initialized

        try:
            QTimer.singleShot(0, self._reposition_overlay_elements)

        except Exception as e:
            self.logger.error(f"Error in resizeEvent: {e}", exc_info=True)


    def _reposition_overlay_elements(self) -> None:
        """Reposition stats bar over the current tab's content area; keep hamburger icon fixed on graph."""
        if self._is_closing or not all(hasattr(self, attr) for attr in ['tab_widget', 'hamburger_icon', 'stats_bar']):
            return

        try:
            current_tab_content_widget = self.tab_widget.currentWidget()
            if not current_tab_content_widget:
                self.logger.warning("Cannot reposition overlays: No current tab content widget.")
                return

            # Get geometry of the tab content area relative to GraphWindow
            tab_content_rect_global = current_tab_content_widget.mapToGlobal(current_tab_content_widget.rect().topLeft())
            tab_content_pos_in_graph_window = self.mapFromGlobal(tab_content_rect_global)
            container_width = current_tab_content_widget.width()

            # Fix hamburger icon position relative to graph widget's top-right
            if self.tab_widget.currentWidget() == self.graph_widget:
                hamburger_x = tab_content_pos_in_graph_window.x() + container_width - self.hamburger_icon.width() - constants.graph.HAMBURGER_ICON_OFFSET_X
                hamburger_y = tab_content_pos_in_graph_window.y() + constants.graph.HAMBURGER_ICON_OFFSET_Y
                self.hamburger_icon.move(hamburger_x, hamburger_y)

            # Reposition stats bar (top-center of tab content)
            # Make the stats bar always fill the width of the graph area (minus a small margin)
            margin = 16
            stats_bar_width = container_width - margin
            self.stats_bar.setFixedWidth(stats_bar_width)
            stats_bar_x = tab_content_pos_in_graph_window.x() + (container_width - stats_bar_width) // 2
            stats_bar_y = tab_content_pos_in_graph_window.y() + constants.graph.STATS_BAR_OFFSET_Y
            self.stats_bar.move(stats_bar_x, stats_bar_y)

            self.hamburger_icon.raise_()
            self.stats_bar.raise_()
        except Exception as e:
            self.logger.error(f"Error repositioning overlay elements: {e}", exc_info=True)


    def toggle_live_update(self, checked: bool) -> None:
        """ Starts or stops the real-time update timer. """
        if self._is_closing: return
        try:
            if checked:
                # The timer is now connected in __init__, so we just need to start it.
                self._realtime_timer.start(constants.graph.REALTIME_UPDATE_INTERVAL_MS)
                self.logger.info("Live updates enabled.")
            else:
                self._realtime_timer.stop()
                self.logger.info("Live updates disabled.")

        except Exception as e:
            self.logger.error(f"Error toggling live update: {e}", exc_info=True)

    def _update_realtime(self) -> None:
        """ Slot for _realtime_timer. Fetches latest data and updates the active view. """
        if self._is_closing or not self.isVisible() or not self.realtime.isChecked():
            if not self.realtime.isChecked() and self._realtime_timer.isActive():
                self._realtime_timer.stop()
            return
        
        try:
            if not self._parent or not self._parent.widget_state:
                self.logger.warning("Parent or widget_state missing for real-time update.")
                return
            
            current_slider_value = self.history_period.value()
            self.update_history_period(current_slider_value)

        except Exception as e:
            self.logger.error(f"Error in real-time update: {e}", exc_info=True)

    def _update_history_period_text(self, value: int) -> None:
        """Update the history period slider's value text.
        
        Args:
            value: The history period value to set
        """
        if not isinstance(value, int):
            self.logger.error(f"Invalid history period value type: {type(value)}, expected int")
            value = 0  # Default to System Uptime
            
        try:
            if hasattr(self, 'history_period'):
                period_key = constants.data.history_period.PERIOD_MAP.get(value, constants.data.history_period.DEFAULT_PERIOD)
                translated_period = getattr(self.i18n, period_key, period_key)
                self.history_period.setValueText(translated_period)
                # Do NOT set any title or timeline label on the graph
                if self.tab_widget.currentIndex() == 0 and hasattr(self, 'ax'):
                    self.ax.set_title("")
                    if hasattr(self, 'canvas'):
                        self.canvas.draw_idle()
        except Exception as e:
            self.logger.error(f"Error updating history period text: {e}", exc_info=True)

    def update_history_period(self, value: int, initial_setup: bool = False) -> None:
        """Triggers a graph update based on the selected history period."""
        if self._is_closing:
            return
        try:
            start_time, end_time = self._get_time_range_from_ui()
            # We call the throttled update_graph, which will then call _perform_graph_update
            self.update_graph(xlim=(start_time, end_time))
            self.logger.debug("History period update triggered.")
        except Exception as e:
            self.logger.error(f"Error updating history period: {e}", exc_info=True)


    def export_history(self) -> None:
        """Export network speed history to CSV, respecting the current UI filters."""
        import csv

        if self._is_closing: return
        try:
            if not self._parent or not self._parent.widget_state:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, self.i18n.EXPORT_DATA_ACCESS_ERROR_MESSAGE)
                return

            # Get filters from UI to export exactly what the user is seeing
            start_time, _ = self._get_time_range_from_ui()
            selected_interface_key = self.interface_filter.currentData()
            interface_to_query = None if selected_interface_key == "all" else selected_interface_key

            history_tuples = self._parent.widget_state.get_speed_history(
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
        import matplotlib.pyplot as plt

        try:
            # On application shutdown, the parent's cleanup routine now handles saving.
            # We only need to handle the case where the user closes the graph window
            # while the main app is still running. In this case, we just hide it.
            if getattr(self, '_is_closing', False):
                self.logger.debug("Performing full cleanup for graph window...")
                self._realtime_timer.stop()
                self._update_timer.stop()
                self._db_size_update_timer.stop()
                
                if hasattr(self, 'figure'): plt.close(self.figure)
                if hasattr(self, 'canvas'): self.canvas.deleteLater()
                
                event.accept()
            else:
                # If just closing the window, save its state immediately and hide it.
                final_graph_settings = {
                    "graph_window_pos": {"x": self.pos().x(), "y": self.pos().y()},
                    "history_period_slider_value": self.history_period.value()
                }
                self._notify_parent_of_setting_change(final_graph_settings)
                self.logger.debug("Hiding graph window instead of closing.")
                self.hide()
                event.ignore()
        except Exception as e:
            self.logger.error(f"Error in closeEvent: {e}", exc_info=True)            
            event.ignore()


    def _get_db_size_mb(self) -> float:
        """Get the size of the database file in megabytes."""
        try:
            # FIX: Access the db_path via the db_worker attribute on widget_state.
            if (hasattr(self._parent, "widget_state") and
                    hasattr(self._parent.widget_state, "db_worker") and
                    hasattr(self._parent.widget_state.db_worker, "db_path")):
                db_path = self._parent.widget_state.db_worker.db_path
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
            if hasattr(self, 'keep_data'):
                # Check if the slider is still set to "Year" before updating.
                days = constants.data.retention.DAYS_MAP.get(self.keep_data.value(), 30)
                if days == 365:
                    db_size_mb = self._get_db_size_mb()
                    # Pass the raw float `db_size_mb` directly.
                    self.keep_data.setValueText(self.i18n.YEAR_WITH_DB_SIZE_LABEL.format(size_mb=db_size_mb))
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
        if not hasattr(self, 'keep_data'):
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
            self.keep_data.setValueText(self.i18n.DAYS_TEMPLATE.format(days=days))
            if self._db_size_update_timer.isActive():
                self._db_size_update_timer.stop()


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


    def update_graph(self, history_data: Optional[List] = None, xlim: Optional[Tuple[datetime, datetime]] = None) -> None:
        """
        Schedules a throttled update for the graph. It only passes the time range.
        """
        if self._is_closing:
            return

        if self._graph_update_pending:
            return

        self._graph_update_pending = True
        self._update_timer.singleShot(
            constants.graph.GRAPH_UPDATE_THROTTLE_MS,
            lambda: self._perform_graph_update(xlim=xlim)
        )


    def _configure_xaxis_format(self, start_time: datetime, end_time: datetime) -> None:
        """
        Intelligently configures the x-axis locator and formatter based on the
        time range to prevent Matplotlib warnings and improve readability.
        """
        import matplotlib.dates as mdates

        if not start_time or not end_time or start_time >= end_time:
            self.ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            return

        time_delta_seconds = (end_time - start_time).total_seconds()
        
        # --- MORE GRANULAR LOGIC ---
        if time_delta_seconds <= 900:  # <= 15 minutes
            locator = mdates.MinuteLocator(interval=2)
            formatter = mdates.DateFormatter('%H:%M:%S')
        elif time_delta_seconds <= 3600 * 2:  # <= 2 hours
            locator = mdates.MinuteLocator(interval=15)
            formatter = mdates.DateFormatter('%H:%M')
        elif time_delta_seconds <= 3600 * 8:  # Handles 3h and 6h views
            locator = mdates.HourLocator(interval=1)
            formatter = mdates.DateFormatter('%H:%M')
        elif time_delta_seconds <= 86400 * 2: # Handles 12h and 24h views
            locator = mdates.HourLocator(interval=3)
            formatter = mdates.DateFormatter('%H:%M\n%b %d')
        elif time_delta_seconds <= 86400 * 8: # Handles 1 week view
            locator = mdates.DayLocator(interval=1)
            formatter = mdates.DateFormatter('%a %d') # e.g., "Mon 12"
        elif time_delta_seconds <= 86400 * 32: # Handles 1 month view
            locator = mdates.WeekdayLocator(byweekday=mdates.MO)
            formatter = mdates.DateFormatter('%b %d')
        else:  # > 1 month (e.g., "All")
            locator = mdates.MonthLocator()
            formatter = mdates.DateFormatter('%Y-%b')

        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(formatter)


    def _get_nice_y_axis_top(self, max_speed: float) -> float:
        """Calculates a 'nice' round number for the top of the Y-axis."""
        import math

        min_range_mbps = 0.1  # Equivalent to 100 Kbps

        if max_speed <= min_range_mbps:
            return min_range_mbps

        # Calculate the order of magnitude (e.g., 10, 100, 1000)
        power = 10 ** math.floor(math.log10(max_speed))
        # Normalize the max_speed to a value between 1 and 10
        normalized_max = max_speed / power

        # Determine the next 'nice' number in the 1, 2, 5, 10 sequence
        if normalized_max <= 1:
            nice_top = 1
        elif normalized_max <= 2:
            nice_top = 2
        elif normalized_max <= 5:
            nice_top = 5
        else:
            nice_top = 10
        
        return nice_top * power


    def _perform_graph_update(self, xlim: Optional[Tuple[datetime, datetime]] = None) -> None:
        """
        Performs the actual rendering of the graph by fetching data for the
        currently selected interface and time period.
        """
        self._graph_update_pending = False
        if self._is_closing or not self.isVisible():
            return

        try:
            import numpy as np
            import matplotlib.dates as mdates
            from matplotlib.colors import SymLogNorm
            from matplotlib.ticker import AutoLocator, ScalarFormatter, FixedLocator

            if not self._parent or not self._parent.widget_state:
                self._show_graph_error(self.i18n.DATA_SOURCE_UNAVAILABLE_ERROR)
                return

            period_key = constants.data.history_period.PERIOD_MAP.get(self.history_period.value(), "")
            is_session_view = (period_key == "TIMELINE_SESSION")
            history_data = []

            # process per-interface live data
            selected_interface_key = self.interface_filter.currentData()
            interface_to_query = None if selected_interface_key == "all" else selected_interface_key

            if is_session_view:
                # The "Session" view uses the live, in-memory data source.
                mem_history = self._parent.widget_state.get_in_memory_speed_history()

                # Process the new snapshot structure to filter data for the selected interface.
                processed_history = []
                for snapshot in mem_history:
                    if interface_to_query is None:  # "All (Aggregated)" view
                        total_upload = sum(up for up, down in snapshot.speeds.values())
                        total_download = sum(down for up, down in snapshot.speeds.values())
                        processed_history.append((snapshot.timestamp, total_upload, total_download))
                    else:  # A specific interface is selected
                        up_speed, down_speed = snapshot.speeds.get(interface_to_query, (0.0, 0.0))
                        processed_history.append((snapshot.timestamp, up_speed, down_speed))
                history_data = processed_history

            else:
                # All other views (Uptime, 3 Hours, etc.) query the database directly.
                start_time, end_time = self._get_time_range_from_ui()

                is_special_view = "System Uptime" in period_key or "All" in period_key
                if start_time and not is_special_view:
                    earliest_data_time = self._parent.widget_state.get_earliest_data_timestamp()
                    if earliest_data_time and start_time < earliest_data_time:
                        self.logger.info(
                            f"Requested start time {start_time} is before the earliest data point "
                            f"({earliest_data_time}). Adjusting query to show all available data."
                        )
                        start_time = None

                history_data = self._parent.widget_state.get_speed_history(
                    start_time=start_time,
                    end_time=end_time,
                    interface_name=interface_to_query
                )

            if len(history_data) < 2:
                active_interfaces = self._parent.get_active_interfaces() if hasattr(self._parent, 'get_active_interfaces') else []

                is_selected_interface_active = self.interface_filter.currentData() == "all" or self.interface_filter.currentText() in active_interfaces

                is_live_view = "Session" in period_key or "Uptime" in period_key

                if is_live_view and is_selected_interface_active:
                    self._show_graph_message(getattr(self.i18n, 'COLLECTING_DATA_MESSAGE', "Collecting data..."), is_error=False)
                else:
                    self._show_graph_error(getattr(self.i18n, 'NO_DATA_MESSAGE', "No data available for the selected period."))
                return

            # --- Graph Restoration and Theming ---
            if self._no_data_text_obj: self._no_data_text_obj.set_visible(False)
            self.ax.set_yscale('linear')
            self.upload_line.set_visible(True)
            self.download_line.set_visible(True)
            for spine in self.ax.spines.values(): spine.set_visible(True)
            self.ax.xaxis.get_label().set_visible(True)
            self.ax.yaxis.get_label().set_visible(True)
            self.ax.yaxis.set_major_locator(AutoLocator())
            self.ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            self.ax.set_xlabel(self.i18n.TIME_LABEL)
            self.ax.set_ylabel(self.i18n.MBITS_LABEL)
            self.ax.tick_params(labelbottom=True, labelleft=True)
            is_dark = self.dark_mode.isChecked()
            graph_bg = constants.styles.GRAPH_BG_DARK if is_dark else constants.styles.GRAPH_BG_LIGHT
            grid_color = getattr(constants.styles, 'GRID_COLOR_DARK', '#444444') if is_dark else getattr(constants.styles, 'GRID_COLOR_LIGHT', '#CCCCCC')
            text_color = constants.styles.DARK_MODE_TEXT_COLOR if is_dark else constants.styles.LIGHT_MODE_TEXT_COLOR
            self.figure.patch.set_facecolor(graph_bg)
            self.ax.set_facecolor(graph_bg)
            self.ax.tick_params(colors=text_color, which='both')
            self.ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=grid_color)

            self._update_stats_bar(history_data)

            timestamps = [entry[0] for entry in history_data]
            upload_speeds_mbps = [(entry[1] * 8) / 1_000_000 for entry in history_data]
            download_speeds_mbps = [(entry[2] * 8) / 1_000_000 for entry in history_data]

            self.upload_line.set_data(timestamps, upload_speeds_mbps)
            self.download_line.set_data(timestamps, download_speeds_mbps)

            all_speeds = upload_speeds_mbps + download_speeds_mbps
            non_zero_speeds = [s for s in all_speeds if s > 0.1]

            final_thresh = 10
            if non_zero_speeds:
                dynamic_thresh = np.quantile(non_zero_speeds, 0.90)
                final_thresh = max(1, min(dynamic_thresh, 50))

            self.ax.set_yscale('symlog', linthresh=final_thresh)
            self.ax.yaxis.set_major_formatter(ScalarFormatter())
            self.ax.yaxis.set_minor_formatter(ScalarFormatter())

            max_speed = max(all_speeds) if all_speeds else 0
            nice_top = self._get_nice_y_axis_top(max_speed)

            rounded_thresh_for_tick = round(final_thresh / 5) * 5
            y_ticks = {0.0, rounded_thresh_for_tick}

            if nice_top > 10:
                log_tick = 10.0
                while log_tick < nice_top:
                    y_ticks.add(log_tick)
                    log_tick *= 10

            y_ticks.add(nice_top)

            if 0.0 in y_ticks and 0 in y_ticks and 0.0 != 0:
                y_ticks.remove(0)

            self.ax.yaxis.set_major_locator(FixedLocator(sorted(list(y_ticks))))
            self.ax.set_ylim(bottom=0, top=nice_top)

            effective_start_time = min(timestamps)
            effective_end_time = max(timestamps)
            self.ax.set_xlim(effective_start_time, effective_end_time)
            self._configure_xaxis_format(effective_start_time, effective_end_time)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self.figure.autofmt_xdate(rotation=30, ha='right')

            self.canvas.draw_idle()
        except Exception as e:
            self.logger.error(f"Error updating graph: {e}", exc_info=True)
            self._show_graph_error(self.i18n.GRAPH_UPDATE_ERROR_TEMPLATE.format(error=str(e)))


    def _calculate_period_stats(self, period_data: List[Tuple[datetime, float, float]]) -> Dict[str, Any]:
        """
        Calculates statistics for a pre-filtered list of data points.
        The input data is (timestamp, upload_bytes_sec, download_bytes_sec).
        """
        try:
            if not period_data or len(period_data) < 2:
                return {
                    "max_upload": 0.0, "max_download": 0.0, 
                    "total_upload": 0.0, "total_upload_unit": "B",
                    "total_download": 0.0, "total_download_unit": "B"
                }

            upload_bytes_sec = [up for _, up, _ in period_data]
            download_bytes_sec = [down for _, _, down in period_data]
            
            max_upload_mbps = (max(upload_bytes_sec) * 8 / 1_000_000) if upload_bytes_sec else 0.0
            max_download_mbps = (max(download_bytes_sec) * 8 / 1_000_000) if download_bytes_sec else 0.0

            total_upload_bytes = 0.0
            total_download_bytes = 0.0
            for i in range(1, len(period_data)):
                dt_seconds = (period_data[i][0] - period_data[i-1][0]).total_seconds()
                
                # Ignore large time gaps (e.g., from sleep) in the total calculation
                if not (0 < dt_seconds < 60):
                    continue
                
                avg_upload_speed = (period_data[i][1] + period_data[i-1][1]) / 2
                avg_download_speed = (period_data[i][2] + period_data[i-1][2]) / 2
                
                total_upload_bytes += avg_upload_speed * dt_seconds
                total_download_bytes += avg_download_speed * dt_seconds

            # Format the final totals ONCE after the loop is complete.
            total_upload_display, total_upload_unit = helpers.format_data_size(total_upload_bytes, self.i18n)
            total_download_display, total_download_unit = helpers.format_data_size(total_download_bytes, self.i18n)

            return {
                "max_upload": max_upload_mbps,
                "max_download": max_download_mbps,
                "total_upload": total_upload_display,
                "total_upload_unit": total_upload_unit,
                "total_download": total_download_display,
                "total_download_unit": total_download_unit,
            }
        except Exception as e:
            self.logger.error(f"Error calculating period stats: {e}", exc_info=True)
            return {"max_upload": 0, "max_download": 0, "total_upload": 0, "total_download_unit": "B"}


    def _update_stats_bar(self, history_data: List[Tuple[datetime, float, float]]) -> None:
        """
        Update the stats bar with statistics for the provided (pre-filtered) period.
        """
        try:
            if not history_data:
                self.stats_bar.setText(self.i18n.NO_DATA_MESSAGE)
                return

            stats = self._calculate_period_stats(history_data)
            
            # --- Max speed unit is now always Mbps ---
            speed_unit = "Mbps"
            
            stats_text = self.i18n.DEFAULT_STATS_TEXT_TEMPLATE.format(
                max_up=stats['max_upload'],
                max_up_unit=speed_unit,
                max_down=stats['max_download'],
                max_down_unit=speed_unit,
                up_total=stats['total_upload'],
                up_unit=stats['total_upload_unit'],
                down_total=stats['total_download'],
                down_unit=stats['total_download_unit']
            )
            self.stats_bar.setText(stats_text)
            self.stats_bar.adjustSize()
            
        except Exception as e:
            self.logger.error(f"Error updating stats bar: {e}", exc_info=True)
            self.stats_bar.setText(self.i18n.STATS_CALCULATION_ERROR)