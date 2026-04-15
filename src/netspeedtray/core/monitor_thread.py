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
from typing import Dict, Any, Optional, List, NamedTuple, Tuple

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
from netspeedtray.utils.rdp_utils import is_rdp_session

logger = logging.getLogger("NetSpeedTray.StatsMonitorThread")


class GpuPollResult(NamedTuple):
    """Structured result from GPU polling, replacing opaque 4-tuple."""
    util: float = 0.0
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    temp: Optional[float] = None
    power: Optional[float] = None


class StatsMonitorThread(QThread):
    """
    Background thread that polls hardware statistics at a regular interval.
    Emits a unified dictionary of metrics for processing in the controller.
    """
    stats_ready = pyqtSignal(dict)  # Contains 'network', 'cpu', 'gpu' keys if enabled
    error_occurred = pyqtSignal(str)
    lhm_not_detected = pyqtSignal()  # Emitted once when temps/power enabled but no source found

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
        
        # WMI for CPU temperatures (ACPI fallback)
        self._wmi: Any = None
        # LibreHardwareMonitor / OpenHardwareMonitor WMI object.
        # None = not yet tried, False = tried and unavailable, object = connected.
        self._wmi_ohm: Any = None
        self._ohm_empty_ns_logged: set = set()  # Namespaces already warned about (log once)
        self._lhm_notice_emitted: bool = False  # One-time notification flag
        self._lhm_check_polls: int = 0  # Count polls before emitting notice
        self._nvidia_smi_path: Optional[str] = self._get_cached_path("nvidia-smi")

        # PDH Queries for GPU
        self._gpu_query: Optional[int] = None
        self._gpu_util_counters: List[int] = []
        self._gpu_vram_counters: List[int] = []

        # PDH Query for CPU thermal zones
        self._thermal_query: Optional[int] = None
        self._thermal_counters: List[int] = []
        self._thermal_hp_counters: List[int] = []

        # PDH Query for power (Intel RAPL via Energy Meter)
        self._power_query: Optional[int] = None
        self._power_pkg_counter: Optional[int] = None   # CPU package power (PKG)
        self._power_pp1_counter: Optional[int] = None    # Intel iGPU power (PP1)

        self.logger.debug("StatsMonitorThread initialized with interval %.2fs", self.interval)

    def set_interval(self, interval: float) -> None:
        """Dynamically updates the polling interval."""
        self.interval = max(0.1, interval)
        self.logger.debug("Monitoring interval updated to %.2fs", self.interval)

    def update_config(self, config: Dict[str, Any]) -> None:
        """Updates internal config copy and resets hardware queries if needed."""
        self.config = config
        # Reset queries so they re-initialize with the new config on next poll
        self._cleanup_gpu_query()
        self._cleanup_thermal_query()
        self._cleanup_power_query()
        self._wmi_ohm = None  # Re-probe OHM/LHM on next temp poll

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

    def _poll_gpu_hybrid(self, include_temp: bool = True, include_power: bool = False) -> GpuPollResult:
        """
        Collects GPU stats using a hybrid approach:
        - Utilization & VRAM via Universal PDH (all vendors)
        - Temperature via LHM/OHM WMI if available (all vendors), else nvidia-smi (NVIDIA only)
        - Power via LHM/OHM WMI (all vendors) → nvidia-smi (NVIDIA) → PDH RAPL PP1 (Intel iGPU)
        Returns: GpuPollResult named tuple
        """
        if not self._gpu_query and not self._init_gpu_query():
            return GpuPollResult()

        util_pct = 0.0
        vram_used = 0.0
        vram_total = None
        temp_c = None
        power_w = None

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
            for handle in self._gpu_vram_counters:
                try:
                    _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None:
                        vram_used += (val / (1024.0 * 1024.0))
                except: continue

        except Exception as e:
            self.logger.debug("GPU PDH polling error: %s", e)

        # 3. Temperature & Power — prefer LHM/OHM (all vendors) over nvidia-smi (NVIDIA only)
        need_smi_temp = include_temp
        need_smi_power = include_power

        if include_temp or include_power:
            self._init_ohm_wmi()
            if self._wmi_ohm:
                # 3a. LHM/OHM GPU temperature
                if include_temp:
                    try:
                        sensors = self._wmi_ohm.ExecQuery(
                            "SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='Temperature'"
                        )
                        for s in sensors:
                            identifier = str(getattr(s, 'Identifier', '')).lower()
                            if 'gpu' not in identifier:
                                continue
                            val = float(s.Value)
                            if 0.0 < val < 150.0:
                                temp_c = val
                                self.logger.debug("LHM/OHM GPU temp from sensor '%s': %.1f°C", getattr(s, 'Name', '?'), val)
                                break
                        if temp_c is None:
                            self.logger.debug("LHM/OHM: no valid GPU temperature sensor found")
                        else:
                            need_smi_temp = False
                    except Exception as e:
                        self.logger.debug("LHM/OHM GPU temp error: %s", e)
                        self._wmi_ohm = None

                # 3b. LHM/OHM GPU power (all vendors)
                if include_power and self._wmi_ohm:
                    try:
                        sensors = self._wmi_ohm.ExecQuery(
                            "SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='Power'"
                        )
                        for s in sensors:
                            identifier = str(getattr(s, 'Identifier', '')).lower()
                            if 'gpu' not in identifier:
                                continue
                            val = float(s.Value)
                            if 0.0 < val < 1000.0:
                                power_w = val
                                self.logger.debug("LHM/OHM GPU power from sensor '%s': %.1fW", getattr(s, 'Name', '?'), val)
                                break
                        if power_w is not None:
                            need_smi_power = False
                    except Exception as e:
                        self.logger.debug("LHM/OHM GPU power error: %s", e)

        # 4. nvidia-smi fallback for temp/power (vram_total comes as bonus)
        if self._nvidia_smi_path and (need_smi_temp or need_smi_power):
            try:
                query_fields = "temperature.gpu,memory.total,power.draw"
                output = subprocess.check_output(
                    [self._nvidia_smi_path, f"--query-gpu={query_fields}", "--format=csv,noheader,nounits"],
                    encoding='utf-8', timeout=0.5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                parts = output.strip().split('\n')[0].split(',')
                if need_smi_temp and len(parts) > 0:
                    try: temp_c = float(parts[0].strip())
                    except: pass
                if len(parts) > 1:
                    try: vram_total = float(parts[1].strip())  # MiB
                    except: pass
                if need_smi_power and len(parts) > 2:
                    try:
                        pw = float(parts[2].strip())
                        if 0.0 < pw < 1000.0:
                            power_w = pw
                    except: pass
            except: pass

        # 5. RAPL PP1 fallback for Intel iGPU power (if no LHM/nvidia-smi power)
        if include_power and power_w is None and self._power_pp1_counter is not None:
            try:
                self._init_power_query()
                if self._power_query:
                    win32pdh.CollectQueryData(self._power_query)
                    _, val = win32pdh.GetFormattedCounterValue(self._power_pp1_counter, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None and val > 0:
                        power_w = val / 1000.0  # mW to W
            except: pass

        return GpuPollResult(util_pct, vram_used, vram_total, temp_c, power_w)

    @lru_cache(maxsize=4)
    def _get_cached_path(self, binary: str) -> Optional[str]:
        """Caches the location of system binaries."""
        path = shutil.which(binary)
        if path:
            return path

        # Common Windows install path for NVIDIA's NVSMI tooling when it's not on PATH.
        if binary.lower() in ("nvidia-smi", "nvidia-smi.exe"):
            try:
                import os
                for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
                    root = os.environ.get(env)
                    if not root:
                        continue
                    candidate = os.path.join(root, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")
                    if os.path.isfile(candidate):
                        return candidate
            except Exception:
                pass

        return None

    def _init_thermal_query(self) -> bool:
        """Initializes Windows PDH query for thermal zone temperatures.

        Adds both 'High Precision Temperature' (tenths of Kelvin, or direct
        Celsius on some OEM systems) and the standard 'Temperature' counter
        as a fallback.
        """
        if not win32pdh:
            return False
        try:
            if self._thermal_query:
                return True

            self._thermal_query = win32pdh.OpenQuery()
            self._thermal_counters = []
            self._thermal_hp_counters = []

            try:
                _, instances = win32pdh.EnumObjectItems(None, None, "Thermal Zone Information", win32pdh.PERF_DETAIL_WIZARD)
                for instance in instances:
                    # High Precision Temperature (preferred — higher resolution)
                    try:
                        hp_path = f"\\Thermal Zone Information({instance})\\High Precision Temperature"
                        handle = win32pdh.AddCounter(self._thermal_query, hp_path)
                        self._thermal_hp_counters.append(handle)
                    except: pass
                    # Standard Temperature (fallback)
                    try:
                        counter_path = f"\\Thermal Zone Information({instance})\\Temperature"
                        handle = win32pdh.AddCounter(self._thermal_query, counter_path)
                        self._thermal_counters.append(handle)
                    except: continue
            except Exception as e:
                self.logger.debug("Failed to enum Thermal Zone counters: %s", e)

            # Initial collection to prime counters
            win32pdh.CollectQueryData(self._thermal_query)
            return bool(self._thermal_counters) or bool(self._thermal_hp_counters)
        except Exception as e:
            self.logger.debug("Failed to init thermal PDH query: %s", e)
            self._cleanup_thermal_query()
            return False

    def _cleanup_thermal_query(self) -> None:
        """Closes the thermal PDH query handle."""
        if self._thermal_query:
            try:
                win32pdh.CloseQuery(self._thermal_query)
            except Exception:
                pass
            self._thermal_query = None
            self._thermal_counters = []
            self._thermal_hp_counters = []

    def _init_power_query(self) -> bool:
        """Initializes Windows PDH query for Intel RAPL power counters (Energy Meter).

        Provides CPU package power (PKG) and Intel iGPU power (PP1) in milliwatts.
        Available on Intel systems without admin rights.
        """
        if not win32pdh:
            return False
        try:
            if self._power_query:
                return True

            self._power_query = win32pdh.OpenQuery()
            self._power_pkg_counter = None
            self._power_pp1_counter = None

            try:
                _, instances = win32pdh.EnumObjectItems(None, None, "Energy Meter", win32pdh.PERF_DETAIL_WIZARD)
                for instance in instances:
                    instance_lower = instance.lower()
                    try:
                        path = f"\\Energy Meter({instance})\\Power"
                        handle = win32pdh.AddCounter(self._power_query, path)
                        if 'pkg' in instance_lower and self._power_pkg_counter is None:
                            self._power_pkg_counter = handle
                        elif 'pp1' in instance_lower and self._power_pp1_counter is None:
                            self._power_pp1_counter = handle
                    except: continue
            except Exception as e:
                self.logger.debug("Failed to enum Energy Meter counters: %s", e)

            if self._power_pkg_counter is None and self._power_pp1_counter is None:
                self._cleanup_power_query()
                return False

            # Initial collection to prime
            win32pdh.CollectQueryData(self._power_query)
            return True
        except Exception as e:
            self.logger.debug("Failed to init power PDH query: %s", e)
            self._cleanup_power_query()
            return False

    def _cleanup_power_query(self) -> None:
        """Closes the power PDH query handle."""
        if self._power_query:
            try:
                win32pdh.CloseQuery(self._power_query)
            except Exception:
                pass
            self._power_query = None
            self._power_pkg_counter = None
            self._power_pp1_counter = None

    def _poll_cpu_power(self) -> Optional[float]:
        """
        Polls CPU power draw in watts, trying sources in order:
          1. PDH RAPL PKG (Intel — milliwatts, non-admin)
          2. LHM/OHM WMI (all vendors, requires admin)
        """
        # 1. PDH RAPL PKG
        if win32pdh:
            if not self._power_query:
                self._init_power_query()
            if self._power_query and self._power_pkg_counter is not None:
                try:
                    win32pdh.CollectQueryData(self._power_query)
                    _, val = win32pdh.GetFormattedCounterValue(self._power_pkg_counter, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None and val > 0:
                        return val / 1000.0  # mW to W
                except Exception as e:
                    self.logger.debug("RAPL PKG power polling error: %s", e)

        # 2. LHM/OHM WMI fallback
        self._init_ohm_wmi()
        if self._wmi_ohm:
            try:
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='Power'"
                )
                for s in sensors:
                    identifier = str(getattr(s, 'Identifier', '')).lower()
                    if 'cpu' not in identifier:
                        continue
                    # Prefer 'package' or 'pkg' sensor over individual cores
                    name = str(getattr(s, 'Name', '')).lower()
                    if 'package' in name or 'pkg' in name or 'total' in name:
                        val = float(s.Value)
                        if 0.0 < val < 1000.0:
                            return val
                # If no package sensor, take first cpu power sensor
                for s in sensors:
                    identifier = str(getattr(s, 'Identifier', '')).lower()
                    if 'cpu' not in identifier:
                        continue
                    val = float(s.Value)
                    if 0.0 < val < 1000.0:
                        return val
            except Exception as e:
                self.logger.debug("LHM/OHM CPU power error: %s", e)

        return None

    def _init_ohm_wmi(self) -> None:
        """
        Probes for a running LibreHardwareMonitor or OpenHardwareMonitor instance
        and caches the WMI connection in self._wmi_ohm.
        _wmi_ohm == None  → not yet probed (or previous probe failed — will retry)
        _wmi_ohm == <obj> → connected and ready
        Failures are NOT permanently cached so the probe retries each poll cycle,
        allowing the app to pick up LHM/OHM if it starts after NetSpeedTray.
        """
        if self._wmi_ohm is not None or not win32com.client:
            return
        for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
            try:
                import pythoncom
                pythoncom.CoInitialize()
                obj = win32com.client.GetObject(f"winmgmts:{ns}")
                results = obj.ExecQuery("SELECT Name FROM Sensor WHERE SensorType='Temperature'")
                # Verify the namespace actually has sensor data.
                # LHM running without admin rights registers the namespace but exposes 0 sensors.
                count = sum(1 for _ in results)
                if count == 0:
                    if ns not in self._ohm_empty_ns_logged:
                        self._ohm_empty_ns_logged.add(ns)
                        self.logger.info(
                            "Hardware monitor: %s namespace exists but has 0 sensors. "
                            "Ensure LibreHardwareMonitor is running as Administrator.", ns
                        )
                    continue
                self._wmi_ohm = obj
                self.logger.debug("Hardware monitor: connected to %s (%d temp sensors)", ns, count)
                return
            except Exception as e:
                self.logger.debug("Hardware monitor: %s probe failed: %s", ns, e)
        # Leave _wmi_ohm as None so we retry next poll (LHM may not be running yet)

    def _poll_cpu_temperature(self) -> Optional[float]:
        """
        Polls CPU temperature, trying sources in order:
          1. PDH Thermal Zone Information  (standard ACPI)
          2. LibreHardwareMonitor / OpenHardwareMonitor WMI  (if running)
          3. WMI MSAcpi_ThermalZoneTemperature  (legacy ACPI fallback)

        Note: Modern Intel/AMD CPUs often require a kernel-driver tool
        (LibreHardwareMonitor, HWiNFO64, etc.) — see the settings note.
        """
        # 1. PDH Thermal Zone Information
        if win32pdh:
            if not self._thermal_query:
                self._init_thermal_query()
            if self._thermal_query and (self._thermal_hp_counters or self._thermal_counters):
                try:
                    win32pdh.CollectQueryData(self._thermal_query)
                    readings = []

                    # 1a. High Precision Temperature (preferred)
                    for handle in self._thermal_hp_counters:
                        try:
                            _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                            if val is not None:
                                # Standard: tenths of Kelvin → Celsius
                                celsius = (val / 10.0) - 273.15
                                if 0.0 < celsius < 150.0:
                                    readings.append(celsius)
                                # Some OEMs (HP, Dell) report direct Celsius
                                elif 15.0 < val < 110.0:
                                    readings.append(val)
                        except: continue

                    # 1b. Standard Temperature counter (fallback)
                    if not readings:
                        for handle in self._thermal_counters:
                            try:
                                _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                                if val is not None:
                                    celsius = (val / 10.0) - 273.15
                                    if 0.0 < celsius < 150.0:
                                        readings.append(celsius)
                            except: continue

                    if readings:
                        return max(readings)
                except Exception as e:
                    self.logger.debug("Thermal PDH polling error: %s", e)

        # 2. LibreHardwareMonitor / OpenHardwareMonitor
        self._init_ohm_wmi()
        if self._wmi_ohm:
            try:
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name='CPU Package'"
                )
                for s in sensors:
                    val = float(s.Value)
                    if 0.0 < val < 150.0:
                        return val
                # Some boards label it differently — return hottest CPU-named sensor
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature'"
                )
                readings = []
                for s in sensors:
                    name = str(s.Name).upper()
                    if "CPU" in name or "CORE" in name or "PACKAGE" in name:
                        val = float(s.Value)
                        if 0.0 < val < 150.0:
                            readings.append(val)
                if readings:
                    return max(readings)
            except Exception as e:
                self.logger.debug("OHM/LHM CPU temp error: %s", e)
                self._wmi_ohm = None

        # 3. WMI MSAcpi_ThermalZoneTemperature (legacy ACPI fallback)
        if not win32com.client:
            return None
        try:
            if not self._wmi:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    self._wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
                except Exception:
                    self._wmi = win32com.client.GetObject("winmgmts:root\\wmi")
            temps = self._wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for t in temps:
                raw = t.CurrentTemperature
                # Standard ACPI: tenths of Kelvin (valid range ~2932–3932 for 20–120°C)
                celsius = (raw / 10.0) - 273.15
                if 0.0 < celsius < 150.0:
                    return celsius
                # Some OEMs (HP, Dell, Lenovo) return direct Celsius instead
                if 15.0 < raw < 110.0:
                    self.logger.debug("ACPI temp raw=%s interpreted as direct Celsius", raw)
                    return float(raw)
        except Exception as e:
            self.logger.debug("CPU Temp WMI fallback error: %s", e)
            if "RPC server is unavailable" in str(e) or "0x800706ba" in str(e):
                self._wmi = None
        return None

    def run(self) -> None:
        """Main monitoring loop."""
        self.logger.debug("StatsMonitorThread starting loop...")

        # Check once at thread startup, not per-iteration — is_rdp_session() is a
        # syscall and the session type does not change while the thread is running.
        # If the user connects via RDP after the app has started they must restart
        # the app for GPU monitoring to be suppressed.
        _in_rdp = is_rdp_session()
        if _in_rdp:
            self.logger.info("RDP session detected — GPU monitoring will be skipped.")

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
                    if self.config.get('show_hardware_temps', False):
                        stats['cpu_temp'] = self._poll_cpu_temperature()
                    if self.config.get('show_hardware_power', False):
                        stats['cpu_power'] = self._poll_cpu_power()

                    # RAM is often grouped with CPU in simple monitors
                    mem = psutil.virtual_memory()
                    stats['ram_used'] = mem.used / (1024**3) # GB
                    stats['ram_total'] = mem.total / (1024**3) # GB

                # 3. GPU / VRAM (Optional — skipped entirely in RDP sessions)
                if self.config.get('monitor_gpu_enabled', False) and not _in_rdp:
                    try:
                        include_temp = bool(self.config.get('show_hardware_temps', False))
                        include_power = bool(self.config.get('show_hardware_power', False))
                        gpu = self._poll_gpu_hybrid(include_temp=include_temp, include_power=include_power)

                        stats['gpu'] = gpu.util
                        if include_temp:
                            stats['gpu_temp'] = gpu.temp
                        if include_power:
                            stats['gpu_power'] = gpu.power

                        if gpu.vram_used is not None:
                            stats['vram_used'] = gpu.vram_used / 1024.0  # MiB to GiB
                        if gpu.vram_total is not None:
                            stats['vram_total'] = gpu.vram_total / 1024.0  # MiB to GiB
                    except Exception as gpu_err:
                        self.logger.warning("GPU polling error (skipped, not counted against circuit breaker): %s", gpu_err)
                
                if stats:
                    self.stats_ready.emit(stats)

                # One-time LHM notice: if temps/power enabled but no readings after a few polls
                if not self._lhm_notice_emitted:
                    wants_temps = self.config.get('show_hardware_temps', False)
                    wants_power = self.config.get('show_hardware_power', False)
                    if wants_temps or wants_power:
                        self._lhm_check_polls += 1
                        # Wait 5 polls (~5s) to give LHM time to be detected
                        if self._lhm_check_polls >= 5:
                            has_any_reading = any(
                                stats.get(k) is not None
                                for k in ('cpu_temp', 'gpu_temp', 'cpu_power', 'gpu_power')
                            )
                            if not has_any_reading:
                                self._lhm_notice_emitted = True
                                self.lhm_not_detected.emit()

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
        self._cleanup_thermal_query()
        self._cleanup_power_query()
        self._cleanup_ohm_wmi()
        self._cleanup_com()

    def _cleanup_ohm_wmi(self) -> None:
        """Releases the cached WMI connection to LHM/OHM."""
        if self._wmi_ohm is not None and self._wmi_ohm is not False:
            try:
                del self._wmi_ohm
            except Exception:
                pass
        self._wmi_ohm = None

    def _cleanup_com(self) -> None:
        """Releases COM apartment initialised for WMI access."""
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def stop(self) -> None:
        """Gracefully stops the monitoring loop."""
        self._is_running = False
        self.wait(constants.timeouts.MONITOR_THREAD_STOP_WAIT_MS)
        self.logger.info("StatsMonitorThread stopped.")
