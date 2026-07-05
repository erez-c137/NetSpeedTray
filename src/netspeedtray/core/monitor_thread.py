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
    import win32com
    # A frozen install under read-only Program Files makes win32com's default gen_py cache
    # dir (inside the bundle, `_internal\win32com\gen_py`) unwritable. Importing win32com.client
    # then crashes while (re)building its COM cache there -- and it raises FileNotFoundError,
    # NOT ImportError, so the old guard let it through and the app failed to start (#210).
    # Point the generated-code cache at a writable per-user temp dir BEFORE importing
    # win32com.client. (We only ever use late-bound GetObject("winmgmts:...") for WMI, which
    # doesn't need this cache at all; the redirect just keeps the import from crashing.)
    import os as _os, tempfile as _tempfile
    try:
        _gen_dir = _os.path.join(_tempfile.gettempdir(), "netspeedtray_win32com_gen")
        _os.makedirs(_gen_dir, exist_ok=True)
        win32com.__gen_path__ = _gen_dir
    except OSError:
        pass
    import win32com.client
except (ImportError, OSError):
    # win32com is a bundled dependency, so the package import above effectively always
    # succeeds; on the off chance win32com.client still can't load, degrade gracefully
    # (WMI hardware monitoring disabled) instead of crashing the app.
    win32com.client = None

import subprocess
import shutil
from functools import lru_cache

from netspeedtray import constants
from netspeedtray.utils.rdp_utils import is_rdp_session
from netspeedtray.utils.network_utils import get_connected_network_identity

logger = logging.getLogger("NetSpeedTray.StatsMonitorThread")

# Network identity (Wi-Fi band / SSID) changes rarely, so poll it on a slow sub-cadence off the
# per-second network readout - never on the GUI thread. See releases/v2.1/KICKOFF.md §2/§3.
_IDENTITY_POLL_INTERVAL_SEC: float = 5.0


