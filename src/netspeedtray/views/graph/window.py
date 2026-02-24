import logging
import warnings
from datetime import datetime
from typing import Tuple, Optional, Any, List, Dict

# Suppress Matplotlib AutoDateLocator interval warnings globally
warnings.filterwarnings("ignore", "AutoDateLocator was unable to pick an appropriate interval")
warnings.filterwarnings("ignore", "Tight layout not applied")

# --- Third-Party Imports ---
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.dates as mdates
import numpy as np

# --- PyQt6 Imports ---
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent, QThread
from PyQt6.QtGui import QIcon, QResizeEvent, QShowEvent, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QWidget, QDialog
)

from netspeedtray import constants
from netspeedtray.utils import helpers, styles as style_utils
from netspeedtray.core.position_manager import ScreenUtils

# Modular Graph Components
from netspeedtray.views.graph.interaction import GraphInteractionHandler
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.graph.worker import GraphDataWorker
from netspeedtray.views.graph.ui import GraphWindowUI
from netspeedtray.views.graph.logic import GraphLogic
from netspeedtray.views.graph.controls import GraphSettingsPanel
from netspeedtray.views.graph.config_handler import GraphConfigHandler
from netspeedtray.views.graph.coordinator import GraphCoordinator
from netspeedtray.views.graph.request import DataRequest

