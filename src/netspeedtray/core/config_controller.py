"""
Configuration Controller for NetSpeedTray.

This module extracts configuration management logic from the main widget,
handling loading, saving, application, and rollback of settings.
"""

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING


from netspeedtray import constants
from netspeedtray.utils.window_state import set_window_always_on_top
from netspeedtray.utils.styles import set_app_reduce_motion
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray.utils.helpers import effective_display_mode

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
        # Last applied always-on-top state. apply_all_settings is the LIVE-PREVIEW path, not a
        # commit path - SettingsDialog re-emits on a 250ms throttle while you drag a slider - so a
        # native SetWindowPos here would fire several times a second. Only act on an actual change.
        self._last_keep_on_top: Optional[bool] = None
        self._rolling_back = False   # re-entrancy guard for rollback_config's re-apply

    def _apply_keep_on_top(self, on_top: bool) -> None:
        """Push the always-on-top preference to the open auxiliary windows, once per change."""
        on_top = bool(on_top)
        if on_top == self._last_keep_on_top:
            return
        self._last_keep_on_top = on_top
        w = self.widget
        for window in (getattr(w, "settings_dialog", None), getattr(w, "monitor_window", None)):
            # Only touch a window that actually exists and is on screen; a hidden Settings dialog
            # picks the state up from its own showEvent on the next open.
            if window is not None and window.isVisible():
                set_window_always_on_top(window, on_top)

    def load_initial_config(self, taskbar_height: int) -> Dict[str, Any]:
        """Loads the configuration. ``taskbar_height`` is runtime state (the widget keeps it as
        self.taskbar_height); it used to be injected here, then written into config.json on every
        save, and read back as an unknown "newer version?" key on every launch (C3)."""
        self.logger.debug("Loading initial configuration...")
        return self.config_manager.load()

    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True,
                      apply_and_repaint: bool = True) -> None:
        """
        Updates the internal configuration and optionally saves it to disk.

        ``apply_and_repaint`` (C0 apply-split): single-setting callers (tray pause, data-cap
        dialog, flags) leave it True to get an immediate renderer rebuild + repaint. The
        settings *commit* path passes False because ``apply_all_settings`` runs right after
        and does the full visual apply once - avoiding a redundant double renderer rebuild
        and double repaint on every Apply.
        """
        self.logger.debug(f"Updating configuration with {len(updates)} items... (Save: {save_to_disk})")

        if not self.widget.config:
             # Should practically never happen if initialized correctly
             raise RuntimeError("Configuration not initialized in widget.")

        try:
            self.widget.config.update(updates)

            if apply_and_repaint:
                # Forward updates to renderer immediately if it exists
                if hasattr(self.widget, 'renderer') and self.widget.renderer:
                    self.widget.renderer.update_config(self.widget.config)

            if save_to_disk:
                self.config_manager.save(self.widget.config)
                self.logger.debug("Configuration updated and saved successfully.")

            if apply_and_repaint:
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
            
            # --- Hardware Monitor Integration Logic ---
            cpu_enabled = updated_config.get('monitor_cpu_enabled', old_config.get('monitor_cpu_enabled', False))
            gpu_enabled = updated_config.get('monitor_gpu_enabled', old_config.get('monitor_gpu_enabled', False))
            ram_enabled = updated_config.get('monitor_ram_enabled', old_config.get('monitor_ram_enabled', False))
            vram_enabled = updated_config.get('monitor_vram_enabled', old_config.get('monitor_vram_enabled', False))
            
            current_mode = updated_config.get('widget_display_mode', old_config.get('widget_display_mode', 'network_only'))

            # Rule 2: Fallback to network-only if cycle mode has no hardware monitors to rotate through.
            # cpu_only/gpu_only are ephemeral internal cycle states, never persisted to config.
            # side_by_side gracefully degrades to network-only at render time and is never downgraded here.
            if current_mode == "cycle" and not cpu_enabled and not gpu_enabled:
                self.logger.info(f"Display mode '{current_mode}' is no longer valid. Falling back to network-only.")
                updated_config['widget_display_mode'] = "network_only"

            if save_to_disk:
                # Persist only - apply_all_settings() below does the single visual apply (C0).
                self.update_config(updated_config, apply_and_repaint=False)
            else:
                # Direct memory update if not saving (e.g. preview)
                self.widget.config.update(updated_config)

            # Apply the changes to the system
            self.apply_all_settings()

            self.logger.debug("Settings successfully handled and applied.")

        except Exception as e:
            self.logger.error(f"Failed to handle settings change: {e}", exc_info=True)
            self.rollback_config(old_config, save_to_disk=save_to_disk)
            raise

    def rollback_config(self, old_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Restores a previous configuration state: in memory, on disk, and in the live components.

        A failed apply_all_settings stops partway, so the renderer / controller / monitor thread may
        already hold the new values while the rest still run the old ones. Saving the old config
        alone left that half-applied state on screen until the next restart (rc.2 S8), so the
        restored config goes back through the same apply path. If that re-apply fails too, it is
        logged and swallowed - the caller re-raises the original error.

        ``save_to_disk`` mirrors the failed call: a live-preview apply (save_to_disk=False) never wrote
        the file, so its rollback must not either - the in-memory config it would save can hold
        earlier previewed values that Cancel is about to revert (#432 review)."""
        self.logger.warning("Rolling back configuration changes due to apply failure.")
        restored = dict(old_config)
        cfg = self.widget.config
        if isinstance(cfg, dict):
            # In place, so anything holding the widget's config dict sees the restored values too.
            cfg.clear()
            cfg.update(restored)
        else:
            self.widget.config = restored
        if save_to_disk:
            try:
                self.config_manager.save(self.widget.config)
                self.logger.info("Configuration rolled back and saved successfully.")
            except Exception as e:
                self.logger.error(f"CRITICAL: Error saving rolled-back configuration: {e}", exc_info=True)

        if self._rolling_back:
            return   # an apply that itself rolled back: never re-enter
        self._rolling_back = True
        try:
            self.apply_all_settings()
            self.logger.info("Rolled-back configuration re-applied.")
        except Exception as e:
            self.logger.error(f"CRITICAL: Re-applying the rolled-back configuration failed: {e}", exc_info=True)
        finally:
            self._rolling_back = False

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
                w.monitor_thread.update_config(w.config)
                # Same resolver as startup (main.py), so a settings save can never hand the sampler
                # a value startup would not (#273: a raw value once hit set_interval's 0.1 s floor).
                update_rate = w.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)
                w.monitor_thread.set_interval(resolve_poll_interval_seconds(update_rate))
            
            if w.widget_state:
                self.logger.debug("Applying widget state config...")
                w.widget_state.apply_config(w.config)

            # If the user disables temperature/power display, clear any stale values
            # so layout calculation and rendering immediately reflect the change.
            if not w.config.get("show_hardware_temps", False):
                if hasattr(w, "cpu_temp"):
                    w.cpu_temp = None
                if hasattr(w, "gpu_temp"):
                    w.gpu_temp = None
            if not w.config.get("show_hardware_power", False):
                if hasattr(w, "cpu_power"):
                    w.cpu_power = None
                if hasattr(w, "gpu_power"):
                    w.gpu_power = None

            # 2. Directly set the font, which also triggers the resize.
            self.logger.debug("Applying font and resizing via layout manager...")
            w.layout_manager.set_font(resize=True)
            
            # 3. AFTER resizing, update the position.
            self.logger.debug("Updating widget position...")
            w.update_position()

            # 3a. "Show on all taskbars" (#229): build / retire / re-size the mirrors after the main
            # widget has its new size. Nothing is built or connected while the setting is off.
            if hasattr(w, "apply_mirrors"):
                w.apply_mirrors()
            
            # 4. Manage Cycling Timer (stopped while "Show hardware in the hover card" collapses the
            # widget to network-only - nothing to rotate; the saved Auto-Cycle comes back with it off)
            if effective_display_mode(w.config) == "cycle":
                if not w._cycle_timer.isActive():
                    w._cycle_timer.start(constants.renderer.CYCLE_INTERVAL_MS)
                    self.logger.debug("Cycle timer started via ConfigController.")
            else:
                if w._cycle_timer.isActive():
                    w._cycle_timer.stop()
                    self.logger.debug("Cycle timer stopped via ConfigController.")

            # 5. Always-on-top for the Settings/Monitor windows (#213). Guarded: see
            # _last_keep_on_top - this method runs on every throttled live-preview tick.
            self._apply_keep_on_top(w.config.get("keep_windows_on_top", False))

            # 5a. Reduce motion: the app setting forces it on; off follows Windows (see styles).
            set_app_reduce_motion(w.config.get("reduce_motion", False))

            # 5b. Local metrics endpoint (2.2 scope B): start / stop / re-port live with the toggle.
            if hasattr(w, "apply_metrics_endpoint"):
                w.apply_metrics_endpoint()

            # 5c. Latency probe: follows the Latency switch live (privacy review 2.2 - turning it off
            # used to keep pinging until the next restart).
            if hasattr(w, "apply_latency_probe"):
                w.apply_latency_probe()

            # 6. Schedule a repaint to reflect all changes.
            w.update()
            
            self.logger.info("All settings applied successfully.")
        except Exception as e:
            self.logger.error(f"Error applying settings to components: {e}", exc_info=True)
            raise RuntimeError(f"Failed to apply settings: {e}") from e