class GpuPollResult(NamedTuple):
    """Structured result from GPU polling, replacing opaque 4-tuple."""
    util: float = 0.0
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    temp: Optional[float] = None
    power: Optional[float] = None
    present: bool = False   # True only when GPU Engine util counters were actually found


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
        # Runtime override: the Monitor window flips this on while it's open so its Overview/Hardware
        # screens show CPU/GPU/RAM/VRAM (+ temps) even when the TASKBAR WIDGET has hardware monitoring
        # off. OR'd with the config flags in run(); reverts to the widget's config when the Monitor
        # closes. config is shared by reference, so the Monitor sets this attribute directly.
        self._force_hardware_collection = False
        self.consecutive_errors = 0
        # Recoverable circuit breaker: after N consecutive errors we notify ONCE and back off
        # exponentially (capped) instead of permanently killing the thread, so a transient
        # fault (driver reload, sleep/resume, WMI hiccup) self-heals when it clears.
        self._error_notified = False
        self._ERROR_NOTIFY_THRESHOLD = 10
        self._MAX_BACKOFF_SEC = 30.0
        self.logger = logger
        
        # WMI for CPU temperatures (ACPI fallback)
        self._wmi: Any = None
        # LibreHardwareMonitor / OpenHardwareMonitor WMI object.
        # None = not yet tried, False = tried and unavailable, object = connected.
        self._wmi_ohm: Any = None
        self._ohm_guidance_logged: bool = False  # One-time "no sensor source" guidance, logged only when NO namespace connects
        self._lhm_notice_emitted: bool = False  # One-time notification flag
        self._lhm_check_polls: int = 0  # Count polls before emitting notice
        self._last_identity_poll: float = 0.0  # monotonic ts of the last network-identity sub-poll (0 = poll immediately)
        self._nvidia_smi_path: Optional[str] = self._get_cached_path("nvidia-smi")
        # nvidia-smi is a synchronous subprocess (up to NVIDIA_SMI_TIMEOUT_SEC). Temps/power
        # change slowly, so poll it on a slow sub-cadence and cache between calls - keeps the
        # per-second network readout off the subprocess critical path.
        self._NVIDIA_POLL_INTERVAL_SEC = 5.0
        self._nvidia_last_poll: float = 0.0
        self._nvidia_cache_temp: Optional[float] = None
        self._nvidia_cache_power: Optional[float] = None
        self._nvidia_cache_vram_total: Optional[float] = None

        # PDH Queries for GPU
        self._gpu_query: Optional[int] = None
        self._gpu_util_counters: List[int] = []
        self._gpu_vram_counters: List[int] = []
        # Set by update_config (GUI thread); the PDH handles are owned by run()'s thread, so the actual
        # cleanup/re-init is deferred to the loop (see update_config / run).
        self._hw_queries_dirty: bool = False

        # PDH Query for CPU thermal zones
        self._thermal_query: Optional[int] = None
        self._thermal_counters: List[int] = []
        self._thermal_hp_counters: List[int] = []

        # PDH Query for power (Intel RAPL via Energy Meter)
        self._power_query: Optional[int] = None
        self._power_pkg_counter: Optional[int] = None   # CPU package power (PKG)
        self._power_pp1_counter: Optional[int] = None    # Intel iGPU power (PP1)
        self._power_psys_counter: Optional[int] = None   # Intel RAPL PSYS / platform power (some CPUs)
        # True whole-system power only comes from the battery (laptops on battery) - DischargeRate via
        # WMI root\wmi. None=untried, False=unavailable, object=connected. Cached on a slow sub-cadence.
        self._wmi_battery: Any = None
        self._battery_cache: tuple = (0.0, None)         # (monotonic_ts, watts_or_None)
        self._battery_cadence_sec: float = 8.0

        self.logger.info("StatsMonitorThread initialized with interval %.2fs", self.interval)

    def set_interval(self, interval: float) -> None:
        """Dynamically updates the polling interval."""
        previous = self.interval
        self.interval = max(0.1, interval)
        if previous != self.interval:
            self.logger.info("Monitoring interval changed: %.2fs -> %.2fs", previous, self.interval)

    def update_config(self, config: Dict[str, Any]) -> None:
        """Update the config copy and FLAG the hardware queries for re-init on the worker thread.

        Called from the GUI thread (ConfigController.apply_all_settings), but the PDH query handles are
        created and CollectQueryData'd on this thread's run() loop. Closing them from the GUI thread
        mid-poll could free a handle the loop is actively using → a swallowed PDH error and a spurious
        circuit-breaker increment / "monitor degraded" notice. So just flag it; run() applies the
        cleanup + LHM re-probe on its own thread at the top of the next tick."""
        self.config = config
        self._hw_queries_dirty = True

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
        vram_used = None   # None = no VRAM counter available -> N/A (not a misleading "0.0 GB used")
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

            # 2. Universal VRAM (Dedicated Usage in bytes, convert to MiB). Only report a real
            # number if at least one counter contributed - otherwise leave None (N/A).
            _vram_acc = 0.0
            _had_vram = False
            for handle in self._gpu_vram_counters:
                try:
                    _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None:
                        _vram_acc += (val / (1024.0 * 1024.0))
                        _had_vram = True
                except: continue
            if _had_vram:
                vram_used = _vram_acc

        except Exception as e:
            self.logger.debug("GPU PDH polling error: %s", e)

        # 3. Temperature & Power - prefer LHM/OHM (all vendors) over nvidia-smi (NVIDIA only)
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

        # 4. nvidia-smi fallback for temp/power (vram_total comes as bonus). The subprocess
        #    runs on a slow sub-cadence; values are cached and reused on intervening polls so
        #    a synchronous (up to 1.5s) nvidia-smi call never stalls the network readout.
        if self._nvidia_smi_path and (need_smi_temp or need_smi_power):
            now_mono = time.monotonic()
            if (now_mono - self._nvidia_last_poll) >= self._NVIDIA_POLL_INTERVAL_SEC:
                # Stamp first: a hung/failed call then waits the full interval before retry,
                # instead of re-running (and re-stalling) every poll.
                self._nvidia_last_poll = now_mono
                try:
                    query_fields = "temperature.gpu,memory.total,power.draw"
                    output = subprocess.check_output(
                        [self._nvidia_smi_path, f"--query-gpu={query_fields}", "--format=csv,noheader,nounits"],
                        encoding='utf-8', timeout=constants.timeouts.NVIDIA_SMI_TIMEOUT_SEC,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    parts = output.strip().split('\n')[0].split(',')
                    if len(parts) > 0:
                        try:
                            _t = float(parts[0].strip())
                            if 0.0 < _t < 150.0:  # sanity-range like every other temp path
                                self._nvidia_cache_temp = _t
                        except: pass
                    if len(parts) > 1:
                        try: self._nvidia_cache_vram_total = float(parts[1].strip())  # MiB
                        except: pass
                    if len(parts) > 2:
                        try:
                            pw = float(parts[2].strip())
                            if 0.0 < pw < 1000.0:
                                self._nvidia_cache_power = pw
                        except: pass
                except Exception as e:
                    self.logger.debug("nvidia-smi temp/power query failed: %s", e)
                    # Sensor likely dropped out - invalidate the cache so readings fall back to
                    # N/A instead of freezing the last good value forever (the N/A design intent).
                    self._nvidia_cache_temp = None
                    self._nvidia_cache_power = None
                    self._nvidia_cache_vram_total = None

            # Apply the (possibly just-refreshed) cached values to whatever still needs a source.
            if need_smi_temp and temp_c is None and self._nvidia_cache_temp is not None:
                temp_c = self._nvidia_cache_temp
            if need_smi_power and power_w is None and self._nvidia_cache_power is not None:
                power_w = self._nvidia_cache_power
            if vram_total is None and self._nvidia_cache_vram_total is not None:
                vram_total = self._nvidia_cache_vram_total

        # 5. RAPL PP1 fallback for Intel iGPU power (if no LHM/nvidia-smi power).
        # Init the PDH query FIRST (it's idempotent and sets _power_pp1_counter on first call) -
        # the old guard checked the counter before anything could ever set it, so this whole
        # fallback was dead code.
        if include_power and power_w is None:
            try:
                self._init_power_query()
                if self._power_query and self._power_pp1_counter is not None:
                    win32pdh.CollectQueryData(self._power_query)
                    _, val = win32pdh.GetFormattedCounterValue(self._power_pp1_counter, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None and val > 0:
                        power_w = val / 1000.0  # mW to W
            except: pass

        # Clamp utilization to [0, 100] - PDH GPU-Engine counters can momentarily read >100%.
        util_pct = max(0.0, min(100.0, util_pct))
        return GpuPollResult(util_pct, vram_used, vram_total, temp_c, power_w,
                             present=bool(self._gpu_util_counters))

    @lru_cache(maxsize=4)
    def _get_cached_path(self, binary: str) -> Optional[str]:
        """Resolve a system binary from TRUSTED locations only - never the current directory.

        Security: shutil.which() on Windows searches the CURRENT DIRECTORY first, and the app chdir's
        into its own folder, which is user-writable for the portable ZIP (Downloads / USB). A planted
        ``nvidia-smi.exe`` there would otherwise be launched hidden (CREATE_NO_WINDOW) on the GPU
        sub-cadence - code execution. So check the known absolute install paths first, and the PATH
        fallback drops the current directory / relative entries and accepts only an absolute result.
        """
        import os
        # 1. Known absolute install locations (trusted), checked FIRST.
        if "nvidia-smi" in binary.lower():
            for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
                root = os.environ.get(env)
                if root:
                    candidate = os.path.join(root, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")
                    if os.path.isfile(candidate):
                        return candidate
            sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
            if os.path.isfile(sys32):
                return sys32

        # 2. Hardened PATH fallback - strip '.'/relative entries so the CWD is never searched.
        try:
            safe = os.pathsep.join(
                d for d in os.environ.get("PATH", "").split(os.pathsep)
                if d and d != "." and os.path.isabs(d))
            resolved = shutil.which(binary, path=safe) if safe else None
            if resolved and os.path.isabs(resolved):
                return resolved
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
                    # High Precision Temperature (preferred - higher resolution)
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
                        elif ('psys' in instance_lower or 'platform' in instance_lower) \
                                and self._power_psys_counter is None:
                            self._power_psys_counter = handle   # broader platform power (when exposed)
                    except: continue
            except Exception as e:
                self.logger.debug("Failed to enum Energy Meter counters: %s", e)

            if self._power_pkg_counter is None and self._power_pp1_counter is None \
                    and self._power_psys_counter is None:
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
            self._power_psys_counter = None

    def _poll_system_power(self) -> Optional[float]:
        """True-ish whole-system power, when the platform exposes it: Intel RAPL PSYS/platform domain
        (some CPUs) - broader than CPU+GPU; else the battery DischargeRate (the only real whole-system
        draw, on a laptop running on battery). Returns watts, or None when neither is available."""
        # 1. RAPL PSYS / platform (PDH Energy Meter, milliwatts).
        if self._power_query is not None and self._power_psys_counter is not None:
            try:
                win32pdh.CollectQueryData(self._power_query)
                _, val = win32pdh.GetFormattedCounterValue(self._power_psys_counter, win32pdh.PDH_FMT_DOUBLE)
                if val and val > 0:
                    return float(val) / 1000.0
            except Exception:
                pass
        # 2. Battery discharge (laptops on battery) - cached on a slow sub-cadence (WMI is heavy).
        return self._poll_battery_power()

    def _poll_battery_power(self) -> Optional[float]:
        now = time.monotonic()
        last_ts, last_val = self._battery_cache
        if (now - last_ts) < self._battery_cadence_sec:
            return last_val
        watts = None
        try:
            if self._wmi_battery is None:
                import wmi  # already a dependency (LHM temps)
                self._wmi_battery = wmi.WMI(namespace="root\\wmi")
            if self._wmi_battery:
                for b in self._wmi_battery.BatteryStatus():
                    # DischargeRate (mW) is non-zero only while actually discharging on battery.
                    rate = getattr(b, "DischargeRate", 0) or 0
                    if getattr(b, "Discharging", False) and rate > 0:
                        watts = float(rate) / 1000.0
                        break
        except Exception:
            self._wmi_battery = False   # no battery / WMI class - stop retrying
        self._battery_cache = (now, watts)
        return watts

    def _poll_cpu_power(self) -> Optional[float]:
        """
        Polls CPU power draw in watts, trying sources in order:
          1. PDH RAPL PKG (Intel - milliwatts, non-admin)
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
        _wmi_ohm == None  → not yet probed (or previous probe failed - will retry)
        _wmi_ohm == <obj> → connected and ready
        Failures are NOT permanently cached so the probe retries each poll cycle,
        allowing the app to pick up LHM/OHM if it starts after NetSpeedTray.
        """
        if self._wmi_ohm is not None or not win32com.client:
            return
        # Probe every namespace BEFORE deciding on any user-facing guidance: a working
        # fallback (e.g. a live root\OpenHardwareMonitor) must not trigger a misleading
        # "install LHM v0.9.4" note just because the LHM namespace happens to be absent
        # (issue #134 / CMTriX - his sensors came from a live OHM publisher). Per-namespace
        # outcomes stay at DEBUG; the single actionable line is logged once, after the loop,
        # only if NOTHING connected.
        saw_empty_namespace = False
        probe_errors: List[str] = []
        for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
            try:
                obj = win32com.client.GetObject(f"winmgmts:{ns}")
                # Validate the namespace exposes ANY sensor, not just Temperature:
                # a Power/Load-only source must not be rejected (issue #130). LHM
                # running without admin rights registers the namespace but exposes 0 sensors.
                results = obj.ExecQuery("SELECT SensorType FROM Sensor")
                count = sum(1 for _ in results)
                if count == 0:
                    saw_empty_namespace = True
                    self.logger.debug("Hardware monitor: %s exists but exposes 0 sensors.", ns)
                    continue
                self._wmi_ohm = obj
                self.logger.info("Hardware monitor: connected to %s (%d sensors).", ns, count)
                return
            except Exception as e:
                # A missing namespace (0x8004100e WBEM_E_INVALID_NAMESPACE) is normal when
                # that particular tool isn't the one running - keep it quiet here.
                tool = ns.rsplit("\\", 1)[-1]  # LibreHardwareMonitor / OpenHardwareMonitor
                probe_errors.append(f"{tool}: {e}")
                self.logger.debug("Hardware monitor: %s probe failed: %s", ns, e)
        # Nothing connected across all namespaces. Surface ONE actionable line, once.
        if not self._ohm_guidance_logged:
            self._ohm_guidance_logged = True
            if saw_empty_namespace:
                # Genuine "running but not elevated" case: a namespace exists, 0 sensors.
                self.logger.info(
                    "Hardware monitor: a sensor namespace exists but exposes 0 sensors. "
                    "Run LibreHardwareMonitor as Administrator to expose CPU/GPU temps and power."
                )
            else:
                # No namespace at all: tool not running, or LHM v0.9.5+ (which removed WMI).
                self.logger.info(
                    "Hardware monitor: no CPU/GPU sensor source found (%s). Start "
                    "LibreHardwareMonitor if it is not running. Note: LibreHardwareMonitor "
                    "v0.9.5+ removed its WMI provider, so temps and power need LHM v0.9.4 "
                    "(the last WMI-capable release).", "; ".join(probe_errors)
                )
        # Leave _wmi_ohm as None so we retry next poll (LHM may not be running yet)

    def _poll_cpu_temperature(self) -> Optional[float]:
        """
        Polls CPU temperature, trying sources in order:
          1. PDH Thermal Zone Information  (standard ACPI)
          2. LibreHardwareMonitor / OpenHardwareMonitor WMI  (if running)
          3. WMI MSAcpi_ThermalZoneTemperature  (legacy ACPI fallback)

        Note: Modern Intel/AMD CPUs often require a kernel-driver tool
        (LibreHardwareMonitor, HWiNFO64, etc.) - see the settings note.
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
                # Some boards label it differently (AMD Ryzen exposes "Core (Tctl/Tdie)",
                # not "CPU Package"). Match on the LHM Identifier (/amdcpu/ or /intelcpu/),
                # which reliably scopes to the CPU regardless of the display name, and
                # broaden the name keywords to cover Ryzen's Tctl/Tdie/Tccd labels. (#148)
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value, Name, Identifier FROM Sensor WHERE SensorType='Temperature'"
                )
                readings = []
                for s in sensors:
                    name = str(getattr(s, 'Name', '')).upper()
                    ident = str(getattr(s, 'Identifier', '')).lower()
                    is_cpu = (
                        "/amdcpu/" in ident or "/intelcpu/" in ident
                        or any(k in name for k in ("CPU", "CORE", "PACKAGE", "TCTL", "TDIE", "TCCD"))
                    )
                    if not is_cpu:
                        continue
                    try:
                        val = float(s.Value)
                    except (TypeError, ValueError):
                        continue
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
                try:
                    self._wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
                except Exception:
                    self._wmi = win32com.client.GetObject("winmgmts:root\\wmi")
            temps = self._wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for t in temps:
                raw = t.CurrentTemperature
                # Standard ACPI: tenths of Kelvin (valid range ~2932-3932 for 20-120°C)
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
        self.logger.info("StatsMonitorThread starting loop.")

        # Check once at thread startup, not per-iteration - is_rdp_session() is a
        # syscall and the session type does not change while the thread is running.
        # If the user connects via RDP after the app has started they must restart
        # the app for GPU monitoring to be suppressed.
        _in_rdp = is_rdp_session()
        if _in_rdp:
            self.logger.info("RDP session detected - GPU monitoring will be skipped.")

        # Initialise the COM apartment ONCE for this thread (H4). Was done per-poll inside
        # the WMI helpers, leaking a COM ref every poll while no LHM source was connected.
        self._init_com()

        # Prime psutil.cpu_percent so the FIRST real reading is a true delta, not the 0.0 it
        # returns on its first-ever call (which otherwise showed/recorded a bogus 0% CPU).
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        while self._is_running:
            try:
                # Apply any config-driven hardware-query reset HERE, on the owning thread (set by
                # update_config from the GUI thread) - never close a PDH handle out from under this loop.
                if self._hw_queries_dirty:
                    self._hw_queries_dirty = False
                    self._cleanup_gpu_query()
                    self._cleanup_thermal_query()
                    self._cleanup_power_query()
                    self._wmi_ohm = None   # re-probe LHM/OHM on the next temp poll

                stats = {}

                # 1. Network (Always enabled for core functionality)
                network_counters = psutil.net_io_counters(pernic=True)
                if network_counters:
                    stats['network'] = network_counters

                # 1b. Network identity (Wi-Fi band / SSID) - only when the widget wants it, and only
                # on the slow sub-cadence. get_connected_network_identity() never raises. Emitting on
                # a present key lets the controller forward it exactly like the temp/power stats.
                if self.config.get('show_network_identity', False):
                    now = time.monotonic()
                    if now - self._last_identity_poll >= _IDENTITY_POLL_INTERVAL_SEC:
                        self._last_identity_poll = now
                        stats['network_identity'] = get_connected_network_identity()

                # Monitor-window override forces hardware collection even with the widget's flags off.
                _force_hw = self._force_hardware_collection
                # Always-on history recording: collect cheap CPU/GPU/RAM utilisation so the Monitor's
                # graphs have real past data. Temps/power stay on their own (heavier) gates below.
                _record = self.config.get('record_hardware_history', True)

                # 2. CPU / RAM (Optional)
                if self.config.get('monitor_cpu_enabled', False) or _force_hw or _record:
                    # non-blocking (percpu=False)
                    stats['cpu'] = psutil.cpu_percent(interval=None)
                    if self.config.get('show_hardware_temps', False) or _force_hw:
                        stats['cpu_temp'] = self._poll_cpu_temperature()
                    if self.config.get('show_hardware_power', False) or _force_hw:
                        stats['cpu_power'] = self._poll_cpu_power()

                    # RAM is often grouped with CPU in simple monitors
                    mem = psutil.virtual_memory()
                    stats['ram_used'] = mem.used / (1024**3) # GB
                    stats['ram_total'] = mem.total / (1024**3) # GB

                # 3. GPU / VRAM (Optional - skipped entirely in RDP sessions)
                if (self.config.get('monitor_gpu_enabled', False) or _force_hw or _record) and not _in_rdp:
                    try:
                        include_temp = bool(self.config.get('show_hardware_temps', False)) or _force_hw
                        include_power = bool(self.config.get('show_hardware_power', False)) or _force_hw
                        gpu = self._poll_gpu_hybrid(include_temp=include_temp, include_power=include_power)

                        stats['gpu'] = gpu.util
                        stats['gpu_present'] = gpu.present   # lets the Monitor hide GPU tiles on no-GPU boxes
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

                # Whole-system power, when the platform actually exposes it (RAPL PSYS / battery
                # discharge) - distinct from the CPU+GPU sum; emitted only when a real source is found.
                if self.config.get('show_hardware_power', False) or _force_hw:
                    sysp = self._poll_system_power()
                    if sysp is not None:
                        stats['system_power'] = sysp

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

                # Success - reset the circuit breaker (and announce recovery if we'd notified)
                if self.consecutive_errors > 0:
                    if self._error_notified:
                        self.logger.info("Monitor recovered after %d consecutive errors.", self.consecutive_errors)
                    self.consecutive_errors = 0
                    self._error_notified = False

            except Exception as e:
                self.consecutive_errors += 1
                self.logger.error("Error fetching stats (consecutive=%d): %s", self.consecutive_errors, e)

                # Notify ONCE when we cross the threshold - but keep running. The thread is
                # never permanently bricked; it backs off and retries so it can self-heal.
                if self.consecutive_errors == self._ERROR_NOTIFY_THRESHOLD and not self._error_notified:
                    self._error_notified = True
                    self.logger.warning("Monitor degraded (>= %d errors); backing off and retrying.",
                                        self._ERROR_NOTIFY_THRESHOLD)
                    self.error_occurred.emit(f"Hardware monitor is having trouble: {e}")

            # Responsive sleep, with exponential backoff while in an error streak so a failing
            # source isn't hammered every second (capped at _MAX_BACKOFF_SEC; recovers on success).
            if self.consecutive_errors == 0:
                effective_interval = self.interval
            else:
                effective_interval = min(
                    self._MAX_BACKOFF_SEC,
                    self.interval * (2 ** min(self.consecutive_errors, 5)),
                )
            sleep_remaining = effective_interval
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

    def _init_com(self) -> None:
        """Initialise the COM apartment ONCE for this thread (WMI/LHM access)."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

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
