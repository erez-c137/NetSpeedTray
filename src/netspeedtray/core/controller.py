"""
Controller module for NetSpeedTray.

This module defines the `NetworkController` class, which manages network data acquisition
and processing. It fetches interface statistics via psutil, calculates speeds, and emits
updates for the view. It supports monitoring all or selected interfaces based on config.
"""

import logging
import time
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Tuple

from PyQt6.QtCore import pyqtSignal, QObject
import psutil

from ..constants.constants import ConfigConstants, NetworkSpeedConstants

# Type checking imports
if TYPE_CHECKING:
    from ..views.widget import NetworkSpeedWidget
    from ..core.widget_state import WidgetState

logger = logging.getLogger("NetSpeedTray.NetworkController")


class NetworkController(QObject):
    """
    Manages network data acquisition and speed calculations for NetSpeedTray.

    Fetches network stats using psutil, calculates upload/download speeds, and emits them
    in Mbps via the `speeds_updated` signal. Stores speeds in bytes/sec in `WidgetState`.
    Supports 'all' or 'selected' interface modes based on configuration.

    Attributes:
        speeds_updated: Signal emitting upload/download speeds in Mbps.
        config: Application configuration dictionary.
        widget_state: Optional WidgetState for speed history storage.
        view: Reference to NetworkSpeedWidget (set via `set_view`).
        interface_mode: 'all' or 'selected' monitoring mode.
        selected_interfaces: List of interfaces to monitor if mode is 'selected'.
    """
    speeds_updated = pyqtSignal(float, float, int, int)
    """Signal with new speeds: (upload Mbps, download Mbps, bytes_sent_diff, bytes_recv_diff)."""

    def __init__(self, config: Dict[str, Any], widget_state: Optional['WidgetState'] = None) -> None:
        """
        Initializes the controller with config and optional WidgetState.

        Sets up initial network monitoring state and performs a baseline stats fetch.

        Args:
            config: Configuration dict with `interface_mode` and `selected_interfaces`.
            widget_state: Optional WidgetState for storing speed history (bytes/sec).

        Raises:
            ImportError: If psutil is not installed.
        """
        super().__init__()
        self.logger = logger
        self.config = config
        self.widget_state = widget_state
        self.view: Optional['NetworkSpeedWidget'] = None
        self.last_check_time: float = time.monotonic()
        self.last_bytes_sent: int = 0
        self.last_bytes_recv: int = 0
        self.interface_counters: Optional[Dict[str, Any]] = None
        self.interface_mode: str = config.get("interface_mode", ConfigConstants.DEFAULT_INTERFACE_MODE)
        self.selected_interfaces: List[str] = config.get("selected_interfaces", [])
        if psutil is None:
            self.logger.critical("psutil is required but not installed.")
            raise ImportError("psutil is required for network monitoring.")
        self._fetch_network_stats()
        self.logger.debug("NetworkController initialized with mode: %s", self.interface_mode)
        
    def set_view(self, view: 'NetworkSpeedWidget') -> None:
        self.view = view
        self.speeds_updated.connect(view.update_stats)
        self.logger.debug("View set and signal connected.")
        mode = self.config.get("interface_mode", ConfigConstants.DEFAULT_INTERFACE_MODE)
        if mode not in ConfigConstants.VALID_INTERFACE_MODES:
            self.logger.warning("Invalid interface_mode '%s', defaulting to '%s'.", mode, ConfigConstants.DEFAULT_INTERFACE_MODE)
            self.interface_mode = ConfigConstants.DEFAULT_INTERFACE_MODE
        else:
            self.interface_mode = mode
        self.selected_interfaces = self.config.get("selected_interfaces", [])

    def update_interface_settings(self, mode: str, selected: List[str]) -> None:
        if mode not in ConfigConstants.VALID_INTERFACE_MODES:
            self.logger.warning("Invalid mode '%s', ignoring update.", mode)
            return
        self.interface_mode = mode
        if mode == "selected":
            available = self.get_available_interfaces()
            self.selected_interfaces = [iface for iface in selected if iface in available]
            if not self.selected_interfaces:
                self.logger.warning("No valid selected interfaces; mode may fall back to defaults.")
        else:
            self.selected_interfaces = []
        self.interface_counters = None
        self.last_check_time = time.monotonic()
        self.logger.info("Interface settings updated: mode=%s, selected=%s", mode, self.selected_interfaces)

    def _fetch_network_stats(self) -> Optional[Dict[str, Any]]:
        try:
            counters = psutil.net_io_counters(pernic=True)
            self.logger.debug("Fetched network stats for %d interfaces.", len(counters))
            return counters
        except psutil.AccessDenied:
            self.logger.error("Permission denied fetching network stats.")
            return None
        except Exception as e:
            self.logger.error("Error fetching network stats: %s", e)
            return None

    def _aggregate_counters(self, current_counters: Dict[str, Any]) -> Tuple[int, int]:
        total_sent, total_recv = 0, 0
        exclude = ["loopback", "lo", "pseudo-interface"]
        if self.interface_mode == "all":
            for iface, stats in current_counters.items():
                if any(kw in iface.lower() for kw in exclude):
                    continue
                total_sent += getattr(stats, 'bytes_sent', 0)
                total_recv += getattr(stats, 'bytes_recv', 0)
        elif self.interface_mode == "selected":
            for iface in self.selected_interfaces:
                stats = current_counters.get(iface)
                if stats:
                    total_sent += getattr(stats, 'bytes_sent', 0)
                    total_recv += getattr(stats, 'bytes_recv', 0)
                else:
                    self.logger.warning("Interface '%s' not found in counters.", iface)
        return total_sent, total_recv

    def update_speeds(self) -> None:
        """Calculates speeds and raw byte differences, then emits them."""
        current_time = time.monotonic()
        current_counters = self._fetch_network_stats()

        if current_counters is None:
            self.logger.error("Failed to fetch network stats; emitting zero values.")
            self.speeds_updated.emit(0.0, 0.0, 0, 0)
            return

        bytes_sent, bytes_recv = self._aggregate_counters(current_counters)
        time_diff = current_time - self.last_check_time
        
        upload_speed_bps, download_speed_bps = 0.0, 0.0
        sent_diff, recv_diff = 0, 0

        if self.interface_counters is not None and time_diff > 0.1:
            if bytes_sent < self.last_bytes_sent or bytes_recv < self.last_bytes_recv:
                self.logger.warning("Network counters appear to have reset. Skipping one update cycle.")
            else:
                sent_diff = bytes_sent - self.last_bytes_sent
                recv_diff = bytes_recv - self.last_bytes_recv
                upload_speed_bps = sent_diff / time_diff
                download_speed_bps = recv_diff / time_diff
        
        self.last_check_time = current_time
        self.last_bytes_sent = bytes_sent
        self.last_bytes_recv = bytes_recv
        self.interface_counters = current_counters

        upload_speed_mbps = (upload_speed_bps * 8) / 1_000_000
        download_speed_mbps = (download_speed_bps * 8) / 1_000_000
        
        # --- CHANGE: Emit the raw byte difference along with the speed rate ---
        self.speeds_updated.emit(upload_speed_mbps, download_speed_mbps, sent_diff, recv_diff)
        self.logger.debug(f"Emitted - Up: {upload_speed_mbps:.2f} Mbps, Down: {download_speed_mbps:.2f} Mbps, Sent: {sent_diff} B, Recv: {recv_diff} B")
    
    def pause(self) -> None:
        self.logger.debug("Controller paused (future use)")

    def resume(self) -> None:
        self.logger.debug("Controller resumed (future use)")
        self.update_speeds()

    def get_available_interfaces(self) -> List[str]:
        try:
            return list(psutil.net_io_counters(pernic=True).keys())
        except Exception as e:
            self.logger.error("Failed to get interfaces: %s", e)
            return []

    def apply_config(self, config: Dict[str, Any]) -> None:
        self.config = config.copy()
        self.update_interface_settings(
            config.get("interface_mode", ConfigConstants.DEFAULT_INTERFACE_MODE),
            config.get("selected_interfaces", [])
        )
        self.logger.debug("Configuration applied.")

    def cleanup(self) -> None:
        if self.view:
            try:
                self.speeds_updated.disconnect(self.view.update_stats)
            except TypeError:
                pass
            self.view = None
        self.interface_counters = None
        self.last_check_time = 0.0
        self.last_bytes_sent = 0
        self.last_bytes_recv = 0
        self.logger.debug("Cleanup completed.")