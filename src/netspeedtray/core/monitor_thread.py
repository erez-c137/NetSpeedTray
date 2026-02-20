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
        # Ensure interval is always a positive, sane value to avoid busy loops
        min_interval = constants.timers.MINIMUM_INTERVAL_MS / 1000.0
        try:
            self.interval = max(min_interval, float(interval))
        except Exception:
            self.interval = min_interval
        self._is_running = True
        self.consecutive_errors = 0
        self.logger = logger
        self.logger.debug("NetworkMonitorThread initialized with interval %.2fs", interval)

    def set_interval(self, interval: float) -> None:
        """Dynamically updates the polling interval."""
        self.interval = max(0.1, interval)
        self.logger.debug("Monitoring interval updated to %.2fs", self.interval)

    def run(self) -> None:
        """Main monitoring loop with circuit breaker logic."""
        self.logger.debug("NetworkMonitorThread starting loop...")
        
        while self._is_running:
            try:
                # Polling network stats: This is the I/O that we want off the main thread.
                counters = psutil.net_io_counters(pernic=True)
                if counters:
                    self.counters_ready.emit(counters)
                    
                # Success - reset circuit breaker
                if self.consecutive_errors > 0:
                    self.logger.info("Network monitor recovered from transient errors.")
                    self.consecutive_errors = 0
                    
            except (psutil.AccessDenied, OSError) as e:
                self.consecutive_errors += 1
                self.logger.error("Error fetching stats (Attempt %d/10): %s", self.consecutive_errors, e, exc_info=True)
                
                if self.consecutive_errors > 10:
                    self.logger.critical("Too many consecutive errors (%d). Stopping monitor thread to prevent log spam.", self.consecutive_errors)
                    self.error_occurred.emit(f"Critical: Monitor thread stopped after {self.consecutive_errors} failures: {e}")
                    self._is_running = False
                    break
                    
            except Exception as e:
                self.logger.error("Unexpected error in monitoring thread: %s", e, exc_info=True)
                self.error_occurred.emit(str(e))
                # For unexpected exceptions, we might also want to increment error count
                # or handle differently. For now, we replicate original 'warn but continue' behavior
                # or arguably, we should also track these. Let's count them too for safety.
                self.consecutive_errors += 1
                if self.consecutive_errors > 10:
                    self._is_running = False
                    break
            
            # Use sliced sleep to remain responsive to shutdown requests
            sleep_remaining = self.interval
            while sleep_remaining > 0 and self._is_running:
                sleep_slice = min(0.1, sleep_remaining)
                time.sleep(sleep_slice)
                sleep_remaining -= sleep_slice

    def stop(self) -> None:
        """Gracefully stops the monitoring loop."""
        self._is_running = False
        self.wait(constants.timeouts.MONITOR_THREAD_STOP_WAIT_MS) # Wait for thread to terminate
        self.logger.info("NetworkMonitorThread stopped.")
