"""
Background Network Monitor Thread for NetSpeedTray.

This module provides a dedicated QThread for polling network interface statistics
using psutil. Offloading this I/O from the main UI thread ensures consistent
60+ FPS widget movement and prevents micro-stutters during network stack latency.
"""

import logging
import time
from typing import Dict, Optional

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.NetworkMonitorThread")


class NetworkMonitorThread(QThread):
    """
    Background thread that polls network I/O counters at a regular interval.
    Emits the raw counters for processing in the controller.
    """
    counters_ready = pyqtSignal(dict)  # Dict[str, psutil._common.snetio]
    error_occurred = pyqtSignal(str)

    def __init__(self, interval: float = 1.0) -> None:
        super().__init__()
        self.interval = interval
        self._is_running = True
        self.logger = logger
        self.logger.info("NetworkMonitorThread initialized with interval %.2fs", interval)

    def set_interval(self, interval: float) -> None:
        """Dynamically updates the polling interval."""
        self.interval = max(0.1, interval)
        self.logger.debug("Monitoring interval updated to %.2fs", self.interval)

    def run(self) -> None:
        """Main monitoring loop."""
        self.logger.debug("NetworkMonitorThread starting loop...")
        while self._is_running:
            try:
                # Polling network stats: This is the I/O that we want off the main thread.
                counters = psutil.net_io_counters(pernic=True)
                if counters:
                    self.counters_ready.emit(counters)
            except (psutil.AccessDenied, OSError) as e:
                self.logger.error("Permission denied or OS error fetching stats: %s", e)
                # We emit but don't stop the thread, permitting recovery if it's transient.
            except Exception as e:
                self.logger.error("Unexpected error in monitoring thread: %s", e, exc_info=True)
                self.error_occurred.emit(str(e))
            
            # Use precise sleep to maintain timing.
            time.sleep(self.interval)

    def stop(self) -> None:
        """Gracefully stops the monitoring loop."""
        self._is_running = False
        self.wait(1000) # Wait up to 1 second for thread to terminate
        self.logger.info("NetworkMonitorThread stopped.")
