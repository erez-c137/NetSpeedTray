
from PyQt6.QtCore import QObject, QTimer
import logging
import os
from netspeedtray.views.graph.logic import GraphLogic

class GraphConfigHandler(QObject):
    """
    Handles configuration management, debounced saving, and database monitoring 
    for the Graph module.
    """
    
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.logger = logging.getLogger(__name__)
        
        # Debounce timer for configuration saving
        self.config_debounce_timer = QTimer(self)
        self.config_debounce_timer.setSingleShot(True)
        self.config_debounce_timer.setInterval(500)
        self.config_debounce_timer.timeout.connect(self._process_pending_config_save)
        self.pending_config = {}
        
        # Database size monitoring
        self.db_size_update_timer = QTimer(self)
        self.db_size_update_timer.timeout.connect(self.update_db_size_display)

    def queue_config_update(self, settings_dict: dict):
        """Accumulates settings changes and triggers a debounced save."""
        self.pending_config.update(settings_dict)
        self.config_debounce_timer.start()

    def save_slider_value(self, config_key: str, value: int):
        """Directly saves a slider value to config without debouncing (if requested)."""
        if self.window._main_widget:
            self.window._main_widget.config[config_key] = value
            try:
                self.window._main_widget.config_manager.save(self.window._main_widget.config)
            except Exception as e:
                self.logger.error(f"Failed to save {config_key}: {e}")

    def _process_pending_config_save(self):
        """Processes the actual save call to the main widget."""
        if not self.pending_config: return
        try:
            if self.window._main_widget and hasattr(self.window._main_widget, 'handle_graph_settings_update'):
                self.window._main_widget.handle_graph_settings_update(self.pending_config)
                self.pending_config = {}
        except Exception as e:
            self.logger.error(f"Error in debounced config save: {e}", exc_info=True)

    def init_db_size_monitoring(self):
        """Starts the periodic database size check."""
        self.update_db_size_display()
        self.db_size_update_timer.start(300000) # 5 minutes

    def update_db_size_display(self):
        """ authoritative function to update the database size display in the UI. """
        try:
            db_path = None
            if hasattr(self.window._main_widget, "widget_state") and hasattr(self.window._main_widget.widget_state, "db_worker"):
                # Ensure db_path is actually a string path, not a widget object
                potential_path = self.window._main_widget.widget_state.db_worker.db_path
                if isinstance(potential_path, (str, os.PathLike)):
                    db_path = str(potential_path)
                else:
                    self.logger.warning(f"Invalid DB Path type found: {type(potential_path)}")
            
            db_size_mb = GraphLogic.get_db_size_mb(db_path)
            if hasattr(self.window, 'settings_widget') and self.window.settings_widget:
                # We update the 'Year' label or similar to show DB size if possible, 
                # or just pass it to the widget for formatting.
                if hasattr(self.window.settings_widget, 'update_db_size_text'):
                    self.window.settings_widget.update_db_size_text(db_size_mb)
                elif hasattr(self.window, '_update_keep_data_text'):
                    # Fallback to the method that handles retention text formatting
                    val = 3 # default index for 30 days
                    if hasattr(self.window.settings_widget, 'keep_data_slider'):
                        val = self.window.settings_widget.keep_data_slider.value()
                    self.window._update_keep_data_text(val)
        except Exception as e:
            self.logger.error(f"Error updating DB size display: {e}")
