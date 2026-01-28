import logging
import math
from typing import List, Tuple, Optional
from datetime import datetime

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.font_manager as font_manager
import matplotlib.dates as mdates
from matplotlib.ticker import NullLocator

from netspeedtray import constants
from netspeedtray.constants import styles as style_constants

class GraphRenderer(QObject):
    """
    Handles all Matplotlib rendering logic for the GraphWindow.
    Owns the Figure, Canvas, and Axes.
    """
    
    def __init__(self, parent_widget: QWidget, i18n, logger=None):
        super().__init__()
        self.logger = logger or logging.getLogger(__name__)
        self.i18n = i18n
        self.parent_widget = parent_widget
        
        # UI Elements
        self.figure = None
        self.canvas = None
        self.ax_download = None
        self.ax_upload = None
        self.axes = []
        
        # State
        self._current_date_formatter_type = None

        self._init_matplotlib()

    def _init_matplotlib(self):
        """Initialize matplotlib with a dual-axis, stacked subplot layout."""
        self.logger.debug("Initializing Matplotlib canvas...")
        
        # Create Figure
        self.figure = Figure(figsize=(8, 4), dpi=100)
        self.figure.set_layout_engine('tight')
        
        # Create Canvas (Widget)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.canvas.setMouseTracking(True)  # Essential for hover events
        
        # Add canvas to parent layout - layout MUST exist (created in window.setupUi)
        existing_layout = self.parent_widget.layout()
        if existing_layout is not None:
            existing_layout.addWidget(self.canvas)
        else:
            # Fallback: create layout if none exists
            self.logger.warning("No existing layout found for graph widget, creating one")
            layout = QVBoxLayout(self.parent_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.canvas)

        # Create Stacked Subplots (2 rows, 1 column)
        self.ax_download = self.figure.add_subplot(2, 1, 1)
        self.ax_upload = self.figure.add_subplot(2, 1, 2, sharex=self.ax_download)
        self.axes = [self.ax_download, self.ax_upload]
        
        # Initial formatting
        self._format_axes()

    def _format_axes(self):
        """Initial configuration of axes properties."""
        # Download Axis (Top)
        self.ax_download.set_ylabel(self.i18n.DOWNLOAD_LABEL)
        self.ax_download.tick_params(labelbottom=False) # Hide x-labels on top plot
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA)

        # Upload Axis (Bottom)
        self.ax_upload.set_ylabel(self.i18n.UPLOAD_LABEL)
        self.ax_upload.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA)

        # Common
        for ax in self.axes:
            ax.spines['right'].set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.margins(x=0) # Tight x-axis

        # Re-enable top spine for upload to separate plots clearly? 
        # Or just rely on layout.
        self.ax_upload.spines['top'].set_visible(True)

    def apply_theme(self, is_dark_mode: bool):
        """Applies colors based on theme."""
        graph_bg = style_constants.GRAPH_BG_DARK if is_dark_mode else style_constants.GRAPH_BG_LIGHT
        text_color = style_constants.DARK_MODE_TEXT_COLOR if is_dark_mode else style_constants.LIGHT_MODE_TEXT_COLOR
        grid_color = style_constants.GRID_COLOR_DARK if is_dark_mode else style_constants.GRID_COLOR_LIGHT
        
        self.figure.patch.set_facecolor(graph_bg)
        
        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            ax.xaxis.label.set_color(text_color)
            ax.yaxis.label.set_color(text_color)
            ax.tick_params(colors=text_color, which='both')
            ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=grid_color)
            for spine in ax.spines.values():
                spine.set_color(grid_color)
            
            # Legend
            leg = ax.get_legend()
            if leg:
                for text_obj in leg.get_texts():
                    text_obj.set_color(text_color)
                leg.get_frame().set_facecolor(graph_bg)
                leg.get_frame().set_edgecolor(grid_color)
        
        self.canvas.draw_idle()

    def render(self, history_data: List[Tuple[float, float, float]], start_time: datetime, end_time: datetime, period_key: str):
        """
        Renders the graph.
        Returns: Tuple of numpy arrays (timestamps, upload_mbps, download_mbps) for interaction cache,
                 or None if no data.
        """
        if not history_data:
            for ax in self.axes:
                ax.clear()
            self.canvas.draw_idle()
            return None

        # Data Processing
        raw_data = np.array(history_data, dtype=float)
        timestamps = raw_data[:, 0]
        upload_speeds = raw_data[:, 1]
        download_speeds = raw_data[:, 2]

        # Convert to Mbps for plotting
        upload_mbps = (upload_speeds * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
        download_mbps = (download_speeds * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
        
        upload_mbps = np.maximum(upload_mbps, 0)
        download_mbps = np.maximum(download_mbps, 0)

        # Clear Axes and re-apply formatting
        for ax in self.axes:
            ax.clear()
        
        # Re-apply axis formatting (grid, spines, labels)
        self._format_axes()

        # Determine Aggregation
        is_long_timespan = False
        if start_time is None:
            is_long_timespan = True
        else:
             is_long_timespan = (end_time - start_time).days > 2
        
        if len(timestamps) > 0:
            total_duration_hours = (timestamps.max() - timestamps.min()) / 3600
            if total_duration_hours < 48:
                is_long_timespan = False

        # Convert UTC epoch floats to local datetime objects for robust plotting
        # Matplotlib handles datetime objects natively and correctly manages its internal epoch.
        plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
        plot_datetimes_array = np.array(plot_datetimes)

        # Plotting Logic
        if is_long_timespan:
            self._plot_aggregated(plot_datetimes_array, upload_mbps, download_mbps)
        else:
            self._plot_high_res(plot_datetimes_array, upload_mbps, download_mbps)

        # Configure Limits and Formats
        self._configure_axes(start_time, end_time, period_key, timestamps, upload_mbps, download_mbps)
        
        self.canvas.draw_idle()
        
        return timestamps, upload_speeds, download_speeds

    def _plot_aggregated(self, plot_datetimes, upload_mbps, download_mbps):
        """Daily Aggregation and Plotting"""
        # Bin by day (using datetime attributes for safety)
        day_bins = np.array([dt.date().toordinal() for dt in plot_datetimes])
        unique_days, indices = np.unique(day_bins, return_inverse=True)
        
        counts = np.bincount(indices)
        down_mean = np.bincount(indices, weights=download_mbps) / counts
        up_mean = np.bincount(indices, weights=upload_mbps) / counts
        
        # Min/Max aggregation for fill
        change_points = np.where(np.diff(indices) > 0)[0] + 1
        reduce_indices = np.concatenate(([0], change_points))
        
        if len(reduce_indices) == len(unique_days):
            up_max = np.maximum.reduceat(upload_mbps, reduce_indices)
            up_min = np.minimum.reduceat(upload_mbps, reduce_indices)
            down_max = np.maximum.reduceat(download_mbps, reduce_indices)
            down_min = np.minimum.reduceat(download_mbps, reduce_indices)
        else:
            up_max = up_mean; up_min = up_mean
            down_max = down_mean; down_min = down_mean

        # Convert unique_days (ordinals) to datetime objects for the center of the day
        from datetime import date
        agg_dates = [datetime.combine(date.fromordinal(d), datetime.min.time()) for d in unique_days]
        
        self.ax_download.plot(agg_dates, down_mean, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
        self.ax_download.fill_between(agg_dates, down_min, down_max, color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.3)
        self.ax_upload.plot(agg_dates, up_mean, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
        self.ax_upload.fill_between(agg_dates, up_min, up_max, color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.3)

    def _plot_high_res(self, plot_datetimes, upload_mbps, download_mbps):
        """Segmented Plotting with Gap Detection"""
        # Detect gaps > 10 seconds to account for jitter or sleep.
        # This prevents the graph from breaking into single points that draw nothing.
        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        gaps = np.diff(timestamps_float) > 10.0
        gap_indices = np.where(gaps)[0] + 1
        
        segments_ts = np.split(plot_datetimes, gap_indices)
        segments_up = np.split(upload_mbps, gap_indices)
        segments_down = np.split(download_mbps, gap_indices)
        
        for ts, up, down in zip(segments_ts, segments_up, segments_down):
            if len(ts) == 0: continue
            
            self.ax_download.plot(ts, down, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5)
            self.ax_download.fill_between(ts, 0, down, color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.1)
            
            self.ax_upload.plot(ts, up, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5)
            self.ax_upload.fill_between(ts, 0, up, color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.1)

    def _configure_axes(self, start_time, end_time, period_key, timestamps, upload_mbps, download_mbps):
        """Sets limits and Formatters."""
        # Y-Axis Scaling
        max_up = np.max(upload_mbps) if len(upload_mbps) > 0 else 0
        max_down = np.max(download_mbps) if len(download_mbps) > 0 else 0
        
        y_top_up = self._get_nice_y_axis_top(max_up)
        y_top_down = self._get_nice_y_axis_top(max_down)
        
        self.ax_upload.set_ylim(bottom=0, top=y_top_up)
        self.ax_download.set_ylim(bottom=0, top=y_top_down)

        if start_time and end_time:
             self.ax_upload.set_xlim(start_time, end_time)
        elif len(timestamps) > 0:
             # Auto-range logic
             min_dt = datetime.fromtimestamp(timestamps.min())
             max_dt = datetime.fromtimestamp(timestamps.max())
             self.ax_upload.set_xlim(min_dt, max_dt)

        # X-Axis Formatting
        self._configure_xaxis_format(period_key)


    def _get_nice_y_axis_top(self, max_speed: float) -> float:
        """Calculates a 'nice' round number for the top of the Y-axis."""
        min_range_mbps = 0.1 
        if max_speed <= constants.graph.MINIMUM_Y_AXIS_MBPS:
            return constants.graph.MINIMUM_Y_AXIS_MBPS

        power = 10 ** math.floor(math.log10(max_speed))
        normalized_max = max_speed / power
        
        if normalized_max <= 1.0: nice_norm = 1.0
        elif normalized_max <= 2.0: nice_norm = 2.0
        elif normalized_max <= 5.0: nice_norm = 5.0
        else: nice_norm = 10.0
        
        return nice_norm * power

    def _configure_xaxis_format(self, period_key: str) -> None:
        """
        Intelligently configures the x-axis locator and formatter.
        """
        axis = self.ax_upload
        
        major_locator = None
        major_formatter = mdates.DateFormatter('%H:%M') 

        if period_key == "TIMELINE_3_HOURS":
            major_locator = mdates.HourLocator(interval=1)
        elif period_key == "TIMELINE_6_HOURS":
            major_locator = mdates.HourLocator(interval=1)
        elif period_key == "TIMELINE_12_HOURS":
            major_locator = mdates.HourLocator(interval=2)
        elif period_key == "TIMELINE_24_HOURS":
            major_locator = mdates.HourLocator(interval=3)
        elif period_key == "TIMELINE_WEEK":
            major_locator = mdates.DayLocator(interval=1)
            major_formatter = mdates.DateFormatter('%a %d')
        elif period_key == "TIMELINE_MONTH":
            major_locator = mdates.WeekdayLocator(byweekday=mdates.MO)
            major_formatter = mdates.DateFormatter('%b %d')
        else:
            major_locator = mdates.AutoDateLocator(maxticks=8)
            major_formatter = mdates.ConciseDateFormatter(major_locator)

        axis.xaxis.set_major_locator(major_locator)
        axis.xaxis.set_major_formatter(major_formatter)
        
        if "HOURS" in (period_key or ""):
            axis.xaxis.set_minor_locator(NullLocator())
