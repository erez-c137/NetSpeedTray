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

# --- Third-Party Imports (Centralized) ---
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter, FixedLocator
import numpy as np
import pandas as pd

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
from netspeedtray.utils.position_utils import ScreenUtils
from netspeedtray.utils import styles
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils import db_utils


class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    """
    data_ready = pyqtSignal(object)
    error = pyqtSignal(str)


    def __init__(self, widget_state):
        """
        Initializes the worker.
        
        Args:
            widget_state: A direct reference to the application's WidgetState object.
        """
        super().__init__()
        self.widget_state = widget_state


    def process_data(self, start_time, end_time, interface_to_query, is_session_view):
        """The main data processing method."""
        try:
            if not self.widget_state:
                self.error.emit("Data source (WidgetState) not available.")
                return

            if is_session_view:
                # Get data from the in-memory deque for the current session
                history_data = self.widget_state.get_in_memory_speed_history()
                
                # The in-memory data is a list of SpeedDataSnapshot objects,
                # so we need to process it into the tuple format the graph expects.
                processed_history = []
                for snapshot in history_data:
                    if interface_to_query is None:
                        up = sum(s[0] for s in snapshot.speeds.values())
                        down = sum(s[1] for s in snapshot.speeds.values())
                    else:
                        up, down = snapshot.speeds.get(interface_to_query, (0.0, 0.0))
                    processed_history.append((snapshot.timestamp, up, down))
                history_data = processed_history
            else:
                # For all other timelines, get data from the database
                history_data = self.widget_state.get_speed_history(
                    start_time=start_time, end_time=end_time, interface_name=interface_to_query
                )

            if len(history_data) < 2:
                self.data_ready.emit(pd.DataFrame()) # Emit empty DataFrame for "No data" message
                return

            df = pd.DataFrame(history_data, columns=['timestamp', 'upload_speed', 'download_speed'])
            self.data_ready.emit(df)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))


class GraphWindow(QWidget):
    """
    A window for displaying network speed history and per-app bandwidth usage using PyQt6.
    (Docstring remains largely the same as provided)
    """
    request_data_processing = pyqtSignal(object, object, object, bool)

    def __init__(self, parent=None, logger=None, i18n=None, session_start_time: Optional[datetime] = None):
        """ Initialize the GraphWindow with its UI components. """
        super().__init__()
        self._parent = parent
        self.logger = logger or logging.getLogger(__name__)
        self.i18n = i18n
        self.session_start_time = session_start_time or datetime.now()
        
        # --- State variables ---
        self._is_closing = False
        self._initial_load_done = False
        self._is_dark_mode = self._parent.config.get("dark_mode", True)
        self._is_live_update_enabled = self._parent.config.get("live_update", True)
        self._history_period_value = self._parent.config.get('history_period_slider_value', 0)
        self._data_cache: Dict[Tuple[str, str], pd.DataFrame] = {}
        self._graph_data_cache = [] # Used for crosshair lookup

        # --- Setup UI and Timers ---
        self.setupUi()
        self.setWindowTitle(constants.graph.WINDOW_TITLE)
        try:
            icon_path = helpers.get_app_asset_path(constants.app.ICON_FILENAME)
            if icon_path.exists(): self.setWindowIcon(QIcon(str(icon_path)))
        except Exception as e: self.logger.error(f"Error setting window icon: {e}", exc_info=True)

        # --- Initialize Core Components in Order ---
        self._init_matplotlib()
        
        # Initialize interactive elements (crosshair, tooltip)
        self.crosshair_v_download = self.ax_download.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)
        self.crosshair_v_upload = self.ax_upload.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)
        self.tooltip = QLabel(self.canvas)
        self.tooltip.setObjectName("graphTooltip")
        self.tooltip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.tooltip.setVisible(False)
        self.tooltip.setStyleSheet(style_utils.graph_tooltip_style())

        # Initialize overlay elements (stats bar, hamburger menu)
        self._init_overlay_elements()

        # Final assembly of the visual layout
        self.graph_layout.addWidget(self.stats_bar)
        self.graph_layout.addWidget(self.canvas)

        # Now that the UI is fully built, initialize the background worker
        self._init_worker_thread(parent.widget_state if parent else None)
        
        # Finally, connect all signals
        self._connect_signals()


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
        self.interface_filter = None
        self.settings_widget = None
        self.history_period_label = None
        self.keep_data_label = None
        self.dark_mode = None
        self.realtime = None
        self.history_period = None
        self.keep_data = None

        # Timers and state
        self._realtime_timer = QTimer(self)
        self._db_size_update_timer = QTimer(self)
        self._no_data_text_obj = None
        self._current_date_formatter_type = None

        # Call window positioning after UI is set up
        self._position_window()


    def _init_matplotlib(self):
        """ Initialize matplotlib with a dual-axis, stacked subplot layout. """
        try:
            # These imports are now needed here for the subplots call
            from PyQt6.QtWidgets import QSizePolicy
            import matplotlib.pyplot as plt

            fig_size = getattr(constants.graph, 'FIGURE_SIZE', (8, 6))

            # sharex=True is CRITICAL. It links the X-axis (time) of both plots,
            # so zooming/panning on one automatically updates the other.
            # gridspec_kw controls the height ratio; we give slightly more to download.
            self.figure, self.axes = plt.subplots(
                2, 1,
                sharex=True,
                tight_layout=True,
                figsize=fig_size,
                gridspec_kw={'height_ratios': [3, 2]} # e.g., Download gets 60%, Upload 40%
            )

            # Assign the axes to clear, named attributes
            self.ax_download, self.ax_upload = self.axes

            self.canvas = FigureCanvas(self.figure)
            self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

            # Configure the new axes
            self.ax_download.set_ylabel(self.i18n.DOWNLOAD_LABEL)
            self.ax_upload.set_ylabel(self.i18n.UPLOAD_LABEL)

            # We no longer use self.upload_line and self.download_line directly for plotting,
            # but we can keep them for legend purposes if needed. For now, let's clear them.
            self.upload_line = None
            self.download_line = None

            # Hide the X-axis labels and ticks on the top (download) plot to avoid clutter
            self.ax_download.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            self.ax_upload.set_xlabel(self.i18n.TIME_LABEL)

            # Configure grids for both plots
            for ax in self.axes:
                ax.grid(True, linestyle=getattr(constants.graph, 'GRID_LINESTYLE', '--'), alpha=getattr(constants.graph, 'GRID_ALPHA', 0.5))

            self._no_data_text_obj = None

        except Exception as e:
            self.logger.error(f"Error initializing matplotlib: {e}", exc_info=True)


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


    def _plot_continuous_segments(self, axis, timestamps, speeds, color, label):
        """
        Plots data on a given axis, splitting it into continuous segments
        to create clean visual gaps where data is missing (NaN).
        """
        # Clear any previous lines from this axis to prevent over-drawing
        axis.clear() # This is a simple but effective way to reset for a full refresh

        df = pd.DataFrame({'timestamp': timestamps, 'speed': speeds})
        
        # Find the indices where a gap starts (i.e., the value is NaN)
        nan_indices = df.index[df['speed'].isna()]
        
        start_index = 0
        for end_index in nan_indices:
            # Plot the segment before the gap
            segment = df.iloc[start_index:end_index]
            if not segment.empty:
                axis.plot(segment['timestamp'], segment['speed'], color=color, linewidth=constants.graph.LINE_WIDTH)
            start_index = end_index + 1
            
        # Plot the final segment after the last gap (or the whole thing if no gaps)
        final_segment = df.iloc[start_index:]
        if not final_segment.empty:
            # Add the label only to the last segment to avoid duplicates in the legend
            axis.plot(final_segment['timestamp'], final_segment['speed'], color=color, linewidth=constants.graph.LINE_WIDTH, label=label)


    def _toggle_settings_panel_visibility(self):
        """
        Shows or hides the settings panel, ensuring it is perfectly aligned with the stats bar.
        """
        try:
            if self.settings_widget is None:
                self.logger.debug("First use: Creating settings panel shell.")
                self.settings_widget = QWidget(self)
                self.settings_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self.settings_widget.hide()
                QTimer.singleShot(100, self._populate_settings_panel)

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
        Creates and lays out the widgets inside the settings panel.
        This is called by a QTimer to avoid blocking the UI on first open.
        """
        if not self.settings_widget:
            self.logger.error("Attempted to populate a non-existent settings panel.")
            return
            
        self.logger.debug("Populating settings panel widgets.")
        try:
            self._init_settings_panel() # This now contains the widget creation logic
            self._connect_settings_signals()
            self._populate_interface_filter()
            # Ensure the populated widget is visible if the panel is still open
            if self.settings_widget.isVisible():
                self.settings_widget.show()
        except Exception as e:
            self.logger.error(f"Error populating settings panel: {e}", exc_info=True)


    def _init_settings_panel(self):
        """Initializes the widgets within the settings panel container."""
        from netspeedtray.utils.components import Win11Slider, Win11Toggle
        from PyQt6.QtWidgets import QSlider

        self.settings_widget.setObjectName("settingsPanel")

        settings_main_layout = QGridLayout(self.settings_widget)
        settings_main_layout.setContentsMargins(15, 15, 15, 15)
        settings_main_layout.setVerticalSpacing(15)
        settings_main_layout.setHorizontalSpacing(10)

        title_label = QLabel(getattr(self.i18n, 'GRAPH_SETTINGS_LABEL', 'Graph Settings'))
        title_label.setObjectName("settingsTitleLabel")
        settings_main_layout.addWidget(title_label, 0, 0, 1, 2)

        controls_container = QWidget()
        controls_container.setObjectName("controlsContainer")
        group_content_layout = QGridLayout(controls_container)
        group_content_layout.setContentsMargins(15, 15, 15, 15)
        group_content_layout.setVerticalSpacing(15)
        group_content_layout.setHorizontalSpacing(10)
        
        current_row = 0

        interface_label = QLabel(self.i18n.INTERFACE_LABEL)
        self.interface_filter = QComboBox()
        self.interface_filter.addItem(self.i18n.ALL_INTERFACES_AGGREGATED_LABEL, "all")
        group_content_layout.addWidget(interface_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.interface_filter, current_row, 0, 1, 2)
        current_row += 1

        self.history_period_label = QLabel(getattr(self.i18n, 'HISTORY_PERIOD_LABEL_NO_VALUE', 'Timeline'))
        self.history_period = Win11Slider(value=self._history_period_value)
        if hasattr(self.history_period, 'slider'):
            self.history_period.slider.setMinimum(0)
            self.history_period.slider.setMaximum(len(constants.data.history_period.PERIOD_MAP) - 1)
        group_content_layout.addWidget(self.history_period_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.history_period, current_row, 0, 1, 2)
        current_row += 1

        self.keep_data_label = QLabel(getattr(self.i18n, 'DATA_RETENTION_LABEL_NO_VALUE', 'Data Retention'))
        initial_slider_value = self._days_to_slider_value(self._parent.config.get("keep_data", 30))
        self.keep_data = Win11Slider(value=initial_slider_value)
        if hasattr(self.keep_data, 'slider'):
            self.keep_data.slider.setMinimum(0)
            self.keep_data.slider.setMaximum(len(constants.data.retention.DAYS_MAP) - 1)
        group_content_layout.addWidget(self.keep_data_label, current_row, 0, 1, 2)
        current_row += 1
        group_content_layout.addWidget(self.keep_data, current_row, 0, 1, 2)
        current_row += 1

        dm_label = QLabel(getattr(self.i18n, 'DARK_MODE_LABEL', 'Dark Mode'))
        self.dark_mode = Win11Toggle(initial_state=self._is_dark_mode)
        group_content_layout.addWidget(dm_label, current_row, 0)
        group_content_layout.addWidget(self.dark_mode, current_row, 1, Qt.AlignmentFlag.AlignLeft)
        current_row += 1
        
        lu_label = QLabel(getattr(self.i18n, 'LIVE_UPDATE_LABEL', 'Live Update'))
        self.realtime = Win11Toggle(initial_state=self._is_live_update_enabled)
        group_content_layout.addWidget(lu_label, current_row, 0)
        group_content_layout.addWidget(self.realtime, current_row, 1, Qt.AlignmentFlag.AlignLeft)
        current_row += 1

        # --- Show Legend Toggle ---
        legend_label = QLabel(getattr(self.i18n, 'SHOW_LEGEND_LABEL', 'Show Legend'))
        self.show_legend = Win11Toggle(initial_state=self._parent.config.get("show_legend", True))
        group_content_layout.addWidget(legend_label, current_row, 0)
        group_content_layout.addWidget(self.show_legend, current_row, 1, Qt.AlignmentFlag.AlignLeft)

        settings_main_layout.addWidget(controls_container, 1, 0, 1, 2)
        settings_main_layout.setRowStretch(2, 1)

        self.settings_widget.setStyleSheet(styles.graph_settings_panel_style())


    def _init_worker_thread(self, widget_state):
        """Initializes the background worker thread for data processing."""
        self.worker_thread = QThread()
        self.data_worker = GraphDataWorker(widget_state)
        self.data_worker.moveToThread(self.worker_thread)
        
        self.data_worker.data_ready.connect(self._on_data_ready)
        self.data_worker.error.connect(self._show_graph_error)
        
        self.request_data_processing.connect(self.data_worker.process_data)
        
        self.worker_thread.start()


    def _on_data_ready(self, df: pd.DataFrame):
        """Slot to receive processed data from the worker and render it."""
        if self._is_closing: return
        if df.empty or len(df) < 2:
            self._show_graph_message(self.i18n.NO_DATA_MESSAGE, is_error=False)
            return
        
        # Call the new, dedicated rendering function
        self._render_graph(df)


    def _apply_theme(self):
        """
        The single, authoritative function to apply the current theme to all axes.
        Called AFTER all plotting to prevent race conditions.
        """
        is_dark = self._is_dark_mode
        graph_bg = style_constants.GRAPH_BG_DARK if is_dark else style_constants.GRAPH_BG_LIGHT
        grid_color = style_constants.GRID_COLOR_DARK if is_dark else style_constants.GRID_COLOR_LIGHT
        text_color = style_constants.DARK_MODE_TEXT_COLOR if is_dark else style_constants.LIGHT_MODE_TEXT_COLOR
        
        self.figure.patch.set_facecolor(graph_bg)
        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            
            # Set the color for ALL text elements on the axis at once
            ax.xaxis.label.set_color(text_color)
            ax.yaxis.label.set_color(text_color)
            ax.tick_params(axis='x', colors=text_color)
            ax.tick_params(axis='y', colors=text_color)
            
            for spine in ax.spines.values():
                spine.set_color(grid_color)
            ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=grid_color)
            
            # Re-apply the number formatter every time the theme is applied
            ax.yaxis.set_major_formatter(ScalarFormatter())
            ax.yaxis.get_major_formatter().set_scientific(False)

        self.ax_upload.spines['top'].set_visible(True)


    def _on_mouse_move(self, event):
        """Handles mouse movement over the canvas to display a synchronized crosshair and tooltip."""
        if not event.inaxes or not self._graph_data_cache:
            self._on_mouse_leave(event)
            return

        mouse_timestamp = event.xdata
        
        # This is a highly optimized way to find the closest data point
        timestamps = [p[0] for p in self._graph_data_cache]
        dt_mouse_timestamp = datetime.fromtimestamp(plt.dates.num2date(mouse_timestamp).timestamp())
        
        index = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - dt_mouse_timestamp))
        
        data_point = self._graph_data_cache[index]
        timestamp, upload_bps, download_bps = data_point

        self.crosshair_v_download.set_xdata([timestamp])
        self.crosshair_v_upload.set_xdata([timestamp])
        self.crosshair_v_download.setVisible(True)
        self.crosshair_v_upload.setVisible(True)

        download_mbps = (download_bps * 8) / 1_000_000
        upload_mbps = (upload_bps * 8) / 1_000_000
        
        tooltip_text = (
            f"<div style='font-weight: bold;'>{timestamp.strftime('%Y-%m-%d %H:%M:%S')}</div>"
            f"<div style='color: {constants.graph.DOWNLOAD_LINE_COLOR};'>↓ {download_mbps:.2f} Mbps</div>"
            f"<div style='color: {constants.graph.UPLOAD_LINE_COLOR};'>↑ {upload_mbps:.2f} Mbps</div>"
        )
        self.tooltip.setText(tooltip_text)
        self.tooltip.adjustSize()
        
        mouse_pos = self.canvas.mapFromGlobal(self.cursor().pos())
        tooltip_x = mouse_pos.x() + 15
        tooltip_y = mouse_pos.y() - self.tooltip.height() - 15

        if tooltip_x + self.tooltip.width() > self.canvas.width():
            tooltip_x = mouse_pos.x() - self.tooltip.width() - 15
        if tooltip_y < 0:
            tooltip_y = mouse_pos.y() + 15

        self.tooltip.move(tooltip_x, tooltip_y)
        self.tooltip.setVisible(True)
        
        self.canvas.draw_idle()

    def _on_mouse_leave(self, event):
        """Hides the crosshair and tooltip when the mouse leaves the axes."""
        if self.crosshair_v_download.get_visible():
            self.crosshair_v_download.setVisible(False)
            self.crosshair_v_upload.setVisible(False)
            self.tooltip.setVisible(False)
            self.canvas.draw_idle()

    def _on_legend_pick(self, event):
        """Handles clicking on a legend item to toggle its visibility."""
        legend = event.artist
        is_visible = legend.get_visible()
        legend.set_visible(not is_visible)
        
        # Find the corresponding line/fill plot and toggle its visibility
        for line in self.ax_download.lines + self.ax_download.collections:
            if line.get_label() == legend.get_label():
                line.set_visible(not is_visible)
        for line in self.ax_upload.lines + self.ax_upload.collections:
            if line.get_label() == legend.get_label():
                line.set_visible(not is_visible)
        
        self.canvas.draw_idle()


    def _populate_interface_filter(self) -> None:
        """
        Populates the interface filter QComboBox using the unified list from the parent widget.
        """
        if self.interface_filter is None:
            return

        self.logger.debug("Populating interface filter...")
        try:
            if not self._parent or not hasattr(self._parent, 'get_unified_interface_list'):
                self.logger.warning("Cannot populate interfaces: parent or required method missing.")
                return

            self.interface_filter.blockSignals(True)
            
            current_selection = self.interface_filter.currentText()
            self.interface_filter.clear()
            
            self.interface_filter.addItem(self.i18n.ALL_INTERFACES_AGGREGATED_LABEL, "all")
            
            # Call the new unified method on the parent widget.
            distinct_interfaces = self._parent.get_unified_interface_list()
            
            if distinct_interfaces:
                self.interface_filter.addItems(sorted(distinct_interfaces))
            
            index = self.interface_filter.findText(current_selection)
            if index != -1:
                self.interface_filter.setCurrentIndex(index)
            
            self.logger.info("Interface filter populated with %d interfaces.", len(distinct_interfaces))

        except Exception as e:
            self.logger.error("Failed to populate interface filter: %s", e, exc_info=True)
            if self.interface_filter.count() == 0:
                self.interface_filter.addItem("All (Aggregated)")
        finally:
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
        """
        import psutil

        now = datetime.now()
        period_value = self._history_period_value
        period_key = constants.data.history_period.PERIOD_MAP.get(period_value, constants.data.history_period.DEFAULT_PERIOD)
        
        start_time: Optional[datetime] = None
        if period_key == "TIMELINE_SYSTEM_UPTIME":
            # psutil.boot_time() returns a timestamp, so we must convert it.
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
        """ Applies dark or light mode theme to the GraphWindow and its components. """
        if self._is_closing: return
        try:
            self._is_dark_mode = checked
            is_dark = self._is_dark_mode
            self.logger.debug(f"Applying graph dark mode theme: {is_dark}")

            if hasattr(self, 'dark_mode') and self.dark_mode and self.dark_mode.isChecked() != is_dark:
                self.dark_mode.blockSignals(True)
                self.dark_mode.setChecked(is_dark)
                self.dark_mode.blockSignals(False)

            if self._parent and hasattr(self._parent, "config_manager"):
                self._parent.config["dark_mode"] = is_dark
                self._parent.config_manager.save(self._parent.config)

            # Check for the new `axes` attribute
            if not hasattr(self, 'figure') or not hasattr(self, 'axes') or not hasattr(self, 'canvas'):
                return

            graph_bg = style_constants.GRAPH_BG_DARK if is_dark else style_constants.GRAPH_BG_LIGHT
            text_color = style_constants.DARK_MODE_TEXT_COLOR if is_dark else style_constants.LIGHT_MODE_TEXT_COLOR
            grid_color = style_constants.GRID_COLOR_DARK if is_dark else style_constants.GRID_COLOR_LIGHT

            self.figure.patch.set_facecolor(graph_bg)

            # --- Loop through both axes to apply themes ---
            for ax in self.axes:
                ax.set_facecolor(graph_bg)
                ax.xaxis.label.set_color(text_color)
                ax.yaxis.label.set_color(text_color)
                ax.tick_params(colors=text_color, which='both') # Use 'both' for major and minor ticks
                ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=grid_color)
                for spine in ax.spines.values():
                    spine.set_color(grid_color)
                
                # Theme the legend for each axis
                leg = ax.get_legend()
                if leg:
                    for text_obj in leg.get_texts():
                        text_obj.set_color(text_color)
                    leg.get_frame().set_facecolor(graph_bg)
                    leg.get_frame().set_edgecolor(grid_color)

            # The rest of the function remains the same
            self.stats_bar.setStyleSheet(style_utils.graph_overlay_style())
            self.stats_bar.raise_()
            self.hamburger_icon.raise_()
            self._reposition_overlay_elements()
            
            self.canvas.draw_idle()

        except Exception as e:
            self.logger.error(f"Error in toggle_dark_mode: {e}", exc_info=True)


    def _connect_signals(self):
        """Connect all relevant signals for UI interactivity."""
        self._realtime_timer.timeout.connect(self._update_realtime)
        # All other signal connections are moved to _connect_settings_signals

        # Connect Canvas Mouse and Legend Click Events
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.canvas.mpl_connect('axes_leave_event', self._on_mouse_leave)
        self.canvas.mpl_connect('pick_event', self._on_legend_pick)


    def _connect_settings_signals(self):
        """Connect signals for the settings panel after it has been created."""
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

        if hasattr(self, 'show_legend') and self.show_legend:
            self.show_legend.toggled.connect(self._on_legend_toggled)

        # Set initial text values for sliders
        if hasattr(self, 'history_period'):
            self._update_history_period_text(self.history_period.value())
        if hasattr(self, 'keep_data'):
            self._update_keep_data_text(self.keep_data.value())


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


    def _on_history_slider_released(self) -> None:
        """
        Handles the timeline slider release event. This triggers an immediate graph
        update and notifies the parent to save the new setting for persistence.
        """

        # Get the current value from the slider
        current_value = self.history_period.value()
        self._history_period_value = current_value # UPDATE STATE VARIABLE

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


    def showEvent(self, event: QShowEvent) -> None:
        """
        Override the show event to trigger the initial data load after the window
        is visible, preventing UI blocking on startup.
        """
        super().showEvent(event)
        if not self._initial_load_done:
            self._initial_load_done = True
            # Display a "collecting data" message immediately for better UX
            self._show_graph_message(
                getattr(self.i18n, 'COLLECTING_DATA_MESSAGE', "Collecting data..."),
                is_error=False
            )
            # Schedule the actual data fetch and graph update.
            # A 50ms delay gives the UI time to fully paint itself.
            QTimer.singleShot(50, self._perform_initial_update)
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
                self.update_graph()
            # If not on session view, do nothing.

        except Exception as e:
            self.logger.error(f"Error in real-time update: {e}", exc_info=True)


    def _update_history_period_text(self, value: int) -> None:
        """Updates the text label for the history period slider in the settings panel."""
        try:
            # This function should ONLY ever interact with the settings panel widgets.
            if hasattr(self, 'history_period') and self.history_period:
                period_key = constants.data.history_period.PERIOD_MAP.get(value, constants.data.history_period.DEFAULT_PERIOD)
                translated_period = getattr(self.i18n, period_key, period_key)
                self.history_period.setValueText(translated_period)
        except Exception as e:
            self.logger.error(f"Error updating history period text: {e}", exc_info=True)


    def update_history_period(self, value: int, initial_setup: bool = False) -> None:
        """Triggers a graph update based on the selected history period."""
        if self._is_closing:
            return
        try:
            # The new architecture is simple: just trigger a full update.
            self.update_graph()
            self.logger.debug("History period update triggered for a full refresh.")
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
            # This flag is set by the parent widget during a full application shutdown.
            if getattr(self, '_is_closing', False):
                self.logger.debug("Performing full cleanup for graph window on app exit...")
                
                # Stop all timers and the worker thread
                self._realtime_timer.stop()
                self._db_size_update_timer.stop()
                if hasattr(self, 'worker_thread') and self.worker_thread.isRunning():
                    self.worker_thread.quit()
                    self.worker_thread.wait(1000) # Wait up to 1 second
                
                if hasattr(self, 'figure'): plt.close(self.figure)
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
                
                self.hide()
                event.ignore()
                
                # Tell the parent widget that this instance is "dead"
                # so that it will create a fresh one next time.
                if self._parent:
                    self._parent.graph_window = None
                
        except Exception as e:
            self.logger.error(f"Error in closeEvent: {e}", exc_info=True)            
            event.ignore()

    def _get_db_size_mb(self) -> float:
        """Get the size of the database file in megabytes."""
        try:
            # Access the db_path via the db_worker attribute on widget_state.
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


    def _on_legend_toggled(self, checked: bool) -> None:
        """Handles the Show Legend toggle, saves the state, and updates the graph."""
        if self._is_closing or not self._parent:
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


    def update_graph(self):
        """
        Triggers a data refresh by emitting a signal to the worker thread.
        This is the primary entry point for all graph updates.
        """
        if self._is_closing or not hasattr(self, 'request_data_processing'):
            return
        
        # Show a loading message immediately for a responsive feel.
        self._show_graph_message(self.i18n.COLLECTING_DATA_MESSAGE, is_error=False)

        # Get the current filter settings from the UI.
        start_time, end_time = self._get_time_range_from_ui()
        interface_to_query = self.interface_filter.currentData() if self.interface_filter and self.interface_filter.currentData() != "all" else None
        period_key = constants.data.history_period.PERIOD_MAP.get(self._history_period_value, "")
        is_session_view = period_key == "TIMELINE_SESSION"

        # Emit the signal to the worker thread to start processing the data.
        self.request_data_processing.emit(start_time, end_time, interface_to_query, is_session_view)


    def _configure_xaxis_format(self, start_time: datetime, end_time: datetime) -> None:
        """
        Intelligently configures the x-axis locator and formatter based on the
        time range to prevent Matplotlib warnings and improve readability.
        """
        import matplotlib.dates as mdates
        
        # Target the bottom plot's X-axis, as it is shared with the top plot.
        axis_to_configure = self.ax_upload

        if not start_time or not end_time or start_time >= end_time:
            axis_to_configure.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
            axis_to_configure.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            return

        time_delta_seconds = (end_time - start_time).total_seconds()
        
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
            formatter = mdates.DateFormatter('%a %d')
        elif time_delta_seconds <= 86400 * 32: # Handles 1 month view
            locator = mdates.WeekdayLocator(byweekday=mdates.MO)
            formatter = mdates.DateFormatter('%b %d')
        else:  # > 1 month (e.g., "All")
            locator = mdates.MonthLocator()
            formatter = mdates.DateFormatter('%Y-%b')

        axis_to_configure.xaxis.set_major_locator(locator)
        axis_to_configure.xaxis.set_major_formatter(formatter)


    def _get_nice_y_axis_top(self, max_speed: float) -> float:
        """Calculates a 'nice' round number for the top of the Y-axis."""
        import math

        min_range_mbps = 0.1  # Equivalent to 100 Kbps

        if max_speed <= constants.graph.MINIMUM_Y_AXIS_MBPS:
            return constants.graph.MINIMUM_Y_AXIS_MBPS

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


    def _render_graph(self, df: pd.DataFrame):
        """
        The definitive rendering function. Takes a processed DataFrame and draws it
        with a true-zero floor and an adaptive logarithmic scale.
        """
        try:
            from matplotlib.ticker import FixedLocator, FuncFormatter

            df['upload_mbps'] = (df['upload_speed'] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            df['download_mbps'] = (df['download_speed'] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            df['upload_mbps'] = df['upload_mbps'].clip(lower=0)
            df['download_mbps'] = df['download_mbps'].clip(lower=0)
            
            for ax in self.axes:
                ax.clear()

            start_time, end_time = self._get_time_range_from_ui()
            # A timespan is "long" if the user selected "All" (start_time is None)
            # OR if the selected range is greater than 2 days.
            is_long_timespan = start_time is None or (end_time - start_time).days > 2
            
            # However, if the total data available is very short, always use the detailed view.
            total_data_duration_hours = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / 3600
            if total_data_duration_hours < 48:
                is_long_timespan = False

            if is_long_timespan:
                # Mean/Range logic
                df.set_index('timestamp', inplace=True)
                daily_agg = df.resample('D').agg({'download_mbps': ['mean', 'min', 'max'], 'upload_mbps': ['mean', 'min', 'max']}).fillna(0)
                df.reset_index(inplace=True)
                self.ax_download.plot(daily_agg.index, daily_agg[('download_mbps', 'mean')], color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
                self.ax_download.fill_between(daily_agg.index, daily_agg[('download_mbps', 'min')], daily_agg[('download_mbps', 'max')], color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.3, label=self.i18n.DOWNLOAD_LABEL)
                self.ax_upload.plot(daily_agg.index, daily_agg[('upload_mbps', 'mean')], color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
                self.ax_upload.fill_between(daily_agg.index, daily_agg[('upload_mbps', 'min')], daily_agg[('upload_mbps', 'max')], color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.3, label=self.i18n.UPLOAD_LABEL)
            else:
                # Detailed line plot logic
                df['time_diff'] = df['timestamp'].diff().dt.total_seconds()
                gap_indices = df[df['time_diff'] > 60].index
                for index in reversed(gap_indices):
                    gap_row = df.loc[index].copy()
                    gap_row['upload_mbps'], gap_row['download_mbps'] = np.nan, np.nan
                    df = pd.concat([df.iloc[:index], pd.DataFrame([gap_row]), df.iloc[index:]], ignore_index=True)
                self._plot_continuous_segments(self.ax_download, df['timestamp'], df['download_mbps'], color=constants.graph.DOWNLOAD_LINE_COLOR, label=self.i18n.DOWNLOAD_LABEL)
                self._plot_continuous_segments(self.ax_upload, df['timestamp'], df['upload_mbps'], color=constants.graph.UPLOAD_LINE_COLOR, label=self.i18n.UPLOAD_LABEL)

            self._apply_theme()
            
            for ax, speed_col in [(self.ax_download, 'download_mbps'), (self.ax_upload, 'upload_mbps')]:
                all_speeds = [s for s in df[speed_col] if pd.notna(s)]
                if all_speeds:
                    max_speed = max(all_speeds)
                    nice_top = self._get_nice_y_axis_top(max_speed)
                    ax.set_ylim(bottom=0, top=nice_top)
                    ax.set_yscale('symlog', linthresh=1.0)
                    
                    ticks = {0.0, 1.0}
                    if nice_top > 1:
                        power = 10
                        while power < nice_top * 0.9:
                            ticks.add(float(power))
                            power *= 10
                    ticks.add(float(nice_top))
                    
                    ax.yaxis.set_major_locator(FixedLocator(sorted(list(ticks))))
                    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, pos: f'{x:.1f}'.rstrip('0').rstrip('.')))
            
            if self._parent and self._parent.config.get("show_legend", True):
                for ax in self.axes:
                    leg = ax.legend()
                    if leg:
                        for legend_handle in leg.legendHandles:
                            legend_handle.set_picker(5)

            self.ax_download.set_ylabel(f"{self.i18n.DOWNLOAD_LABEL} (Mbps)")
            self.ax_upload.set_ylabel(f"{self.i18n.UPLOAD_LABEL} (Mbps)")

            effective_start_time = df['timestamp'].min()
            effective_end_time = df['timestamp'].max()
            self.ax_upload.set_xlim(effective_start_time, effective_end_time)
            self._configure_xaxis_format(effective_start_time, effective_end_time)
            
            history_data = list(zip(df['timestamp'], df['upload_speed'], df['download_speed']))
            self._update_stats_bar(history_data)

            self.figure.autofmt_xdate(rotation=30, ha='right')
            self.canvas.draw_idle()

        except Exception as e:
            self.logger.error(f"Error rendering graph: {e}", exc_info=True)
            self._show_graph_error(str(e))


    def _update_stats_bar(self, history_data: List[Tuple[datetime, float, float]]) -> None:
        """
        Update the stats bar. It calculates max speed from the plot data,
        but gets the accurate total bandwidth from a dedicated, efficient database query.
        """
        try:
            if not history_data:
                self.stats_bar.setText(self.i18n.NO_DATA_MESSAGE)
                return

            upload_bytes_sec = [up for _, up, _ in history_data if up is not None]
            download_bytes_sec = [down for _, _, down in history_data if down is not None]
            max_upload_mbps = (max(upload_bytes_sec) * 8 / 1_000_000) if upload_bytes_sec else 0.0
            max_download_mbps = (max(download_bytes_sec) * 8 / 1_000_000) if download_bytes_sec else 0.0

            start_time, end_time = self._get_time_range_from_ui()
            interface_to_query = self.interface_filter.currentData() if self.interface_filter and self.interface_filter.currentData() != "all" else None
            
            total_upload_bytes, total_download_bytes = db_utils.get_total_bandwidth_for_period(
                self._parent.widget_state.db_worker.db_path,
                start_time,
                end_time,
                interface_to_query
            )

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