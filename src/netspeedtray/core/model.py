"""
Model module for NetSpeedTray.

This module defines the `Model` class, responsible for retrieving and calculating network
speed data using the `psutil` library. It provides a robust interface for the controller
to fetch speeds, handling interface selection, unit conversions, and error conditions.
"""

from __future__ import annotations
import logging
from datetime import datetime
import psutil
from typing import Tuple, Dict, List, Optional, Set
from dataclasses import dataclass
import time

from ..constants.constants import (
    InterfaceConstants,
    NetworkSpeedConstants,
    ControllerConstants,
    UnitConstants,
)


@dataclass
class CoreSpeedData:
    """
    Represents core network speed data with upload and download speeds and a timestamp.

    Attributes:
        timestamp: The timestamp when the speed data was recorded.
        upload: Upload speed in bytes/sec.
        download: Download speed in bytes/sec.
    """
    timestamp: datetime
    upload: float
    download: float


@dataclass
class NetworkSpeed:
    """
    Represents network speed data with upload and download speeds.

    Attributes:
        upload_speed: Upload speed in the specified unit (default: bytes/sec).
        download_speed: Download speed in the specified unit (default: bytes/sec).
        unit: Unit of the speeds ("Bps" for bytes/sec, "bps" for bits/sec).
    """
    upload_speed: float
    download_speed: float
    unit: str = NetworkSpeedConstants.DEFAULT_UNIT_BYTES