class GraphWindow(QWidget):
    """
    A lean controller for the Graph Window.
    Delegates logic to specialized handlers.
    """
    request_data_processing = pyqtSignal(DataRequest)
    window_closed = pyqtSignal()

    def __init__(self, main_widget, parent=None, logger=None, i18n=None, session_start_time: Optional[datetime] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        self.logger = logger or logging.getLogger(__name__)
        self._main_widget = main_widget
        self.i18n = i18n
        self.session_start_time = session_start_time or datetime.now()
        
        # State
        self._is_closing = False
        self._initial_load_done = False
        self._is_dark_mode = self.config.get("dark_mode", True)
        self._is_live_update_enabled = self.config.get("live_update", True)
        self._history_period_value = self.config.get('history_period_slider_value', 2)
        self._current_request_id = 0
        self._last_processed_id = -1
        self.interface_filter = None
        self._show_loading_status = self.config.get("show_loading", False)
        self._settings_panel_base_width = 0  # Will be set at initialization
        
        # Handlers
        self.ui = GraphWindowUI(self)
        self.ui.setupUi()
        self.ui.init_overlay_elements()
        self.renderer = GraphRenderer(self.ui.graph_widget, self.i18n, self.logger)
        self.interaction = GraphInteractionHandler(self)
        self.config_handler = GraphConfigHandler(self)
        self.coordinator = GraphCoordinator(self)
        
        # Connections
        self.interaction.zoom_range_selected.connect(self.coordinator.handle_zoom_selection)
        self.interaction.zoom_reset_requested.connect(self.coordinator.reset_zoom_state)
        self._init_worker_thread(self._main_widget.widget_state)
        self._connect_signals()
        
        # Styling & Position
        self.renderer.apply_theme(self._is_dark_mode)
        self.config_handler.init_db_size_monitoring()
        self.setWindowTitle(constants.graph.WINDOW_TITLE)
        self._apply_icon()
        self._position_window()

    @property
    def config(self):
        return self._main_widget.config if self._main_widget else {}

    def _apply_icon(self):
        try:
            icon_path = helpers.get_app_asset_path(constants.app.ICON_FILENAME)
            if icon_path.exists(): self.setWindowIcon(QIcon(str(icon_path)))
        except Exception as e: self.logger.error(f"Icon error: {e}")

    def _toggle_settings_panel_visibility(self):
        if self._is_closing: return
        try:
            if not hasattr(self, 'settings_widget') or self.settings_widget is None:
                self._init_settings_panel()
            
            if self.settings_widget.isVisible():
                self.settings_widget.hide()
                # Calculate current panel width and shrink window
                # FIX for #103: Use stored base width, not hardcoded 320px
                # This prevents shrinking more than intended on each toggle
                current_width = self.settings_widget.width() if self.settings_widget.width() > 0 else self._settings_panel_base_width
                self.resize(self.width() - current_width, self.height())
            else:
                # Show the panel and expand window to accommodate it
                # Use the base width measured at init (self._settings_panel_base_width)
                # instead of hardcoded value. This ensures consistent delta on all toggles.
                panel_width = self._settings_panel_base_width if self._settings_panel_base_width > 0 else 320
                self.settings_widget.setFixedWidth(panel_width)
                self.resize(self.width() + panel_width, self.height())
                self.settings_widget.show()
                
            self.ui.reposition_overlay_elements()
        except Exception as e: self.logger.error(f"Error toggling settings: {e}")

    def _init_settings_panel(self):
        db_path = None
        if hasattr(self._main_widget, "widget_state") and hasattr(self._main_widget.widget_state, "db_worker"):
            db_path = self._main_widget.widget_state.db_worker.db_path
        db_size_mb = GraphLogic.get_db_size_mb(db_path)

        initial_state = {
            'is_dark_mode': self._is_dark_mode,
            'is_live_update_enabled': self._is_live_update_enabled,
            'history_period_value': self._history_period_value,
            'retention_days': self.config.get("keep_data", 30),
            'show_loading': self._show_loading_status,
            'db_size_mb': db_size_mb
        }
        self.settings_widget = GraphSettingsPanel(self, i18n=self.i18n, initial_state=initial_state)
        self.interface_filter = self.settings_widget.interface_filter
        
        # Add to the side layout (Hidden by default)
        self.ui.add_settings_panel(self.settings_widget)
        self.settings_widget.hide()
        
        self.settings_widget.populate_interfaces(self._main_widget.get_unified_interface_list())
        self._connect_settings_signals()
        self.interface_filter.view().parent().installEventFilter(self)
        
        # FIX for Issue #103: Measure and cache the base panel width at initialization
        # ============================================================================
        # Bug: Window shrinks with each settings toggle click
        # Root cause: Previous code used hardcoded 320px, but actual panel was 340-350px
        #            Each hide-show cycle shrank window by (actual - 320) = 20-30px cumulative
        #
        # Solution: Measure the actual panel at init using sizeHint()
        #           Cache result in self._settings_panel_base_width
        #           All future toggles use this consistent delta
        #
        # Why measurement at init?
        #   - Panel size is determined by its largest tab's content (usually "Display" tab)
        #   - This doesn't change during runtime (tabs are populated once)
        #   - Measuring once at init is efficient (avoids sizeHint() calls on every toggle)
        #   - Fallback to 320px if measurement fails (minimum reasonable width)
        #
        # Calculate and store base panel width (size of largest tab, typically Display)
        self.settings_widget.setVisible(True)  # Temporarily show to measure
        self._settings_panel_base_width = self.settings_widget.sizeHint().width()
        if self._settings_panel_base_width < 300:
            self._settings_panel_base_width = 320  # Fallback to minimum reasonable width
        self.settings_widget.setHidden(True)  # Hide again

    def _populate_interface_filter(self):
        """ database refresh hook. """
        if hasattr(self, 'settings_widget') and self.settings_widget:
            self.settings_widget.populate_interfaces(self._main_widget.get_unified_interface_list())

    def _init_worker_thread(self, widget_state):
        self.worker_thread = QThread()
        self.data_worker = GraphDataWorker(widget_state)
        self.data_worker.moveToThread(self.worker_thread)
        self.data_worker.data_ready.connect(self._on_data_ready)
        self.data_worker.error.connect(self.ui.show_graph_error)
        self.request_data_processing.connect(self.data_worker.process_data)
        self.worker_thread.start()

    def _get_time_range_from_ui(self):
        period_key = GraphLogic.get_period_key(self._history_period_value)
        boot_time = getattr(self, '_cached_boot_time', None)
        earliest_db = getattr(self, '_cached_earliest_db', None)
        
        if period_key == "TIMELINE_SYSTEM_UPTIME" and not boot_time:
            boot_time = GraphLogic.get_boot_time()
            earliest_db = self._main_widget.widget_state.get_earliest_data_timestamp()
            self._cached_boot_time, self._cached_earliest_db = boot_time, earliest_db

        return GraphLogic.get_time_range(self._history_period_value, self.session_start_time, boot_time, earliest_db)

    def _position_window(self):
        try:
            screen = QApplication.primaryScreen()
            if not screen: return self.move(100, 100)
            
            saved_pos = self.config.get("graph_window_pos", {})
            if saved_pos and "x" in saved_pos and "y" in saved_pos:
                validated = ScreenUtils.validate_position(saved_pos["x"], saved_pos["y"], (self.width(), self.height()), screen)
                self.move(validated.x, validated.y)
            else:
                geom = screen.geometry()
                self.move((geom.width()-self.width())//2 + geom.x(), (geom.height()-self.height())//2 + geom.y())
        except Exception as e: self.logger.error(f"Position error: {e}")

    def _on_tab_changed(self, index: int):
        """Handle tab switching by moving the renderer's canvas and updating the graph."""
        if self._is_closing: return
        
        # Move the canvas to the new tab
        target_widget = self.ui.tab_widget.widget(index)
        if target_widget and hasattr(target_widget, 'layout') and target_widget.layout():
            # Remove from old layout, add to new
            self.renderer.canvas.setParent(None)
            target_widget.layout().addWidget(self.renderer.canvas)
            
            # Reposition overlays in the NEW active layout
            self.ui.header_widget.setParent(None)
            target_widget.layout().insertWidget(0, self.ui.header_widget)
            
        self.update_graph()
        self.ui.reposition_overlay_elements()

    def _connect_signals(self):
        self.ui.tab_widget.currentChanged.connect(self._on_tab_changed)
        self.ui.hamburger_icon.clicked.connect(self._toggle_settings_panel_visibility)
        self.interaction.interaction_detected.connect(self._on_interaction_detected)
        self.ui.reset_zoom_btn.clicked.connect(self.coordinator.reset_zoom_state)

    def _connect_settings_signals(self):
        if not hasattr(self, 'settings_widget'): return
        sw = self.settings_widget
        sw.dark_mode_toggled.connect(self.toggle_dark_mode)
        sw.live_update_toggled.connect(self.toggle_live_update)
        sw.interface_filter_changed.connect(self._on_interface_filter_changed)
        sw.timeline_changed.connect(self.coordinator.handle_timeline_change)
        sw.retention_changed.connect(self._on_retention_changed)
        sw.retention_changing.connect(self._update_keep_data_text)
        sw.show_loading_toggled.connect(self._on_loading_toggled)

    def _on_interaction_detected(self):
        import time as pytime
        self._last_interaction_ts = pytime.time()

    def eventFilter(self, watched, event):
        if hasattr(self, 'interface_filter') and watched == self.interface_filter.view().parent():
            if event.type() == QEvent.Type.Show: self.coordinator.pause_realtime()
            elif event.type() == QEvent.Type.Hide: self.coordinator.resume_realtime()
        return super().eventFilter(watched, event)

    def update_graph(self, show_loading=True):
        if self._is_closing: return
        if show_loading and self._show_loading_status:
            self.ui.show_graph_message(getattr(self.i18n, 'COLLECTING_DATA_MESSAGE', "Loading..."), is_error=False)
        start, end = self._get_time_range_from_ui()
        interface = self.interface_filter.currentData() if self.interface_filter else None
        period_key = GraphLogic.get_period_key(self._history_period_value)
        
        # Determine stat type from active tab
        tab_index = self.ui.tab_widget.currentIndex()
        stat_type = "overview"
        if tab_index == 1: stat_type = "network"
        elif tab_index == 2: stat_type = "cpu"
        elif tab_index == 3: stat_type = "gpu"
        
        self._current_request_id += 1
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name=None if interface == "all" else interface,
            is_session_view=period_key == "TIMELINE_SESSION",
            sequence_id=self._current_request_id,
            stat_type=stat_type
        )
        self.request_data_processing.emit(request)

    def update_graph_range(self, start, end):
        if self._is_closing: return
        interface = self.interface_filter.currentData() if self.interface_filter else None
        period_key = GraphLogic.get_period_key(self._history_period_value)
        self._current_request_id += 1
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name=None if interface == "all" else interface,
            is_session_view=period_key == "TIMELINE_SESSION",
            sequence_id=self._current_request_id
        )
        self.request_data_processing.emit(request)

    def toggle_dark_mode(self, checked):
        self._is_dark_mode = checked
        self.config_handler.queue_config_update({"dark_mode": checked})
        self.renderer.apply_theme(checked)
        self.ui.stats_bar.setStyleSheet(style_utils.graph_stats_bar_style())
        self.ui.reposition_overlay_elements()

    def toggle_live_update(self, checked):
        self._is_live_update_enabled = checked
        self.config_handler.queue_config_update({"live_update": checked})
        if checked: self.coordinator.start_realtime()
        else: self.coordinator.stop_realtime()

    def _on_interface_filter_changed(self, name):
        self.coordinator.reset_zoom_state(trigger_update=True)

    def _on_settings_layout_changed(self):
        """
        Handle dynamic resizing when settings panel internal layout changes.
        
        Context (Issue #103):
        - Settings panel has expandable sections with arrow toggles
        - When section expands/collapses, panel width might change
        - Window must adjust to prevent panel from being cut off
        
        Strategy:
        1. Get new panel size from sizeHint()
        2. Calculate delta from base width measured at init
        3. Adjust window width by delta if change is significant
        4. Only resize if delta > ~10px (avoid micro-adjustments)
        
        Why this approach:
        - Efficient: Only called when layout actually changes (rare)
        - Precise: Uses actual sizeHint() for accurate measurement
        - Safe: Works with any panel modifications (arrows, expansions, etc.)
        """
        if not self.settings_widget or not self.settings_widget.isVisible():
            return
        
        try:
            # Get the new required size for the settings panel
            new_width = self.settings_widget.sizeHint().width()
            if new_width <= 0:
                new_width = self._settings_panel_base_width
            
            # Calculate the difference from base width
            width_delta = new_width - self._settings_panel_base_width
            
            # Adjust window width if needed (only for significant changes > 5px)
            if abs(width_delta) > 5:
                new_window_width = self.width() + width_delta
                self.resize(new_window_width, self.height())
                self.settings_widget.setFixedWidth(new_width)
                self.ui.reposition_overlay_elements()
        except Exception as e:
            self.logger.error(f"Error resizing settings panel: {e}")

    def _on_loading_toggled(self, checked):
        self._show_loading_status = checked
        self.config_handler.queue_config_update({"show_loading": checked})
        if not checked: self.ui.hide_graph_message()

    def _on_retention_changed(self, value):
        if not self._main_widget: return
        self._main_widget.config["keep_data"] = value 
        self._main_widget.config_manager.save(self._main_widget.config)
        self._update_keep_data_text(value)

    def _update_keep_data_text(self, value):
        if hasattr(self, 'settings_widget'):
            db_path = None
            if hasattr(self._main_widget, "widget_state") and hasattr(self._main_widget.widget_state, "db_worker"):
                db_path = self._main_widget.widget_state.db_worker.db_path
            
            db_size_mb = GraphLogic.get_db_size_mb(db_path)
            self.settings_widget.update_retention_text(value, db_size_mb)

    def showEvent(self, event):
        super().showEvent(event)
        self.coordinator.start_realtime()
        if not self._initial_load_done:
            self._initial_load_done = True
            QTimer.singleShot(50, lambda: self.update_graph(show_loading=True))

    def closeEvent(self, event):
        self._is_closing = True
        self.coordinator.stop_realtime()
        if hasattr(self, 'worker_thread'):
            self.worker_thread.quit()
            self.worker_thread.wait(500)
        self.window_closed.emit()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.ui.reposition_overlay_elements()

    def _on_data_ready(self, data, total_up, total_down, sequence_id):
        if sequence_id < self._last_processed_id or self._is_closing: return
        self._last_processed_id = sequence_id
        if not data: self.ui.show_graph_message(self.i18n.NO_DATA_MESSAGE, is_error=False)
        try: self._render_graph(data, total_up, total_down)
        except Exception as e: self.logger.error(f"Render callback error: {e}")

    def _render_graph(self, history_data, total_up=0.0, total_down=0.0):
        try:
            start, end = (self.coordinator.custom_zoom_start, self.coordinator.custom_zoom_end) if self.coordinator.is_custom_zoom_active else self._get_time_range_from_ui()
            period_key = GraphLogic.get_period_key(self._history_period_value)
            
            tab_index = self.ui.tab_widget.currentIndex()
            stat_type = "overview"
            if tab_index == 1: stat_type = "network"
            elif tab_index == 2: stat_type = "cpu"
            elif tab_index == 3: stat_type = "gpu"

            result = self.renderer.render(history_data, start, end, period_key, boot_time=getattr(self, '_cached_boot_time', None), stat_type=stat_type)
            
            # If we rendered actual data, show 'LIVE' status
            if history_data:
                self.ui.show_graph_message("LIVE", is_error=False)
            if result:
                self.interaction.update_data_cache(result[0], result[2], result[3], x_coords=result[1])
            else:
                self.interaction.update_data_cache(np.array([]), np.array([]), np.array([]))
            self._update_stats_bar(history_data, total_up, total_down)
        except Exception as e: self.logger.error(f"Render error: {e}")

    def _update_stats_bar(self, history_data, total_up=0.0, total_down=0.0):
        try:
            if not history_data or not self.ui.max_stat_val: return
            
            tab_index = self.ui.tab_widget.currentIndex()
            if tab_index == 0: # Overview
                if isinstance(history_data, dict):
                    cpu_vals = [d[1] for d in history_data.get("cpu", [])]
                    gpu_vals = [d[1] for d in history_data.get("gpu", [])]
                    c_avg = sum(cpu_vals)/len(cpu_vals) if cpu_vals else 0
                    g_avg = sum(gpu_vals)/len(gpu_vals) if gpu_vals else 0
                    self.ui.max_stat_val.setText(f"CPU Avg: {c_avg:.1f}%")
                    self.ui.avg_stat_val.setText(f"GPU Avg: {g_avg:.1f}%")
                    self.ui.total_stat_val.setText("System Overview")
                else:
                    self.ui.total_stat_val.setText("Loading Overview...")
            elif tab_index == 1: # Network
                stats = GraphLogic.calculate_stats(history_data)
                self.ui.max_stat_val.setText(f"↓ {stats['max_down']:.1f}  ↑ {stats['max_up']:.1f} Mbps")
                self.ui.avg_stat_val.setText(f"↓ {stats['avg_down']:.1f}  ↑ {stats['avg_up']:.1f} Mbps")
                t_up_v, t_up_u = helpers.format_data_size(total_up, self.i18n)
                t_dw_v, t_dw_u = helpers.format_data_size(total_down, self.i18n)
                self.ui.total_stat_val.setText(f"↓ {t_dw_v}{t_dw_u}  ↑ {t_up_v}{t_up_u}")
            elif tab_index == 2 or tab_index == 3: # Hardware (CPU/GPU separate)
                vals = [d[1] for d in history_data]
                v_max = max(vals) if vals else 0
                v_avg = sum(vals)/len(vals) if vals else 0
                self.ui.max_stat_val.setText(f"Max: {v_max:.1f}%")
                self.ui.avg_stat_val.setText(f"Avg: {v_avg:.1f}%")
                self.ui.total_stat_val.setText("Utilization Profile")
            self.ui.hide_graph_message()
        except Exception as e: self.logger.error(f"Stats error: {e}")

    def export_history(self) -> None:
        """Export network speed history to CSV using DataExporter."""
        if self._is_closing: return
        try:
            if not self._main_widget or not self._main_widget.widget_state:
                QMessageBox.warning(self, self.i18n.WARNING_TITLE, self.i18n.EXPORT_DATA_ACCESS_ERROR_MESSAGE)
                return

            start_time, _ = self._get_time_range_from_ui()
            selected_interface = self.interface_filter.currentData()
            
            history_tuples = self._main_widget.widget_state.get_speed_history(
                start_time=start_time,
                interface_name=None if selected_interface == "all" else selected_interface
            )

            from netspeedtray.utils.exporters import DataExporter
            DataExporter.export_to_csv(self, self.i18n, history_tuples)
        except Exception as e:
            self.logger.error(f"Error exporting history: {e}", exc_info=True)

    def save_figure(self) -> None:
        """Save the current graph as a PNG image using DataExporter."""
        if self._is_closing: return
        from netspeedtray.utils.exporters import DataExporter
        DataExporter.save_graph_image(self, self.i18n, self.renderer.figure)
