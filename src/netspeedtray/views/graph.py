"""
Graph module for NetSpeedTray.

This module defines the `GraphWindow` class, which provides a graphical interface for visualizing
network speed history and per-app bandwidth usage using Matplotlib and PyQt6.
"""

import csv
import logging
import os
import psutil
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any


import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.dates import DateFormatter
from PyQt6.QtWidgets import QSizePolicy

from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtGui import QResizeEvent, QCloseEvent, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QApplication,
    QLabel, QComboBox, QProgressBar, QDialog, QGroupBox,
    QTabWidget, QMessageBox, QFileDialog, QGridLayout
)

from netspeedtray.utils.styles import get_accent_color
from netspeedtray.utils.components import Win11Toggle, Win11Slider
from netspeedtray.core.widget_state import AggregatedSpeedData, AppBandwidthData, PerInterfaceSpeedData
from netspeedtray.constants import (
    GraphConstants, HistoryPeriodConstants, DataRetentionConstants,
    LegendPositionConstants, UIStyleConstants, SliderConstants,
    ExportConstants, HelperConstants, UnitConstants, ConfigConstants,
    ColorConstants, AppConstants
)
from netspeedtray.utils.position_utils import ScreenUtils
from netspeedtray.utils import helpers
from ..constants.i18n_strings import I18nStrings


