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
from ..utils.network_utils import get_primary_interface_name

if TYPE_CHECKING:
    from ..views.widget import NetworkSpeedWidget
    from ..core.widget_state import WidgetState

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

        # self._update_primary_interface_name() # REMOVED FROM INIT - it's too early.
        self.logger.info("NetworkController initialized.")


    def set_view(self, view: 'NetworkSpeedWidget') -> None:
        """Connects the controller to the main widget view."""
        self.view = view
        # The signal name was wrong in my refactoring. It should be display_speed_updated
        self.display_speed_updated.connect(self.view.update_display_speeds)
        self.logger.debug("View set and signal connected.")


    def update_speeds(self) -> None:
        """
        The main update loop. Fetches stats, calculates per-interface speeds,
        sends granular data to WidgetState, and sends aggregated data to the view.
        """
        current_time = time.monotonic()
        current_counters = self._fetch_network_stats()
        if not current_counters:
            self.display_speed_updated.emit(0.0, 0.0)
            return

        # On the very first run, just store the counters and exit.
        if not self.last_interface_counters:
            self.logger.debug("First run. Storing baseline counters.")
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            return

        time_diff = current_time - self.last_check_time
        
        # Prevent division by a tiny number if updates are too fast
        if time_diff < 0.1:
            return
            
        # Check for long pauses, indicative of the computer waking from sleep.
        update_interval = self.config.get("update_rate", 1.0)
        sleep_threshold = max(5.0, update_interval * 5)

        if time_diff > sleep_threshold:
            self.logger.info("Long time delta (%.1fs) detected. Re-priming counters to prevent speed spike.", time_diff)
            # Fully reset the state, just like on the first run
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self.display_speed_updated.emit(0.0, 0.0)
            return # Exit immediately after re-priming.

        self.current_speed_data.clear()
        exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)

        for name, current in current_counters.items():
            if any(kw in name.lower() for kw in exclusions):
                continue

            last = self.last_interface_counters.get(name)
            if last:
                # Silently handle counter resets (e.g., driver restart)
                if current.bytes_sent < last.bytes_sent or current.bytes_recv < last.bytes_recv:
                    continue

                up_diff = current.bytes_sent - last.bytes_sent
                down_diff = current.bytes_recv - last.bytes_recv
                
                # Calculate as integer Bytes/sec for data integrity
                up_speed_bps = int(up_diff / time_diff)
                down_speed_bps = int(down_diff / time_diff)
                
                # The hardcoded sanity check remains as a final safety net
                max_speed = constants.network.interface.MAX_REASONABLE_SPEED_BPS
                if up_speed_bps > max_speed or down_speed_bps > max_speed:
                    self.logger.warning(
                        f"Discarding impossibly high speed for '{name}': "
                        f"Up={up_speed_bps} B/s. This may be a hardware or driver anomaly."
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
        An interface is considered active if its calculated speed is above a small threshold.
        """
        if not self.current_speed_data:
            return []
        
        # Return interfaces that have any meaningful traffic
        return [
            name for name, (up_speed, down_speed) in self.current_speed_data.items()
            if up_speed > 1.0 or down_speed > 1.0 # Speeds are in Bytes/sec
        ]


    def _aggregate_for_display(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """
        Aggregates the calculated per-interface speeds based on the current monitoring mode.
        Returns total upload and download speeds in Bytes/sec.
        """
        mode = self.config.get("interface_mode", "auto")

        if mode == "selected":
            selected = self.config.get("selected_interfaces", [])
            if not selected:
                return 0.0, 0.0
            total_up, total_down = 0.0, 0.0
            for name in selected:
                if name in per_interface_speeds:
                    up, down = per_interface_speeds[name]
                    total_up += up
                    total_down += down
            return total_up, total_down

        elif mode == "auto":
            # Call the function to identify the primary interface. This will trigger the log.
            self._update_primary_interface_name()
            
            if self.primary_interface and self.primary_interface in per_interface_speeds:
                # If we found it, return the speeds for only that interface.
                return per_interface_speeds[self.primary_interface]
            else:
                # If we couldn't find it, show zero until it's found on the next tick.
                return 0.0, 0.0
        
        # If mode is "all" or any other value, sum everything.
        return self._sum_all(per_interface_speeds)


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
            # This calls the reliable function instead of using netifaces
            new_primary_interface = get_primary_interface_name()

            if self.primary_interface != new_primary_interface:
                if new_primary_interface:
                    self.logger.info("Found new primary interface: '%s'", new_primary_interface)
                else:
                    self.logger.warning("Could not determine primary interface. Will aggregate all physical interfaces.")
                self.primary_interface = new_primary_interface

        except Exception as e:
            self.logger.error("Unexpected error updating primary interface: %s", e, exc_info=True)
            self.primary_interface = None


    def get_available_interfaces(self) -> List[str]:
        """Returns a list of all non-excluded interface names."""
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