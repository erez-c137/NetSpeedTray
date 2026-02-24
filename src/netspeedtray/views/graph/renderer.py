import logging
import math
from typing import List, Tuple, Optional
from datetime import datetime
import threading

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
from netspeedtray.constants.renderer import RendererConstants
from netspeedtray.utils.helpers import calculate_monotone_cubic_interpolation
import matplotlib.colors as mcolors
from matplotlib.patches import PathPatch
from matplotlib.path import Path

class GraphRenderer(QObject):
    """
    Handles all Matplotlib rendering logic for the GraphWindow.
    Owns the Figure, Canvas, and Axes.
    """
    
    # Class-level gradient cache (shared across instances, never regenerated)
    _GRADIENT_CACHE = {}
    _GRADIENT_CACHE_LOCK = threading.Lock()  # Thread-safety for cache access
    
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
        self.ax_cpu = None
        self.ax_gpu = None
        self.axes = []
        
        # State
        self._current_date_formatter_type = None
        
        # Reusable Artists ("Reuse, Don't Recreate" pattern)
        self.line_download = None
        self.line_upload = None
        self.fill_download = None
        self.fill_upload = None
        
        self._gradient_im_download = None
        self._gradient_patch_download = None
        self._gradient_im_upload = None
        self._gradient_patch_upload = None
        
        self._peak_artists_download = {}  # {'outer': artist, 'middle': artist, 'inner': artist, 'label': artist}
        self._peak_artists_upload = {}
        
        # Event Markers (Boot time, etc.)
        self._event_artists = []

        self._current_ylim_up = constants.graph.MINIMUM_Y_AXIS_MBPS
        self._current_ylim_down = constants.graph.MINIMUM_Y_AXIS_MBPS
        
        self._last_render_mode = None  # 'high_res' or aggregate modes
        self._last_period_key = None
        
        self._init_matplotlib()

    def reset_ylim(self):
        """Reset sticky Y-axis limits when timeline changes to prevent stale cached limits."""
        self._current_ylim_up = constants.graph.MINIMUM_Y_AXIS_MBPS
        self._current_ylim_down = constants.graph.MINIMUM_Y_AXIS_MBPS

    def clear_plot(self):
        """Clear all plot artists to prevent stale visuals while new data loads."""
        # Clear lines
        if self.line_download is not None:
            self.line_download.remove()
            self.line_download = None
        if self.line_upload is not None:
            self.line_upload.remove()
            self.line_upload = None
        
        # Clear fills
        if self.fill_download is not None:
            self.fill_download.remove()
            self.fill_download = None
        if self.fill_upload is not None:
            self.fill_upload.remove()
            self.fill_upload = None
        
        # Clear peak markers
        for artist in self._peak_artists_download.values():
            if hasattr(artist, 'remove'):
                artist.remove()
        for artist in self._peak_artists_upload.values():
            if hasattr(artist, 'remove'):
                artist.remove()
        self._peak_artists_download = {}
        self._peak_artists_upload = {}
        
        # Redraw
        self.canvas.draw_idle()

    def _init_matplotlib(self):
        """Initialize matplotlib canvas."""
        self.logger.debug("Initializing Matplotlib canvas...")
        
        # Create Figure
        self.figure = Figure(figsize=(8, 4), dpi=100)
        
        # Create Canvas (Widget)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.canvas.setMouseTracking(True)
        
        existing_layout = self.parent_widget.layout()
        if existing_layout is not None:
            existing_layout.addWidget(self.canvas)
        else:
            layout = QVBoxLayout(self.parent_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.canvas)

        # We'll create axes dynamically in render() to avoid fixed subplot limits
        self._setup_standard_axes()

    def _format_axes(self):
        """Initial configuration of axes properties."""
        # Ensure default color if not set
        if not hasattr(self, '_current_text_color'):
             # Default to light mode if unknown (or check system theme?)
             # Usually apply_theme is called immediately after init.
             self._current_text_color = style_constants.LIGHT_MODE_TEXT_COLOR
        
        if not hasattr(self, '_current_grid_color'):
             self._current_grid_color = style_constants.GRID_COLOR_LIGHT

        # Download Axis (Top)
        self.ax_download.set_ylabel(self.i18n.DOWNLOAD_LABEL, color=self._current_text_color)
        self.ax_download.tick_params(labelbottom=False, colors=self._current_text_color, which='both') 
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        self.ax_download.yaxis.label.set_color(self._current_text_color) # Redundant but safe

        # Upload Axis (Bottom)
        self.ax_upload.set_ylabel(self.i18n.UPLOAD_LABEL, color=self._current_text_color)
        self.ax_upload.tick_params(colors=self._current_text_color, which='both')
        self.ax_upload.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        self.ax_upload.yaxis.label.set_color(self._current_text_color)

        # Common
        for ax in self.axes:
            ax.spines['right'].set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.set_xmargin(0) # Strict zero margin for X-axis
            
            # Update spines color
            for spine in ax.spines.values():
                spine.set_color(self._current_grid_color)

        # Fixed Subplots Adjust to keep graph area size constant
        self.figure.subplots_adjust(
            left=0.12,   # Expanded to 12% to prevent 5-digit speeds from pushing the Y-axis label off-screen
            right=0.98,  # Minimum right margin
            top=0.95,    # Space for peak labels
            bottom=0.12, # Space for time labels
            hspace=0.25  # Space between up/down plots
        )

        # Re-enable top spine for upload to separate plots clearly? 
        self.ax_upload.spines['top'].set_visible(True)
        self.ax_upload.spines['top'].set_color(self._current_grid_color) # Ensure separator is visible

    def apply_theme(self, is_dark_mode: bool):
        """Applies colors based on theme."""
        self._is_dark_mode = is_dark_mode # Save state
        graph_bg = style_constants.GRAPH_BG_DARK if is_dark_mode else style_constants.GRAPH_BG_LIGHT
        
        # PERSIST COLORS
        self._current_text_color = style_constants.DARK_MODE_TEXT_COLOR if is_dark_mode else style_constants.LIGHT_MODE_TEXT_COLOR
        self._current_grid_color = style_constants.GRID_COLOR_DARK if is_dark_mode else style_constants.GRID_COLOR_LIGHT
        
        self.figure.patch.set_facecolor(graph_bg)
        
        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            
            # Explicitly set label colors
            ax.xaxis.label.set_color(self._current_text_color)
            ax.yaxis.label.set_color(self._current_text_color)
            
            # Update tick labels
            ax.tick_params(axis='x', colors=self._current_text_color)
            ax.tick_params(axis='y', colors=self._current_text_color)
            
            # Update spines
            ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
            for spine in ax.spines.values():
                spine.set_color(self._current_grid_color)
            
            # Force update of existing labels if they persist
            ylabel = ax.get_ylabel()
            if ylabel:
                ax.set_ylabel(ylabel, color=self._current_text_color)
            
            # Legend
            leg = ax.get_legend()
            if leg:
                for text_obj in leg.get_texts():
                    text_obj.set_color(self._current_text_color)
                leg.get_frame().set_facecolor(graph_bg)
                leg.get_frame().set_edgecolor(self._current_grid_color)
        
        self.canvas.draw_idle()
        
        # Invalidate gradient artists when theme changes (they need recapture)
        self._gradient_im_download = None
        self._gradient_im_upload = None

    @classmethod
    def _get_cached_gradient(cls, color_hex: str) -> np.ndarray:
        """
        Returns a cached gradient array for the given color.
        The gradient is 256x1 RGBA, fading from color at top to transparent at bottom.
        This is called once per color, then reused forever.
        
        Thread-safe: Uses lock to protect concurrent access to shared cache.
        """
        with cls._GRADIENT_CACHE_LOCK:
            if color_hex not in cls._GRADIENT_CACHE:
                rgb = mcolors.to_rgb(color_hex)
                gradient = np.zeros((RendererConstants.GRADIENT_IMAGE_HEIGHT, 1, 4))
                gradient[:, 0, :3] = rgb
                gradient[:, 0, 3] = np.linspace(RendererConstants.GRADIENT_ALPHA_TOP, RendererConstants.GRADIENT_ALPHA_BOTTOM, RendererConstants.GRADIENT_IMAGE_HEIGHT)
                cls._GRADIENT_CACHE[color_hex] = gradient
            return cls._GRADIENT_CACHE[color_hex]

    def _apply_gradient_fill(self, ax, x_data, y_data, color_hex: str, artist_prefix: str):
        """
        Creates or updates a gradient-filled area under a line plot.
        Uses the 'Reuse, Don't Recreate' pattern for live update performance.
        
        Args:
            ax: The matplotlib axes
            x_data: X coordinates (datetime objects or matplotlib float days)
            y_data: Y coordinates (speed values in Mbps)
            color_hex: Hex color string (e.g., '#00ff00')
            artist_prefix: 'download' or 'upload' - used to store artist references
        """
        if len(x_data) == 0 or len(y_data) == 0:
            return
        
        # Get cached gradient array (never regenerated after first call)
        gradient_array = self._get_cached_gradient(color_hex)
        
        # Calculate extent for the gradient image
        x_min = mdates.date2num(x_data[0]) if isinstance(x_data[0], datetime) else x_data.min()
        x_max = mdates.date2num(x_data[-1]) if isinstance(x_data[-1], datetime) else x_data.max()
        
        # Guard: Prevent singular extent (warning fix)
        if abs(x_max - x_min) < RendererConstants.EXTENT_EPSILON:
             x_max += RendererConstants.EXTENT_EPSILON # Add tiny epsilon
             
        y_max = float(np.max(y_data)) * RendererConstants.Y_AXIS_PADDING_FACTOR  # Slight padding
        
        # Guard: Prevent singular Y extent if y_max is 0 (flat line)
        if y_max < 0.001: 
             y_max = RendererConstants.FLAT_DATA_FALLBACK_RANGE  # Default to 1 Mbps range if flat 0
             
        extent = [x_min, x_max, 0, y_max]
        
        # Create polygon path for clipping
        x_numeric = mdates.date2num(x_data) if isinstance(x_data[0], datetime) else x_data
        verts = list(zip(x_numeric, y_data))
        verts.append((x_numeric[-1], 0))  # Close to baseline (right)
        verts.append((x_numeric[0], 0))   # Close to baseline (left)
        codes = [Path.MOVETO] + [Path.LINETO] * (len(verts) - 1)
        path = Path(verts, codes)
        
        # Get existing artists or create new ones
        im_attr = f'_gradient_im_{artist_prefix}'
        patch_attr = f'_gradient_patch_{artist_prefix}'
        
        existing_im = getattr(self, im_attr, None)
        existing_patch = getattr(self, patch_attr, None)
        
        # FAST PATH: Update existing artists
        if existing_im is not None and existing_im.axes is not None:
            try:
                # Update extent (changes position/scale)
                existing_im.set_extent(extent)
                # Update clip path (new data shape)
                new_patch = PathPatch(path, facecolor='none', edgecolor='none')
                ax.add_patch(new_patch)
                existing_im.set_clip_path(new_patch)
                # Remove old patch
                if existing_patch is not None:
                    try:
                        existing_patch.remove()
                    except Exception:
                        pass
                setattr(self, patch_attr, new_patch)
                return
            except Exception as e:
                self.logger.debug(f"Gradient update failed, recreating: {e}")
        
        # SLOW PATH: Create new artists (first render or after theme change)
        patch = PathPatch(path, facecolor='none', edgecolor='none')
        ax.add_patch(patch)
        
        im = ax.imshow(
            gradient_array, 
            aspect='auto', 
            extent=extent, 
            origin='lower', 
            zorder=1,  # Behind the line (which is zorder=10)
            interpolation='bilinear'
        )
        im.set_clip_path(patch)
        
        # Store references for future updates
        setattr(self, im_attr, im)
        setattr(self, patch_attr, patch)

    # ========== PHASE 3: ANALYTICAL INTELLIGENCE ==========
    def _get_peak_label_placement(self, ax, peak_x: float, peak_y: float) -> Tuple[Tuple[int, int], str, str]:
        """
        Decide peak label offset/alignment so labels stay inside the plot area.
        Returns (xytext_offset_points, horizontal_alignment, vertical_alignment).
        """
        x_offset = 8
        y_offset = 8
        ha = 'left'
        va = 'bottom'

        try:
            x_min, x_max = ax.get_xlim()
            y_min, y_max = ax.get_ylim()

            if x_max > x_min:
                x_norm = (peak_x - x_min) / (x_max - x_min)
                # If peak is in the right 20% of the graph, flip label to the left
                # Threshold reduced from 0.88 to 0.8 to be more aggressive against cutoffs.
                if x_norm >= 0.8:
                    x_offset = -8
                    ha = 'right'

            if y_max > y_min:
                y_norm = (peak_y - y_min) / (y_max - y_min)
                # If peak is in the top 10% of the graph, move label below peak
                if y_norm >= 0.9:
                    y_offset = -8
                    va = 'top'
        except Exception as e:
            self.logger.debug(f"Error calculating peak label placement: {e}")
            # Keep defaults if calculation fails

        return (x_offset, y_offset), ha, va
    
    def _add_peak_markers(self, ax, x_data, y_data, color_hex: str, label_prefix: str, artist_prefix: str):
        """
        Adds or updates a marker at the maximum value with a persistent label.
        Creates a "glowing dot" effect with outer glow + inner dot.
        Uses the 'Reuse, Don't Recreate' pattern.
        """
        if y_data is None or len(y_data) == 0:
            # Hide existing artists if no data
            artist_dict = getattr(self, f'_peak_artists_{artist_prefix}', {})
            for artist in artist_dict.values():
                if artist: artist.set_visible(False)
            return
        
        try:
            peak_idx = np.argmax(y_data)
            peak_x = x_data[peak_idx]
            peak_y = y_data[peak_idx]
            
            # Skip if peak is negligible (< 0.1 Mbps)
            if peak_y < 0.1:
                artist_dict = getattr(self, f'_peak_artists_{artist_prefix}', {})
                for artist in artist_dict.values():
                    if artist: artist.set_visible(False)
                return
            
            # Convert to matplotlib date number if needed
            if isinstance(peak_x, datetime):
                peak_x = mdates.date2num(peak_x)
            
            artist_dict = getattr(self, f'_peak_artists_{artist_prefix}', {})
            
            # FAST PATH: Update existing artists
            if artist_dict and 'inner' in artist_dict and artist_dict['inner'].axes is not None:
                artist_dict['outer'].set_data([peak_x], [peak_y])
                artist_dict['middle'].set_data([peak_x], [peak_y])
                artist_dict['inner'].set_data([peak_x], [peak_y])
                
                label = artist_dict['label']
                label_offset, label_ha, label_va = self._get_peak_label_placement(ax, peak_x, peak_y)
                label.xy = (peak_x, peak_y)
                label.set_position(label_offset)
                label.set_ha(label_ha)
                label.set_va(label_va)
                label.set_text(f"{label_prefix}: {peak_y:.1f} Mbps")
                label.get_bbox_patch().set_edgecolor(color_hex)
                
                for artist in artist_dict.values():
                    artist.set_visible(True)
                return

            # SLOW PATH: Create new artists
            artist_dict = {}
            
            # Outer glow (soft, large, transparent)
            artist_dict['outer'] = ax.plot(peak_x, peak_y, 'o', 
                    markersize=RendererConstants.PEAK_MARKER_SIZE_OUTER, 
                    color=color_hex, 
                    alpha=RendererConstants.PEAK_MARKER_ALPHA_OUTER,
                    zorder=10)[0]
            
            # Middle glow
            artist_dict['middle'] = ax.plot(peak_x, peak_y, 'o', 
                    markersize=RendererConstants.PEAK_MARKER_SIZE_MIDDLE, 
                    color=color_hex, 
                    alpha=RendererConstants.PEAK_MARKER_ALPHA_MIDDLE,
                    zorder=11)[0]
            
            # Inner dot (solid, small)
            artist_dict['inner'] = ax.plot(peak_x, peak_y, 'o', 
                    markersize=RendererConstants.PEAK_MARKER_SIZE_INNER, 
                    color=color_hex, 
                    alpha=RendererConstants.PEAK_MARKER_ALPHA_INNER,
                    zorder=12)[0]

            label_offset, label_ha, label_va = self._get_peak_label_placement(ax, peak_x, peak_y)
            
            # Label with background
            artist_dict['label'] = ax.annotate(
                f"{label_prefix}: {peak_y:.1f} Mbps",
                xy=(peak_x, peak_y),
                xytext=label_offset,
                textcoords='offset points',
                fontsize=8,
                color='white',
                weight='medium',
                ha=label_ha,
                va=label_va,
                bbox=dict(
                    boxstyle='round,pad=0.3', 
                    facecolor='#2d2d2d', 
                    edgecolor=color_hex,
                    linewidth=0.5,
                    alpha=0.9
                ),
                zorder=13
            )
            
            setattr(self, f'_peak_artists_{artist_prefix}', artist_dict)
            
        except Exception as e:
            self.logger.debug(f"Could not add/update peak marker: {e}")

    def _add_event_markers(self, ax, start_time: datetime, end_time: datetime, boot_time: Optional[datetime] = None):
        """
        Draws or updates vertical lines for significant system events (e.g., boot time).
        Uses 'Reuse, Don't Recreate' strategy.
        """
        if boot_time is None:
            for artist in self._event_artists:
                if artist: artist.set_visible(False)
            return
        
        try:
            # HARDENING: Ensure all values are datetimes before comparison
            if start_time is None or boot_time is None or end_time is None:
                 is_visible = False
            else:
                 is_visible = (start_time <= boot_time <= end_time)
            
            if self._event_artists and self._event_artists[0].axes is not None:
                # Update existing
                line, text = self._event_artists
                if is_visible:
                    boot_x = mdates.date2num(boot_time)
                    line.set_xdata([boot_x, boot_x])
                    y_top = ax.get_ylim()[1]
                    text.set_position((boot_x, y_top * 0.92))
                    
                line.set_visible(is_visible)
                text.set_visible(is_visible)
                return

            if not is_visible:
                return

            # Create new
            boot_x = mdates.date2num(boot_time)
            line = ax.axvline(
                x=boot_x, 
                color='#666', 
                linestyle='--', 
                linewidth=1, 
                alpha=0.6,
                zorder=5
            )
            
            y_top = ax.get_ylim()[1]
            text = ax.text(
                boot_x, y_top * 0.92, 
                ' ↑ Boot', 
                fontsize=7, 
                color='#888', 
                va='top',
                ha='left',
                style='italic',
                zorder=6
            )
            self._event_artists = [line, text]

        except Exception as e:
            self.logger.debug(f"Could not add/update event marker: {e}")


    def render(self, history_data, start_time: datetime, end_time: datetime, period_key: str, boot_time: Optional[datetime] = None, force_rebuild: bool = False, stat_type: str = "network"):
        """
        Renders the graph. 
        """
        if not history_data:
            for ax in self.axes: ax.clear()
            self.canvas.draw_idle()
            return None

        # Check if we need to clear everything
        rebuild_required = (
            force_rebuild or 
            period_key != self._last_period_key or
            stat_type != getattr(self, '_last_stat_type', 'network') or
            (stat_type != "overview" and self.line_download is None) # Overview handles its own lines
        )
        
        self._last_period_key = period_key
        self._last_stat_type = stat_type

        if rebuild_required:
            self.figure.clear()
            if stat_type == "overview":
                self._setup_overview_axes()
                # Overlays (peak markers, etc) are more complex in Overview, skip for now
            elif stat_type == "network":
                self._setup_standard_axes()
                self._format_axes()
            else:
                self._setup_standard_axes()
                self.ax_upload.set_visible(False)
                self.figure.subplots_adjust(hspace=0, bottom=0.15)
                self._format_hardware_axes(stat_type)
            
            self.line_download = None
            self.line_upload = None

        if stat_type == "overview":
            self._render_overview(history_data, actual_end=end_time)
        elif stat_type == "network":
            raw_data = np.array(history_data, dtype=float)
            timestamps = raw_data[:, 0]
            plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
            plot_datetimes_array = np.array(plot_datetimes)
            
            upload_mbps = (raw_data[:, 1] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            download_mbps = (raw_data[:, 2] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            
            plotted_ts, plotted_up, plotted_down = self._plot_high_res(plot_datetimes_array, upload_mbps, download_mbps, target_end_time=end_time)
            self._configure_axes(start_time, end_time, period_key, timestamps, plotted_up, plotted_down)
        else:
            raw_data = np.array(history_data, dtype=float)
            timestamps = raw_data[:, 0]
            plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
            plot_datetimes_array = np.array(plot_datetimes)
            values = raw_data[:, 1]
            color = constants.renderer.CPU_LINE_COLOR if stat_type == "cpu" else constants.renderer.GPU_LINE_COLOR
            plotted_ts, _, plotted_vals = self._plot_high_res(plot_datetimes_array, np.zeros_like(values), values, target_end_time=end_time, color=color)
            self._configure_hardware_axes(start_time, end_time, period_key, timestamps, plotted_vals)

        self.canvas.draw_idle()

    def _setup_standard_axes(self):
        """Creates standard 2-row layout."""
        self.ax_download = self.figure.add_subplot(2, 1, 1)
        self.ax_upload = self.figure.add_subplot(2, 1, 2, sharex=self.ax_download)
        self.axes = [self.ax_download, self.ax_upload]

    def _setup_overview_axes(self):
        """Creates 4-row layout for Overview."""
        self.ax_download = self.figure.add_subplot(4, 1, 1)
        self.ax_upload = self.figure.add_subplot(4, 1, 2, sharex=self.ax_download)
        self.ax_cpu = self.figure.add_subplot(4, 1, 3, sharex=self.ax_download)
        self.ax_gpu = self.figure.add_subplot(4, 1, 4, sharex=self.ax_download)
        self.axes = [self.ax_download, self.ax_upload, self.ax_cpu, self.ax_gpu]
        
        # Basic overview formatting
        self.figure.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.1, hspace=0.3)
        for ax in self.axes:
            ax.spines['right'].set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.tick_params(colors=getattr(self, '_current_text_color', 'white'), labelsize=8)
            ax.grid(True, linestyle=':', alpha=0.3)
            if ax != self.ax_gpu:
                ax.tick_params(labelbottom=False)

        self.ax_download.set_ylabel("DW", fontsize=8)
        self.ax_upload.set_ylabel("UP", fontsize=8)
        self.ax_cpu.set_ylabel("CPU", fontsize=8)
        self.ax_gpu.set_ylabel("GPU", fontsize=8)

    def _render_overview(self, data_dict, actual_end):
        """Internal helper for overview plotting."""
        # Plot Network
        net = np.array(data_dict.get("network", []), dtype=float)
        if len(net) > 0:
            ts = [datetime.fromtimestamp(t) for t in net[:, 0]]
            up = (net[:, 1] * 8) / 1e6
            dw = (net[:, 2] * 8) / 1e6
            self.ax_download.plot(ts, dw, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1)
            self.ax_upload.plot(ts, up, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1)
            self.ax_download.set_ylim(bottom=0, top=max(dw)*1.2 if max(dw)>0 else 1)
            self.ax_upload.set_ylim(bottom=0, top=max(up)*1.2 if max(up)>0 else 1)

        # Plot CPU
        cpu = np.array(data_dict.get("cpu", []), dtype=float)
        if len(cpu) > 0:
            ts = [datetime.fromtimestamp(t) for t in cpu[:, 0]]
            self.ax_cpu.plot(ts, cpu[:, 1], color=constants.renderer.CPU_LINE_COLOR, linewidth=1)
            self.ax_cpu.set_ylim(0, 100)

        # Plot GPU
        gpu = np.array(data_dict.get("gpu", []), dtype=float)
        if len(gpu) > 0:
            ts = [datetime.fromtimestamp(t) for t in gpu[:, 0]]
            self.ax_gpu.plot(ts, gpu[:, 1], color=constants.renderer.GPU_LINE_COLOR, linewidth=1)
            self.ax_gpu.set_ylim(0, 100)

        # Add Event Markers (system boot)
        self._add_event_markers(self.ax_download, start_time, end_time, boot_time=boot_time)
        
        self.canvas.draw_idle()

        # Return the DATA THAT WAS ACTUALLY PLOTTED
        plotted_x_coords = None
        if plotted_ts is not None and len(plotted_ts) > 0 and isinstance(plotted_ts[0], datetime):
             plotted_x_coords = date2num(plotted_ts)
             plotted_ts = np.array([dt.timestamp() for dt in plotted_ts])

        if stat_type == "network":
            # Convert Mbps back to Bytes/s
            if plotted_up is not None:
                plotted_up = (plotted_up * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            if plotted_down is not None:
                plotted_down = (plotted_down * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            return plotted_ts, plotted_x_coords, plotted_up, plotted_down
        else:
            return plotted_ts, plotted_x_coords, np.zeros_like(plotted_vals), plotted_vals

    def _format_hardware_axes(self, stat_type: str):
        """Formats the single axis for hardware utilization."""
        label = "CPU Utilization (%)" if stat_type == "cpu" else "GPU Utilization (%)"
        self.ax_download.set_ylabel(label, color=self._current_text_color)
        self.ax_download.tick_params(labelbottom=True, colors=self._current_text_color, which='both')
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        
        # Make the top plot take up more space
        self.ax_download.set_position([0.12, 0.15, 0.86, 0.80])

    def _configure_hardware_axes(self, start_time, end_time, period_key, timestamps, values):
        """Sets limits and formatters for hardware stats."""
        # Y-Axis is always 0-100 for utilization
        self.ax_download.set_ylim(0, 100)
        self.ax_download.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}%")
        
        # X-Axis limits
        if len(timestamps) > 0:
            xlim_start = start_time or datetime.fromtimestamp(timestamps.min())
            xlim_end = end_time or datetime.fromtimestamp(timestamps.max())
            self.ax_download.set_xlim(xlim_start, xlim_end)
        
        self._configure_xaxis_format(period_key)

        
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

    def _plot_aggregated(self, plot_datetimes, upload_mbps, download_mbps, mode="daily", target_end_time=None):
        """
        Plots aggregated data using Bar Charts for "thicker" visibility.
        Uses ax.bar for distinct time blocks.
        """
        if len(plot_datetimes) == 0: return [], [], []

        # Binning logic... (rest of the code remains same until end of method)

        # 1. Binning Logic (Safety: ensures alignment even if SQL returns slight offsets)
        if mode == "daily":
            # Bin by ord day
            bins = np.array([dt.date().toordinal() for dt in plot_datetimes])
            bar_width = 0.8 # Days
        elif mode == "hourly":
            # Bin by hour: ordinal * 24 + hour
            bins = np.array([dt.date().toordinal() * 24 + dt.hour for dt in plot_datetimes])
            bar_width = 0.8 / 24.0 # Hours converted to days
        else:
             # Fallback to lines for minute/raw
             return self._plot_high_res(plot_datetimes, upload_mbps, download_mbps)
            
        unique_bins, indices = np.unique(bins, return_inverse=True)
        counts = np.bincount(indices)
        
        # Use per-bin peak speeds so timeline changes preserve event amplitude.
        down_peak = np.full(len(unique_bins), -np.inf, dtype=float)
        up_peak = np.full(len(unique_bins), -np.inf, dtype=float)
        np.maximum.at(down_peak, indices, download_mbps)
        np.maximum.at(up_peak, indices, upload_mbps)
        down_peak = np.where(np.isfinite(down_peak), down_peak, 0.0)
        up_peak = np.where(np.isfinite(up_peak), up_peak, 0.0)
        
        # Calculate Bin Centers (Timestamps)
        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        bin_timestamps = np.bincount(indices, weights=timestamps_float) / counts
        agg_dates = [datetime.fromtimestamp(ts) for ts in bin_timestamps]
        
        # 2. Render Bars
        # Since bars are hard to animate efficiently (number of bars changes),
        # we treat this as a "Rebuild" scenario for simplicity. 
        # Aggregated views don't update frequently enough to cause lag.
        
        # Clear previous lines if switching modes
        if self.line_download: 
            self.line_download.remove(); self.line_download = None
        if self.line_upload:
            self.line_upload.remove(); self.line_upload = None
        if self.fill_download:
            self.fill_download.remove(); self.fill_download = None
        if self.fill_upload:
            self.fill_upload.remove(); self.fill_upload = None
            
        # Check for existing bars to update (optimization)
        # We store the container in self.bars_download / self.bars_upload
        existing_bars_down = getattr(self, 'bars_download', None)
        existing_bars_up = getattr(self, 'bars_upload', None)
        
        # If count matches, we can update heights? No, positions might change.
        # Safest is to remove and redraw for bars.
        if existing_bars_down:
            for b in existing_bars_down: b.remove()
        if existing_bars_up:
            for b in existing_bars_up: b.remove()
            
        self.bars_download = self.ax_download.bar(
            agg_dates, down_peak, width=bar_width,
            color=constants.graph.DOWNLOAD_LINE_COLOR, alpha=0.9, zorder=10
        )
        
        self.bars_upload = self.ax_upload.bar(
            agg_dates, up_peak, width=bar_width,
            color=constants.graph.UPLOAD_LINE_COLOR, alpha=0.9, zorder=10
        )
        
        # Store for cleanup
        setattr(self, 'bars_download', self.bars_download)
        setattr(self, 'bars_upload', self.bars_upload)

        # === TRAILING BRIDGE for Aggregated Views ===
        # If bars don't reach now, draw a subtle flat line
        if target_end_time is not None and len(agg_dates) > 0:
            last_date = agg_dates[-1]
            gap_to_now = (target_end_time - last_date).total_seconds()
            
            # Use appropriate threshold based on mode
            threshold = 3600 * 24 if mode == "daily" else 3600
            
            if gap_to_now > threshold:
                bridge_ts = [last_date, target_end_time]
                bridge_zero = [0.0, 0.0]
                self.ax_download.plot(bridge_ts, bridge_zero, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5, zorder=9, alpha=0.4, linestyle='--')
                self.ax_upload.plot(bridge_ts, bridge_zero, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5, zorder=9, alpha=0.4, linestyle='--')

        return agg_dates, up_peak, down_peak

    def _plot_high_res(self, plot_datetimes, upload_mbps, download_mbps, target_end_time=None, color=None):
        """Segmented Plotting with Gap Detection, Gradient Fills, and Fluid Interpolation"""
        if len(plot_datetimes) == 0:
            return plot_datetimes, upload_mbps, download_mbps

        # Colors
        color_down = color or constants.graph.DOWNLOAD_LINE_COLOR
        color_up = constants.graph.UPLOAD_LINE_COLOR

        # Adaptive Gap Detection: Calculate threshold from data's natural interval
        # A "gap" is when time between points is significantly larger than normal
        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        intervals = np.diff(timestamps_float)
        
        if len(intervals) > 0:
            median_interval = np.median(intervals)
            # Gap = any interval > 2x the median (accounts for jitter + actual gaps)
            gap_threshold = max(median_interval * RendererConstants.GAP_DETECTION_MULTIPLIER, RendererConstants.MIN_GAP_THRESHOLD_SEC)
        else:
            gap_threshold = RendererConstants.MIN_GAP_THRESHOLD_SEC
            
        gaps = intervals > gap_threshold
        
        # Prepare lists for collecting interpolated segments
        final_ts, final_up, final_down = [], [], []
        
        # Adaptive Quality: If we have > 600 points, interpolation is visually redundant.
        # Skip it to save CPU.
        ENABLE_SPLINE = len(plot_datetimes) <= RendererConstants.SPLINE_INTERPOLATION_POINT_THRESHOLD
        
        if np.any(gaps):
            # Segmented mode - multiple disconnected line segments
            self.ax_download.clear()
            self.ax_upload.clear()
            self._format_axes()
            self.line_download = None # Sentinel
            
            gap_indices = np.where(gaps)[0] + 1
            segments_ts = np.split(plot_datetimes, gap_indices)
            segments_up = np.split(upload_mbps, gap_indices)
            segments_down = np.split(download_mbps, gap_indices)
            
            prev_seg_end = None  # Track end of previous segment for bridging
            
            for seg_idx, (ts, up, down) in enumerate(zip(segments_ts, segments_up, segments_down)):
                if len(ts) == 0: continue
                
                # === GAP BRIDGING: Draw flat line at 0 between segments ===
                if prev_seg_end is not None:
                    # Draw a bridge from previous segment end to this segment start
                    # Goes down to 0, across, then back up (like ECG flatline)
                    bridge_start = prev_seg_end
                    bridge_end = ts[0]
                    
                    # Flat line at 0 across the gap
                    bridge_ts = [bridge_start, bridge_end]
                    bridge_zero = [0.0, 0.0]
                    
                    self.ax_download.plot(bridge_ts, bridge_zero, color=color_down, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')
                    self.ax_upload.plot(bridge_ts, bridge_zero, color=color_up, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')
                
                # Process and interpolate this segment
                seg_ts, seg_up, seg_down = self._process_plot_segment(ts, up, down, enable_spline=ENABLE_SPLINE)
                
                # Plot
                self.ax_download.plot(seg_ts, seg_down, color=color_down, linewidth=1.5, zorder=10)
                self.ax_upload.plot(seg_ts, seg_up, color=color_up, linewidth=1.5, zorder=10)
                
                # Add Gradient (per segment)
                self._apply_gradient_fill(self.ax_download, np.array(seg_ts), seg_down, color_down, 'download')
                self._apply_gradient_fill(self.ax_upload, np.array(seg_ts), seg_up, color_up, 'upload')
                
                # Accrue for return (just raw or interpolated? detailed return allows tooltips to snap to curve)
                final_ts.extend(seg_ts)
                final_up.extend(seg_up)
                final_down.extend(seg_down)
                
                # Track for next iteration
                prev_seg_end = ts[-1]

            # === TRAILING BRIDGE: Bridge from last point to now ===
            if target_end_time is not None:
                last_ts = plot_datetimes[-1]
                gap_to_now = (target_end_time - last_ts).total_seconds()
                
                if gap_to_now > gap_threshold:
                    bridge_ts = [last_ts, target_end_time]
                    bridge_zero = [0.0, 0.0]
                    self.ax_download.plot(bridge_ts, bridge_zero, color=constants.graph.DOWNLOAD_LINE_COLOR, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')
                    self.ax_upload.plot(bridge_ts, bridge_zero, color=constants.graph.UPLOAD_LINE_COLOR, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')
                
        else:
            # FAST PATH: Single continuous line
            # Process and interpolate the whole chunk
            dense_ts, dense_up, dense_down = self._process_plot_segment(plot_datetimes, upload_mbps, download_mbps, enable_spline=ENABLE_SPLINE)
            
            final_ts, final_up, final_down = dense_ts, dense_up, dense_down
            
            if self.line_download is not None and self.line_download.axes is not None:
                try:
                    self.line_download.set_data(dense_ts, dense_down)
                    self.line_upload.set_data(dense_ts, dense_up)
                except Exception as e:
                    self.logger.debug(f"High-res update failed, rebuilding: {e}")
                    self.line_download, = self.ax_download.plot(dense_ts, dense_down, color=color_down, linewidth=1.5, zorder=10)
                    self.line_upload, = self.ax_upload.plot(dense_ts, dense_up, color=color_up, linewidth=1.5, zorder=10)
            else:
                self.line_download, = self.ax_download.plot(
                    dense_ts, dense_down, 
                    color=color_down, linewidth=1.5, zorder=10
                )
                self.line_upload, = self.ax_upload.plot(
                    dense_ts, dense_up, 
                    color=color_up, linewidth=1.5, zorder=10
                )
            
            # Apply premium gradient fills
            self._apply_gradient_fill(
                self.ax_download, np.array(dense_ts), dense_down,
                color_down, 'download'
            )
            self._apply_gradient_fill(
                self.ax_upload, np.array(dense_ts), dense_up,
                color_up, 'upload'
            )

            # === TRAILING BRIDGE (Fast Path): Bridge from last point to now ===
            if target_end_time is not None:
                last_ts = plot_datetimes[-1]
                gap_to_now = (target_end_time - last_ts).total_seconds()
                
                if gap_to_now > gap_threshold:
                    bridge_ts = [last_ts, target_end_time]
                    bridge_zero = [0.0, 0.0]
                    self.ax_download.plot(bridge_ts, bridge_zero, color=color_down, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')
                    self.ax_upload.plot(bridge_ts, bridge_zero, color=color_up, linewidth=1.5, zorder=9, alpha=0.5, linestyle='--')

        # Return the INTERPOLATED data so interactions snap to the smooth line
        return np.array(final_ts), np.array(final_up), np.array(final_down)

    def _process_plot_segment(self, ts_dates, upload_data, download_data, enable_spline: bool = True):
        """
        Process and interpolate a single plot segment.
        
        Extracted from nested function in _plot_high_res for unit testability.
        Applies monotone cubic interpolation if enabled and data allows.
        
        Args:
            ts_dates: Array of datetime objects
            upload_data: Array of upload speeds in Mbps
            download_data: Array of download speeds in Mbps
            enable_spline: Whether to enable interpolation
            
        Returns:
            Tuple of (interpolated_datetimes, interpolated_upload, interpolated_download) 
        """
        if not enable_spline or len(ts_dates) < 2:
            # Bypass interpolation, return raw data
            return ts_dates, upload_data, download_data
        
        try:
            # FLUID MOTION: Apply Monotone Cubic Spline (Vectorized)
            # We interpolate based on timestamp float values
            ts_floats = np.array([t.timestamp() for t in ts_dates])
            
            # Density 4 provides ample smoothness
            dense_ts_floats, dense_down = calculate_monotone_cubic_interpolation(ts_floats, download_data, density=RendererConstants.SPLINE_INTERPOLATION_DENSITY)
            _, dense_up = calculate_monotone_cubic_interpolation(ts_floats, upload_data, density=RendererConstants.SPLINE_INTERPOLATION_DENSITY)
            
            # Clip negative values
            dense_down = np.maximum(dense_down, 0)
            dense_up = np.maximum(dense_up, 0)
            
            # Convert back to datetimes for Matplotlib
            dense_ts_dt = [datetime.fromtimestamp(t) for t in dense_ts_floats]
            
            return dense_ts_dt, dense_up, dense_down
            
        except Exception as e:
            # If interpolation fails, return raw data as fallback
            self.logger.debug(f"Segment interpolation failed, using raw data: {e}")
            return ts_dates, upload_data, download_data



    def _configure_axes(self, start_time, end_time, period_key, timestamps, upload_mbps, download_mbps):
        """Sets limits and Formatters with sticky behavior to prevent jitter."""
        # Y-Axis Scaling (Sticky Logic with smart rounding)
        max_up = np.max(upload_mbps) if len(upload_mbps) > 0 else 0
        max_down = np.max(download_mbps) if len(download_mbps) > 0 else 0
        
        y_top_up = self._get_sticky_y_top(max_up, self._current_ylim_up)
        y_top_down = self._get_sticky_y_top(max_down, self._current_ylim_down)
        
        self._current_ylim_up = y_top_up
        self._current_ylim_down = y_top_down
        
        # X-Axis Limits
        # Ensure tight fit and consistent range extension
        if len(timestamps) > 0:
            if period_key == "TIMELINE_SESSION":
                # Fit tightly to actual data range for SESSION
                min_dt = datetime.fromtimestamp(timestamps.min())
                max_dt = datetime.fromtimestamp(timestamps.max())
                self.ax_upload.set_xlim(min_dt, max_dt)
                self.ax_download.set_xlim(min_dt, max_dt)
            elif start_time and end_time:
                # Use requested range (standardizes width for BOOT, 24H, etc.)
                self.ax_upload.set_xlim(start_time, end_time)
                self.ax_download.set_xlim(start_time, end_time)
            else:
                # Fallback: tight fit if no range requested
                min_dt = datetime.fromtimestamp(timestamps.min())
                max_dt = datetime.fromtimestamp(timestamps.max())
                self.ax_upload.set_xlim(min_dt, max_dt)
                self.ax_download.set_xlim(min_dt, max_dt)
        elif start_time and end_time:
             # Even if no data, show the requested range empty
             self.ax_upload.set_xlim(start_time, end_time)
             self.ax_download.set_xlim(start_time, end_time)

        # === Y-AXIS: Linear scale with smart tick placement ===
        # Only show significant tick marks (multiples that make sense for the range)
        self.ax_upload.set_yscale('linear')
        self.ax_download.set_yscale('linear')
        
        # Set smart integer ticks based on the Y-axis range
        self._set_smart_y_ticks(self.ax_upload, y_top_up)
        self._set_smart_y_ticks(self.ax_download, y_top_down)
        
        # Formatters for clean labels (no decimals for large ranges)
        self.ax_upload.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}" if x >= 1 else f"{x:.1f}")
        self.ax_download.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}" if x >= 1 else f"{x:.1f}")

        self.ax_upload.set_ylim(bottom=0, top=y_top_up)
        self.ax_download.set_ylim(bottom=0, top=y_top_down)

        # X-Axis Formatting
        self._configure_xaxis_format(period_key)

    def _set_smart_y_ticks(self, ax, y_max: float):
        """
        Sets intelligent Y-axis tick locations using Matplotlib's MaxNLocator.
        This provides clean, non-overlapping labels regardless of the data scale.
        """
        from matplotlib.ticker import MaxNLocator
        
        if y_max <= 0:
            ax.set_yticks([0])
            return
        
        # Use MaxNLocator for robust, nicely-spaced ticks without overlap
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 2.5, 5, 10], min_n_ticks=3))


    def _get_nice_y_axis_top(self, max_speed: float) -> float:
        """
        Calculates a "nice" top limit with flat ~12% padding and logical step rounding.
        Prevents massive empty space jumps (e.g. snapping 1000 to 2000).
        """
        if max_speed <= constants.graph.MINIMUM_Y_AXIS_MBPS:
            return constants.graph.MINIMUM_Y_AXIS_MBPS
        
        # Add ~12% padding for visual breathing room
        padded = max_speed * 1.12
        
        # Logical rounding steps based on the value size
        if padded <= 10:
            step = 1
        elif padded <= 50:
            step = 5
        elif padded <= 100:
            step = 10
        elif padded <= 500:
            step = 50
        elif padded <= 1000:
            step = 100
        else:
            step = 250
            
        # Round up to the nearest multiple of the step
        return float(math.ceil(padded / step) * step)
    
    def _get_sticky_y_top(self, max_speed: float, current_top: float) -> float:
        """
        Calculates a top limit with sticky behavior.
        Only updates if we exceed current or drop significantly below it.
        Uses smart rounding to nice numbers.
        """
        min_limit = constants.graph.MINIMUM_Y_AXIS_MBPS
        
        # 1. If we exceed current, scale up immediately to nice number
        if max_speed > current_top:
            return self._get_nice_y_axis_top(max_speed)
            
        # 2. If we drop below 70% of current, scale down to nice number
        if max_speed < current_top * 0.7:
            suggested = self._get_nice_y_axis_top(max_speed)
            return max(suggested, min_limit)
            
        # 3. Otherwise, stay sticky
        return current_top

    def _configure_xaxis_format(self, period_key: str) -> None:
        """
        Intelligently configures the x-axis locator and formatter.
        """
        axis = self.ax_upload
        
        # Default for most views
        major_locator = mdates.AutoDateLocator(maxticks=8)
        major_formatter = mdates.DateFormatter('%H:%M') 

        # For extremely short views (e.g. freshly started Session), show seconds
        # This prevents repeating labels like "19:27, 19:27, 19:27"
        xlim = axis.get_xlim()
        if xlim[0] < xlim[1]:
            duration_days = xlim[1] - xlim[0]
            duration_sec = duration_days * 86400
            if duration_sec < 120: # Less than 2 minutes
                major_formatter = mdates.DateFormatter('%H:%M:%S')
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
        Uses gradient fills for premium visual effect.
        """
        if not hasattr(self, 'line_download') or not self.line_download or self.line_download.axes is None:
            return False
                
        # Update X/Y data for lines
        self.line_download.set_data(plot_datetimes, download_mbps)
        self.line_upload.set_data(plot_datetimes, upload_mbps)
        
        # Update gradient fills
        self._apply_gradient_fill(
            self.ax_download, np.array(plot_datetimes), download_mbps,
            constants.graph.DOWNLOAD_LINE_COLOR, 'download'
        )
        self._apply_gradient_fill(
            self.ax_upload, np.array(plot_datetimes), upload_mbps,
            constants.graph.UPLOAD_LINE_COLOR, 'upload'
        )
        
        # Update Limits and Formatters (Using sticky logic)
        timestamps = np.array([dt.timestamp() for dt in plot_datetimes])
        self._configure_axes(start_time, end_time, "TIMELINE_SESSION", timestamps, upload_mbps, download_mbps)
        
        self.canvas.draw_idle()
        return True
