"""
Controller module for NetSpeedTray.

This module defines the NetworkController, which manages network data acquisition,
per-interface speed calculation, and smart aggregation. It acts as the brain
for network monitoring, providing accurate and relevant data to the view and data layers.
"""

import logging
import time
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Tuple

from PyQt6.QtCore import pyqtSignal, QObject
import psutil

from netspeedtray import constants
from netspeedtray.utils.network_utils import get_primary_interface_name

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.core.widget_state import WidgetState

logger = logging.getLogger("NetSpeedTray.NetworkController")


class NetworkController(QObject):
    """
    Manages network data acquisition, per-interface speed calculation, and smart aggregation.
    """
    # This signal is for the VIEW, emitting the final aggregated speed in Mbps.
    display_speed_updated = pyqtSignal(float, float)


    def __init__(self, config: Dict[str, Any], widget_state: 'WidgetState') -> None:
        super().__init__()
        self.logger = logger
        self.config = config
        self.widget_state = widget_state
        self.view: Optional['NetworkSpeedWidget'] = None
        
        self.last_check_time: float = 0.0
        self.last_interface_counters: Dict[str, psutil._common.snetio] = {}
        self.current_speed_data: Dict[str, Tuple[float, float]] = {}
        self.primary_interface: Optional[str] = None
        self.last_primary_check_time: float = 0.0

        self.repriming_needed: int = 0  # Number of priming cycles needed after a resume

        self.logger.info("NetworkController initialized.")


    def set_view(self, view: 'NetworkSpeedWidget') -> None:
        """Connects the controller to the main widget view."""
        self.view = view
        self.display_speed_updated.connect(self.view.update_display_speeds)
        self.logger.debug("View set and signal connected.")


    def update_speeds(self) -> None:
        """
        The main update loop. Fetches stats, calculates per-interface speeds,
        and handles a re-priming state after resume-from-sleep to prevent phantom spikes.
        """
        current_time = time.monotonic()
        current_counters = self._fetch_network_stats()
        if not current_counters:
            self.display_speed_updated.emit(0.0, 0.0)
            return

        if not self.last_interface_counters:
            self.logger.debug("First run. Storing baseline counters.")
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            return

        time_diff = current_time - self.last_check_time
        update_interval = self.config.get("update_rate", 1.0)

        # Skip updates that are triggered too soon (e.g., more than 2x the requested rate)
        # Fix: Using * 0.5 instead of / 0.5 to correctly allow jitter down to 500ms for a 1s interval.
        if time_diff < (update_interval * 0.5):
            return
            
        # Increase validity threshold to be more tolerant of system lag (10s minimum)
        validity_threshold = max(10.0, update_interval * 5.0)

        # --- LAYER 1: DETECT RESUME EVENT ---
        if time_diff > validity_threshold:
            self.logger.info(
                "Abnormal time delta (%.1fs) detected. Entering re-priming state to prevent speed spike.",
                time_diff
            )
            self.repriming_needed = 2  # Require 2 good readings before trusting the data
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self.display_speed_updated.emit(0.0, 0.0)
            return

        # --- LAYER 2: EXECUTE RE-PRIMING STATE ---
        if self.repriming_needed > 0:
            self.logger.debug("Re-priming cycle: %s remaining.", self.repriming_needed)
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self.display_speed_updated.emit(0.0, 0.0)
            self.repriming_needed -= 1
            if self.repriming_needed == 0:
                self.logger.info("Re-priming complete. Resuming normal speed calculation.")
            return

        # --- NORMAL OPERATION ---
        self.current_speed_data.clear()

        for name, current in current_counters.items():
            last = self.last_interface_counters.get(name)
            if last:
                # --- LAYER 3: COUNTER ROLLOVER CHECK ---
                if current.bytes_sent < last.bytes_sent or current.bytes_recv < last.bytes_recv:
                    self.logger.warning("Counter rollover or reset detected for '%s'. Skipping this cycle.", name)
                    continue

                up_diff = current.bytes_sent - last.bytes_sent
                down_diff = current.bytes_recv - last.bytes_recv
                
                up_speed_bps = int(up_diff / time_diff)
                down_speed_bps = int(down_diff / time_diff)
                
                # --- LAYER 4: SANITY CHECK ---
                max_speed = constants.network.interface.MAX_REASONABLE_SPEED_BPS
                if up_speed_bps > max_speed or down_speed_bps > max_speed:
                    self.logger.warning(
                        f"Discarding impossibly high speed for '{name}': "
                        f"Up={up_speed_bps} B/s, Down={down_speed_bps} B/s."
                    )
                    continue

                self.current_speed_data[name] = (up_speed_bps, down_speed_bps)

        if self.current_speed_data:
            self.widget_state.add_speed_data(self.current_speed_data)

        agg_upload, agg_download = self._aggregate_for_display(self.current_speed_data)

        upload_mbps = (agg_upload * 8) / 1_000_000
        download_mbps = (agg_download * 8) / 1_000_000
        
        self.display_speed_updated.emit(upload_mbps, download_mbps)

        self.last_check_time = current_time
        self.last_interface_counters = current_counters


    def get_active_interfaces(self) -> List[str]:
        """
        Returns a list of interface names that currently have active network speed data.
        """
        if not self.current_speed_data:
            return []
        
        return [
            name for name, (up_speed, down_speed) in self.current_speed_data.items()
            if up_speed > 1.0 or down_speed > 1.0
        ]

    # --- CORRECTED AGGREGATION LOGIC ---
    def _aggregate_for_display(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """
        Aggregates the calculated per-interface speeds based on the current monitoring mode.
        Returns total upload and download speeds in Bytes/sec.
        """
        mode = self.config.get("interface_mode", "auto")

        if mode == "selected":
            selected_interfaces = self.config.get("selected_interfaces", [])
            if not selected_interfaces:
                return 0.0, 0.0
            
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if name in selected_interfaces)
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if name in selected_interfaces)
            return total_up, total_down

        elif mode == "auto":
            self._update_primary_interface_name()
            if self.primary_interface and self.primary_interface in per_interface_speeds:
                return per_interface_speeds[self.primary_interface]
            else:
                return 0.0, 0.0

        elif mode == "all_physical":
            exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            return total_up, total_down

        elif mode == "all_virtual":
            # No filtering applied, sum everything.
            return self._sum_all(per_interface_speeds)
        
        else: # Fallback for unknown/legacy mode
            self.logger.warning("Unknown interface_mode '%s'. Defaulting to 'auto'.", mode)
            self.config["interface_mode"] = "auto"
            return self._aggregate_for_display(per_interface_speeds)


    def _sum_all(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """Helper to sum all values in the provided speeds dictionary."""
        total_up = sum(up for up, down in per_interface_speeds.values())
        total_down = sum(down for up, down in per_interface_speeds.values())
        return total_up, total_down


    def _fetch_network_stats(self) -> Optional[Dict[str, psutil._common.snetio]]:
        """Fetches raw I/O counters for all network interfaces from psutil."""
        try:
            return psutil.net_io_counters(pernic=True)
        except (psutil.AccessDenied, OSError) as e:
            self.logger.error("Permission denied fetching network stats: %s", e)
            return None
        except Exception as e:
            self.logger.error("Error fetching network stats: %s", e, exc_info=True)
            return None


    def _update_primary_interface_name(self) -> None:
        """
        Identifies and updates the primary network interface using the robust
        socket-based method from network_utils.
        """
        try:
            new_primary_interface = get_primary_interface_name()
            if self.primary_interface != new_primary_interface:
                if new_primary_interface:
                    self.logger.info("Found new primary interface: '%s'", new_primary_interface)
                else:
                    self.logger.warning("Could not determine primary interface. Speeds may show as 0 in 'Auto' mode.")
                self.primary_interface = new_primary_interface
        except Exception as e:
            self.logger.error("Unexpected error updating primary interface: %s", e, exc_info=True)
            self.primary_interface = None


    def get_available_interfaces(self) -> List[str]:
        """
        Returns a clean list of interface names for the UI, still respecting exclusions
        to avoid cluttering the "Select Specific Interfaces" list.
        """
        try:
            all_interfaces = psutil.net_io_counters(pernic=True).keys()
            exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
            return sorted([
                name for name in all_interfaces 
                if not any(kw in name.lower() for kw in exclusions)
            ])
        except Exception as e:
            self.logger.error("Failed to get available interfaces: %s", e)
            return []


    def apply_config(self, config: Dict[str, Any]) -> None:
        """Applies a new configuration dictionary to the controller."""
        self.config = config.copy()
        self.logger.debug("Configuration applied to controller.")


    def cleanup(self) -> None:
        """Disconnects signals and cleans up resources."""
        if self.view:
            try:
                self.display_speed_updated.disconnect(self.view.update_display_speeds)
            except (TypeError, RuntimeError):
                pass
            self.view = None
        self.last_interface_counters.clear()
        self.logger.debug("Controller cleanup completed.")