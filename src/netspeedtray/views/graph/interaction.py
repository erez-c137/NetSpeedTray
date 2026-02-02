from datetime import datetime
import logging
import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel
from PyQt6.QtGui import QCursor
import matplotlib.dates as mdates
from matplotlib.widgets import SpanSelector

from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.constants import styles as style_constants


class GraphInteractionHandler(QObject):
    """
    Handles internal graph interactions: Mouse movement (crosshair/tooltip)
    and clicking (legend toggling).
    
    Phase 4 Upgrades:
    - Binary search for O(log n) point lookup
    - Focus dots (glowing cursor) that snap to data points
    - Blitting for 60 FPS mouse interactions
    
    Phase 5 Upgrades:
    - SpanSelector for brush zoom (drag to select time range)
    - Double-click to reset zoom
    """
    
    # Signal emitted when user zooms into a custom range
    zoom_range_selected = pyqtSignal(datetime, datetime)
    zoom_reset_requested = pyqtSignal()
    interaction_detected = pyqtSignal()
    
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.logger = logging.getLogger(__name__)

        # Data Cache for efficient lookup
        self._graph_x_cache = None
        self._graph_data_ts_raw = None
        self._graph_data_ups = None
        self._graph_data_downs = None

        # UI Elements
        self.tooltip = None
        self.crosshair_v_download = None
        self.crosshair_v_upload = None
        self.crosshair_h_download = None
        self.crosshair_h_upload = None
        
        # Focus Dots (Phase 4: Glowing cursor)
        self.focus_dot_download_outer = None
        self.focus_dot_download_inner = None
        self.focus_dot_upload_outer = None
        self.focus_dot_upload_inner = None
        
        # Blitting (Phase 4: 60 FPS)
        self._blit_background = None
        self._dynamic_artists = []
        
        # Span Selector (Phase 5: Brush Zoom)
        self.span_selector = None
        self._is_dragging = False
        
        # Initialize
        self._init_ui_overlays()
        self._init_span_selector()
        self._connect_mpl_events()
        
        # Phase 6: Robust Mouse Exit Detection (Qt Layer)
        if hasattr(self.window, 'renderer') and self.window.renderer.canvas:
             self.window.renderer.canvas.installEventFilter(self)



    def _init_ui_overlays(self):
        """Create tooltip, crosshair lines, and focus dots."""
        if not hasattr(self.window, 'renderer') or not self.window.renderer.axes: return

        # CLEANUP: Destroy existing tooltip widget to prevent memory leak (zombie widgets)
        if self.tooltip:
            try:
                self.tooltip.deleteLater()
            except RuntimeError:
                pass # Already deleted
            self.tooltip = None

        # Reset references (Previous artists are removed by ax.clear() in renderer)
        self.crosshair_v_download = None
        self.crosshair_v_upload = None
        self.crosshair_h_download = None
        self.crosshair_h_upload = None
        self.focus_dot_download_outer = None
        self.focus_dot_download_inner = None
        self.focus_dot_upload_outer = None
        self.focus_dot_upload_inner = None
        self._dynamic_artists = []
        self._blit_background = None

        try:
            ax_down = self.window.renderer.ax_download
            ax_up = self.window.renderer.ax_upload
            
            # Crosshairs
            self.crosshair_v_download = ax_down.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=100, visible=False)
            self.crosshair_v_upload = ax_up.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=100, visible=False)
            self.crosshair_h_download = ax_down.axhline(y=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=100, visible=False)
            self.crosshair_h_upload = ax_up.axhline(y=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=100, visible=False)
            
            # Focus Dots - Download (outer glow + inner dot)
            self.focus_dot_download_outer = ax_down.plot(
                [], [], 'o',
                markersize=12,
                color=constants.graph.DOWNLOAD_LINE_COLOR,
                alpha=0.25,
                zorder=101,
                animated=True  # Required for blitting
            )[0]
            self.focus_dot_download_inner = ax_down.plot(
                [], [], 'o',
                markersize=6,
                color=constants.graph.DOWNLOAD_LINE_COLOR,
                alpha=1.0,
                zorder=102,
                animated=True
            )[0]
            
            # Focus Dots - Upload (outer glow + inner dot)
            self.focus_dot_upload_outer = ax_up.plot(
                [], [], 'o',
                markersize=12,
                color=constants.graph.UPLOAD_LINE_COLOR,
                alpha=0.25,
                zorder=101,
                animated=True
            )[0]
            self.focus_dot_upload_inner = ax_up.plot(
                [], [], 'o',
                markersize=6,
                color=constants.graph.UPLOAD_LINE_COLOR,
                alpha=1.0,
                zorder=102,
                animated=True
            )[0]
            
            # Collect dynamic artists for blitting
            self._dynamic_artists = [
                self.crosshair_v_download, self.crosshair_v_upload,
                self.crosshair_h_download, self.crosshair_h_upload,
                self.focus_dot_download_outer, self.focus_dot_download_inner,
                self.focus_dot_upload_outer, self.focus_dot_upload_inner
            ]
            
        except Exception as e:
            self.logger.error(f"Error creating interaction overlays: {e}")

        # Tooltip
        self.tooltip = QLabel(self.window)
        self.tooltip.setObjectName("graphTooltip")
        self.tooltip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.tooltip.setVisible(False)
        self.tooltip.setStyleSheet(style_utils.graph_tooltip_style())

    def _init_span_selector(self):
        """Initialize the SpanSelector for brush zoom functionality."""
        if not hasattr(self.window, 'renderer') or not self.window.renderer.ax_download:
            return
        
        try:
            # Create SpanSelector on the download axis (spans both visually)
            self.span_selector = SpanSelector(
                self.window.renderer.ax_download,
                self._on_span_selected,
                'horizontal',
                useblit=True,
                props=dict(alpha=0.3, facecolor='#0078d4', edgecolor='#0078d4'),
                interactive=True,
                drag_from_anywhere=True,
                button=[1]  # Standard Left-click for zoom
            )
            self.logger.debug("SpanSelector initialized (Left-Click)")

        except Exception as e:
            self.logger.error(f"Could not initialize SpanSelector: {e}")
            self.span_selector = None

    def _on_span_selected(self, xmin: float, xmax: float):
        """
        Called when user completes a span selection (brush zoom).
        Converts matplotlib float dates to datetime and emits signal.
        """
        try:
            # ALWAYS CLEAR SELECTION: zero out extents immediately to remove blue box/lines
            self.clear_selection()
            
            # Minimum span threshold (prevent accidental micro-zooms)
            if abs(xmax - xmin) < 0.0001:  # ~10 seconds
                return
            
            # PHASE 5 UPGRADE: Use the internal data cache to get timestamps.
            # This is 100% robust against timezone/epoch issues because it uses
            # the same timestamps used to render the graph.
            start_ts = self._find_timestamp_for_mpl_x(xmin)
            end_ts = self._find_timestamp_for_mpl_x(xmax)
            
            # Convert to naive local datetimes for the UI logic
            start_dt = datetime.fromtimestamp(start_ts)
            end_dt = datetime.fromtimestamp(end_ts)
            
            # Emit signal for window to handle zoom
            self.zoom_range_selected.emit(start_dt, end_dt)
            
            # Invalidate blit background (graph will change)
            self._blit_background = None
            
        except Exception as e:
            self.logger.error(f"Error processing span selection: {e}")

    def clear_selection(self):
        """Clears the current span selection rectangle from the graph."""
        if self.span_selector:
            try:
                # Force-clear the internal extents so the rectangle has zero area
                self.span_selector.extents = (0, 0)
                
                # Toggle visibility to force artist removal
                self.span_selector.set_visible(False)
                self.span_selector.set_visible(True)
                
                if hasattr(self.window.renderer, 'canvas'):
                    self.window.renderer.canvas.draw_idle()
            except Exception as e:
                self.logger.error(f"Error clearing span selection: {e}")

    def _find_timestamp_for_mpl_x(self, x: float) -> float:
        """
        Calculates the Unix timestamp for a Matplotlib float date using linear interpolation
        against the cached plotted points.
        """
        if self._graph_x_cache is not None and len(self._graph_x_cache) >= 2:
            try:
                # Binary search for neighbors
                idx = np.searchsorted(self._graph_x_cache, x)
                
                # Boundary cases (extrapolate slightly)
                if idx == 0:
                    delta_t = self._graph_data_ts_raw[1] - self._graph_data_ts_raw[0]
                    delta_x = self._graph_x_cache[1] - self._graph_x_cache[0]
                    return float(self._graph_data_ts_raw[0] + (x - self._graph_x_cache[0]) * (delta_t / delta_x))
                
                if idx >= len(self._graph_x_cache):
                    n = len(self._graph_x_cache)
                    delta_t = self._graph_data_ts_raw[n-1] - self._graph_data_ts_raw[n-2]
                    delta_x = self._graph_x_cache[n-1] - self._graph_x_cache[n-2]
                    return float(self._graph_data_ts_raw[n-1] + (x - self._graph_x_cache[n-1]) * (delta_t / delta_x))
                
                # Linear Interpolation between points
                x0, x1 = self._graph_x_cache[idx-1], self._graph_x_cache[idx]
                t0, t1 = self._graph_data_ts_raw[idx-1], self._graph_data_ts_raw[idx]
                
                factor = (x - x0) / (x1 - x0)
                return float(t0 + factor * (t1 - t0))
            except Exception as e:
                self.logger.warning(f"Cache-based timestamp lookup failed: {e}")
        
        # Final Fallback to standard Matplotlib conversion
        # This handles the case where there is no data at all yet.
        dt_aware = mdates.num2date(x)
        # Stripping tzinfo and using .timestamp() reverses the local conversion in renderer.py
        return dt_aware.replace(tzinfo=None).timestamp()

    def _on_double_click(self, event):
        """Handle double-click to reset zoom."""
        if event.dblclick and event.inaxes:
            self.logger.debug("Double-click detected, resetting zoom")
            self.zoom_reset_requested.emit()
            self._blit_background = None
        
    def _on_press(self, event):
        """Handle mouse button press."""
        if event.button == 1:
            self._is_dragging = True
            self._drag_start_pos = (event.x, event.y)
            # Hide tooltip immediately when starting a drag/selection
            if self.tooltip:
                self.tooltip.setVisible(False)

    def _on_release(self, event):
        """Handle mouse button release."""
        if event.button == 1:
            self._is_dragging = False
            
            # Detect single click (distance < 5 pixels) for reset
            if hasattr(self, '_drag_start_pos') and event.inaxes:
                start_x, start_y = self._drag_start_pos
                dist = np.hypot(event.x - start_x, event.y - start_y)
                
                # If it's a click (not a drag) AND strictly reset behavior is desired
                if dist < 5:
                    self.logger.debug("Single click detected on graph, requesting zoom reset.")
                    self.zoom_reset_requested.emit()

    def _on_resize(self, event):
        """Handle canvas resize to invalidate blit background."""
        self._blit_background = None
        
    def _connect_mpl_events(self):
        """Connect matplotlib event handlers."""
        if not hasattr(self.window, 'renderer') or not self.window.renderer.canvas: return
        
        canvas = self.window.renderer.canvas
        canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        canvas.mpl_connect('axes_leave_event', self._on_mouse_leave)
        canvas.mpl_connect('pick_event', self._on_legend_pick)
        canvas.mpl_connect('button_press_event', self._on_double_click)
        canvas.mpl_connect('button_press_event', self._on_press)
        canvas.mpl_connect('button_release_event', self._on_release)
        canvas.mpl_connect('resize_event', self._on_resize)



    def update_data_cache(self, timestamps, upload_speeds, download_speeds, x_coords=None):
        """
        Updates the internal cache used for mouse interaction.
        Arguments are expected to be numpy arrays.
        x_coords: Optional pre-calculated MPL x-coordinates (float days).
                  If None, they will be calculated (legacy fallback).
        """
        if len(timestamps) > 0:
            if x_coords is not None and len(x_coords) == len(timestamps):
                # Use the exact coordinates from the renderer (Float days in Matplotlib epoch)
                self._graph_x_cache = x_coords
            else:
                # Optimized Fallback: Map epoch to MPL float days. 
                # This is now rarely used as Renderer always returns x_coords.
                # Modern Matplotlib epoch is 1970-01-01 (0.0).
                self._graph_x_cache = (timestamps / 86400.0)
        else:
            self._graph_x_cache = None

        self._graph_data_ups = upload_speeds * constants.network.units.BITS_PER_BYTE
        self._graph_data_downs = download_speeds * constants.network.units.BITS_PER_BYTE
        self._graph_data_ts_raw = timestamps
        
        # Invalidate blit background when data changes
        self._blit_background = None
        
        self.logger.debug(f"Interaction handler cache updated with {len(timestamps)} points.")

    def _find_nearest_point(self, mouse_x: float) -> int | None:
        """
        O(log n) lookup for nearest data point using binary search.
        Returns the index of the closest point, or None if no data.
        """
        if self._graph_x_cache is None or len(self._graph_x_cache) == 0:
            return None
        
        # Binary search for insertion point
        idx = np.searchsorted(self._graph_x_cache, mouse_x)
        
        # Handle edge cases
        if idx == 0:
            return 0
        if idx >= len(self._graph_x_cache):
            return len(self._graph_x_cache) - 1
        
        # Check neighbors for closest
        left_dist = abs(self._graph_x_cache[idx - 1] - mouse_x)
        right_dist = abs(self._graph_x_cache[idx] - mouse_x)
        
        return idx - 1 if left_dist < right_dist else idx

    def _capture_blit_background(self):
        """Capture the static background for blitting."""
        try:
            canvas = self.window.renderer.canvas
            # Hide dynamic artists before capturing
            for artist in self._dynamic_artists:
                if artist:
                    artist.set_visible(False)
            canvas.draw()
            self._blit_background = canvas.copy_from_bbox(canvas.figure.bbox)
        except Exception as e:
            self.logger.debug(f"Could not capture blit background: {e}")
            self._blit_background = None

    def _blit_update(self):
        """Efficient redraw using blitting - only redraws dynamic artists."""
        try:
            canvas = self.window.renderer.canvas
            
            if self._blit_background is None:
                # Fall back to regular draw if no background cached
                canvas.draw_idle()
                return
            
            # Restore static background
            canvas.restore_region(self._blit_background)
            
            # Redraw only dynamic artists
            for artist in self._dynamic_artists:
                if artist and artist.get_visible() and artist.axes:
                    artist.axes.draw_artist(artist)
            
            # Blit the result
            canvas.blit(canvas.figure.bbox)
            
        except Exception as e:
            # Fallback to regular draw
            self.logger.debug(f"Blit failed, falling back to draw_idle: {e}")
            self.window.renderer.canvas.draw_idle()

    def _on_mouse_move(self, event):
        """Handles mouse movement over the canvas to display a synchronized crosshair and tooltip."""
        # Fix: Hide overlays if mouse is outside the axes
        if not event.inaxes:
            self._on_mouse_leave(event)
            return

        # Signal that user is actively interacting (for window throttling)
        self.interaction_detected.emit()
        try:
            # If we are actively dragging (SpanSelector or panning), 
            # suppress the crosshair/tooltip to reduce visual noise.
            if self._is_dragging:
                self._on_mouse_leave(event) # Reuse hiding logic
                return

            if not event.inaxes:
                self._on_mouse_leave(event)
                return

            
            if not hasattr(self.window, 'renderer'): return
            self.window.renderer.canvas.setCursor(Qt.CursorShape.CrossCursor)

            if not hasattr(self, '_graph_data_ts_raw') or self._graph_data_ts_raw is None or len(self._graph_data_ts_raw) == 0:
                return

            mouse_timestamp = event.xdata
            if mouse_timestamp is None:
                return

            if self._graph_x_cache is None or len(self._graph_x_cache) == 0:
                return

            # Capture blit background on first move (lazy initialization)
            if self._blit_background is None:
                self._capture_blit_background()

            # Find closest point using binary search (O(log n))
            index = self._find_nearest_point(mouse_timestamp)
            if index is None:
                return
            
            raw_ts = self._graph_data_ts_raw[index]
            upload_bps = self._graph_data_ups[index]
            download_bps = self._graph_data_downs[index]
            
            timestamp_dt = datetime.fromtimestamp(raw_ts)
            timestamp_mpl = self._graph_x_cache[index]
            
            # Calculate Mbps for focus dots
            download_mbps = download_bps / 1_000_000
            upload_mbps = upload_bps / 1_000_000

            # Update Crosshairs (snap to data point X)
            for line in [self.crosshair_v_download, self.crosshair_v_upload]:
                if line:
                     line.set_xdata([timestamp_mpl, timestamp_mpl])
                     line.set_visible(True)

            if event.inaxes == self.window.renderer.ax_download:
                if self.crosshair_h_download:
                    self.crosshair_h_download.set_ydata([download_mbps, download_mbps])
                    self.crosshair_h_download.set_visible(True)
                if self.crosshair_h_upload: self.crosshair_h_upload.set_visible(False)
            elif event.inaxes == self.window.renderer.ax_upload:
                if self.crosshair_h_upload:
                    self.crosshair_h_upload.set_ydata([upload_mbps, upload_mbps])
                    self.crosshair_h_upload.set_visible(True)
                if self.crosshair_h_download: self.crosshair_h_download.set_visible(False)

            # Update Focus Dots (magnetic snapping to actual data points)
            if self.focus_dot_download_outer:
                self.focus_dot_download_outer.set_data([timestamp_mpl], [download_mbps])
                self.focus_dot_download_outer.set_visible(True)
            if self.focus_dot_download_inner:
                self.focus_dot_download_inner.set_data([timestamp_mpl], [download_mbps])
                self.focus_dot_download_inner.set_visible(True)
            if self.focus_dot_upload_outer:
                self.focus_dot_upload_outer.set_data([timestamp_mpl], [upload_mbps])
                self.focus_dot_upload_outer.set_visible(True)
            if self.focus_dot_upload_inner:
                self.focus_dot_upload_inner.set_data([timestamp_mpl], [upload_mbps])
                self.focus_dot_upload_inner.set_visible(True)

            # Tooltip Formatting
            from netspeedtray.utils.helpers import format_speed
            unit_type = self.window.config.get("unit_type", "bits_decimal")
            decimal_places = self.window.config.get("decimal_places", 1)
            up_str = format_speed(upload_bps, self.window.i18n, unit_type=unit_type, decimal_places=decimal_places, short_labels=True)
            down_str = format_speed(download_bps, self.window.i18n, unit_type=unit_type, decimal_places=decimal_places, short_labels=True)
            
            tooltip_text = (
                f'<div style="font-family: Segoe UI, sans-serif; font-size: 11px; font-weight: 400; line-height: 1.4;">'
                f'<span style="color: #888;">{timestamp_dt.strftime("%Y-%m-%d")}</span> <span style="color: #ccc;">{timestamp_dt.strftime("%H:%M:%S")}</span><br/>'
                f'<span style="color: {constants.graph.DOWNLOAD_LINE_COLOR};">▼ {down_str}</span><br/>'
                f'<span style="color: {constants.graph.UPLOAD_LINE_COLOR};">▲ {up_str}</span>'
                f'</div>'
            )
            self.tooltip.setText(tooltip_text)
            self.tooltip.adjustSize()
            
            mouse_pos = self.window.mapFromGlobal(QCursor.pos())
            tooltip_x = mouse_pos.x() + 15
            tooltip_y = mouse_pos.y() - self.tooltip.height() - 15

            if tooltip_x + self.tooltip.width() > self.window.width():
                tooltip_x = mouse_pos.x() - self.tooltip.width() - 15
            if tooltip_y < 0:
                tooltip_y = mouse_pos.y() + 15

            self.tooltip.move(tooltip_x, tooltip_y)
            self.tooltip.setVisible(True)
            self.tooltip.raise_()
            
            # Use blitting for 60 FPS performance
            self._blit_update()
            
        except Exception as e:
            self.logger.error(f"Error in _on_mouse_move: {e}", exc_info=True)

    def _on_mouse_leave(self, event):
        """Hides the crosshair, focus dots, and tooltip."""
        if hasattr(self.window, 'renderer') and self.window.renderer.canvas:
            self.window.renderer.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        
        # Hide all dynamic artists
        for artist in self._dynamic_artists:
            if artist:
                artist.set_visible(False)
             
        if self.tooltip:
             self.tooltip.setVisible(False)
             
        if hasattr(self.window, 'renderer') and self.window.renderer.canvas:
            self._blit_update()

    def refresh_overlays(self):
        """
        Ensures crosshairs, tooltips, and span selector exist on the current axes.
        Called after axes are cleared/recreated.
        """
        # Re-initialize overlays (Matplotlib clears artists on ax.clear())
        # It's safer to recreate them than to try to re-attach detached artists.
        self._init_ui_overlays()
        
        # Re-initialize span selector (Phase 5)
        self._init_span_selector()
        
        # Invalidate blit background
        self._blit_background = None
        
        # Ensure tooltip is raised
        if self.tooltip:
            self.tooltip.raise_()


    def eventFilter(self, watched, event):
        """
        Qt Event Filter to robustly detect when mouse leaves the canvas widget.
        Matplotlib's 'axes_leave_event' can be flaky if mouse moves fast.
        """
        if self.window and hasattr(self.window, 'renderer') and watched == self.window.renderer.canvas:
             if event.type() == 11: # QEvent.Type.Leave (11)
                 self.logger.debug("Qt Leave Event detected on canvas - Hiding overlays")
                 self._on_mouse_leave(None) # Force hide
                 
        return super().eventFilter(watched, event)

    def _on_legend_pick(self, event):
        """Handles legend clicking."""
        legend = event.artist
        is_visible = legend.get_visible()
        legend.set_visible(not is_visible)
        
        # Toggle lines
        if hasattr(self.window, 'renderer'):
            for ax in self.window.renderer.axes:
                for line in ax.lines + ax.collections:
                    if line.get_label() == legend.get_label():
                        line.set_visible(not is_visible)
            
            # Invalidate background and redraw
            self._blit_background = None
            self.window.renderer.canvas.draw_idle()

