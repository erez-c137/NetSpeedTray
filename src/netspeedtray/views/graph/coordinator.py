
from PyQt6.QtCore import QObject, QTimer, Qt
from datetime import datetime
import logging
from netspeedtray import constants
from netspeedtray.views.graph.logic import GraphLogic

class GraphCoordinator(QObject):
    """
    Coordinates high-level events (Timeline changes, Zooming, Updates) 
    between the UI, Worker, and Renderer.
    """
    
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.logger = logging.getLogger(__name__)
        
        # Debounce timer for graph data updates
        self.update_debounce_timer = QTimer(self)
        self.update_debounce_timer.setSingleShot(True)
        self.update_debounce_timer.setInterval(150)
        self.update_debounce_timer.timeout.connect(self._execute_debounced_update)
        self._pending_show_loading = True
        
        # Real-time periodic update timer
        self.realtime_timer = QTimer(self)
        self.realtime_timer.setInterval(constants.graph.REALTIME_UPDATE_INTERVAL_MS)
        self.realtime_timer.timeout.connect(self.trigger_refresh)
        
        # Internal State
        self.is_custom_zoom_active = False
        self.custom_zoom_start = None
        self.custom_zoom_end = None

    def handle_timeline_change(self, period_key: str):
        """Processes a change in the timeline pills."""
        self.logger.debug(f"Coordinator: Handling timeline change to {period_key}")
        
        # 1. Update window state (mapping key to legacy index for config compatibility)
        period_index = 2 
        for idx, key in constants.data.history_period.PERIOD_MAP.items():
            if key == period_key:
                period_index = idx
                break
        self.window._history_period_value = period_index
        
        # 2. Clear zoom
        self.reset_zoom_state(trigger_update=False)
        
        # 3. Reset Y-axis limits and clear graph (prevents "compressed" appearance)
        if hasattr(self.window, 'renderer') and self.window.renderer:
            self.window.renderer.reset_ylim()
            # Clear old data to prevent stale visuals while new data loads
            self.window.renderer.clear_plot()
        
        # 4. Show loading indicator for non-session views
        if period_key != "TIMELINE_SESSION" and hasattr(self.window, 'ui'):
            self.window.ui.show_graph_message("Loading...", is_error=False)
        
        # 5. Queue update
        self._pending_show_loading = (period_key != "TIMELINE_SESSION")
        self.update_debounce_timer.start()
        
        # 4. Notify config handler
        if hasattr(self.window, 'config_handler'):
            self.window.config_handler.queue_config_update({'history_period_slider_value': period_index})

    def handle_zoom_selection(self, start_dt: datetime, end_dt: datetime):
        """Processes a custom zoom range selection."""
        self.is_custom_zoom_active = True
        self.custom_zoom_start = start_dt
        self.custom_zoom_end = end_dt
        
        # Update UI overlays
        self.window.ui.reset_zoom_btn.show()
        self.window.ui.show_zoom_hint()
        self.window.ui.reposition_overlay_elements()
        
        # Trigger immediate update
        self.trigger_refresh(use_custom_range=True)

    def reset_zoom_state(self, trigger_update: bool = True):
        """Clears custom zoom and returns to timeline view."""
        self.is_custom_zoom_active = False
        self.custom_zoom_start = None
        self.custom_zoom_end = None
        
        if hasattr(self.window, 'interaction'):
            self.window.interaction.clear_selection()
            
        self.window.ui.reset_zoom_btn.hide()
        
        if trigger_update:
            self.trigger_refresh(show_loading=False)

    def trigger_refresh(self, show_loading: bool = True, use_custom_range: bool = False):
        """authoritative entry point for triggering a graph data refresh."""
        if use_custom_range and self.is_custom_zoom_active:
             self.window.update_graph_range(self.custom_zoom_start, self.custom_zoom_end)
        else:
             self.window.update_graph(show_loading=show_loading)

    def start_realtime(self):
        """Starts periodic updates if enabled."""
        if self.window._is_live_update_enabled:
            self.realtime_timer.start()

    def stop_realtime(self):
        """Stops periodic updates."""
        self.realtime_timer.stop()

    def pause_realtime(self):
        """Temporarily pauses updates (e.g. during UI interaction)."""
        self.stop_realtime()

    def resume_realtime(self):
        """Resumes periodic updates if still enabled."""
        self.start_realtime()

    def _execute_debounced_update(self):
        """Callback for the debounce timer."""
        self.trigger_refresh(show_loading=self._pending_show_loading)
