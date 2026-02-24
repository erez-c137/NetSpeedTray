"""
Background Hardware Monitor Thread for NetSpeedTray.

This module provides a dedicated QThread for polling system statistics:
- Network I/O (via psutil)
- CPU Utilization (via psutil)
- GPU Utilization (via Windows PDH)

Offloading this I/O from the main UI thread ensures consistent 60+ FPS widget movement
and prevents micro-stutters during system stack latency.
"""

import logging
import time
from typing import Dict, Any, Optional, List, Tuple

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

# Windows-specific imports for GPU monitoring via PDH
try:
    import win32pdh
except ImportError:
    win32pdh = None

try:
    import win32com.client
except ImportError:
    win32com.client = None

import subprocess
import shutil
from functools import lru_cache

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.StatsMonitorThread")


class StatsMonitorThread(QThread):
    """
    Background thread that polls hardware statistics at a regular interval.
    Emits a unified dictionary of metrics for processing in the controller.
    """
    stats_ready = pyqtSignal(dict)  # Contains 'network', 'cpu', 'gpu' keys if enabled
    error_occurred = pyqtSignal(str)

    def __init__(self, interval: float = 1.0, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.config = config or {}
        
        # Ensure interval is always a positive, sane value to avoid busy loops
        min_interval = constants.timers.MINIMUM_INTERVAL_MS / 1000.0
        try:
            self.interval = max(min_interval, float(interval))
        except Exception:
            self.interval = min_interval
            
        self._is_running = True
        self.consecutive_errors = 0
        self.logger = logger
        
        # WMI for temperatures
        self._wmi: Any = None
        self._nvidia_smi_path: Optional[str] = self._get_cached_path("nvidia-smi")
        
        # PDH Queries for GPU
        self._gpu_query: Optional[int] = None
        self._gpu_util_counters: List[int] = []
        self._gpu_vram_counters: List[int] = []

        self.logger.debug("StatsMonitorThread initialized with interval %.2fs", self.interval)

    def set_interval(self, interval: float) -> None:
        """Dynamically updates the polling interval."""
        self.interval = max(0.1, interval)
        self.logger.debug("Monitoring interval updated to %.2fs", self.interval)

    def update_config(self, config: Dict[str, Any]) -> None:
        """Updates internal config copy and resets hardware queries if needed."""
        self.config = config
        # Reset GPU query if toggled to ensure clean state
        self._cleanup_gpu_query()

    def _init_gpu_query(self) -> bool:
        """Initializes Windows PDH query for universal GPU utilization and VRAM."""
        if not win32pdh:
            return False
            
        try:
            if self._gpu_query:
                return True
                
            self._gpu_query = win32pdh.OpenQuery()
            self._gpu_util_counters = []
            self._gpu_vram_counters = []
            
            # 1. Utilization Counters (\GPU Engine(*)\Utilization Percentage)
            try:
                _, instances = win32pdh.EnumObjectItems(None, None, "GPU Engine", win32pdh.PERF_DETAIL_WIZARD)
                for instance in instances:
                    # Filter for 3D engine if possible, otherwise take all and we'll MAX them
                    counter_path = f"\\GPU Engine({instance})\\Utilization Percentage"
                    try:
                        handle = win32pdh.AddCounter(self._gpu_query, counter_path)
                        self._gpu_util_counters.append(handle)
                    except: continue
            except Exception as e:
                self.logger.debug("Failed to enum GPU Engine counters: %s", e)

            # 2. VRAM Counters (\GPU Adapter Memory(*)\Dedicated Usage)
            try:
                _, instances = win32pdh.EnumObjectItems(None, None, "GPU Adapter Memory", win32pdh.PERF_DETAIL_WIZARD)
                for instance in instances:
                    counter_path = f"\\GPU Adapter Memory({instance})\\Dedicated Usage"
                    try:
                        handle = win32pdh.AddCounter(self._gpu_query, counter_path)
                        self._gpu_vram_counters.append(handle)
                    except: continue
            except Exception as e:
                self.logger.debug("Failed to enum GPU VRAM counters: %s", e)

            # Initial collection to prime
            win32pdh.CollectQueryData(self._gpu_query)
            return True
        except Exception as e:
            self.logger.error("Failed to initialize GPU PDH query: %s", e)
            self._cleanup_gpu_query()
            return False

    def _cleanup_gpu_query(self) -> None:
        """Closes the PDH query handle."""
        if self._gpu_query:
            try:
                win32pdh.CloseQuery(self._gpu_query)
            except Exception:
                pass
            self._gpu_query = None
            self._gpu_util_counters = []
            self._gpu_vram_counters = []

    def _poll_gpu_hybrid(self) -> Tuple[float, Optional[float], Optional[float], Optional[float]]:
        """
        Collects GPU stats using a hybrid approach:
        - Utilization & VRAM via Universal PDH (all vendors)
        - Temperature via nvidia-smi (optional, NVIDIA only)
        Returns: (utilization_pct, vram_used_mib, vram_total_mib, temp_c)
        """
        if not self._gpu_query and not self._init_gpu_query():
            return 0.0, None, None, None
            
        util_pct = 0.0
        vram_used = 0.0
        temp_c = None
        
        try:
            win32pdh.CollectQueryData(self._gpu_query)
            
            # 1. Broad Utilization (Max among engines, usually represents 3D load)
            for handle in self._gpu_util_counters:
                try:
                    _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None:
                        util_pct = max(util_pct, val)
                except: continue

            # 2. Universal VRAM (Dedicated Usage in bytes, convert to MiB)
            # We SUM these because different adapters/instances represent total memory footprint
            for handle in self._gpu_vram_counters:
                try:
                    _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None:
                        # Value is in bytes, convert to MiB
                        vram_used += (val / (1024.0 * 1024.0))
                except: continue
                
        except Exception as e:
            self.logger.debug("GPU PDH polling error: %s", e)

        # 3. Vendor-specific Temperature (NVIDIA only for now)
        if self._nvidia_smi_path:
            try:
                output = subprocess.check_output(
                    [self._nvidia_smi_path, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                    encoding='utf-8', timeout=0.5
                )
                temp_c = float(output.strip().split('\n')[0])
            except: pass
            
        return util_pct, vram_used, None, temp_c

    def _poll_gpu_utilization(self) -> float:
        # Deprecated: usage now part of _poll_gpu_hybrid
        return 0.0

    @lru_cache(maxsize=4)
    def _get_cached_path(self, binary: str) -> Optional[str]:
        """Caches the location of system binaries."""
        return shutil.which(binary)

    def _poll_cpu_temperature(self) -> Optional[float]:
        """Polls CPU temperature via WMI (MSAcpi_ThermalZoneTemperature)."""
        if not win32com.client:
            return None
            
        try:
            if not self._wmi:
                # Initialize WMI with specific namespace and co-initialize for thread safety
                import pythoncom
                pythoncom.CoInitialize()
                self._wmi = win32com.client.GetObject("winmgmts:/root/wmi")
                
            # Perform query
            temps = self._wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for t in temps:
                # Value is in tenths of Kelvin
                return (t.CurrentTemperature / 10.0) - 273.15
        except Exception as e:
            self.logger.debug("CPU Temp polling error: %s", e)
            # Do not reset self._wmi to None immediately to avoid repetitive expensive reconnections
            # unless it's a critical comm error
            if "RPC server is unavailable" in str(e) or "0x800706ba" in str(e):
                self._wmi = None 
        return None

    def _poll_gpu_stats(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Polls GPU temperature and VRAM in a single batched subprocess call.
        Returns: (temp_c, vram_used_mib, vram_total_mib)
        """
        if not self._nvidia_smi_path:
            return None, None, None
            
        try:
            # Batch query: temperature, memory used, total memory
            output = subprocess.check_output(
                [
                    self._nvidia_smi_path, 
                    "--query-gpu=temperature.gpu,memory.used,memory.total", 
                    "--format=csv,noheader,nounits"
                ],
                encoding='utf-8',
                timeout=1.0
            )
            
            parts = [p.strip() for p in output.strip().split('\n')[0].split(',')]
            if len(parts) >= 3:
                return float(parts[0]), float(parts[1]), float(parts[2])
                
        except Exception as e:
            self.logger.debug("Batched GPU stats polling error: %s", e)
        return None, None, None

    def run(self) -> None:
        """Main monitoring loop."""
        self.logger.debug("StatsMonitorThread starting loop...")
        
        while self._is_running:
            try:
                stats = {}
                
                # 1. Network (Always enabled for core functionality)
                network_counters = psutil.net_io_counters(pernic=True)
                if network_counters:
                    stats['network'] = network_counters
                
                # 2. CPU / RAM (Optional)
                if self.config.get('monitor_cpu_enabled', False):
                    # non-blocking (percpu=False)
                    stats['cpu'] = psutil.cpu_percent(interval=None)
                    stats['cpu_temp'] = self._poll_cpu_temperature()
                    
                    # RAM is often grouped with CPU in simple monitors
                    mem = psutil.virtual_memory()
                    stats['ram_used'] = mem.used / (1024**3) # GB
                    stats['ram_total'] = mem.total / (1024**3) # GB
                
                # 3. GPU / VRAM (Optional)
                if self.config.get('monitor_gpu_enabled', False):
                    gpu_util, vram_used, _, gpu_temp = self._poll_gpu_hybrid()
                    
                    stats['gpu'] = gpu_util
                    stats['gpu_temp'] = gpu_temp
                    
                    if vram_used is not None:
                        stats['vram_used'] = vram_used / 1024.0 # MiB to GiB
                        # VRAM Total is hard to get universally via PDH without more জটিল enum
                        # We'll set it to None and let the UI handle it or fallback to a known value
                        stats['vram_total'] = None
                
                if stats:
                    self.stats_ready.emit(stats)
                    
                # Success - reset circuit breaker
                if self.consecutive_errors > 0:
                    self.consecutive_errors = 0
                    
            except Exception as e:
                self.consecutive_errors += 1
                self.logger.error("Error fetching stats (Attempt %d/10): %s", self.consecutive_errors, e)
                
                if self.consecutive_errors > 10:
                    self.logger.critical("Circuit breaker tripped. Stopping monitor thread.")
                    self.error_occurred.emit(f"Critical Hardware Monitor Failure: {e}")
                    self._is_running = False
                    break
            
            # Responsive sleep
            sleep_remaining = self.interval
            while sleep_remaining > 0 and self._is_running:
                sleep_slice = min(0.1, sleep_remaining)
                time.sleep(sleep_slice)
                sleep_remaining -= sleep_slice

        self._cleanup_gpu_query()

    def stop(self) -> None:
        """Gracefully stops the monitoring loop."""
        self._is_running = False
        self.wait(constants.timeouts.MONITOR_THREAD_STOP_WAIT_MS)
        self.logger.info("StatsMonitorThread stopped.")
