"""
Configuration Controller for NetSpeedTray.

This module extracts configuration management logic from the main widget,
handling loading, saving, application, and rollback of settings.
"""

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from PyQt6.QtGui import QColor

from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager

if TYPE_CHECKING:
    from netspeedtray.views.widget.main import NetworkSpeedWidget


class ConfigController:
    """
    Manages configuration operations for the NetworkSpeedWidget.
    Functions as the bridge between the View (Widget) and the Data/Persistence (ConfigManager).
    """

    def __init__(self, widget: 'NetworkSpeedWidget', config_manager: ConfigManager):
        self.widget = widget
        self.config_manager = config_manager
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.ConfigController")

    def load_initial_config(self, taskbar_height: int) -> Dict[str, Any]:
        """Loads configuration and injects taskbar height."""
        self.logger.debug("Loading initial configuration...")
        config = self.config_manager.load()
        config["taskbar_height"] = taskbar_height
        return config

    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Updates the internal configuration and optionally saves it to disk."""
        self.logger.debug(f"Updating configuration with {len(updates)} items... (Save: {save_to_disk})")
        
        if not self.widget.config:
             # Should practically never happen if initialized correctly
             raise RuntimeError("Configuration not initialized in widget.")

        try:
            self.widget.config.update(updates)
            
            # Forward updates to renderer immediately if it exists
            if hasattr(self.widget, 'renderer') and self.widget.renderer:
                self.widget.renderer.update_config(self.widget.config)
            
            if save_to_disk:
                self.config_manager.save(self.widget.config)
                self.logger.debug("Configuration updated and saved successfully.")
            
            # Trigger a repaint to reflect changes (e.g. if 'paused' changed, or immediate values)
            self.widget.update()
            
        except Exception as e:
            self.logger.error(f"Error updating/saving configuration: {e}", exc_info=True)
            raise RuntimeError(f"Failed to save configuration: {e}") from e

    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """
        Handles configuration changes. Applies them to the widget and optionally saves them.
        """
        self.logger.debug(f"Handling settings change request... (Save to disk: {save_to_disk})")
        
        old_config = self.widget.config.copy()

        try:
            free_move_was_enabled = old_config.get('free_move', False)
            free_move_is_now_enabled = updated_config.get('free_move', False)

            if free_move_was_enabled and not free_move_is_now_enabled:
                self.logger.debug("Free Move was disabled. Clearing saved coordinates.")
                updated_config['position_x'] = None
                updated_config['position_y'] = None
            
            # Capture current position when Free Move is active upon saving
            if free_move_is_now_enabled:
                current_pos = self.widget.pos()
                updated_config['position_x'] = current_pos.x()
                updated_config['position_y'] = current_pos.y()
                self.logger.debug(f"Free Move is active. Capturing current position ({current_pos.x()}, {current_pos.y()}) for save.")

            if save_to_disk:
                self.update_config(updated_config)
            else:
                # Direct memory update if not saving (e.g. preview)
                self.widget.config.update(updated_config)
            
            # Apply the changes to the system
            self.apply_all_settings()

            self.logger.debug("Settings successfully handled and applied.")

        except Exception as e:
            self.logger.error(f"Failed to handle settings change: {e}", exc_info=True)
            self.rollback_config(old_config)
            raise

    def rollback_config(self, old_config: Dict[str, Any]) -> None:
        """Restores a previous configuration state."""
        self.logger.warning("Rolling back configuration changes due to apply failure.")
        self.widget.config = old_config
        try:
            self.config_manager.save(self.widget.config)
            # Re-apply old settings to ensure consistency? 
            # Ideally yes, but if apply failed, re-applying old might also fail or be redundant.
            # For now, just saving state.
            self.logger.info("Configuration rolled back and saved successfully.")
        except Exception as e:
            self.logger.error(f"CRITICAL: Error saving rolled-back configuration: {e}", exc_info=True)

    def apply_all_settings(self) -> None:
        """
        Applies all settings from the current config in a specific, synchronous order
        to prevent race conditions.
        """
        self.logger.debug("Applying all settings from current configuration...")
        w = self.widget
        
        if not w.config:
            raise RuntimeError("Configuration not loaded.")

        try:
            # 1. Update all non-visual components.
            if w.renderer:
                self.logger.debug("Updating renderer config...")
                w.renderer.update_config(w.config)
            
            if w.controller:
                self.logger.debug("Applying controller config...")
                w.controller.apply_config(w.config)
            
            if w.monitor_thread:
                self.logger.debug("Updating monitor thread interval...")
                update_rate = w.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)
                w.monitor_thread.set_interval(update_rate)
            
            if w.widget_state:
                self.logger.debug("Applying widget state config...")
                w.widget_state.apply_config(w.config)

            self.logger.debug("Synchronizing internal QColor objects.")
            w.default_color = QColor(w.config.get("default_color", constants.config.defaults.DEFAULT_COLOR))
            w.high_color = QColor(w.config.get("high_speed_color", constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR))
            w.low_color = QColor(w.config.get("low_speed_color", constants.config.defaults.DEFAULT_LOW_SPEED_COLOR))

            # 2. Directly set the font, which also triggers the resize.
            self.logger.debug("Applying font and resizing via layout manager...")
            w.layout_manager.set_font(resize=True)
            
            # 3. AFTER resizing, update the position.
            self.logger.debug("Updating widget position...")
            w.update_position()
            
            # 4. Schedule a repaint to reflect all changes.
            w.update()
            
            self.logger.info("All settings applied successfully.")
        except Exception as e:
            self.logger.error(f"Error applying settings to components: {e}", exc_info=True)
            raise RuntimeError(f"Failed to apply settings: {e}") from e