class GraphWindow(QWidget):
    """
    A window for displaying network speed history and per-app bandwidth usage using PyQt6.
    (Docstring remains largely the same as provided)
    """

    def __init__(self, parent=None, logger=None, i18n=None):
        """ Initialize the GraphWindow with its UI components. """
        super().__init__()
        self._parent = parent
        self.logger = logger or logging.getLogger(__name__)
        # Set i18n, fallback to parent's i18n if not provided
        if i18n is not None:
            self.i18n = i18n
        elif parent is not None and hasattr(parent, 'i18n'):
            self.i18n = parent.i18n
        else:
            self.i18n = None
        self._is_closing = False
        self._current_data = None
        self._last_stats_update = time.time()
        self._stats_update_interval = GraphConstants.STATS_UPDATE_INTERVAL
        self._cached_stats = {}

        # Setup timers
        self._realtime_timer = QTimer(self)
        self._update_timer = QTimer(self)
        self._db_size_update_timer = QTimer(self)
        self._db_size_update_timer.timeout.connect(self._update_db_size)
        
        # Setup UI
        self.setupUi(self)
        self.setWindowTitle(GraphConstants.WINDOW_TITLE)

        # Set window icon
        try:
            icon_filename = getattr(AppConstants, 'ICON_FILENAME', 'NetSpeedTray.ico')
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
        self.tab_widget.addTab(self.graph_widget, self.tr("Graph"))

        # --- App Usage tab (Feature coming soon) ---
        # self.app_usage_widget = QWidget()
        # self.app_list_layout = QVBoxLayout(self.app_usage_widget)
        # 
        # # Filter combo for app usage
        # filter_container = QHBoxLayout()
        # filter_label = QLabel(getattr(self.i18n, 'FILTER_BY_LABEL', 'Filter by:'))
        # self.app_filter_combo = QComboBox()
        # self.app_filter_combo.addItems([
        #     getattr(self.i18n, 'LAST_30_DAYS_LABEL', 'Last 30 Days'),
        #     getattr(self.i18n, 'LAST_7_DAYS_LABEL', 'Last 7 Days'),
        #     getattr(self.i18n, 'SESSION_LABEL', 'Current Session'),
        # ])
        # self.app_filter_combo.currentIndexChanged.connect(self._update_app_usage)
        # filter_container.addWidget(filter_label)
        # filter_container.addWidget(self.app_filter_combo)
        # filter_container.addStretch()
        # self.app_list_layout.addLayout(filter_container)
        # 
        # self.app_usage_widget.setLayout(self.app_list_layout)
        # self.tab_widget.addTab(self.app_usage_widget, self.tr("App Usage"))

        # # Optionally: add stretch to app usage layout for spacing
        # self.app_list_layout.addStretch(1)


        # Connect tab change signal
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        # Set minimum size for usability
        self.setMinimumSize(GraphConstants.GRAPH_WIDGET_WIDTH, GraphConstants.GRAPH_WIDGET_HEIGHT)

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

        # App start time (for session period)
        self.app_start_time = datetime.now()

        # Call window positioning after UI is set up
        self._position_window()

    def _init_matplotlib(self):
        """ Initialize matplotlib figure and canvas """
        try:
            # Use FIGURE_SIZE tuple (width, height)
            fig_size = getattr(GraphConstants, 'FIGURE_SIZE', (8, 6))
            self.figure = Figure(figsize=fig_size)
            self.canvas = FigureCanvas(self.figure)
            self.ax = self.figure.add_subplot(111)
            # Use i18n or fallback for axis labels            self.ax.set_xlabel('Time')  # Using default English labels
            self.ax.set_ylabel('Speed')
            self.ax.grid(True, linestyle=getattr(GraphConstants, 'GRID_LINESTYLE', '--'), alpha=getattr(GraphConstants, 'GRID_ALPHA', 0.5))

            # Initialize the upload and download lines
            self.upload_line, = self.ax.plot([], [], 
                color=GraphConstants.UPLOAD_LINE_COLOR, 
                linewidth=GraphConstants.LINE_WIDTH,
                label=self.i18n.UPLOAD_LABEL if hasattr(self, 'i18n') else 'Upload'
            )
            self.download_line, = self.ax.plot([], [], 
                color=GraphConstants.DOWNLOAD_LINE_COLOR, 
                linewidth=GraphConstants.LINE_WIDTH,
                label=self.i18n.DOWNLOAD_LABEL if hasattr(self, 'i18n') else 'Download'
            )
            
            # Initialize axis limits and "no data" text object
            self._no_data_text_obj = None
            self.ax.set_xlim(0, 1)
            self.ax.set_ylim(0, GraphConstants.MIN_Y_AXIS_LIMIT)
            
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
            initial_stats_text = getattr(GraphConstants, 'INITIAL_STATS_TEXT', "")
            self.stats_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stats_bar.setText(initial_stats_text)
            # Make the stats bar always fill the width of the graph area
            self.stats_bar.setMinimumWidth(0)
            self.stats_bar.setMaximumWidth(16777215)  # Max possible width
            self.stats_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.stats_bar.show()
              # Hamburger Menu
            hamburger_size = getattr(GraphConstants, 'HAMBURGER_ICON_SIZE', 24)  # Fallback to 24px if missing
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
        # Import panel_styles for QGroupBox and QComboBox, but labels will be styled explicitly below.
        from netspeedtray.utils.styles import always_dark_panel_style
        from netspeedtray.utils.components import Win11Slider, Win11Toggle
        # Constants are already imported at the class/module level

        panel_styles = always_dark_panel_style()
        explicit_label_style = (
            f"color: {UIStyleConstants.SETTINGS_PANEL_TEXT_DARK}; "
            "background-color: transparent; "
            "border: none; "
            "outline: none; "
            "padding: 0px; " # Explicitly reset padding
            "margin: 0px; "  # Explicitly reset margin
            "font-size: 13px; "
            "font-family: 'Segoe UI Variable';"
        )

        PANEL_TEXT_COLOR_FOR_SLIDER_VALUES = UIStyleConstants.GRAPH_TEXT_DARK 

        self.settings_widget = QWidget(self)
        self.settings_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Style the main settings panel container: always dark, no border for the container itself
        self.settings_widget.setStyleSheet(
            f"QWidget {{ background-color: {UIStyleConstants.GRAPH_BG_DARK}; border: none; outline: none; }}"
        )

        settings_main_layout = QVBoxLayout(self.settings_widget)
        settings_main_layout.setContentsMargins(12, 12, 12, 12)
        settings_main_layout.setSpacing(15) 
        self.settings_widget.setLayout(settings_main_layout)

        group_box = QGroupBox(self.settings_widget)
        group_box_title = getattr(self.i18n, 'GRAPH_SETTINGS_LABEL', 'Graph Settings')
        group_box.setTitle(group_box_title)
        # Apply QGroupBox_PanelDark style for the GroupBox frame and title
        group_box.setStyleSheet(panel_styles.get("QGroupBox_PanelDark"))
        
        group_content_layout = QGridLayout(group_box)
        group_content_layout.setVerticalSpacing(10)
        group_content_layout.setHorizontalSpacing(8)
        group_content_layout.setContentsMargins(10, 15, 10, 10)

        current_row = 0

        # --- History period Slider ---
        self.history_period_label = QLabel(getattr(self.i18n, 'HISTORY_PERIOD_LABEL_NO_VALUE', 'Timeline'))
        self.history_period_label.setStyleSheet(explicit_label_style) 

        current_history_period_str = self._parent.config.get('history_period', HistoryPeriodConstants.DEFAULT_PERIOD)
        initial_history_slider_val = self._parent.config.get('history_period_slider_value', 
            next((k for k, v in HistoryPeriodConstants.PERIOD_MAP.items() if v == current_history_period_str), 0))
        self.history_period = Win11Slider(
            min_value=0, max_value=len(HistoryPeriodConstants.PERIOD_MAP) - 1,
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

        config_days = self._parent.config.get("keep_data", ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS)
        initial_slider_value = self._days_to_slider_value(config_days)
        self.keep_data = Win11Slider(
            min_value=0, max_value=len(DataRetentionConstants.DAYS_MAP) - 1,
            value=initial_slider_value, page_step=1, has_ticks=True, parent=group_box,
            value_label_text_color=PANEL_TEXT_COLOR_FOR_SLIDER_VALUES
        )
        group_content_layout.addWidget(self.keep_data_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.keep_data, current_row, 0, 1, 2)
        current_row += 1

        # --- Dark Mode Toggle ---
        dm_label = QLabel(getattr(self.i18n, 'DARK_MODE_LABEL', 'Dark Mode'))
        dm_label.setStyleSheet(explicit_label_style) 
        
        self.dark_mode = Win11Toggle(
            label_text="",
            initial_state=self._parent.config.get('dark_mode', ConfigConstants.DEFAULT_DARK_MODE),
            parent=group_box
        )
        group_content_layout.addWidget(dm_label, current_row, 0, Qt.AlignmentFlag.AlignVCenter)
        group_content_layout.addWidget(self.dark_mode, current_row, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        current_row += 1
        
        # --- Live Update Toggle ---
        lu_label = QLabel(getattr(self.i18n, 'LIVE_UPDATE_LABEL', 'Live Update'))
        lu_label.setStyleSheet(explicit_label_style) 
        
        self.realtime = Win11Toggle(
            label_text="", 
            initial_state=True,
            parent=group_box
        )
        group_content_layout.addWidget(lu_label, current_row, 0, Qt.AlignmentFlag.AlignVCenter)
        group_content_layout.addWidget(self.realtime, current_row, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        current_row += 1

        settings_main_layout.addWidget(group_box)
        settings_main_layout.addStretch(1)
        self.settings_widget.hide()


    def _perform_initial_update(self):
        """ Perform initial graph and stats update so UI is populated immediately on load. """
        try:
            # Update the graph with the current history period (as if the slider was just set)
            if self._parent and hasattr(self._parent, 'widget_state'):
                history = self._parent.widget_state.get_speed_history()
                self.update_graph(history)
            # Update the app usage stats bar and list
            self._update_app_usage()
            self._reposition_overlay_elements()  # Ensure proper positioning
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
            current_history = []
            if self._parent and self._parent.widget_state:
                 current_history = self._parent.widget_state.get_speed_history()

            # Only Graph tab is currently active
            if index == 0:  # Speed Graph
                self.update_graph(current_history) # Will use its throttle
            # elif index == 1:  # App Usage (Feature coming soon)
            #     self._update_app_usage() # Fetches its own data as needed

            # Crucially, reposition overlays as the reference widget (current tab) changes
            QTimer.singleShot(0, self._reposition_overlay_elements) # Ensure layout is settled

            self.logger.debug(f"Switched to tab {index}")
        except Exception as e:
            self.logger.error(f"Error handling tab change: {e}", exc_info=True)
            # QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.GRAPH_ERROR_MESSAGE.format(error=str(e)))


    def _update_app_usage(self) -> None:
        """ Fetches and displays the per-application bandwidth usage list, optimizing widget updates."""
        if self._is_closing:
            return
        self.logger.debug("Updating app usage view...")

        try:
            if not self._parent or not self._parent.widget_state:
                error_msg = getattr(self.i18n, 'GRAPH_DEPENDENCY_ERROR', "Graph dependencies missing.")
                self._show_app_usage_error(error_msg)
                self.logger.error("Cannot update app usage: Parent or widget_state missing.")
                return

            filter_text = self.app_filter_combo.currentText()
            start_time: Optional[datetime] = None
            now = datetime.now()

            # Safe access to i18n strings
            last_30_days_label = getattr(self.i18n, 'LAST_30_DAYS_LABEL', 'Last 30 Days')
            last_7_days_label = getattr(self.i18n, 'LAST_7_DAYS_LABEL', 'Last 7 Days')
            session_label = getattr(self.i18n, 'SESSION_LABEL', 'Current Session')

            if filter_text == last_30_days_label:
                start_time = now - timedelta(days=30)
            elif filter_text == last_7_days_label:
                start_time = now - timedelta(days=7)
            elif filter_text == session_label:
                start_time = self.app_start_time
            
            app_data: List[AppBandwidthData] = self._parent.widget_state.get_app_bandwidth_history(start_time=start_time)
            
            app_totals: Dict[str, Tuple[float, float]] = {}
            for entry in app_data:
                try:
                    bytes_sent = float(entry.bytes_sent if entry.bytes_sent is not None else 0.0)
                    bytes_recv = float(entry.bytes_recv if entry.bytes_recv is not None else 0.0)
                    app_name = str(entry.app_name or "Unknown App") 
                    aggregation_key = os.path.basename(app_name).lower() 
                    sent, recv = app_totals.get(aggregation_key, (0.0, 0.0))
                    app_totals[aggregation_key] = (sent + bytes_sent, recv + bytes_recv)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Invalid bandwidth data for entry {entry}: {e}")
                    continue
            
            sorted_apps = sorted(app_totals.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)
            
            # Calculate max total usage for progress bar normalization
            max_total_usage_bytes = 0.0
            if sorted_apps: # only if there are apps
                max_total_usage_bytes = sum(sorted_apps[0][1]) # Sum of sent+recv of the top app
            if max_total_usage_bytes == 0: # Avoid division by zero if top app has 0 usage or no apps
                max_total_usage_bytes = 1.0

            total_sent_bytes = sum(sent for _, (sent, _) in sorted_apps)
            total_recv_bytes = sum(recv for _, (_, recv) in sorted_apps)

            # Use helpers.format_data_size
            sent_val_num, sent_unit_str = helpers.format_data_size(total_sent_bytes)
            recv_val_num, recv_unit_str = helpers.format_data_size(total_recv_bytes)
            
            up_unit = sent_unit_str
            down_unit = recv_unit_str
            try:
                stats_template = getattr(self.i18n, 'APP_USAGE_STATS_TEXT_TEMPLATE', "Up: {up_total:.2f} {up_unit} | Down: {down_total:.2f} {down_unit}")
                stats_bar_text = stats_template.format(
                    up_total=float(sent_val_num), up_unit=up_unit,
                    down_total=float(recv_val_num), down_unit=down_unit
                )
                self.stats_bar.setText(stats_bar_text)
            except (ValueError, KeyError, AttributeError) as e_fmt: 
                self.logger.error(f"Error formatting APP_USAGE_STATS_TEXT_TEMPLATE. Values: up={sent_val_num}{up_unit}, down={recv_val_num}{down_unit}. Error: {e_fmt}")
                self.stats_bar.setText(f"Up: {float(sent_val_num):.2f} {up_unit} | Down: {float(recv_val_num):.2f} {down_unit}")
            
            self.stats_bar.adjustSize()
            self._reposition_overlay_elements() 

            theme_text_color = UIStyleConstants.DARK_MODE_TEXT_COLOR if self.dark_mode.isChecked() else UIStyleConstants.LIGHT_MODE_TEXT_COLOR
            progress_bar_chunk_color = ColorConstants.APP_USAGE_PROGRESS_CHUNK 
            prog_bar_bg_color = ColorConstants.APP_USAGE_PROGRESS_BG_DARK if self.dark_mode.isChecked() else ColorConstants.APP_USAGE_PROGRESS_BG_LIGHT

            current_ui_app_keys = set(self._app_entry_widgets.keys())
            data_app_keys = set()

            # Handle "No app data" message
            no_app_data_msg = getattr(self.i18n, 'NO_APP_DATA_MESSAGE', "No application data available for this period.")
            if not sorted_apps:
                if self._no_app_data_label is None:
                    self._no_app_data_label = QLabel(no_app_data_msg)
                    # Insert after the filter combo layout (which is at index 0)
                    self.app_list_layout.insertWidget(1, self._no_app_data_label) 
                
                self._no_app_data_label.setText(no_app_data_msg)
                self._no_app_data_label.setStyleSheet(f"color: {theme_text_color}; font-style: italic; padding: 10px; qproperty-alignment: AlignCenter;")
                self._no_app_data_label.setVisible(True)
            elif self._no_app_data_label:
                self._no_app_data_label.setVisible(False)

            # The app_list_layout contains:
            # 0: filter_container (QHBoxLayout)
            # 1: (optional) _no_app_data_label OR first app entry
            # ... app entries ...
            # N: stretch item
            
            # Clear existing app entries (widgets) before adding/updating, starting after the filter_container and _no_app_data_label
            # More robust approach: recycle or hide/show existing ones.
            # For now, let's stick to your existing recycling logic but be careful with indices.

            # Iterate through sorted apps and update/create widgets
            for list_idx, (app_name_key, (s_bytes, r_bytes)) in enumerate(sorted_apps):
                data_app_keys.add(app_name_key)
                tot_bytes = s_bytes + r_bytes
                
                # Use helpers.format_data_size
                val_num, u_str = helpers.format_data_size(tot_bytes)
                
                prog_val = int((tot_bytes / max_total_usage_bytes) * 100) if max_total_usage_bytes > 0 else 0
                
                # The actual insert index in the QVBoxLayout, accounting for the filter combo
                # and potentially the _no_app_data_label (though it should be hidden if sorted_apps is not empty)
                # Let's aim to insert starting from index 1 of app_list_layout
                target_layout_index = list_idx + 1 

                if app_name_key in self._app_entry_widgets: 
                    entry_widget = self._app_entry_widgets[app_name_key]
                    _, name_label, usage_label, progress_bar = self._app_entry_labels_progress[app_name_key]
                    
                    name_label.setText(app_name_key) 
                    name_label.setStyleSheet(f"color: {theme_text_color}; font-size: 13px;")
                    usage_label.setText(f"{float(val_num):.2f} {u_str}")
                    usage_label.setStyleSheet(f"color: {theme_text_color}; font-size: 13px;")
                    progress_bar.setValue(prog_val)
                    progress_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid #555; background-color: {prog_bar_bg_color}; height: 8px; border-radius: 4px; }} QProgressBar::chunk {{ background-color: {progress_bar_chunk_color}; border-radius: 3px; margin: 1px; }}")
                    
                    # Ensure widget is at the correct position if order changed
                    current_layout_item = self.app_list_layout.itemAt(target_layout_index)
                    if not current_layout_item or current_layout_item.widget() != entry_widget:
                        # Remove from old position if it exists elsewhere
                        for i in range(self.app_list_layout.count()):
                             item = self.app_list_layout.itemAt(i)
                             if item and item.widget() == entry_widget:
                                 self.app_list_layout.takeAt(i)
                                 break # Found and removed
                        self.app_list_layout.insertWidget(target_layout_index, entry_widget)
                    entry_widget.show() # Ensure it's visible
                else: 
                    entry_widget = QWidget()
                    entry_layout = QHBoxLayout(entry_widget)
                    entry_layout.setContentsMargins(2,2,2,2)

                    icon_label = QLabel() 
                    icon_label.setFixedSize(24,24)
                    icon_label.setStyleSheet(f"border: 1px solid {theme_text_color if not self.dark_mode.isChecked() else '#555'}; border-radius: 3px; qproperty-alignment: AlignCenter;")
                    entry_layout.addWidget(icon_label)

                    name_label = QLabel(app_name_key)
                    name_label.setStyleSheet(f"color: {theme_text_color}; font-size: 13px;")
                    name_label.setToolTip(app_name_key)
                    entry_layout.addWidget(name_label, stretch=2)

                    usage_label = QLabel(f"{float(val_num):.2f} {u_str}")
                    usage_label.setStyleSheet(f"color: {theme_text_color}; font-size: 13px;")
                    usage_label.setMinimumWidth(85)
                    usage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    entry_layout.addWidget(usage_label, stretch=1)
                    
                    progress_bar = QProgressBar()
                    progress_bar.setMaximum(100)
                    progress_bar.setValue(prog_val)
                    progress_bar.setTextVisible(False)
                    progress_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid #555; background-color: {prog_bar_bg_color}; height: 8px; border-radius: 4px; }} QProgressBar::chunk {{ background-color: {progress_bar_chunk_color}; border-radius: 3px; margin: 1px; }}")
                    entry_layout.addWidget(progress_bar, stretch=3)
                    
                    self.app_list_layout.insertWidget(target_layout_index, entry_widget)
                    self._app_entry_widgets[app_name_key] = entry_widget
                    self._app_entry_labels_progress[app_name_key] = (icon_label, name_label, usage_label, progress_bar)
            
            # Remove or hide widgets for apps no longer in data
            apps_to_remove = current_ui_app_keys - data_app_keys
            for key_to_remove in apps_to_remove:
                if key_to_remove in self._app_entry_widgets:
                    widget_to_remove = self._app_entry_widgets.pop(key_to_remove)
                    self._app_entry_labels_progress.pop(key_to_remove) # Remove from mapping
                    # self.app_list_layout.removeWidget(widget_to_remove) # This can be problematic if indices shift
                    # widget_to_remove.deleteLater()
                    widget_to_remove.hide() # Hiding is safer for now than full removal during iteration
            
            # Ensure any remaining old widgets beyond the current data are hidden
            # Start checking from after the last current app + 1 (for filter combo)
            start_checking_idx = len(sorted_apps) + 1 
            for i in range(start_checking_idx, self.app_list_layout.count()):
                item = self.app_list_layout.itemAt(i)
                if item and item.widget():
                    # If it's not the _no_app_data_label and not a stretch item
                    if item.widget() != self._no_app_data_label:
                        item.widget().hide()
                elif item and item.spacerItem(): # If it's a stretch item, break
                    break


            # Manage stretch: remove if exists, then add back at the end
            for i in reversed(range(self.app_list_layout.count())):
                item = self.app_list_layout.itemAt(i)
                if item and item.spacerItem():
                    self.app_list_layout.takeAt(i) # Remove existing stretch
                    # delete item.spacerItem() # Not strictly necessary for QSpacerItem
                    break 
            self.app_list_layout.addStretch(1)


            self.logger.debug(f"Successfully updated app usage list UI with {len(sorted_apps)} entries.")

        except ValueError as ve:
             self.logger.error(f"Value error processing app usage data: {ve}", exc_info=True)
             error_msg = getattr(self.i18n, 'APP_USAGE_CONFIG_ERROR', "App usage configuration error. Check logs.")
             self._show_app_usage_error(error_msg)
        except AttributeError as ae: 
            self.logger.error(f"AttributeError in app usage (likely missing i18n string): {ae}", exc_info=True)
            self._show_app_usage_error("A translation string is missing for app usage. Check logs.")
        except Exception as e:
            self.logger.error(f"Unexpected error updating app usage view: {e}", exc_info=True)
            error_msg = getattr(self.i18n, 'APP_USAGE_ERROR_MESSAGE', "Failed to load app usage data. Check logs for details.")
            self._show_app_usage_error(error_msg)


    def _show_app_usage_error(self, message: str) -> None:
        """ Displays an error message within the app usage list area. """
        self.logger.warning(f"Displaying app usage error: {message}")
        
        # Hide all existing app entries
        for widget in self._app_entry_widgets.values():
            widget.setVisible(False) # Hide instead of remove, easier to bring back

        theme_text_color = UIStyleConstants.DARK_MODE_TEXT_COLOR if self.dark_mode.isChecked() else UIStyleConstants.LIGHT_MODE_TEXT_COLOR
        if self._no_app_data_label is None:
            self._no_app_data_label = QLabel(message)
            self.app_list_layout.addWidget(self._no_app_data_label) # Add to layout once
        else:
            self._no_app_data_label.setText(message) # Update text
        
        self._no_app_data_label.setStyleSheet(f"color: {theme_text_color}; font-style: italic; padding: 10px; qproperty-alignment: AlignCenter;")
        self._no_app_data_label.setVisible(True)
        # Ensure it's not pushed down by a stretch if list was previously empty
        last_item_index = self.app_list_layout.count() -1
        if last_item_index >=0 and self.app_list_layout.itemAt(last_item_index).spacerItem():
            pass # Stretch is fine
        else:
            self.app_list_layout.addStretch(1)


    def _show_graph_error(self, message: str) -> None:
        """ Displays an error message overlayed on the Matplotlib graph area. """
        self.logger.error(f"Displaying graph error: {message}")
        try:
            if not hasattr(self, 'ax') or not hasattr(self, 'canvas'): return

            self.upload_line.set_data([],[])
            self.download_line.set_data([],[])
            self.upload_line.set_visible(False)
            self.download_line.set_visible(False)
            if self.ax.get_legend(): self.ax.get_legend().set_visible(False)

            error_text_color = ColorConstants.RED 
            facecolor = UIStyleConstants.DARK_MODE_BG_COLOR if self.dark_mode.isChecked() else UIStyleConstants.LIGHT_MODE_BG_COLOR
            
            if self._no_data_text_obj: # Reuse if exists
                self._no_data_text_obj.set_text(message)
                self._no_data_text_obj.set_color(error_text_color)
                self._no_data_text_obj.set_visible(True)
            else:
                self._no_data_text_obj = self.ax.text(0.5, 0.5, message,
                            ha='center', va='center', transform=self.ax.transAxes,
                            color=error_text_color, fontsize=GraphConstants.ERROR_MESSAGE_FONTSIZE, visible=True)
            
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.ax.set_xlabel("")
            self.ax.set_ylabel("")
            for spine in self.ax.spines.values(): spine.set_visible(False)
            
            self.ax.set_facecolor(facecolor)
            self.figure.patch.set_facecolor(facecolor)
            self.canvas.draw_idle()
        except Exception as e_draw:
            self.logger.error(f"Failed to display graph error message on canvas: {e_draw}", exc_info=True)
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
                    self.update_graph(self._current_data if self._current_data else [])
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

            graph_bg = UIStyleConstants.GRAPH_BG_DARK if is_dark else UIStyleConstants.GRAPH_BG_LIGHT
            text_color = UIStyleConstants.DARK_MODE_TEXT_COLOR if is_dark else UIStyleConstants.LIGHT_MODE_TEXT_COLOR
            grid_color = getattr(UIStyleConstants, 'GRID_COLOR_DARK', '#444444') if is_dark else getattr(UIStyleConstants, 'GRID_COLOR_LIGHT', '#CCCCCC')

            self.figure.patch.set_facecolor(graph_bg)
            self.ax.set_facecolor(graph_bg)
            self.ax.xaxis.label.set_color(text_color)
            self.ax.yaxis.label.set_color(text_color)
            self.ax.tick_params(colors=text_color) # Styles tick numbers and lines
            
            # Grid styling
            self.ax.grid(True, linestyle=GraphConstants.GRID_LINESTYLE, alpha=GraphConstants.GRID_ALPHA, color=grid_color)
            
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
            self.stats_bar.setStyleSheet(UIStyleConstants.STATS_DARK_STYLE if is_dark else UIStyleConstants.STATS_LIGHT_STYLE)
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
        if hasattr(self, 'dark_mode') and self.dark_mode:
            self.dark_mode.toggled.connect(self.toggle_dark_mode)
        if hasattr(self, 'realtime') and self.realtime:
            self.realtime.toggled.connect(self.toggle_live_update)
        
        if hasattr(self, 'history_period') and self.history_period:
            self.history_period.sliderReleased.connect(
                lambda: self._save_slider_value_to_config('history_period_slider_value', self.history_period.value())
            )
            self.history_period.valueChanged.connect(lambda v: self.update_history_period(v, False))
            self.history_period.valueChanged.connect(self._update_history_period_text)
        
        if hasattr(self, 'keep_data') and self.keep_data:
            self.keep_data.sliderReleased.connect(
                lambda: self._save_slider_value_to_config('keep_data_slider_value', self.keep_data.value())
            )
            self.keep_data.valueChanged.connect(lambda v: self.update_keep_data(v, False))
            self.keep_data.valueChanged.connect(self._update_keep_data_text)

        # Set initial text values for sliders
        if hasattr(self, 'history_period'):
            self._update_history_period_text(self.history_period.value())
        if hasattr(self, 'keep_data'):
            self._update_keep_data_text(self.keep_data.value())

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
                hamburger_x = tab_content_pos_in_graph_window.x() + container_width - self.hamburger_icon.width() - GraphConstants.HAMBURGER_ICON_OFFSET_X
                hamburger_y = tab_content_pos_in_graph_window.y() + GraphConstants.HAMBURGER_ICON_OFFSET_Y
                self.hamburger_icon.move(hamburger_x, hamburger_y)

            # Reposition stats bar (top-center of tab content)
            # Make the stats bar always fill the width of the graph area (minus a small margin)
            margin = 16
            stats_bar_width = container_width - margin
            self.stats_bar.setFixedWidth(stats_bar_width)
            stats_bar_x = tab_content_pos_in_graph_window.x() + (container_width - stats_bar_width) // 2
            stats_bar_y = tab_content_pos_in_graph_window.y() + GraphConstants.STATS_BAR_OFFSET_Y
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
                self._realtime_timer.start(GraphConstants.REALTIME_UPDATE_INTERVAL_MS)
                self.logger.info("Live updates enabled.")
                if self._parent and self._parent.widget_state: # Trigger immediate update
                    if self.tab_widget.currentIndex() == 0:
                        self.update_graph(self._parent.widget_state.get_speed_history())
                    elif self.tab_widget.currentIndex() == 1:
                        self._update_app_usage()
            else:
                self._realtime_timer.stop()
                self.logger.info("Live updates disabled.")

        except Exception as e:
            self.logger.error(f"Error toggling live update: {e}", exc_info=True)

    def _update_realtime(self) -> None:
        """ Slot for _realtime_timer. Fetches latest data and updates the active view. """
        if self._is_closing or not self.isVisible() or not self.realtime.isChecked():
            if not self.realtime.isChecked() and self._realtime_timer.isActive():
                self._realtime_timer.stop() # Ensure timer stops if toggle is off
            return
        
        try:
            if not self._parent or not self._parent.widget_state:
                self.logger.warning("Parent or widget_state missing for real-time update.")
                return

            if self.tab_widget.currentIndex() == 0: # Graph tab
                history = self._parent.widget_state.get_speed_history() # Consider get_latest_points if available
                # self.logger.debug(f"Real-time update: fetched {len(history)} speed history records for graph")
                self.update_graph(history)
            elif self.tab_widget.currentIndex() == 1: # App Usage tab
                # App usage might not need 1-sec updates unless data changes that fast.
                # Consider a separate, slower timer or update less frequently for app usage.
                # For now, updating it as per original logic.
                self._update_app_usage()
        except Exception as e:
            self.logger.error(f"Error in real-time update: {e}", exc_info=True)            # self._realtime_timer.stop() # Potentially stop if error is critical
            # QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.GRAPH_ERROR_MESSAGE.format(error=str(e)))

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
                period = HistoryPeriodConstants.PERIOD_MAP.get(value, HistoryPeriodConstants.DEFAULT_PERIOD)
                self.history_period.setValueText(period)
                # Do NOT set any title or timeline label on the graph
                if self.tab_widget.currentIndex() == 0 and hasattr(self, 'ax'):
                    self.ax.set_title("")
                    if hasattr(self, 'canvas'):
                        self.canvas.draw_idle()
        except Exception as e:
            self.logger.error(f"Error updating history period text: {e}", exc_info=True)

    def update_history_period(self, value: int, initial_setup: bool = False) -> None:
        """Update the graph and stats for the selected history period."""
        try:
            if not self._parent or not self._parent.widget_state:
                return
            history_data = self._parent.widget_state.get_speed_history()
            now = datetime.now()
            period = HistoryPeriodConstants.PERIOD_MAP.get(value, HistoryPeriodConstants.DEFAULT_PERIOD)
            # Filtering logic for each period
            if "3 Hours" in period:
                cutoff = now - timedelta(hours=3)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "6 Hours" in period:
                cutoff = now - timedelta(hours=6)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "12 Hours" in period:
                cutoff = now - timedelta(hours=12)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "24 Hours" in period or "Day" in period:
                cutoff = now - timedelta(days=1)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "Week" in period:
                cutoff = now - timedelta(weeks=1)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "Month" in period and "3" not in period and "6" not in period:
                cutoff = now - timedelta(days=30)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "3 Months" in period:
                cutoff = now - timedelta(days=90)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "6 Months" in period:
                cutoff = now - timedelta(days=180)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "Year" in period:
                cutoff = now - timedelta(days=365)
                filtered_history = [entry for entry in history_data if entry[0] >= cutoff]
                start_time = cutoff
            elif "Session" in period:
                start_time = self.app_start_time
                filtered_history = [entry for entry in history_data if entry[0] >= start_time]
            elif "System Uptime" in period:
                if history_data:
                    start_time = min(entry[0] for entry in history_data)
                else:
                    start_time = now - timedelta(hours=1)
                filtered_history = history_data
            else:
                # Default: show all data
                filtered_history = history_data
                start_time = min(entry[0] for entry in history_data) if history_data else now - timedelta(hours=1)
            # Update stats bar and graph with filtered data
            self._update_stats_bar(filtered_history, start_time)
            self.update_graph(filtered_history, xlim=(start_time, now))
            # Remove any timeline label/title if it was set by matplotlib
            if hasattr(self, 'ax') and self.ax.get_title():
                self.ax.set_title("")
                if hasattr(self, 'canvas'):
                    self.canvas.draw_idle()
            self.logger.debug(f"History period updated to: {period}")
        except Exception as e:
            self.logger.error(f"Error updating history period (timeline label removal): {e}", exc_info=True)

    def export_history(self) -> None:
        """Export network speed history to CSV."""
        if self._is_closing: return
        try:
            if not self._parent or not self._parent.widget_state:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, "Cannot access data for export.")
                return

            history = self._parent.widget_state.get_speed_history()
            if not history:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, self.i18n.NO_HISTORY_DATA_MESSAGE)
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = ExportConstants.CSV_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            file_path, _ = QFileDialog.getSaveFileName(
                self, self.i18n.EXPORT_CSV_TITLE, os.path.join(ExportConstants.DEFAULT_EXPORT_PATH, suggested_name), self.i18n.CSV_FILE_FILTER
            )
            if file_path:
                os.makedirs(os.path.dirname(file_path), exist_ok=True) # Ensure dir exists
                with open(file_path, "w", newline="", encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "Upload (Kbps)", "Download (Kbps)", "Interface"])
                    for entry in history:
                        writer.writerow([
                            entry.timestamp.isoformat(),
                            entry.upload,
                            entry.download,
                            entry.interface
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
            suggested_name = ExportConstants.IMAGE_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            file_path, _ = QFileDialog.getSaveFileName(
                self, self.i18n.EXPORT_GRAPH_IMAGE_TITLE, os.path.join(ExportConstants.DEFAULT_EXPORT_PATH, suggested_name), self.i18n.PNG_FILE_FILTER
            )
            if file_path:
                os.makedirs(os.path.dirname(file_path), exist_ok=True) # Ensure dir exists
                # Use figure's current facecolor for saved image background
                self.figure.savefig(file_path, bbox_inches='tight', dpi=ExportConstants.IMAGE_DPI, facecolor=self.figure.get_facecolor())
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
        """Handle window closure by hiding the window instead of closing it completely."""
        try:
            # Save current window position and settings
            if self._parent and hasattr(self._parent, "config_manager") and self._parent.config:
                self._parent.config["graph_window_pos"] = {"x": self.pos().x(), "y": self.pos().y()}
                self._parent.config["dark_mode"] = self.dark_mode.isChecked()
                self._parent.config["keep_data"] = DataRetentionConstants.DAYS_MAP.get(self.keep_data.value(), 30)
                self._parent.config["history_period"] = HistoryPeriodConstants.PERIOD_MAP.get(self.history_period.value(), HistoryPeriodConstants.DEFAULT_PERIOD)
                self._parent.config_manager.save(self._parent.config)

            # If this is part of application shutdown, do full cleanup
            if getattr(self, '_is_closing', False):
                self.logger.debug("Performing full cleanup for graph window...")
                # Stop all timers
                self._realtime_timer.stop()
                self._update_timer.stop()
                self._db_size_update_timer.stop()
                
                # Clean up matplotlib resources
                plt.close(self.figure)
                self.canvas.deleteLater()
                
                if self._parent:
                    self._parent.graph_window = None
                    
                event.accept()
            else:
                # Just hide the window and keep it alive
                self.logger.debug("Hiding graph window instead of closing")
                self.hide()
                event.ignore()  # This keeps the window alive
        except Exception as e:
            self.logger.error(f"Error in closeEvent: {e}", exc_info=True)            
            event.ignore()  # On error, prefer to keep the window

    def _get_db_size_mb(self) -> float:
        """Get the size of the database file in megabytes."""
        try:
            if hasattr(self._parent, "widget_state") and hasattr(self._parent.widget_state, "db_path"):
                db_path = self._parent.widget_state.db_path
                if os.path.exists(db_path):
                    size_bytes = os.path.getsize(db_path)
                    return size_bytes / (1024 * 1024)  # Convert to MB
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting DB size: {e}", exc_info=True)
            return 0.0

    def _update_db_size(self) -> None:
        """Update the database size display."""
        try:
            if hasattr(self, 'keep_data'):
                days = DataRetentionConstants.DAYS_MAP.get(self.keep_data.value(), 30)
                if days == 365:  # Only update if showing 1 year
                    db_size_mb = self._get_db_size_mb()
                    self.keep_data.setValueText(f"1 Year (DB {db_size_mb:.2f}MB)")
        except Exception as e:
            self.logger.error(f"Error updating keep data label: {e}", exc_info=True)    
    
    def _init_db_size_timer(self) -> None:
        """Initialize DB size update timer and immediately update the display."""
        if not hasattr(self, "_db_size_update_timer"):
            self._db_size_update_timer = QTimer(self)
            self._db_size_update_timer.timeout.connect(self._update_db_size)
            self._db_size_update_timer.setInterval(10000)  # 10 second update interval
        
        # Start timer and do initial update if using 1-year retention
        if hasattr(self, 'keep_data') and self.keep_data.value() in DataRetentionConstants.DAYS_MAP:
            days = DataRetentionConstants.DAYS_MAP[self.keep_data.value()]
            if days == 365:
                self._update_db_size()  # Update size immediately
                if not self._db_size_update_timer.isActive():
                    self._db_size_update_timer.start()    
        
    def update_keep_data(self, value: int, initial_setup: bool = False) -> None:
        """Update the data retention period based on slider.

        Args:
            value: Slider value mapping to retention days.
            initial_setup: True if called during init.
        """
        if self._is_closing:
            return

        try:
            days = DataRetentionConstants.DAYS_MAP.get(value, DataRetentionConstants.DAYS_MAP[3])  # Default to 30 days
            self._update_keep_data_text(value)  # Update label text first

            if not initial_setup:
                if not self._parent or not self._parent.config or not self._parent.widget_state or not hasattr(self._parent, 'config_manager'):
                    self.logger.warning("Cannot update keep_data: parent/config/widget_state missing.")
                    return

                # Update config values
                self._parent.config["keep_data"] = days
                history_minutes = days * 24 * 60  # History minutes based on retention days
                self._parent.config["history_minutes"] = history_minutes

                # Save config first to persist the change
                self._parent.config_manager.save(self._parent.config)
                self.logger.debug(f"Data retention updated to {days} days and saved")

                # Apply the updated config which will trigger data pruning
                self._parent.widget_state.apply_config(self._parent.config)

                # Update timers and DB size display
                if days == DataRetentionConstants.MAX_RETENTION_DAYS:  # 1 year
                    self._update_db_size()  # Update size immediately
                    if not hasattr(self, '_db_size_update_timer') or not self._db_size_update_timer.isActive():
                        self._init_db_size_timer()
                        self._db_size_update_timer.start()
                elif hasattr(self, '_db_size_update_timer') and self._db_size_update_timer.isActive():
                    self._db_size_update_timer.stop()

                # Refresh views since data might have been pruned
                self._cached_stats.clear()  # Clear stats cache
                if self.tab_widget.currentWidget() == self.graph_widget:
                    if self._parent and self._parent.widget_state:
                        self.update_graph(self._parent.widget_state.get_speed_history())
                elif self.tab_widget.currentWidget() == self.app_usage_widget:
                    self._update_app_usage()

        except Exception as e:
            self.logger.error(f"Error in update_keep_data: {e}", exc_info=True)

    def _update_keep_data_text(self, value: int) -> None:
        """Update the keep data slider's value text."""
        if hasattr(self, 'keep_data'):
            days = DataRetentionConstants.DAYS_MAP.get(value, DataRetentionConstants.DAYS_MAP[3])
            # For 1-year retention, include DB size
            if days == 365:
                db_size_mb = self._get_db_size_mb()
                self.keep_data.setValueText(f"1 Year (DB {db_size_mb:.2f}MB)")
                # Start the DB size update timer if not already running
                if not self._db_size_update_timer.isActive():
                    self._db_size_update_timer.start()
            else:
                # For other periods, just show days
                self.keep_data.setValueText(f"{days} Days")
                # Stop the DB size update timer if running
                if self._db_size_update_timer.isActive():
                    self._db_size_update_timer.stop()


    def _days_to_slider_value(self, days: int) -> int:
        """Convert a number of days to the corresponding slider value (0-6).
        
        Args:
            days: The number of days to convert.
            
        Returns:
            The slider value (0-6) that corresponds to the given number of days.
        """
        for slider_value, mapped_days in DataRetentionConstants.DAYS_MAP.items():
            if mapped_days == days:
                return slider_value
        # If not found, default to 30 days (slider value 3)
        return 3
    

    def _convert_speed(self, value_kbps: float) -> float:
        """Convert Kbps to Mbps or MBps depending on config."""
        if self._parent and self._parent.config.get("use_megabytes", False):
            return value_kbps / 8000.0  # 1 MBps = 8000 Kbps
        else:
            return value_kbps / 1000.0  # 1 Mbps = 1000 Kbps

    def _get_speed_unit(self) -> str:
        if self._parent and self._parent.config.get("use_megabytes", False):
            return "MB/s"
        else:
            return "Mbps"

    def update_graph(self, history_data: List, xlim: Optional[Tuple[datetime, datetime]] = None) -> None:
        """
        Schedules an update for the graph with new speed history data.
        Assumes upload/download are already in Kbps (no conversion applied).
        """
        if self._is_closing:
            return

        if not history_data:
            no_data_msg = getattr(self.i18n, 'NO_DATA_MESSAGE', "No data to display.")
            self._show_graph_error(no_data_msg)
            # Even if no data, still update the graph to show empty timeline
            self._last_history = []
            self._graph_update_pending = True
            self._update_timer.singleShot(
                GraphConstants.GRAPH_UPDATE_THROTTLE_MS,
                lambda: self._perform_graph_update([], xlim)
            )
            return

        # Convert upload/download from bytes/sec to Kbps before plotting or calculating stats
        def convert_entry(entry):
            ts, up_bps, down_bps, iface = entry
            up_kbps = up_bps * 8 / 1000.0
            down_kbps = down_bps * 8 / 1000.0
            return (ts, up_kbps, down_kbps, iface)

        converted = [convert_entry(e) for e in history_data]

        self._last_history = converted

        # Validate format
        first_item = converted[0]
        if not (isinstance(first_item, tuple) and len(first_item) == 4 and
                isinstance(first_item[0], datetime) and
                isinstance(first_item[1], (int, float)) and
                isinstance(first_item[2], (int, float)) and
                isinstance(first_item[3], str)):
            self.logger.error(f"Invalid data format in history_data. First item: {first_item}")
            error_msg = getattr(self.i18n, 'GRAPH_INVALID_DATA_FORMAT', "Invalid data format for graph.")
            self._show_graph_error(error_msg)
            return

        if not self._parent.config.get("graph_enabled", True):
            self.logger.warning("Graph is disabled in config. Update might be for internal/debug reasons.")

        if self._graph_update_pending:
            return

        self._graph_update_pending = True
        self._update_timer.singleShot(
            GraphConstants.GRAPH_UPDATE_THROTTLE_MS,
            lambda: self._perform_graph_update(converted, xlim)
        )

    def _perform_graph_update(self, history_data: List[Tuple[datetime, float, float, str]], xlim: Optional[Tuple[datetime, datetime]] = None) -> None:
        """
        Performs the actual rendering of the graph with the provided speed history data.
        """
        try:
            self._graph_update_pending = False 
            if self._is_closing or not self.isVisible():
                return

            unit_suffix = self._get_speed_unit()
            now = datetime.now()
            period_value = 0
            if hasattr(self, 'history_period') and self.history_period:
                period_value = self.history_period.value()
            period_str = HistoryPeriodConstants.PERIOD_MAP.get(period_value, HistoryPeriodConstants.DEFAULT_PERIOD)
            
            start_time: datetime

            if period_str in ("System Uptime", "All"):
                start_time = min(entry[0] for entry in history_data) if history_data else now - timedelta(hours=1)
            elif "3 Hours" in period_str:
                start_time = now - timedelta(hours=3)
            elif "6 Hours" in period_str:
                start_time = now - timedelta(hours=6)
            elif "12 Hours" in period_str:
                start_time = now - timedelta(hours=12)
            elif "24 Hours" in period_str or "Day" in period_str:
                start_time = now - timedelta(days=1)
            elif "Week" in period_str:
                start_time = now - timedelta(weeks=1)
            elif "Month" in period_str and "3" not in period_str and "6" not in period_str:
                start_time = now - timedelta(days=30)
            elif "3 Months" in period_str:
                start_time = now - timedelta(days=90)
            elif "6 Months" in period_str:
                start_time = now - timedelta(days=180)
            elif "Year" in period_str:
                start_time = now - timedelta(days=365)
            elif "Session" in period_str:
                start_time = self.app_start_time
            else:
                # This fallback should now rarely be used.
                start_time = now - timedelta(hours=1)

            # Update the stats bar with the correct period data
            self._update_stats_bar(history_data, start_time)

            # Filter data for the selected time period
            filtered_data = [(ts, up, down, iface) for ts, up, down, iface in history_data if ts >= start_time]

            if xlim is not None:
                x_start, x_end = xlim
            else:
                x_start, x_end = start_time, now

            if not filtered_data:
                self.upload_line.set_data([], [])
                self.download_line.set_data([], [])
                self.upload_line.set_visible(True)
                self.download_line.set_visible(True)
                self.ax.set_xlim(x_start, x_end)
                self.ax.set_ylim(0, GraphConstants.MIN_Y_AXIS_LIMIT)
                self._show_graph_error(getattr(self.i18n, 'NO_DATA_MESSAGE', "No data to display."))
            else:
                timestamps = [entry[0] for entry in filtered_data]
                upload_speeds = [self._convert_speed(entry[1]) for entry in filtered_data]
                download_speeds = [self._convert_speed(entry[2]) for entry in filtered_data]

                self.upload_line.set_data(timestamps, upload_speeds)
                self.download_line.set_data(timestamps, download_speeds)
                self.upload_line.set_visible(True)
                self.download_line.set_visible(True)
                self.ax.set_xlim(x_start, x_end)
                max_speed = max(max(upload_speeds, default=1), max(download_speeds, default=1))
                self.ax.set_ylim(0, max_speed * 1.1 if max_speed > 0 else GraphConstants.MIN_Y_AXIS_LIMIT)

            self.ax.set_ylabel(self._get_speed_unit())
            self.ax.set_title("")
            
            total_seconds = (x_end - x_start).total_seconds()
            if ("Hour" in period_str) or ("Session" in period_str) or (total_seconds <= 24*3600):
                self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                self.ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
            else:
                self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
                self.ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))
            
            self.figure.autofmt_xdate(rotation=30, ha='right')

            if self.ax.get_legend():
                for text in self.ax.get_legend().get_texts():
                    text.set_color(UIStyleConstants.DARK_MODE_TEXT_COLOR if self.dark_mode.isChecked() else UIStyleConstants.LIGHT_MODE_TEXT_COLOR)

            self.canvas.draw_idle()
        except Exception as e:
            self.logger.error(f"Error updating graph: {e}", exc_info=True)
            self._show_graph_error(f"Error updating graph: {str(e)}")

    def _calculate_period_stats(self, history_data: List[Tuple[datetime, float, float, str]], start_time: datetime) -> Dict[str, float]:
        """Calculate statistics for the selected time period.
        
        Args:
            history_data: List of (timestamp, upload, download, interface) tuples
            start_time: Starting timestamp for the period to calculate
            
        Returns:
            Dictionary containing max_upload, max_download, avg_upload, avg_down
        """
        try:
            period_data = [(ts, up, down, iface) for ts, up, down, iface in history_data if ts >= start_time]
            if not period_data or len(period_data) < 2:
                return {"max_upload": 0, "max_download": 0, "avg_upload": 0, "avg_download": 0, "total_upload": 0, "total_download": 0, "total_unit": "MB"}

            uploads = [self._convert_speed(up) for _, up, _, _ in period_data]
            downloads = [self._convert_speed(down) for _, _, down, _ in period_data]

            # Calculate time deltas in seconds between samples
            time_deltas = [
                (period_data[i][0] - period_data[i-1][0]).total_seconds()
                for i in range(1, len(period_data))
            ]
            # Use the average interval if all intervals are similar, else use per-interval
            avg_interval = sum(time_deltas) / len(time_deltas) if time_deltas else 1

            # Calculate total data transferred (in Megabits or Megabytes)
            total_upload_bits = 0.0
            total_download_bits = 0.0
            for i in range(1, len(period_data)):
                up_speed = period_data[i][1]  # in Kbps
                down_speed = period_data[i][2]  # in Kbps
                dt = (period_data[i][0] - period_data[i-1][0]).total_seconds()
                # Data = speed (Kbps) * seconds / 8 (for KB) / 1000 (for MB)
                total_upload_bits += up_speed * dt
                total_download_bits += down_speed * dt

            # Convert total bits to MB or GB as appropriate
            use_megabytes = self._parent and self._parent.config.get("use_megabytes", False)
            # 1 byte = 8 bits, 1 MB = 1,000,000 bytes, 1 GB = 1,000 MB
            if use_megabytes:
                total_upload_MB = total_upload_bits / 8 / 1000  # Kbps * s / 8 / 1000 = KB / 1000 = MB
                total_download_MB = total_download_bits / 8 / 1000
                total_unit = "MB"
                if total_upload_MB > 1000 or total_download_MB > 1000:
                    total_upload_MB /= 1000
                    total_download_MB /= 1000
                    total_unit = "GB"
                total_upload = total_upload_MB
                total_download = total_download_MB
            else:
                # Default: show in Megabits (Mb) or Gigabits (Gb)
                total_upload_Mb = total_upload_bits / 1000  # Kbps * s / 1000 = Mb
                total_download_Mb = total_download_bits / 1000
                total_unit = "Mb"
                if total_upload_Mb > 1000 or total_download_Mb > 1000:
                    total_upload_Mb /= 1000
                    total_download_Mb /= 1000
                    total_unit = "Gb"
                total_upload = total_upload_Mb
                total_download = total_download_Mb

            stats = {
                "max_upload": max(uploads) if uploads else 0,
                "max_download": max(downloads) if downloads else 0,
                "avg_upload": sum(uploads) / len(uploads) if uploads else 0,
                "avg_download": sum(downloads) / len(downloads) if downloads else 0,
                "total_upload": total_upload,
                "total_download": total_download,
                "total_unit": total_unit
            }
            return stats
        except Exception as e:
            self.logger.error(f"Error calculating period stats: {e}", exc_info=True)
            return {"max_upload": 0, "max_download": 0, "total_upload": 0, "total_download": 0, "total_unit": "MB"}

    def _update_stats_bar(self, history_data: List[Tuple[datetime, float, float, str]], start_time: datetime) -> None:
        """Update the stats bar with statistics for the selected period.
        
        Args:
            history_data: List of (timestamp, upload, download, interface) tuples
            start_time: Starting timestamp for the period to calculate
        """
        try:
            if not history_data:
                self.stats_bar.setText("No data available")
                return

            stats = self._calculate_period_stats(history_data, start_time)
            unit = self._get_speed_unit()
            total_unit = stats.get('total_unit', 'MB')
            stats_text = (
                f"Max: ↑{stats['max_upload']:.2f} {unit} ↓{stats['max_download']:.2f} {unit} | "
                f"Total: ↑{stats['total_upload']:.2f} {total_unit} ↓{stats['total_download']:.2f} {total_unit}"
            )
            self.stats_bar.setText(stats_text)
            self.stats_bar.setMinimumHeight(28)
            self.stats_bar.setWordWrap(False)
            self.stats_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Always set a consistent, non-bold, no-background style for the stats bar
            is_dark = self.dark_mode.isChecked() if hasattr(self, 'dark_mode') else False
            text_color = UIStyleConstants.DARK_MODE_TEXT_COLOR if is_dark else UIStyleConstants.LIGHT_MODE_TEXT_COLOR
            self.stats_bar.setStyleSheet(f"color: {text_color}; font-size: 13px; background: none; font-weight: normal;")
            self.stats_bar.adjustSize()
        except Exception as e:
            self.logger.error(f"Error updating stats bar: {e}", exc_info=True)
            self.stats_bar.setText("Error calculating statistics")