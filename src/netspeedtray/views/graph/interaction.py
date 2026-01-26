from datetime import datetime
import logging
import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel
from PyQt6.QtGui import QCursor

from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.constants import styles as style_constants

class GraphInteractionHandler(QObject):
    """
    Handles internal graph interactions: Mouse movement (crosshair/tooltip)
    and clicking (legend toggling).
    """
    
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
        
        # Initialize
        self._init_ui_overlays()
        self._connect_mpl_events()

    def _init_ui_overlays(self):
        """Create tooltip and crosshair lines."""
        if not hasattr(self.window, 'renderer') or not self.window.renderer.axes: return

        # Crosshairs
        self.crosshair_v_download = self.window.renderer.ax_download.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)
        self.crosshair_v_upload = self.window.renderer.ax_upload.axvline(x=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)
        self.crosshair_h_download = self.window.renderer.ax_download.axhline(y=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)
        self.crosshair_h_upload = self.window.renderer.ax_upload.axhline(y=0, color=style_constants.GRID_COLOR_DARK, linewidth=0.8, linestyle='--', zorder=20, visible=False)

        # Tooltip
        self.tooltip = QLabel(self.window)
        self.tooltip.setObjectName("graphTooltip")
        self.tooltip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.tooltip.setVisible(False)
        self.tooltip.setStyleSheet(style_utils.graph_tooltip_style())
        
    def _connect_mpl_events(self):
        """Connect matplotlib event handlers."""
        if not hasattr(self.window, 'renderer') or not self.window.renderer.canvas: return
        
        self.window.renderer.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.window.renderer.canvas.mpl_connect('axes_leave_event', self._on_mouse_leave)
        self.window.renderer.canvas.mpl_connect('pick_event', self._on_legend_pick)

    def update_data_cache(self, timestamps, upload_speeds, download_speeds):
        """
        Updates the internal cache used for mouse interaction.
        Arguments are expected to be numpy arrays.
        """
        if len(timestamps) > 0:
            # We need to replicate the timezone logic or pass the x-coordinates directly?
            # Passing x-coordinates directly from Renderer would be safer to ensure alignment.
            # But the interaction handler needs to map mouse X to timestamp.
            # So we need the exact X coordinates used in plot.
            
            # Recalculate local offset
            import time as pytime
            is_dst = pytime.localtime().tm_isdst > 0
            tz_offset = -pytime.altzone if is_dst else -pytime.timezone
            
            # Compute MPL float dates (X-axis coordinates)
            self._graph_x_cache = ((timestamps + tz_offset) / 86400.0) + 719163.0
        else:
            self._graph_x_cache = None

        self._graph_data_ups = upload_speeds * constants.network.units.BITS_PER_BYTE
        self._graph_data_downs = download_speeds * constants.network.units.BITS_PER_BYTE
        self._graph_data_ts_raw = timestamps
        
        self.logger.debug(f"Interaction handler cache updated with {len(timestamps)} points.")

    def _on_mouse_move(self, event):
        """Handles mouse movement over the canvas to display a synchronized crosshair and tooltip."""
        try:
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

            # Find closest point
            try:
                index = (np.abs(self._graph_x_cache - mouse_timestamp)).argmin()
            except Exception as e:
                return
            
            raw_ts = self._graph_data_ts_raw[index]
            upload_bps = self._graph_data_ups[index]
            download_bps = self._graph_data_downs[index]
            
            timestamp_dt = datetime.fromtimestamp(raw_ts)
            timestamp_mpl = self._graph_x_cache[index]

            # Crosshairs logic
            for line in [self.crosshair_v_download, self.crosshair_v_upload]:
                if line:
                     line.set_xdata([timestamp_mpl, timestamp_mpl])
                     line.set_visible(True)

            download_mbps = download_bps / 1_000_000
            upload_mbps = upload_bps / 1_000_000

            if event.inaxes == self.window.renderer.ax_download:
                if self.crosshair_h_download:
                    self.crosshair_h_download.set_ydata([event.ydata, event.ydata])
                    self.crosshair_h_download.set_visible(True)
                if self.crosshair_h_upload: self.crosshair_h_upload.set_visible(False)
            elif event.inaxes == self.window.renderer.ax_upload:
                if self.crosshair_h_upload:
                    self.crosshair_h_upload.set_ydata([event.ydata, event.ydata])
                    self.crosshair_h_upload.set_visible(True)
                if self.crosshair_h_download: self.crosshair_h_download.set_visible(False)

            # Tooltip
            tooltip_text = (
                f"<div style='font-weight: bold;'>{timestamp_dt.strftime('%Y-%m-%d %H:%M:%S')}</div>"
                f"<div style='color: {constants.graph.DOWNLOAD_LINE_COLOR};'>↓ {download_mbps:.2f} Mbps</div>"
                f"<div style='color: {constants.graph.UPLOAD_LINE_COLOR};'>↑ {upload_mbps:.2f} Mbps</div>"
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
            
        except Exception as e:
            self.logger.error(f"Error in _on_mouse_move: {e}", exc_info=True)

    def _on_mouse_leave(self, event):
        """Hides the crosshair and tooltip."""
        if hasattr(self.window, 'renderer') and self.window.renderer.canvas:
            self.window.renderer.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        
        for item in [self.crosshair_v_download, self.crosshair_v_upload, 
                     self.crosshair_h_download, self.crosshair_h_upload]:
             if item: item.set_visible(False)
             
        if self.tooltip:
             self.tooltip.setVisible(False)
             
        if hasattr(self.window, 'renderer') and self.window.renderer.canvas:
            self.window.renderer.canvas.draw_idle()

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
            
            self.window.renderer.canvas.draw_idle()