class Model:
    """
    Manages network speed data retrieval and calculation for NetSpeedTray.

    Interfaces with `psutil` to collect network statistics and compute upload/download
    speeds for specified interfaces. Supports dynamic interface selection and robust
    error handling.

    Attributes:
        logger: Logger instance for tracking model operations and errors.
        last_time: Timestamp of the last speed update (from `time.monotonic()`).
        last_stats: Map of interface names to last (sent, received) byte counts.
        first_update: Flag indicating if this is the first update (skips calculation).
        selected_interfaces: List of interfaces to monitor (empty for all).
        interface_mode: Mode for interface selection ("all" or "selected").
        available_interfaces: Cached list of available network interfaces.
        _update_counter: Counter for controlling logging frequency.
        _interfaces_to_monitor: Cached set of interfaces to monitor.
    """
    def __init__(self) -> None:
        """
        Initialize the model with default state values.

        Sets up initial state for tracking network stats and retrieves available interfaces.
        """
        self.logger = logging.getLogger(f"netspeedtray.core.{self.__class__.__name__}")
        self.last_time: float = time.monotonic()
        self.last_stats: Dict[str, Tuple[int, int]] = {}
        self.first_update: bool = True
        self.selected_interfaces: List[str] = []
        self.interface_mode: str = InterfaceConstants.DEFAULT_MODE
        self.available_interfaces: List[str] = self._get_available_interfaces()
        self._update_counter: int = 0
        self._interfaces_to_monitor: Set[str] = set()
        self._update_interfaces_to_monitor()
        self.logger.info("Model initialized with %d available interfaces",
                         len(self.available_interfaces))

    def _get_available_interfaces(self) -> List[str]:
        """
        Retrieve the list of available network interfaces from the system.

        Returns:
            List[str]: List of network interface names.

        Raises:
            psutil.Error: If retrieving interfaces fails due to permission issues or psutil errors.
            RuntimeError: If retrieving interfaces fails due to unexpected system errors.
        """
        try:
            interfaces = list(psutil.net_io_counters(pernic=True).keys())
            self.logger.debug("Retrieved %d available interfaces: %s", len(interfaces), interfaces)
            return interfaces
        except psutil.Error as e:
            self.logger.error("Failed to retrieve interfaces due to psutil error: %s", e)
            raise
        except Exception as e:
            self.logger.exception("Unexpected error retrieving interfaces: %s", e)
            raise RuntimeError(f"Cannot retrieve interfaces: {e}")

    def _update_interfaces_to_monitor(self) -> None:
        """
        Updates the cached set of interfaces to monitor based on the current mode and selection.
        """
        if self.interface_mode == "all":
            self._interfaces_to_monitor = {
                iface for iface in self.available_interfaces
                if not any(keyword in iface.lower() for keyword in ["loopback", "lo", "pseudo-interface"])
            }
        else:
            self._interfaces_to_monitor = set(self.selected_interfaces)

    def get_available_interfaces(self) -> List[str]:
        """
        Get the cached list of available network interfaces.

        Returns:
            List[str]: List of available network interface names.
        """
        return self.available_interfaces.copy()

    def set_interfaces(self, interface_mode: str, selected_interfaces: List[str]) -> None:
        """
        Configure the network interfaces to monitor.

        Args:
            interface_mode: Mode for interface selection ("all" or "selected").
            selected_interfaces: List of interface names to monitor if mode is "selected".

        Raises:
            ValueError: If `interface_mode` is invalid or `selected_interfaces` is invalid
                in "selected" mode.

        Examples:
            >>> model = Model()
            >>> model.set_interfaces("selected", ["Ethernet"])
            >>> model.interface_mode
            'selected'
            >>> model.selected_interfaces
            ['Ethernet']
            >>> model.set_interfaces("all", [])
            >>> model.interface_mode
            'all'
        """
        if interface_mode not in InterfaceConstants.VALID_MODES:
            self.logger.error("Invalid interface mode: %s, expected one of %s",
                             interface_mode, InterfaceConstants.VALID_MODES)
            raise ValueError(f"Invalid interface mode: {interface_mode}. Must be one of {InterfaceConstants.VALID_MODES}")

        if interface_mode == "selected":
            if not selected_interfaces:
                self.logger.error("Selected interfaces list is empty in 'selected' mode")
                raise ValueError("Selected interfaces list cannot be empty in 'selected' mode")
            invalid_interfaces = [iface for iface in selected_interfaces if iface not in self.available_interfaces]
            if invalid_interfaces:
                self.logger.error("Invalid interfaces provided: %s", invalid_interfaces)
                raise ValueError(f"Invalid interfaces: {invalid_interfaces}. Available: {self.available_interfaces}")

        self.interface_mode = interface_mode
        self.selected_interfaces = selected_interfaces.copy()
        self._update_interfaces_to_monitor()
        self.last_stats = {iface: stats for iface, stats in self.last_stats.items() if iface in self._interfaces_to_monitor}
        self.logger.info("Interfaces configured: mode=%s, selected=%s",
                        self.interface_mode, self.selected_interfaces)

    def get_network_speeds(self, use_bits: bool = False) -> NetworkSpeed:
        """
        Calculate current upload and download speeds for monitored interfaces.

        Args:
            use_bits: If True, returns speeds in bits/sec (bps); if False, in bytes/sec (Bps).

        Returns:
            NetworkSpeed: Object with calculated upload and download speeds in the specified unit.

        Raises:
            ValueError: If `use_bits` is not a boolean.
            PermissionError: If permission is denied accessing network stats.
            RuntimeError: If network statistics retrieval fails critically.

        Examples:
            >>> model = Model()
            >>> speeds = model.get_network_speeds(use_bits=False)
            >>> print(speeds.upload_speed, speeds.download_speed, speeds.unit)
            0.0 0.0 Bps  # On first update
            >>> speeds = model.get_network_speeds(use_bits=True)
            >>> print(speeds.unit)
            bps
        """
        if not isinstance(use_bits, bool):
            self.logger.error("use_bits must be boolean, got %s", type(use_bits))
            raise ValueError(f"use_bits must be boolean, got {type(use_bits)}")

        try:
            current_time = time.monotonic()
            time_diff = current_time - self.last_time
            if time_diff <= 0:
                self.logger.warning("Non-positive time diff: %.6f, using fallback", time_diff)
                time_diff = NetworkSpeedConstants.MIN_TIME_DIFF

            net_stats = psutil.net_io_counters(pernic=True)
            interfaces_to_monitor = {iface for iface in self._interfaces_to_monitor if iface in net_stats}

            if not interfaces_to_monitor:
                self.logger.warning("No interfaces to monitor: mode=%s, selected=%s",
                                   self.interface_mode, self.selected_interfaces)
                return self._default_speeds(use_bits)

            total_upload = 0.0
            total_download = 0.0
            for iface in interfaces_to_monitor:
                stats = net_stats[iface]
                last_up, last_down = self.last_stats.get(iface, (0, 0))
                if stats.bytes_sent < 0 or stats.bytes_recv < 0:
                    self.logger.warning("Negative byte counts for %s: sent=%d, recv=%d",
                                       iface, stats.bytes_sent, stats.bytes_recv)
                    continue

                if not self.first_update:
                    upload_speed = (stats.bytes_sent - last_up) / time_diff
                    download_speed = (stats.bytes_recv - last_down) / time_diff
                    total_upload += max(0.0, upload_speed)
                    total_download += max(0.0, download_speed)

                self.last_stats[iface] = (stats.bytes_sent, stats.bytes_recv)

            if self.first_update:
                self.first_update = False
                self.logger.debug("First update, returning zero speeds")
                return self._default_speeds(use_bits)

            unit = NetworkSpeedConstants.DEFAULT_UNIT_BITS if use_bits else NetworkSpeedConstants.DEFAULT_UNIT_BYTES
            if use_bits:
                total_upload *= UnitConstants.BITS_PER_BYTE
                total_download *= UnitConstants.BITS_PER_BYTE

            self.last_time = current_time
            self._update_counter += 1
            if self._update_counter % ControllerConstants.SPEED_LOGGING_FREQUENCY == 0:
                self.logger.debug("Speeds: upload=%.2f %s, download=%.2f %s",
                                 total_upload, unit, total_download, unit)

            return NetworkSpeed(upload_speed=total_upload, download_speed=total_download, unit=unit)

        except PermissionError as e:
            self.logger.error("Permission denied accessing network stats: %s", e)
            raise
        except KeyError as e:
            self.logger.error("Interface key error: %s", e)
            raise RuntimeError(f"Interface not found: {e}")
        except Exception as e:
            self.logger.exception("Unexpected error in speed calculation: %s", e)
            raise RuntimeError(f"Failed to calculate speeds: {e}")

    def _default_speeds(self, use_bits: bool) -> NetworkSpeed:
        """
        Return default (zero) speeds with the specified unit.

        Args:
            use_bits: If True, uses bits/sec; if False, uses bytes/sec.

        Returns:
            NetworkSpeed: Object with zero speeds.
        """
        unit = NetworkSpeedConstants.DEFAULT_UNIT_BITS if use_bits else NetworkSpeedConstants.DEFAULT_UNIT_BYTES
        return NetworkSpeed(
            upload_speed=NetworkSpeedConstants.DEFAULT_SPEED,
            download_speed=NetworkSpeedConstants.DEFAULT_SPEED,
            unit=unit,
        )

    def reset(self) -> None:
        """
        Reset the model to its initial state.
        """
        self.logger.info("Resetting model state")
        self.last_time = time.monotonic()
        self.last_stats.clear()
        self.first_update = True
        self.selected_interfaces = []
        self.interface_mode = InterfaceConstants.DEFAULT_MODE
        self.available_interfaces = self._get_available_interfaces()
        self._update_interfaces_to_monitor()
        self._update_counter = 0