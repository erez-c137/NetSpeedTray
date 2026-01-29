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
from matplotlib.dates import date2num
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

        # Convert UTC epoch floats to local datetime objects for robust plotting
        # Matplotlib handles datetime objects natively and correctly manages its internal epoch.
        # OPTIMIZATION: This loop was identified as a bottleneck. 
        # Since we now cap data at 800 points in the worker, this is O(800) which is negligible (<1ms).
        # We rely on the worker to protect this thread.
        try:
             plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
             plot_datetimes_array = np.array(plot_datetimes)
        except Exception as e:
             self.logger.error(f"Error converting timestamps to datetime: {e}")
             return None

        # Determine Aggregation Level
        # < 2 days: High-res
        # 2-14 days: Hourly
        # > 14 days: Daily
        timespan_days = 0
        if start_time and end_time:
            timespan_days = (end_time - start_time).days
        elif start_time is None: # TIMELINE_ALL
            timespan_days = 365 # Assume long
            
        # Plotting Logic
        # Plotting Logic
        plotted_ts, plotted_x_coords, plotted_up, plotted_down = None, None, None, None
        
        # Aggregation Logic
        # < 6 hours: High-res (Raw)
        # 6 hours - 2 days: Minute Aggregation
        # 2 - 14 days: Hourly Aggregation
        # > 14 days: Daily Aggregation
        
        if timespan_days == 0 and (end_time - start_time).total_seconds() <= constants.data.history_period.RES_RAW_THRESHOLD:
             self._plot_high_res(plot_datetimes_array, upload_mbps, download_mbps)
             plotted_ts = timestamps # Keep original floats for high-res
             plotted_up = upload_mbps
             plotted_down = download_mbps
        elif timespan_days == 0 or (end_time - start_time).total_seconds() <= constants.data.history_period.PLOT_MINUTE_THRESHOLD:
            plotted_ts, plotted_up, plotted_down = self._plot_aggregated(plot_datetimes_array, upload_mbps, download_mbps, mode="minute")
        elif (end_time - start_time).total_seconds() <= constants.data.history_period.PLOT_HOURLY_THRESHOLD:
            plotted_ts, plotted_up, plotted_down = self._plot_aggregated(plot_datetimes_array, upload_mbps, download_mbps, mode="hourly")
        else:
            plotted_ts, plotted_up, plotted_down = self._plot_aggregated(plot_datetimes_array, upload_mbps, download_mbps, mode="daily")

        # Configure Limits and Formats
        self._configure_axes(start_time, end_time, period_key, timestamps, upload_mbps, download_mbps)
        
        self.canvas.draw_idle()
        
        # Return the DATA THAT WAS ACTUALLY PLOTTED, so interactions match the visual lines.
        # InteractionHandler needs BOTH:
        # 1. Unix Timestamps (float seconds) - For Tooltip Text
        # 2. Bytes/sec (raw speed) - For Tooltip Text
        # 3. MPL Float Coordinates - For Cache/Mouse Lookup
        
        plotted_x_coords = None

        # Convert aggregated datetimes back to Unix timestamps for the interaction handler
        if plotted_ts is not None and len(plotted_ts) > 0 and isinstance(plotted_ts[0], datetime):
             # Save the MPL coordinates (float days) before converting timestamp
             plotted_x_coords = date2num(plotted_ts)
             plotted_ts = np.array([dt.timestamp() for dt in plotted_ts])

        # Convert Mbps back to Bytes/s for the interaction handler (which expects raw units)
        # Bytes/s = (Mbps * 1,000,000) / 8
        if plotted_up is not None:
            plotted_up = (plotted_up * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        if plotted_down is not None:
            plotted_down = (plotted_down * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE

        return plotted_ts, plotted_x_coords, plotted_up, plotted_down

    def _plot_aggregated(self, plot_datetimes, upload_mbps, download_mbps, mode="daily"):
        """Adaptive Aggregation (Daily, Hourly, or Minute) and Plotting"""
        if len(plot_datetimes) == 0: return None, None, None, None

        if mode == "daily":
            # Bin by day
            bins = np.array([dt.date().toordinal() for dt in plot_datetimes])
        elif mode == "hourly":
            # Bin by hour: (Ordinal * 24) + Hour
            bins = np.array([dt.date().toordinal() * 24 + dt.hour for dt in plot_datetimes])
        else: # minute
            # Bin by minute: (Ordinal * 1440) + (Hour * 60) + Minute
            bins = np.array([dt.date().toordinal() * 1440 + dt.hour * 60 + dt.minute for dt in plot_datetimes])
            
        unique_bins, indices = np.unique(bins, return_inverse=True)
        
        counts = np.bincount(indices)
        down_mean = np.bincount(indices, weights=download_mbps) / counts
        up_mean = np.bincount(indices, weights=upload_mbps) / counts
        
        # Min/Max aggregation for fill
        change_points = np.where(np.diff(indices) > 0)[0] + 1
        reduce_indices = np.concatenate(([0], change_points))
        
        if len(reduce_indices) == len(unique_bins):
            up_max = np.maximum.reduceat(upload_mbps, reduce_indices)
            up_min = np.minimum.reduceat(upload_mbps, reduce_indices)
            down_max = np.maximum.reduceat(download_mbps, reduce_indices)
            down_min = np.minimum.reduceat(download_mbps, reduce_indices)
        else:
            up_max = up_mean; up_min = up_mean
            down_max = down_mean; down_min = down_mean

        # FIX: Instead of mapping back to the START of the bin (e.g. 15:00), 
        # we calculate the MEAN timestamp of all points within the bin.
        # This prevents the visual "lag" or "shift" in aggregated views.
        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        bin_timestamps = np.bincount(indices, weights=timestamps_float) / counts
        agg_dates = [datetime.fromtimestamp(ts) for ts in bin_timestamps]
        
        # Store references to lines for potential updates (though aggregated view updates are rare)
        self.line_download, = self.ax_download.plot(agg_dates, down_mean, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
        self.fill_download = self.ax_download.fill_between(agg_dates, down_min, down_max, color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.3)
        self.line_upload, = self.ax_upload.plot(agg_dates, up_mean, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5, zorder=10)
        self.fill_upload = self.ax_upload.fill_between(agg_dates, up_min, up_max, color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.3)
        
        # Return the aggregated data for the interaction handler
        return agg_dates, up_mean, down_mean

    def _plot_high_res(self, plot_datetimes, upload_mbps, download_mbps):
        """Segmented Plotting with Gap Detection"""
        # Detect gaps > 10 seconds to account for jitter or sleep.
        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        gaps = np.diff(timestamps_float) > 10.0
        
        if np.any(gaps):
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
                
            # Sentinel to indicate we can't easily update via set_data
            self.line_download = None
            self.line_upload = None
            
        else:
            # Single continuous line - OPTIMIZED PATH
            self.line_download, = self.ax_download.plot(plot_datetimes, download_mbps, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5)
            self.fill_download = self.ax_download.fill_between(plot_datetimes, 0, download_mbps, color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.1)
            
            self.line_upload, = self.ax_upload.plot(plot_datetimes, upload_mbps, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5)
            self.fill_upload = self.ax_upload.fill_between(plot_datetimes, 0, upload_mbps, color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.1)

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
        """Calculates a top limit with ~10% padding."""
        if max_speed <= constants.graph.MINIMUM_Y_AXIS_MBPS:
            return constants.graph.MINIMUM_Y_AXIS_MBPS

        # User requested tight 10% padding (e.g. 1000 -> 1100, not 2000)
        return max_speed * 1.1

    def _configure_xaxis_format(self, period_key: str) -> None:
        """
        Intelligently configures the x-axis locator and formatter.
        """
        axis = self.ax_upload
        
        # Default for most views
        major_locator = mdates.AutoDateLocator(maxticks=8)
        major_formatter = mdates.DateFormatter('%H:%M') 

        if period_key == "TIMELINE_WEEK":
            major_formatter = mdates.DateFormatter('%a %d')
        elif period_key == "TIMELINE_MONTH":
            major_formatter = mdates.DateFormatter('%b %d')
        elif period_key == "TIMELINE_ALL":
            major_formatter = mdates.DateFormatter('%Y-%m-%d')
        elif period_key == "TIMELINE_SYSTEM_UPTIME":
             # Uptime can be minutes or days; Concise is best here
             major_formatter = mdates.ConciseDateFormatter(major_locator)
        
        axis.xaxis.set_major_locator(major_locator)
        axis.xaxis.set_major_formatter(major_formatter)
        
        if "HOURS" in (period_key or ""):
            axis.xaxis.set_minor_locator(NullLocator())

    def update_data(self, plot_datetimes, upload_mbps, download_mbps, start_time, end_time):
        """
        Efficiently updates the graph data without clearing axes, if possible.
        """
        # Check if we have valid line objects to update AND they are still attached to axes
        # (if axes were cleared to show a message, artist.axes will be None)
        if not hasattr(self, 'line_download') or not self.line_download or self.line_download.axes is None or \
           not hasattr(self, 'line_upload') or not self.line_upload or self.line_upload.axes is None:
               # Fallback to full render if we don't have updateable lines (e.g. segmented or cleared)
               return False
               
        # Update X/Y data
        self.line_download.set_data(plot_datetimes, download_mbps)
        self.line_upload.set_data(plot_datetimes, upload_mbps)
        
        # Updating fill_between is tricky. It creates a PolyCollection.
        # The most robust way for fill is to remove the old one and add a new one, 
        # which is still faster than full ax.clear() + reformatting.
        
        try:
            if hasattr(self, 'fill_download') and self.fill_download:
                self.fill_download.remove()
            if hasattr(self, 'fill_upload') and self.fill_upload:
                self.fill_upload.remove()
        except Exception:
            pass # Already removed or invalid

        self.fill_download = self.ax_download.fill_between(plot_datetimes, 0, download_mbps, color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.1)
        self.fill_upload = self.ax_upload.fill_between(plot_datetimes, 0, upload_mbps, color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.1)
        
        # Update Limits
        self.ax_upload.set_xlim(start_time, end_time)
        self.ax_download.set_xlim(start_time, end_time)
        
        # Update Y-Limits
        max_up = np.max(upload_mbps) if len(upload_mbps) > 0 else 0
        max_down = np.max(download_mbps) if len(download_mbps) > 0 else 0
        self.ax_upload.set_ylim(0, self._get_nice_y_axis_top(max_up))
        self.ax_download.set_ylim(0, self._get_nice_y_axis_top(max_down))
        
        self.canvas.draw_idle()
        return True
