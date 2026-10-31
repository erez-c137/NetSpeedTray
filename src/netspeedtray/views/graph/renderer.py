import bisect
import logging
import math
from typing import List, Tuple, Optional
from datetime import datetime
import threading

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
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
from netspeedtray.utils.helpers import calculate_monotone_cubic_interpolation, format_decimal
from netspeedtray.utils.mpl_fonts import configure_cjk_font
from netspeedtray.views.graph.time_axis import apply_time_axis
from netspeedtray.views.graph.value_axis import (ScaledMaxNLocator, ValueAxisFormatter, clipped_axis_max,
                                                 clipped_runs, speed_axis_unit)
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

        # Ensure non-Latin graph labels render with real glyphs instead of tofu boxes: CJK (ja/ko/zh,
        # #161) and Hebrew (he, item 6). Uses a Windows system font; a no-op for Latin locales.
        configure_cjk_font(getattr(self.i18n, "language", None))

        # UI Elements
        self.figure = None
        self.canvas = None
        self.ax_download = None
        self.ax_upload = None
        self.ax_cpu = None
        self.ax_gpu = None
        self.ax_ram = None
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
        self._clip_artists = {}   # id(axes) -> the clipped-outlier marks drawn on it
        
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
        # matplotlib marks the canvas opaque, so Qt never paints its background; let it, so the edge
        # sliver the buffer doesn't cover takes the graph color set in apply_theme.
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.canvas.setAutoFillBackground(True)
        
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
        self.ax_download.set_ylabel(self._lbl(self.i18n.DOWNLOAD_LABEL), color=self._current_text_color)
        self.ax_download.tick_params(labelbottom=False, colors=self._current_text_color, which='both') 
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        self.ax_download.yaxis.label.set_color(self._current_text_color) # Redundant but safe

        # Upload Axis (Bottom)
        self.ax_upload.set_ylabel(self._lbl(self.i18n.UPLOAD_LABEL), color=self._current_text_color)
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
        # The rendered buffer can fall a pixel short of the widget at fractional DPI scaling; fill the
        # widget itself with the graph color so that sliver isn't left white.
        if self.canvas is not None:
            pal = self.canvas.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor(graph_bg))
            self.canvas.setPalette(pal)

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

    def _apply_gradient_fill(self, ax, x_data, y_data, color_hex: str, artist_prefix: str,
                             reuse: bool = True):
        """
        Creates or updates a gradient-filled area under a line plot.
        Uses the 'Reuse, Don't Recreate' pattern for live update performance.

        Args:
            ax: The matplotlib axes
            x_data: X coordinates (datetime objects or matplotlib float days)
            y_data: Y coordinates (speed values in Mbps)
            color_hex: Hex color string (e.g., '#00ff00')
            artist_prefix: 'download' or 'upload' - used to store artist references
            reuse: False for one SEGMENT of a gapped series - a fresh, uncached fill per segment. The
                cached pair is one fill per axis, so reusing it moved the fill on to each next segment
                and only the LAST segment kept one (the Network 'All' view filled from Sep 6 on).
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
        if reuse and existing_im is not None and existing_im.axes is not None:
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
        if reuse:
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
                label.set_text(f"{label_prefix}: {format_decimal(peak_y, self.i18n, 1)} {self.i18n.MBITS_UNIT}")
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
                f"{label_prefix}: {format_decimal(peak_y, self.i18n, 1)} {self.i18n.MBITS_UNIT}",
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
                f" ↑ {self._lbl(getattr(self.i18n, 'GRAPH_BOOT_MARKER', 'Boot'))}",
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


    def render(self, history_data, start_time: datetime, end_time: datetime, period_key: str, boot_time: Optional[datetime] = None, force_rebuild: bool = False, stat_type: str = "network", hw_styles: Optional[dict] = None):
        """
        Renders the graph.

        ``hw_styles`` (Monitor only): {"cpu": (color, linestyle), "gpu": (color, linestyle)} for the
        combined ``hwcombined`` mode; None falls back to the vendor-aware defaults.
        """
        # Initialize for return statement
        plotted_ts, plotted_up, plotted_down, plotted_vals = None, None, None, None

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
            elif stat_type == "hwcombined":
                self._setup_standard_axes()
                self.ax_upload.set_visible(False)
                self.figure.subplots_adjust(hspace=0, bottom=0.15)
                self._format_hwcombined_axes()
            elif stat_type == "hwseparate":
                self._setup_hwseparate_axes()
            else:
                self._setup_standard_axes()
                self.ax_upload.set_visible(False)
                self.figure.subplots_adjust(hspace=0, bottom=0.15)
                self._format_hardware_axes(stat_type)
            
            self.line_download = None
            self.line_upload = None
            
            # Re-apply theme after clearing figure (fixes dark mode load bug)
            if hasattr(self, '_is_dark_mode'):
                self.apply_theme(self._is_dark_mode)

        if not history_data:
            for ax in self.axes:
                ax.clear()
                # Restore labels if cleared for single-axis stats (prevent empty axes look)
                if stat_type == "hwcombined":
                    self._format_hwcombined_axes()
                elif stat_type == "hwseparate":
                    self._format_hwseparate_axes()
                elif stat_type != "overview" and stat_type != "network":
                    self._format_hardware_axes(stat_type)
            if stat_type in ("cpu", "gpu") and hw_styles is not None:   # the Monitor's toggle layout
                self._draw_hw_empty_message(self.ax_download, period_key, hw_styles, roles=(stat_type,))
            elif stat_type == "network":
                # Since the reader stopped padding (S4) an empty window really is empty: a fresh install,
                # a range before history starts, the first hour after boot. Keep the frame a reader can
                # orient by - the requested time range, the time axis and the unit labels - under the
                # note, instead of a bare 0-1 grid. The cleared lines are gone; never reuse them.
                self._net_styles = hw_styles or {}
                self.line_download = None
                self.line_upload = None
                self._clip_artists = {}
                self._net_caption_artist = None
                self._format_axes()
                self._configure_axes(start_time, end_time, period_key, np.empty(0), np.empty(0),
                                     np.empty(0), plotted_ts=None)
                self._draw_net_empty_message(period_key, start_time, end_time, hw_styles)
            self.canvas.draw_idle()
            return None

        if stat_type == "overview":
            self._render_overview(history_data, start_time=start_time, end_time=end_time, period_key=period_key, boot_time=boot_time)
        elif stat_type == "hwcombined":
            self._render_hwcombined(history_data, start_time, end_time, period_key, hw_styles)
        elif stat_type == "hwseparate":
            self._render_hwseparate(history_data, start_time, end_time, period_key, hw_styles)
        elif stat_type == "network":
            self._clear_net_empty_message()
            # GraphHost's network styles: unit settings (S2), the stored resolution (S3) and the
            # measured spans (S4). The standalone callers pass none and get the defaults.
            self._net_styles = hw_styles or {}
            safe_data = [
                (ts.timestamp() if isinstance(ts, datetime) else float(ts), float(up), float(dn))
                for ts, up, dn in history_data
            ]
            raw_data = np.array(safe_data, dtype=float)
            timestamps = raw_data[:, 0]
            plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
            plot_datetimes_array = np.array(plot_datetimes)

            upload_mbps = (raw_data[:, 1] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR
            download_mbps = (raw_data[:, 2] * constants.network.units.BITS_PER_BYTE) / constants.network.units.MEGA_DIVISOR

            # The Session view fits the x-axis to its data, so it has no leading/trailing silence.
            ranged = period_key != "TIMELINE_SESSION"
            resolution = self._net_styles.get("resolution")
            plotted_ts, plotted_up, plotted_down = self._plot_high_res(
                plot_datetimes_array, upload_mbps, download_mbps,
                target_end_time=end_time if ranged else None, measured=self._net_styles.get("measured"),
                start_time=start_time if ranged else None,
                bin_seconds=constants.graph.RESOLUTION_BIN_SECONDS.get(resolution))
            self._configure_axes(start_time, end_time, period_key, timestamps, plotted_up, plotted_down,
                                 plotted_ts=plotted_ts)
            self._draw_net_caption(resolution if ranged else None)
            if not raw_data[:, 1:].any():
                # Nothing but zero rows (an idle live Session, or only flagged empty intervals): say
                # so, rather than draw a flat 0 line that reads as "no traffic".
                self._draw_net_empty_message(period_key, start_time, end_time, hw_styles)
        elif stat_type in ("cpu", "gpu") and hw_styles is not None:
            # Monitor's "toggle" layout: a single CPU- or GPU-only line that must honor the same
            # display settings as combined/separate (per-role color, Smooth, fixed/auto y-axis). The
            # standalone GraphWindow passes no hw_styles, so it keeps the legacy path below (+ tooltips).
            self._render_hwsingle(history_data, start_time, end_time, period_key, stat_type, hw_styles)
        else:
            raw_data = np.array(history_data, dtype=float)
            timestamps = raw_data[:, 0]
            plot_datetimes = [datetime.fromtimestamp(t) for t in timestamps]
            plot_datetimes_array = np.array(plot_datetimes)
            values = raw_data[:, 1]
            color = constants.renderer.CPU_LINE_COLOR if stat_type == "cpu" else constants.renderer.GPU_LINE_COLOR
            plotted_ts, _, plotted_vals = self._plot_high_res(plot_datetimes_array, np.zeros_like(values), values, target_end_time=end_time, color=color)
            self._configure_hardware_axes(start_time, end_time, period_key, timestamps, plotted_vals)

        self.canvas.draw()

        # Return data for tooltip/interaction cache
        from matplotlib.dates import date2num
        plotted_x_coords = None
        
        if plotted_ts is not None and len(plotted_ts) > 0 and isinstance(plotted_ts[0], datetime):
            plotted_x_coords = date2num(plotted_ts)
            plotted_ts = np.array([dt.timestamp() for dt in plotted_ts])

        if stat_type == "network" and plotted_ts is not None:
            if plotted_up is not None:
                plotted_up = (plotted_up * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            if plotted_down is not None:
                plotted_down = (plotted_down * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            return plotted_ts, plotted_x_coords, plotted_up, plotted_down
        elif plotted_ts is not None:
            return plotted_ts, plotted_x_coords, np.zeros_like(plotted_vals), plotted_vals
            
        return None

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

        self._format_overview_axes()

    def _format_overview_axes(self):
        """Applies consistent styling for the 4-row Overview layout (used on rebuild and live refresh)."""
        is_dark = getattr(self, "_is_dark_mode", True)
        graph_bg = style_constants.GRAPH_BG_DARK if is_dark else style_constants.GRAPH_BG_LIGHT
        text_color = getattr(self, "_current_text_color", "white")
        grid_color = getattr(self, "_current_grid_color", "#444")

        self.figure.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.1, hspace=0.3)

        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            ax.spines['right'].set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.tick_params(colors=text_color, labelsize=8)
            ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=0.3, color=grid_color)
            for spine in ax.spines.values():
                spine.set_color(grid_color)

            if ax != getattr(self, "ax_gpu", None):
                ax.tick_params(labelbottom=False)

        self.ax_download.set_ylabel(self._lbl(self.i18n.DOWNLOAD_LABEL), fontsize=8, color=text_color)
        self.ax_upload.set_ylabel(self._lbl(self.i18n.UPLOAD_LABEL), fontsize=8, color=text_color)
        self.ax_cpu.set_ylabel(self._lbl(self.i18n.ORDER_TYPE_CPU), fontsize=8, color=text_color)
        self.ax_gpu.set_ylabel(self._lbl(self.i18n.ORDER_TYPE_GPU), fontsize=8, color=text_color)

    @staticmethod
    def _gap_threshold(timestamps) -> float:
        """Seconds between two points beyond which the series has a GAP (no data), from the data's own
        spacing: the median interval x GAP_DETECTION_MULTIPLIER, at least MIN_GAP_THRESHOLD_SEC. One
        rule for the network and the hardware graphs."""
        intervals = np.diff(np.asarray(timestamps, dtype=float))
        if len(intervals) == 0:
            return RendererConstants.MIN_GAP_THRESHOLD_SEC
        return max(float(np.median(intervals)) * RendererConstants.GAP_DETECTION_MULTIPLIER,
                   RendererConstants.MIN_GAP_THRESHOLD_SEC)

    @classmethod
    def _gap_segments(cls, timestamps) -> List[slice]:
        """``timestamps`` (epoch seconds, ascending) split at every gap (``_gap_threshold``)."""
        ts = np.asarray(timestamps, dtype=float)
        if ts.size == 0:
            return []
        cuts = (np.flatnonzero(np.diff(ts) > cls._gap_threshold(ts)) + 1).tolist()
        bounds = [0] + cuts + [ts.size]
        return [slice(a, b) for a, b in zip(bounds[:-1], bounds[1:])]

    def _plot_hw_series(self, ax, ts, values, color, linestyle, label, smooth: bool = False,
                        smooth_window: int = 5):
        """One hardware series as one line PER GAP-FREE SEGMENT (the network graph's gap rule), so a
        month without data is a break, not a straight line drawn across it. Segments share ``label``
        (the hover tooltip collapses them to one row); smoothing runs per segment, never across a gap.
        Returns the first segment's Line2D (the legend handle), or None for an empty series."""
        first = None
        for seg in self._gap_segments(ts):
            ys = np.asarray(values[seg], dtype=float)
            if smooth:
                ys = self._smooth_series(ys, smooth_window)
            dts = [datetime.fromtimestamp(t) for t in ts[seg]]
            # A lone sample is a point, not a zero-length (invisible) line.
            marker = "o" if len(dts) == 1 else None
            line, = ax.plot(dts, ys, color=color, linestyle=linestyle, linewidth=1.5, zorder=10,
                            label=label, marker=marker, markersize=2)
            self._set_measured(line, dts, values[seg])   # the hover reads samples, not the smoothing
            first = first or line
        return first

    @staticmethod
    def _rows_as_floats(series) -> np.ndarray:
        """(timestamp, value, ...) rows as a float array, accepting datetime OR epoch timestamps.

        The two data sources disagree on the first column and both reach these renderers:
        `get_hardware_history()` is typed - and really returns - `List[Tuple[datetime, float]]`,
        while the speed-history path yields epoch floats. `np.array(rows, dtype=float)` cannot
        convert a datetime, so every hardware series raised

            TypeError: float() argument must be ... not 'datetime.datetime'

        which `GraphHost._on_data_ready` swallowed into a log line - leaving the Monitor's Hardware
        tab drawing empty 0.0-1.0 axes with no hint that anything had failed. Mirrors the coercion
        the network branch of `render()` already does inline.
        """
        rows = []
        for row in series:
            ts = row[0]
            rows.append([ts.timestamp() if isinstance(ts, datetime) else float(ts)]
                        + [float(v) for v in row[1:]])
        return np.array(rows, dtype=float)

    def _render_overview(self, data_dict, start_time, end_time, period_key: str, boot_time):
        """Internal helper for overview plotting."""
        # Clear existing artists so live refresh doesn't keep stacking lines.
        for ax in self.axes:
            ax.clear()
        self._format_overview_axes()

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
        cpu = self._rows_as_floats(data_dict.get("cpu", []))
        if len(cpu) > 0:
            ts = [datetime.fromtimestamp(t) for t in cpu[:, 0]]
            self.ax_cpu.plot(ts, cpu[:, 1], color=constants.graph.CPU_LINE_COLOR, linewidth=1)
            self.ax_cpu.set_ylim(0, 100)

        # Plot GPU
        gpu = self._rows_as_floats(data_dict.get("gpu", []))
        if len(gpu) > 0:
            ts = [datetime.fromtimestamp(t) for t in gpu[:, 0]]
            self.ax_gpu.plot(ts, gpu[:, 1], color=constants.graph.GPU_LINE_COLOR, linewidth=1)
            self.ax_gpu.set_ylim(0, 100)

        # Add Event Markers (system boot)
        self._add_event_markers(self.ax_download, start_time, end_time, boot_time=boot_time)
        
        # Force X-Axis limits to match the requested interval viewport
        self.ax_download.set_xlim(start_time, end_time)

        # Apply period-aware formatting to the bottom axis (GPU row).
        # Overview uses 4 stacked axes; we keep x labels only on the last one.
        try:
            self._configure_xaxis_format(period_key, axis=self.ax_gpu)
        except Exception:
            pass
        
        self.canvas.draw()



    def _lbl(self, text: str) -> str:
        """Reshape an axis/legend label for RTL (Hebrew) matplotlib rendering; no-op for LTR locales."""
        from netspeedtray.utils.mpl_fonts import shape_rtl
        return shape_rtl(text, getattr(self.i18n, "language", None))

    def _hw_role_label(self, role: str) -> str:
        """The name a hardware series carries as its matplotlib label - one place, so the legend and
        the hover tooltip (which reads `line.get_label()` and skips unlabelled `_childN` lines)
        agree wherever the line is drawn."""
        return {
            "cpu": getattr(self.i18n, "ORDER_TYPE_CPU", "CPU"),
            "gpu": getattr(self.i18n, "ORDER_TYPE_GPU", "GPU"),
            "ram": getattr(self.i18n, "MONITOR_TILE_RAM", "RAM"),
        }.get(role, str(role).upper())

    def _format_hardware_axes(self, stat_type: str):
        """Formats the single axis for hardware utilization."""
        label = self.i18n.GRAPH_CPU_UTIL_AXIS_LABEL if stat_type == "cpu" else self.i18n.GRAPH_GPU_UTIL_AXIS_LABEL
        self.ax_download.set_ylabel(self._lbl(label), color=self._current_text_color)
        self.ax_download.tick_params(labelbottom=True, colors=self._current_text_color, which='both')
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE, alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        
        # Make the top plot take up more space
        self.ax_download.set_position([0.12, 0.15, 0.86, 0.80])

    def _format_hwcombined_axes(self):
        """Single 0-100% axis for the combined CPU+GPU graph (Monitor Hardware tab)."""
        label = getattr(self.i18n, "GRAPH_HW_UTIL_AXIS_LABEL", "Utilization (%)")
        self.ax_download.set_ylabel(self._lbl(label), color=self._current_text_color)
        self.ax_download.tick_params(labelbottom=True, colors=self._current_text_color, which='both')
        self.ax_download.grid(True, linestyle=constants.graph.GRID_LINESTYLE,
                              alpha=constants.graph.GRID_ALPHA, color=self._current_grid_color)
        self.ax_download.set_position([0.10, 0.15, 0.88, 0.80])

    def _hw_xlim(self, period_key, start_time, end_time, ts_min, ts_max):
        """X-limits for a hardware plot. For the SESSION view, tight-fit to the actual data so the
        graph doesn't show an empty gap before hardware collection began - hardware history only exists
        from when monitoring turned on, which is usually later than app-start (e.g. when the Monitor was
        opened). Mirrors the network renderer's SESSION handling so both surfaces behave identically."""
        if period_key == "TIMELINE_SESSION":
            return datetime.fromtimestamp(ts_min), datetime.fromtimestamp(ts_max)
        return ((start_time or datetime.fromtimestamp(ts_min)),
                (end_time or datetime.fromtimestamp(ts_max)))

    def _render_hwcombined(self, data_dict, start_time, end_time, period_key, hw_styles=None):
        """Plots CPU + GPU on one 0-100% axis: CPU solid, GPU dashed, vendor-colored, with a legend.
        Two lines, no fills - overlapping gradient fills would muddy a shared axis."""
        from netspeedtray.utils import hardware_vendors as hv
        ax = self.ax_download
        ax.clear()
        self._format_hwcombined_axes()
        styles = hw_styles or {}
        smooth = bool(styles.get("smoothing"))
        fixed = bool(styles.get("fixed_axis", True))

        roles = tuple((role, self._hw_role_label(role)) for role in ("cpu", "gpu", "ram"))
        all_ts, handles, data_max = [], [], 0.0
        for role, label in roles:
            series = data_dict.get(role) or []
            if not series:
                continue
            arr = self._rows_as_floats(series)
            ts = arr[:, 0]
            color, ls = styles.get(role) or hv.graph_line_style(role)
            line = self._plot_hw_series(ax, ts, arr[:, 1], color, ls, label, smooth,
                                        styles.get("smooth_window", 5))
            handles.append(line)
            all_ts.append(ts)
            data_max = max(data_max, float(arr[:, 1].max()))

        if all_ts:
            self._configure_hardware_axes(start_time, end_time, period_key, np.concatenate(all_ts), None)
        self._apply_hw_ylim(ax, fixed, data_max)   # after _configure_hardware_axes, which forces 0-100

        if not handles:
            # No CPU/GPU history yet (the common fresh-session state) - say so, don't show a blank grid.
            self._draw_hw_empty_message(ax, period_key, hw_styles)
        elif (hw_styles or {}).get("legend", True):
            # upper-RIGHT: the lines peg high (near 100%) under load, so the upper-left is the busy
            # zone; a near-opaque themed chip keeps the legend readable over pegged traces.
            legend = ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.9, ncols=3)
            frame = legend.get_frame()
            frame.set_facecolor(style_constants.GRAPH_BG_DARK if getattr(self, "_is_dark_mode", True)
                                else style_constants.GRAPH_BG_LIGHT)
            frame.set_edgecolor(self._current_grid_color)
            for text in legend.get_texts():
                text.set_color(self._current_text_color)

    def _draw_net_empty_message(self, period_key, start_time=None, end_time=None, hw_styles=None) -> None:
        """The centered note on a network graph with no traffic for the range, saying WHY honestly:
        the live Session view is still collecting; nothing recorded at all yet; the range ends before
        the first record ("History starts <date>"); or history exists before it and this period simply
        had none - the PC was off, idle or paused (the history keeps no all-zero rows), which is not
        "not enough history". ``hw_styles["earliest"]`` (GraphHost) is the first stored record."""
        earliest = (hw_styles or {}).get("earliest")
        if period_key == "TIMELINE_SESSION":
            text = getattr(self.i18n, "COLLECTING_DATA_MESSAGE", "Collecting data…")
        elif earliest is None:
            text = getattr(self.i18n, "NET_GRAPH_NO_HISTORY_YET", "No history recorded yet.")
        elif end_time is not None and earliest > end_time:
            from netspeedtray.views.graph.time_axis import localized_strftime
            lang = getattr(self.i18n, "language", None)
            date = localized_strftime(earliest, "%b %d", lang, shape=False)
            if end_time.year != earliest.year:
                date = f"{date} {earliest.year}"
            text = getattr(self.i18n, "NET_GRAPH_HISTORY_STARTS", "History starts {date}.").format(date=date)
        else:
            text = getattr(self.i18n, "NET_GRAPH_NO_TRAFFIC",
                           "No traffic recorded in this period.\nThe PC was off, idle or paused.")
        self._clear_net_empty_message()
        ax = self.ax_download
        self._net_empty_artist = ax.text(
            0.5, 0.5, self._lbl(text), transform=ax.transAxes, ha="center", va="center",
            multialignment="center", color=self._current_text_color, alpha=0.55, fontsize=10)

    def _draw_net_caption(self, resolution: Optional[str]) -> None:
        """Name the statistic the network line shows when it is not the measured rate (S3).

        Past 6 h each point is the PEAK of its minute / hour / day bucket, while the header above the
        graph shows the window's volume; without a word on the graph the two read as a contradiction.
        Top-left above the download axis (the clipped-axis peak, if any, is top-right)."""
        old = getattr(self, "_net_caption_artist", None)
        self._net_caption_artist = None
        if old is not None:
            try:
                old.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass   # already gone with a cleared axes
        key = constants.graph.NET_GRAPH_STAT_CAPTION_KEYS.get(resolution or "")
        text = getattr(self.i18n, key, None) if key else None
        if not text or self.ax_download is None:
            return
        g = constants.graph
        self._net_caption_artist = self.ax_download.text(
            0.0, 1.0, self._lbl(text), transform=self.ax_download.transAxes, ha="left", va="bottom",
            fontsize=g.NET_GRAPH_CAPTION_FONTSIZE, alpha=g.NET_GRAPH_CAPTION_ALPHA,
            color=getattr(self, "_current_text_color", style_constants.DARK_MODE_TEXT_COLOR),
            clip_on=False, gid="net_graph_stat_caption")

    def _clear_net_empty_message(self) -> None:
        """The network lines are updated in place between renders, so the note must be taken down
        explicitly once real data arrives."""
        artist = getattr(self, "_net_empty_artist", None)
        self._net_empty_artist = None
        if artist is not None:
            try:
                artist.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass   # already gone with a cleared figure / axes

    def _draw_hw_empty_message(self, ax, period_key, hw_styles=None, roles=("cpu", "gpu")) -> None:
        """The centered note on an empty hardware graph. A fresh session is still collecting; but a
        ranged view whose ``roles`` are all unrecorded will never fill, so it says where to turn
        recording on (Hardware for monitoring, Advanced for the record switch) instead of "Collecting
        data" forever. ``hw_styles["history_off"]`` is graph_host.hardware_history_off_reasons()."""
        reasons = [((hw_styles or {}).get("history_off") or {}).get(r) for r in roles]
        if period_key == "TIMELINE_SESSION" or not reasons or None in reasons:
            text = getattr(self.i18n, "COLLECTING_DATA_MESSAGE", "Collecting data…")
        elif "record" in reasons:
            # A role IS monitored but not recorded: the record switch is the reason (M6). Turning on
            # the other monitor - what the old "monitor"-first order said - would record nothing either.
            text = getattr(self.i18n, "HARDWARE_HISTORY_RECORD_OFF_MESSAGE",
                           "Hardware history is off.\nTurn on Record hardware history in Settings → Advanced.")
        else:
            text = getattr(self.i18n, "HARDWARE_HISTORY_OFF_MESSAGE",
                           "Hardware history is off.\nTurn on CPU or GPU monitoring in Settings → Hardware.")
        ax.text(0.5, 0.5, self._lbl(text), transform=ax.transAxes, ha="center", va="center",
                multialignment="center", color=self._current_text_color, alpha=0.55, fontsize=10)

    def _configure_hardware_axes(self, start_time, end_time, period_key, timestamps, values):
        """Sets limits and formatters for hardware stats."""
        # Y-Axis is always 0-100 for utilization
        self.ax_download.set_ylim(0, 100)
        self.ax_download.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}%")
        
        # X-Axis limits
        if len(timestamps) > 0:
            xlim_start, xlim_end = self._hw_xlim(period_key, start_time, end_time,
                                                 timestamps.min(), timestamps.max())
            self.ax_download.set_xlim(xlim_start, xlim_end)

        # Hardware / hwcombined plot on ax_download (ax_upload hidden) - format the visible axis.
        self._configure_xaxis_format(period_key, axis=self.ax_download)

    # --- hardware graph shaping (shared by hwcombined + hwseparate) ----------------

    @staticmethod
    def _smooth_series(ys, window: int = 5):
        """Light moving-average smoothing (Hann window) for the utilization lines. numpy-only - no
        scipy dep - and a no-op when the series is too short to smooth."""
        window = int(window)
        n = len(ys)
        if window < 3 or n < window:
            return ys
        w = np.hanning(window)
        w = w / w.sum()
        pad = window // 2
        padded = np.pad(np.asarray(ys, dtype=float), pad, mode="edge")
        return np.convolve(padded, w, mode="same")[pad:pad + n]

    def _apply_hw_ylim(self, ax, fixed: bool, data_max: float) -> None:
        """Fixed 0-100% (default, comparable across runs) or auto-scale to the data (a low-utilization
        trace fills the panel). Auto keeps a 10% floor so a near-idle line isn't a degenerate sliver."""
        if fixed:
            ax.set_ylim(0, 100)
        else:
            ax.set_ylim(0, max(10.0, min(100.0, (data_max or 0.0) * 1.2)))
        ax.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}%")

    def _setup_hwseparate_axes(self):
        """Three stacked 0-100% axes for the separate hardware view (CPU top, GPU middle, RAM bottom).
        RAM matches the combined graph, which also plots cpu+gpu+ram - separate just gives each its
        own axis so there's no shared-axis collision."""
        self.ax_cpu = self.figure.add_subplot(3, 1, 1)
        self.ax_gpu = self.figure.add_subplot(3, 1, 2, sharex=self.ax_cpu)
        self.ax_ram = self.figure.add_subplot(3, 1, 3, sharex=self.ax_cpu)
        self.axes = [self.ax_cpu, self.ax_gpu, self.ax_ram]
        self.figure.subplots_adjust(left=0.10, right=0.97, top=0.97, bottom=0.11, hspace=0.42)
        self._format_hwseparate_axes()

    def _format_hwseparate_axes(self):
        """Styling for the three stacked hardware axes. x tick-labels live only on the bottom (RAM) axis;
        all three share the same time window via sharex."""
        text = getattr(self, "_current_text_color", "white")
        grid = getattr(self, "_current_grid_color", "#444")
        cpu_lbl = getattr(self.i18n, "ORDER_TYPE_CPU", "CPU")
        gpu_lbl = getattr(self.i18n, "ORDER_TYPE_GPU", "GPU")
        ram_lbl = getattr(self.i18n, "MONITOR_TILE_RAM", "RAM")
        for ax, lbl in ((self.ax_cpu, cpu_lbl), (self.ax_gpu, gpu_lbl), (self.ax_ram, ram_lbl)):
            ax.set_ylabel(self._lbl(lbl), color=text)
            ax.grid(True, linestyle=constants.graph.GRID_LINESTYLE,
                    alpha=constants.graph.GRID_ALPHA, color=grid)
            ax.tick_params(colors=text, which="both")
            ax.yaxis.set_major_formatter(lambda x, pos: f"{int(x)}%")
        self.ax_cpu.tick_params(labelbottom=False)   # x labels only on the bottom axis
        self.ax_gpu.tick_params(labelbottom=False)
        self.ax_ram.tick_params(labelbottom=True)

    def _render_hwseparate(self, data_dict, start_time, end_time, period_key, hw_styles=None):
        """CPU top, GPU middle, RAM bottom - each its own axis, so no shared-axis collision and thus
        each vendor-colored SOLID (no dashed sibling needed). Mirrors the combined graph's roles."""
        from netspeedtray.utils import hardware_vendors as hv
        styles = hw_styles or {}
        smooth = bool(styles.get("smoothing"))
        fixed = bool(styles.get("fixed_axis", True))
        self.ax_cpu.clear()
        self.ax_gpu.clear()
        self.ax_ram.clear()
        self._format_hwseparate_axes()

        any_data, all_ts = False, []
        for ax, role in ((self.ax_cpu, "cpu"), (self.ax_gpu, "gpu"), (self.ax_ram, "ram")):
            series = data_dict.get(role) or []
            if not series:
                self._apply_hw_ylim(ax, fixed, 0.0)
                continue
            arr = self._rows_as_floats(series)
            ts = arr[:, 0]
            color, _ls = styles.get(role) or hv.graph_line_style(role)
            self._plot_hw_series(ax, ts, arr[:, 1], color, "-", self._hw_role_label(role), smooth,
                                 styles.get("smooth_window", 5))
            self._apply_hw_ylim(ax, fixed, float(arr[:, 1].max()))
            any_data = True
            all_ts.append(ts)

        if not any_data:
            self._draw_hw_empty_message(self.ax_cpu, period_key, hw_styles)
            return
        merged = np.concatenate(all_ts)
        xs, xe = self._hw_xlim(period_key, start_time, end_time, merged.min(), merged.max())
        self.ax_cpu.set_xlim(xs, xe)   # sharex propagates to ax_gpu + ax_ram
        self._configure_xaxis_format(period_key, axis=self.ax_ram)

    def _render_hwsingle(self, history_data, start_time, end_time, period_key, stat_type, hw_styles=None):
        """One CPU- OR GPU-only line for the Monitor's "toggle" layout. Honors the same display
        settings as _render_hwcombined/_render_hwseparate - per-role color (vendor default fallback),
        the Smooth toggle, and the fixed-0-100 vs auto y-axis - which the legacy single-stat path
        (still used by the standalone GraphWindow, hw_styles=None) does not."""
        from netspeedtray.utils import hardware_vendors as hv
        styles = hw_styles or {}
        smooth = bool(styles.get("smoothing"))
        fixed = bool(styles.get("fixed_axis", True))
        ax = self.ax_download
        ax.clear()
        self._format_hardware_axes(stat_type)

        raw = self._rows_as_floats(history_data)
        timestamps = raw[:, 0]
        values = raw[:, 1]
        color, _ls = styles.get(stat_type) or hv.graph_line_style(stat_type)   # solid for a lone line
        self._plot_hw_series(ax, timestamps, values, color, "-", self._hw_role_label(stat_type), smooth,
                             styles.get("smooth_window", 5))

        xs, xe = self._hw_xlim(period_key, start_time, end_time, timestamps.min(), timestamps.max())
        ax.set_xlim(xs, xe)
        self._configure_xaxis_format(period_key, axis=ax)
        # After _configure_xaxis_format (and unlike _configure_hardware_axes, this never forces 0-100),
        # so this is the single authoritative y-limit - mirroring _render_hwcombined's override order.
        self._apply_hw_ylim(ax, fixed, float(values.max()) if len(values) else 0.0)

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

    # --- measured samples behind each drawn line (S1) ------------------------------------------
    # The network line is a spline through the samples (3 interpolated points between each pair at
    # Week / short sessions) and the hardware lines may be smoothed, so the plotted array is mostly
    # values nobody measured. Each labelled line therefore carries the samples it was drawn FROM, and
    # the hover tooltip reads those (graph_hover.measured_xy).
    MEASURED_ATTR = "_nst_measured_xy"

    @classmethod
    def _set_measured(cls, line, xs, ys) -> None:
        """Attach the measured (x in matplotlib date numbers, y in the axis unit) samples to ``line``."""
        try:
            x = np.asarray(xs)
            if x.size and (x.dtype == object or np.issubdtype(x.dtype, np.datetime64)):
                x = mdates.date2num(list(x))
            setattr(line, cls.MEASURED_ATTR, (np.asarray(x, dtype=float), np.asarray(ys, dtype=float)))
        except Exception:
            pass   # the hover falls back to the plotted data

    # --- unmeasured time (S4) ---------------------------------------------------------------------

    @classmethod
    def _net_segments(cls, ts, up, down, measured=None, start_ts=None, end_ts=None,
                      bin_seconds=None) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Split a speed series into the line segments to draw, deciding what each silence MEANS.

        A silence is a stretch with no stored point: between two points further apart than
        ``_gap_threshold``, before the first point (from ``start_ts``) or after the last (to
        ``end_ts``). The v8 history writes no all-zero rows, so a silence is either measured idle (the
        app ran, nothing moved) or unmeasured (the app was off, the PC asleep, or before the first
        recorded session). ``measured`` is the merged running spans (epoch seconds) from
        ``WidgetState.get_graph_coverage``: the parts of a silence inside them are drawn as zero, one
        bin (``bin_seconds``) away from the neighbouring points; the rest breaks the line. The old
        renderer drew every silence as a dashed zero line, which said "measured 0" about the night
        the PC was off. ``measured`` None (the in-memory Session, a v7 file, a failed read) claims
        nothing: every silence is a break. Returns ``[(ts, up, down), ...]`` in plot order."""
        ts = np.asarray(ts, dtype=float)
        up = np.asarray(up, dtype=float)
        down = np.asarray(down, dtype=float)
        if ts.size == 0:
            return []
        thr = cls._gap_threshold(ts)
        if not bin_seconds or bin_seconds <= 0:
            d = np.diff(ts)
            d = d[d > 0]
            bin_seconds = float(min(d.min(), thr)) if d.size else 1.0
        # The resolution is read just after the rows, not in their snapshot; a roll-up committing in
        # between could name a coarser bin than the rows have. Never step further than the data's own
        # gap threshold, so a mismatch can only nudge where a zero run starts, never misplace it.
        step = min(float(bin_seconds), thr)
        spans = sorted((float(s), float(e)) for s, e in (measured or ()) if float(e) > float(s))
        starts = [s for s, _ in spans]
        segments: List[List[Tuple[float, float, float]]] = []
        cur: List[Tuple[float, float, float]] = []

        def close():
            if cur:
                segments.append(list(cur))
                cur.clear()

        def silence(a: float, b: float, left_point: bool, right_point: bool) -> None:
            tail = a if left_point else None          # time of the last sample drawn in `cur`
            first = max(0, bisect.bisect_right(starts, a) - 1)
            for s, e in spans[first:]:
                if s >= b:
                    break
                lo, hi = max(s, a), min(e, b)
                if hi <= lo:
                    continue
                attached = tail is not None and lo - tail <= thr
                if not attached:
                    close()                           # unmeasured time before this run
                x0 = max(lo, tail + step) if attached else lo
                x1 = min(hi, b - step) if (right_point and b - hi <= thr) else hi
                if x1 >= x0:
                    cur.append((x0, 0.0, 0.0))
                    if x1 > x0:
                        cur.append((x1, 0.0, 0.0))
                    tail = x1
            if not (right_point and tail is not None and b - tail <= thr):
                close()                               # unmeasured time after the last run

        if start_ts is not None and ts[0] - float(start_ts) > thr:
            silence(float(start_ts), ts[0], False, True)
        for i in range(ts.size):
            cur.append((ts[i], up[i], down[i]))
            if i + 1 < ts.size and ts[i + 1] - ts[i] > thr:
                silence(ts[i], ts[i + 1], True, True)
        if end_ts is not None and float(end_ts) - ts[-1] > thr:
            silence(ts[-1], float(end_ts), True, False)
        close()
        out = []
        for seg in segments:
            arr = np.asarray(seg, dtype=float)
            out.append((arr[:, 0], arr[:, 1], arr[:, 2]))
        return out

    def _plot_high_res(self, plot_datetimes, upload_mbps, download_mbps, target_end_time=None, color=None,
                       measured=None, start_time=None, bin_seconds=None):
        """Segmented plotting with honest gaps, gradient fills and fluid interpolation.

        ``_net_segments`` decides where the line breaks (unmeasured time) and where a silence is drawn
        as zero (measured idle); nothing is bridged any more. One segment keeps the live fast path
        (artists reused between refreshes); several are drawn fresh, each with its own fill. Every
        line carries the samples it was drawn from (``_set_measured``) for the hover. Returns the
        plotted (interpolated) arrays, which scale the axis."""
        if len(plot_datetimes) == 0:
            return plot_datetimes, upload_mbps, download_mbps

        # Colors
        color_down = color or constants.graph.DOWNLOAD_LINE_COLOR
        color_up = constants.graph.UPLOAD_LINE_COLOR

        timestamps_float = np.array([dt.timestamp() for dt in plot_datetimes])
        segments = self._net_segments(
            timestamps_float, upload_mbps, download_mbps, measured,
            start_ts=start_time.timestamp() if start_time is not None else None,
            end_ts=target_end_time.timestamp() if target_end_time is not None else None,
            bin_seconds=bin_seconds)

        # Adaptive Quality: If we have > 600 points, interpolation is visually redundant.
        # Skip it to save CPU.
        ENABLE_SPLINE = len(plot_datetimes) <= RendererConstants.SPLINE_INTERPOLATION_POINT_THRESHOLD
        final_ts, final_up, final_down = [], [], []

        def dense(seg_ts, seg_up, seg_down):
            dts = np.array([datetime.fromtimestamp(t) for t in seg_ts])
            d_ts, d_up, d_down = self._process_plot_segment(dts, seg_up, seg_down, enable_spline=ENABLE_SPLINE)
            return dts, list(d_ts), np.asarray(d_up, dtype=float), np.asarray(d_down, dtype=float)

        if len(segments) > 1:
            # Segmented mode - multiple disconnected line segments
            self.ax_download.clear()
            self.ax_upload.clear()

            # Reapply correct formatting based on whether this is a hardware plot
            if color:
                self._format_hardware_axes(getattr(self, '_last_stat_type', 'cpu'))
            else:
                self._format_axes()

            self.line_download = None # Sentinel

            for seg_ts, seg_up, seg_down in segments:
                dts, d_ts, d_up, d_down = dense(seg_ts, seg_up, seg_down)
                # A lone sample is a point, not a zero-length (invisible) line.
                marker = "o" if len(dts) == 1 else None
                # label= is what the hover tooltip keys on.
                ld, = self.ax_download.plot(d_ts, d_down, color=color_down, linewidth=1.5, zorder=10,
                                            label=self.i18n.DOWNLOAD_LABEL, marker=marker, markersize=2)
                lu, = self.ax_upload.plot(d_ts, d_up, color=color_up, linewidth=1.5, zorder=10,
                                          label=self.i18n.UPLOAD_LABEL, marker=marker, markersize=2)
                self._set_measured(ld, dts, seg_down)
                self._set_measured(lu, dts, seg_up)

                # Add Gradient (per segment)
                self._apply_gradient_fill(self.ax_download, np.array(d_ts), d_down, color_down, 'download',
                                          reuse=False)
                self._apply_gradient_fill(self.ax_upload, np.array(d_ts), d_up, color_up, 'upload',
                                          reuse=False)
                final_ts.extend(d_ts)
                final_up.extend(d_up)
                final_down.extend(d_down)
        else:
            # FAST PATH: Single continuous line
            seg_ts, seg_up, seg_down = segments[0]
            dts, dense_ts, dense_up, dense_down = dense(seg_ts, seg_up, seg_down)
            final_ts, final_up, final_down = dense_ts, dense_up, dense_down
            marker = "o" if len(dts) == 1 else None

            if self.line_download is not None and self.line_download.axes is not None:
                try:
                    self.line_download.set_data(dense_ts, dense_down)
                    self.line_upload.set_data(dense_ts, dense_up)
                    self.line_download.set_marker(marker)
                    self.line_upload.set_marker(marker)
                except Exception as e:
                    self.logger.debug(f"High-res update failed, rebuilding: {e}")
                    self.line_download, = self.ax_download.plot(dense_ts, dense_down, color=color_down, linewidth=1.5, zorder=10,
                                                                label=self.i18n.DOWNLOAD_LABEL, marker=marker, markersize=2)
                    self.line_upload, = self.ax_upload.plot(dense_ts, dense_up, color=color_up, linewidth=1.5, zorder=10,
                                                            label=self.i18n.UPLOAD_LABEL, marker=marker, markersize=2)
            else:
                self.line_download, = self.ax_download.plot(
                    dense_ts, dense_down,
                    color=color_down, linewidth=1.5, zorder=10, label=self.i18n.DOWNLOAD_LABEL,
                    marker=marker, markersize=2
                )
                self.line_upload, = self.ax_upload.plot(
                    dense_ts, dense_up,
                    color=color_up, linewidth=1.5, zorder=10, label=self.i18n.UPLOAD_LABEL,
                    marker=marker, markersize=2
                )
            self._set_measured(self.line_download, dts, seg_down)
            self._set_measured(self.line_upload, dts, seg_up)

            # Apply premium gradient fills
            self._apply_gradient_fill(
                self.ax_download, np.array(dense_ts), dense_down,
                color_down, 'download'
            )
            self._apply_gradient_fill(
                self.ax_upload, np.array(dense_ts), dense_up,
                color_up, 'upload'
            )

        # Return the INTERPOLATED data: it is what the axis must fit (the hover reads the samples).
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



    def _configure_axes(self, start_time, end_time, period_key, timestamps, upload_mbps, download_mbps,
                        plotted_ts=None):
        """Sets limits and Formatters with sticky behavior to prevent jitter.

        On long windows (VALUE_AXIS_CLIP_PERIODS) each speed axis may scale to the 99th percentile of
        its plotted points instead of the max, so a couple of speed tests stop squashing a month into
        the bottom few percent; the points above the edge are then marked (``_mark_clipped``).
        ``plotted_ts`` (the x of ``upload_mbps``/``download_mbps``) is needed only for those marks.
        """
        # Y-Axis Scaling (Sticky Logic with smart rounding)
        max_up, _ = clipped_axis_max(upload_mbps if upload_mbps is not None else [], period_key)
        max_down, _ = clipped_axis_max(download_mbps if download_mbps is not None else [], period_key)
        
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
        
        # Each axis is shown in the user's unit (S2): one unit per axis, picked from its top the way the
        # widget picks one from a value; the data stays in Mbps and only ticks + label convert.
        styles = getattr(self, "_net_styles", None) or {}
        unit_type = styles.get("unit_type", "bits_decimal")
        force_mega = bool(styles.get("force_mega", False))
        scale_up, unit_up = speed_axis_unit(y_top_up, self.i18n, unit_type, force_mega)
        scale_down, unit_down = speed_axis_unit(y_top_down, self.i18n, unit_type, force_mega)

        # Ticks on round numbers of the displayed unit
        self._set_smart_y_ticks(self.ax_upload, y_top_up, scale_up)
        self._set_smart_y_ticks(self.ax_download, y_top_down, scale_down)

        # Labels carry exactly the precision the tick step needs ("0.5, 1, 1.5"), in the locale's
        # decimal separator - the old int(x) for x >= 1 printed 1.5 as "1" ("0.0, 0.5, 1, 1, 2").
        self.ax_upload.yaxis.set_major_formatter(ValueAxisFormatter(self.i18n, scale=scale_up))
        self.ax_download.yaxis.set_major_formatter(ValueAxisFormatter(self.i18n, scale=scale_down))
        text_color = getattr(self, "_current_text_color", style_constants.LIGHT_MODE_TEXT_COLOR)
        self.ax_download.set_ylabel(self._lbl(f"{self.i18n.DOWNLOAD_LABEL} ({unit_down})"), color=text_color)
        self.ax_upload.set_ylabel(self._lbl(f"{self.i18n.UPLOAD_LABEL} ({unit_up})"), color=text_color)

        self.ax_upload.set_ylim(bottom=0, top=y_top_up)
        self.ax_download.set_ylim(bottom=0, top=y_top_down)

        # Points the axis no longer reaches stay on the line (the hover reads their true value); mark
        # them so they never silently vanish. A no-op off the long windows, where nothing is clipped.
        self._mark_clipped(self.ax_download, plotted_ts, download_mbps, y_top_down,
                           constants.graph.DOWNLOAD_LINE_COLOR, scale_down, unit_down)
        self._mark_clipped(self.ax_upload, plotted_ts, upload_mbps, y_top_up,
                           constants.graph.UPLOAD_LINE_COLOR, scale_up, unit_up)

        # X-Axis Formatting
        self._configure_xaxis_format(period_key)

    def _mark_clipped(self, ax, xs, ys, top: float, color_hex: str, scale: float = 1.0,
                      unit: Optional[str] = None) -> None:
        """Caret markers on the top edge for each run of points above ``top``, plus the true peak
        (stated in the axis unit: ``scale`` converts Mbps to it, ``unit`` names it; Mbps by default).

        Called on every axis configuration, so it first removes its previous marks (a re-render of
        the same period does not rebuild the axes). The markers are a scatter collection, not a
        Line2D, and carry no label, so the hover tooltip - which reads only labelled lines - never
        mistakes them for data.
        """
        store = getattr(self, "_clip_artists", None)
        if store is None:
            store = self._clip_artists = {}
        for artist in store.pop(id(ax), []):
            try:
                artist.remove()
            except Exception:
                pass
        if xs is None or ys is None or len(xs) == 0 or len(xs) != len(ys):
            return
        try:
            x = np.asarray(xs)
            if x.dtype == object or np.issubdtype(x.dtype, np.datetime64):
                x = mdates.date2num(list(x))
            runs = clipped_runs(np.asarray(x, dtype=float), ys, top)
            if not runs:
                return
            g = constants.graph
            artists = [ax.scatter(
                [rx for rx, _ in runs], [1.0] * len(runs), transform=ax.get_xaxis_transform(),
                marker=g.VALUE_AXIS_CLIP_MARKER, s=g.VALUE_AXIS_CLIP_MARKER_SIZE, color=color_hex,
                linewidths=0, clip_on=False, zorder=12, label="_value_axis_clip_markers",
                gid="value_axis_clip_markers")]
            peak = max(ry for _, ry in runs)
            # Through _lbl like every axis label: matplotlib does no bidi, so Hebrew would read reversed.
            text = self._lbl(f"{self.i18n.GRAPH_PEAK_SHORT} {format_decimal(peak * scale, self.i18n, 1)} "
                             f"{unit or self.i18n.MBITS_UNIT}")
            artists.append(ax.text(
                1.0, 1.0, text, transform=ax.transAxes, ha="right", va="bottom",
                fontsize=g.VALUE_AXIS_CLIP_PEAK_FONTSIZE, alpha=g.VALUE_AXIS_CLIP_PEAK_ALPHA,
                color=getattr(self, "_current_text_color", style_constants.DARK_MODE_TEXT_COLOR),
                clip_on=False, gid="value_axis_clip_peak"))
            store[id(ax)] = artists
        except Exception as e:
            self.logger.debug("clip markers skipped: %s", e)

    def _set_smart_y_ticks(self, ax, y_max: float, scale: float = 1.0):
        """
        Sets intelligent Y-axis tick locations using Matplotlib's MaxNLocator, on round numbers of
        the displayed unit (``scale`` converts the plotted Mbps to it).
        This provides clean, non-overlapping labels regardless of the data scale.
        """
        if y_max <= 0:
            ax.set_yticks([0])
            return
        
        # Use MaxNLocator for robust, nicely-spaced ticks without overlap
        g = constants.graph
        ax.yaxis.set_major_locator(ScaledMaxNLocator(scale, nbins=g.VALUE_AXIS_LOCATOR_BINS,
                                                     steps=list(g.VALUE_AXIS_LOCATOR_STEPS), min_n_ticks=3))


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

    def _configure_xaxis_format(self, period_key: str, axis=None) -> None:
        """
        Intelligently configures the x-axis locator and formatter.

        ``axis`` is the Axes that actually carries the bottom tick labels - ax_upload for the 2-row
        network view, but ax_download for the single-axis hardware / hwcombined modes (where ax_upload
        is hidden, so formatters set on it would never reach the visible labels).
        """
        if axis is None:
            axis = self.ax_upload

        # One locator + formatter for every period: the tick step follows the visible span and the
        # axis width, and the label format follows the step, so no two ticks ever print the same text
        # (the old fixed per-period formats printed "18:12, 18:12, 18:13" under 30 s ticks).
        apply_time_axis(axis, getattr(getattr(self, "i18n", None), "language", None))

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
