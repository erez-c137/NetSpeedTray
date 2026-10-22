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
import math
import threading
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
from netspeedtray.core import gpu_adapters
from netspeedtray.core.lhm_http import LhmHttpClient
from netspeedtray.utils.rdp_utils import is_rdp_session
from netspeedtray.utils.network_utils import get_connected_network_identity

logger = logging.getLogger("NetSpeedTray.StatsMonitorThread")

# Network identity (Wi-Fi band / SSID) changes rarely, so poll it on a slow sub-cadence off the
# per-second network readout - never on the GUI thread. See releases/v2.1/KICKOFF.md §2/§3.
_IDENTITY_POLL_INTERVAL_SEC: float = 5.0

# LibreHardwareMonitor identifier prefixes that scope a sensor to something that is NOT the CPU.
# The CPU-temperature search falls back to matching sensor *names* (for boards that label the die
# sensor oddly - Ryzen's "Core (Tctl/Tdie)", super-I/O chips under /lpc/), and a name match must
# never out-vote an identifier that says the sensor belongs to another device: NVIDIA's sensor is
# literally named "GPU Core", which matched the "CORE" keyword, so on a hybrid laptop with no
# CPU-die sensor we reported the GPU's temperature as the CPU's (#237).
_NON_CPU_SENSOR_IDENTS: tuple = (
    "/gpu", "/nvme", "/hdd", "/ssd", "/psu", "/battery", "/network", "/ram",
)


# Stats that only a temperature / power sensor source can fill.
_SENSOR_STAT_KEYS = ('cpu_temp', 'gpu_temp', 'cpu_power', 'gpu_power')


def hardware_collection_gates(config: Dict[str, Any], force: bool) -> Tuple[bool, bool]:
    """(collect_cpu_ram, collect_gpu_vram) for one poll: the user's monitoring switches, or everything
    while the Monitor window forces collection for its live tiles. Recording is NOT a reason to poll -
    persistence is decided downstream (controller.hardware_persist_allowed)."""
    return (bool(config.get('monitor_cpu_enabled', False)) or force,
            bool(config.get('monitor_gpu_enabled', False)) or force)


def memory_collection_gates(config: Dict[str, Any], force: bool) -> Tuple[bool, bool]:
    """(collect_ram, collect_vram) for one poll. RAM/VRAM have their own switches, independent of
    CPU/GPU utilization: gating them on the CPU/GPU switches alone meant a config with RAM (or VRAM)
    on and CPU (or GPU) off collected no memory data at all (rc.2 G5). Settings greys the RAM/VRAM
    toggles out while their host monitor is off, which only stops NEW such configs - existing ones
    keep the switch on. Where the data is SHOWN is still each view's call (the taskbar paints RAM on
    the CPU line), and recording keeps its own consent gate (controller.hardware_persist_allowed)."""
    collect_cpu, collect_gpu = hardware_collection_gates(config, force)
    return (collect_cpu or bool(config.get('monitor_ram_enabled', False)),
            collect_gpu or bool(config.get('monitor_vram_enabled', False)))


def aggregate_gpu_engine_utilization(samples: Dict[Any, Any],
                                     luid: Optional[Tuple[int, int]] = None) -> float:
    """Busiest-engine GPU utilization from a `\\GPU Engine(*)\\Utilization Percentage` array.

    Each instance is ONE process on ONE engine (pid_..._luid_..._phys_N_eng_M_engtype_X), so the
    engine's load is the SUM of its processes; taking max() over instances reported the busiest
    process instead and under-read any shared engine (#328: 44 + 34 + 13 read as 44, not ~91).
    Engines are never added to each other - 3D, Copy and Video run in the same wall-clock interval -
    and neither are adapters: nothing in the app selects an adapter (VRAM is summed across all of
    them), so on a dGPU + iGPU machine this is the busiest engine on ANY adapter, as Task Manager's
    per-GPU pages would each show their own busiest engine.

    Unparseable instance names cannot be attributed to an engine, so each counts as its own engine
    (neither dropped nor summed into a parsed one). Non-numeric, bool, negative and non-finite
    values are skipped. The result is not clamped here; the caller clamps to [0, 100].

    ``luid`` (the GPU picker, #236): only that adapter's engines count, and unparseable instances -
    which cannot be attributed to any adapter - are dropped. None (Automatic) is the readout above.
    """
    engines: Dict[Any, float] = {}
    for instance, value in samples.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            continue
        match = (constants.gpu.ENGINE_INSTANCE_PATTERN.match(instance)
                 if isinstance(instance, str) else None)
        if match:
            luid_high, luid_low, phys, eng = match.groups()
            key: Any = (int(luid_high, 16), int(luid_low, 16), int(phys), int(eng))
            if luid is not None and key[:2] != tuple(luid):
                continue
        elif luid is not None:
            continue
        else:
            key = ("unparsed", instance)
        engines[key] = engines.get(key, 0.0) + value
    return max(engines.values(), default=0.0)


class GpuPollResult(NamedTuple):
    """Structured result from GPU polling, replacing opaque 4-tuple."""
    util: float = 0.0
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    temp: Optional[float] = None
    power: Optional[float] = None
    # True when vram_used/vram_total are an integrated GPU's SHARED system memory (no adapter has
    # dedicated memory - see _poll_gpu_hybrid), not dedicated video memory.
    vram_is_shared: bool = False
    # A PDH engine instance or a hardware-scoped LHM GPU sensor proves presence.
    present: bool = False


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
        # LHM 0.9.5+ removed its WMI provider; its built-in web server is the
        # primary sensor source. The client's short cache shares one data.json
        # response across CPU/GPU temperature and power reads in this poll.
        self._lhm_http = LhmHttpClient()
        self._ohm_guidance_logged: bool = False  # One-time "no sensor source" guidance, logged only when NO namespace connects
        self._last_temp_source_sig: Optional[Tuple[str, str]] = None  # (source, sensor) of the last-logged CPU-temp read; re-logged only on change (#216)
        # ACPI thermal-zone placeholder detection (#237, #275): first raw value seen per zone, the
        # zones that have moved since (trusted for good), and the ones already logged as placeholders.
        self._thermal_first_raw: Dict[Any, float] = {}
        self._thermal_moved: set = set()
        self._thermal_placeholder_logged: set = set()
        self._lhm_notice_emitted: bool = False  # One-time notification flag
        self._lhm_check_polls: int = 0  # Count polls before emitting notice
        self._last_identity_poll: float = 0.0  # monotonic ts of the last network-identity sub-poll (0 = poll immediately)
        self._last_link_stats_poll: float = 0.0  # monotonic ts of the last net_if_stats sub-poll (v8 link history)
        self._nvidia_smi_path: Optional[str] = self._get_cached_path("nvidia-smi")
        # The helper process in flight, if any - stop() kills it (see _run_child).
        self._child: Optional[subprocess.Popen] = None
        self._child_lock = threading.Lock()
        # nvidia-smi is a synchronous subprocess (up to NVIDIA_SMI_TIMEOUT_SEC). Temps/power
        # change slowly, so poll it on a slow sub-cadence and cache between calls - keeps the
        # per-second network readout off the subprocess critical path.
        self._NVIDIA_POLL_INTERVAL_SEC = 5.0
        self._nvidia_last_poll: float = 0.0
        self._nvidia_cache_temp: Optional[float] = None
        self._nvidia_cache_power: Optional[float] = None
        self._nvidia_cache_vram_total: Optional[float] = None
        # Picked NVIDIA card (#236): (picked name, (temp, power, total_mib)) from its own line.
        self._nvidia_pick_cache: Tuple[Optional[str], Tuple[Optional[float], Optional[float], Optional[float]]] = (
            None, (None, None, None))
        self._nvidia_pick_last_poll: float = 0.0

        # PDH Queries for GPU
        self._gpu_query: Optional[int] = None
        # Single WILDCARD counter handles, read via GetFormattedCounterArray - not a list of
        # per-instance handles snapshotted at startup (see _init_gpu_query for why).
        self._gpu_util_counter: Optional[int] = None
        self._gpu_vram_counter: Optional[int] = None
        self._gpu_shared_counter: Optional[int] = None
        # Static DXGI adapter facts (gpu_adapters.enumerate_adapters), re-read on every query
        # (re)build; None = DXGI unavailable, which falls back to the usage latch below.
        self._gpu_adapters: Optional[List[gpu_adapters.AdapterInfo]] = None
        # The set of adapter LUIDs PDH named when the cached DXGI list was last checked against it
        # (rc.2 G4): an eGPU hot-plugged without a display change - or unplugged - shows up as a PDH
        # instance whose LUID the cache lacks (or a cached GPU PDH no longer lists), and the list is
        # re-read ONCE per such change (~0.3 ms), not on every poll. None = check on the next poll.
        self._gpu_pdh_luids_checked: Optional[frozenset] = None
        # An adapter set a fresh DXGI read did NOT resolve (PDH lags DXGI on a hot-plug, or names a
        # LUID DXGI never lists): re-read at most every ADAPTER_RECHECK_SEC, ADAPTER_RECHECK_MAX times.
        self._gpu_luids_pending: Optional[frozenset] = None
        self._gpu_luids_retry_at: float = 0.0
        self._gpu_luids_tries: int = 0
        # The picked adapter name last logged as missing (#236), so the fallback is logged once.
        self._gpu_missing_logged: Optional[str] = None
        # FALLBACK ONLY (no DXGI): True once any adapter has reported dedicated usage at or above
        # constants.gpu.DEDICATED_USAGE_FALLBACK_MIB. From then on VRAM is the dedicated figure.
        # Not reset on a query rebuild, like _gpu_engine_seen.
        self._gpu_dedicated_seen: bool = False
        # A missing/broken PDH provider must not be re-opened on every one-second poll.
        self._gpu_query_retry_after: float = 0.0
        # Latched: True once the wildcard has ever returned at least one GPU Engine instance.
        # AddCounter on a wildcard path succeeds whenever the counter OBJECT exists, so it is not
        # evidence of a GPU - deriving "present" from it would fabricate a 0% readout on RDP
        # sessions, VMs and GPU-less boxes.
        self._gpu_engine_seen: bool = False
        # Same latch for LHM: once its web server has shown a hardware-scoped GPU, the GPU exists -
        # an LHM backoff window or a GPU that is asleep this tick must not flap the tiles out.
        self._lhm_gpu_seen: bool = False
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
        self._battery_retry_at: float = 0.0              # after a transient WMI error
        self._battery_ac_line: Optional[int] = None      # last ACLineStatus (a change drops the cache)
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

    def invalidate_hardware_queries(self) -> None:
        """Flag the PDH/WMI hardware handles for a rebuild on the next poll.

        Same mechanism and same thread-safety reasoning as update_config, but for environment
        changes rather than settings changes: PDH handles do not survive suspend/resume or a GPU
        adapter appearing or disappearing, and a stale handle just returns nothing forever (a flat
        0.0 VRAM after every wake - #237). Safe to call from the GUI thread; run() does the actual
        cleanup on its own thread.
        """
        self._hw_queries_dirty = True

    def _init_gpu_query(self) -> bool:
        """Initializes Windows PDH query for universal GPU utilization and VRAM.

        Both counters are added as WILDCARD paths and read with GetFormattedCounterArray, rather
        than enumerating instances once and adding one handle each. \\GPU Engine instances are
        per-PROCESS (pid_1234_luid_..._engtype_3D), so a snapshot taken at startup only ever sees
        processes that already existed - every app launched afterwards gets a new PID we never
        subscribed to, and its load never appears. On a single-GPU box that is invisible, because
        the compositor's own instance tracks total load closely enough; on a hybrid laptop it means
        the discrete GPU is never sampled at all, since nothing driving it was running at launch
        (#236). The wildcard is re-expanded on every collection, so new processes are picked up.

        The same form is already used by views/monitor/hardware/worker.py, which is why the Monitor
        window's Hardware tab reported the dGPU correctly while the widget did not.
        """
        if not win32pdh:
            return False

        try:
            if self._gpu_query:
                return True

            # Static per-adapter facts first (does any GPU have real dedicated memory?) - re-read
            # on every rebuild, so an eGPU plugged in or a driver reset is picked up on resume.
            self._gpu_adapters = gpu_adapters.enumerate_adapters()
            self._gpu_pdh_luids_checked = None
            self._gpu_luids_pending = None
            if self._gpu_adapters is not None:
                self.logger.debug("DXGI adapters: %s", [
                    (a.name, hex(a.vendor_id), round(a.dedicated_video_bytes / 2 ** 20), a.software)
                    for a in self._gpu_adapters])

            self._gpu_query = win32pdh.OpenQuery()
            self._gpu_util_counter = None
            self._gpu_vram_counter = None
            self._gpu_shared_counter = None

            # 1. Utilization: every engine of every adapter, all processes.
            # Deliberately NOT filtered by engtype: a large share of instances report a blank
            # engtype, and "Compute" is not a distinct value, so filtering to 3D would zero out
            # exactly the compute/CUDA workloads this is meant to surface.
            try:
                self._gpu_util_counter = win32pdh.AddCounter(
                    self._gpu_query, r"\GPU Engine(*)\Utilization Percentage")
            except Exception as e:
                self.logger.debug("Failed to add GPU Engine wildcard counter: %s", e)

            # 2. VRAM (\GPU Adapter Memory(*)\Dedicated Usage)
            try:
                self._gpu_vram_counter = win32pdh.AddCounter(
                    self._gpu_query, r"\GPU Adapter Memory(*)\Dedicated Usage")
            except Exception as e:
                self.logger.debug("Failed to add GPU Adapter Memory wildcard counter: %s", e)

            # 3. Shared GPU memory (\\GPU Adapter Memory(*)\\Shared Usage) - what an integrated GPU
            # actually uses, since it has no dedicated memory. Only consulted when no adapter has any.
            try:
                self._gpu_shared_counter = win32pdh.AddCounter(
                    self._gpu_query, r"\GPU Adapter Memory(*)\Shared Usage")
            except Exception as e:
                self.logger.debug("Failed to add GPU Adapter Memory Shared Usage counter: %s", e)

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
            self._gpu_util_counter = None
            self._gpu_vram_counter = None
            self._gpu_shared_counter = None
            # _gpu_engine_seen is deliberately NOT reset: a settings change or a resume rebuilds
            # the query, and forgetting that this machine has a GPU would blink the Monitor's GPU
            # tiles out until the next non-idle tick.

    def _poll_gpu_hybrid(self, include_temp: bool = True, include_power: bool = False) -> GpuPollResult:
        """
        Collects GPU stats using a hybrid approach:
        - Utilization & VRAM via Universal PDH (all vendors)
        - Temperature via LHM web server (v0.9.5+) → LHM v0.9.4/OHM WMI (all vendors) → nvidia-smi (NVIDIA)
        - Power via LHM web server → LHM/OHM WMI (all vendors) → nvidia-smi (NVIDIA) → PDH RAPL PP1 (Intel iGPU)
        Returns: GpuPollResult named tuple
        """
        now_mono = time.monotonic()
        if not self._gpu_query and now_mono >= self._gpu_query_retry_after:
            if self._init_gpu_query():
                self._gpu_query_retry_after = 0.0
            else:
                self._gpu_query_retry_after = now_mono + constants.gpu.QUERY_RETRY_SEC

        util_pct = 0.0
        vram_used = None   # None = no VRAM counter available -> N/A (not a misleading "0.0 GB used")
        vram_total = None
        vram_is_shared = False
        # Shared Usage per PDH adapter instance, MiB (empty = counter unavailable / no instance), and
        # whether the dedicated read succeeded - a failed one never falls into shared mode.
        shared_mib: Dict[str, float] = {}
        dedicated_read_ok = False
        dedicated_mib: Dict[str, float] = {}   # Dedicated Usage per PDH adapter instance, MiB
        util_arr: Optional[Dict[Any, Any]] = None
        temp_c = None
        power_w = None

        if self._gpu_query:
            try:
                win32pdh.CollectQueryData(self._gpu_query)

                # 1. Utilization of the busiest engine, its processes summed (#328) - what Task Manager
                # shows. The wildcard re-expands each collection, so processes started after us count.
                # Aggregated after the memory reads below: a picked adapter (#236) is resolved by LUID
                # against the DXGI list, which those reads may have just refreshed (G4).
                if self._gpu_util_counter is not None:
                    try:
                        util_arr = win32pdh.GetFormattedCounterArray(
                            self._gpu_util_counter, win32pdh.PDH_FMT_DOUBLE)
                        if util_arr:
                            # A non-empty array is the only honest proof a GPU engine exists. Latched,
                            # because the array is legitimately empty on an idle tick and we must not
                            # flap the Monitor's GPU tiles in and out.
                            self._gpu_engine_seen = True
                    except Exception as e:
                        self.logger.debug("GPU utilization array read failed: %s", e)

                # 2. Universal VRAM (Dedicated Usage in bytes, convert to MiB). Only report a real
                # number if at least one instance contributed - otherwise leave None (N/A).
                if self._gpu_vram_counter is not None:
                    try:
                        arr = win32pdh.GetFormattedCounterArray(
                            self._gpu_vram_counter, win32pdh.PDH_FMT_DOUBLE)
                        _vram_acc = 0.0
                        _had_vram = False
                        for inst, val in arr.items():
                            if isinstance(val, (int, float)):
                                _mib = float(val) / (1024.0 * 1024.0)
                                dedicated_mib[str(inst)] = _mib
                                _vram_acc += _mib
                                _had_vram = True
                                if _mib >= constants.gpu.DEDICATED_USAGE_FALLBACK_MIB:
                                    self._gpu_dedicated_seen = True
                        if _had_vram:
                            vram_used = _vram_acc
                            dedicated_read_ok = True
                    except Exception as e:
                        self.logger.debug("GPU VRAM array read failed: %s", e)

                # 2b. Shared Usage per adapter (bytes -> MiB). Read every poll but only USED below,
                # when no adapter has dedicated memory - a discrete GPU's readout never changes.
                if self._gpu_shared_counter is not None:
                    try:
                        arr = win32pdh.GetFormattedCounterArray(
                            self._gpu_shared_counter, win32pdh.PDH_FMT_DOUBLE)
                        for inst, val in arr.items():
                            if (isinstance(val, (int, float)) and not isinstance(val, bool)
                                    and math.isfinite(float(val)) and float(val) >= 0.0):
                                shared_mib[str(inst)] = float(val) / (1024.0 * 1024.0)
                    except Exception as e:
                        self.logger.debug("GPU shared memory array read failed: %s", e)

            except Exception as e:
                self.logger.debug("GPU PDH polling error: %s", e)

        # 2c. A GPU that came or went since the DXGI list was read (G4), then the picked one (#236).
        self._refresh_adapters_on_luid_change(set(dedicated_mib) | set(shared_mib))
        selected = self._selected_gpu_adapter()
        if util_arr is not None:
            try:
                util_pct = aggregate_gpu_engine_utilization(
                    util_arr, selected.luid if selected is not None else None)
            except Exception as e:
                self.logger.debug("GPU utilization aggregation failed: %s", e)
        if selected is not None and dedicated_read_ok:
            mine = [v for inst, v in dedicated_mib.items()
                    if gpu_adapters.pdh_instance_luid(inst) == selected.luid]
            vram_used = sum(mine) if mine else None

        # 3. Temperature & Power - prefer LHM/OHM (all vendors) over nvidia-smi (NVIDIA only)
        # A picked adapter (#236) takes temperature / power only from a sensor of its own maker;
        # nvidia-smi is NVIDIA's, so it is not asked for another maker's card.
        smi_matches = selected is None or selected.vendor_id == constants.gpu.VENDOR_NVIDIA
        need_smi_temp = include_temp and smi_matches
        need_smi_power = include_power and smi_matches

        # LHM 0.9.5+ HTTP source. SensorId/HardwareId grouping in lhm_http
        # prevents the iGPU or a motherboard "CPU Socket" sensor from winning.
        lhm_snapshot = self._lhm_http.get_snapshot() if (include_temp or include_power) else None
        lhm_gpu = lhm_snapshot.preferred_gpu if lhm_snapshot else None
        if lhm_gpu is not None:
            self._lhm_gpu_seen = True
        if selected is not None:
            lhm_gpu = (lhm_snapshot.gpu_for(selected.vendor_id, selected.name,
                                            gpu_adapters.is_integrated(selected))
                       if lhm_snapshot else None)
        if lhm_gpu:
            if include_temp and lhm_gpu.temperature is not None:
                temp_c = lhm_gpu.temperature
                need_smi_temp = False
            if include_power and lhm_gpu.power is not None:
                power_w = lhm_gpu.power
                need_smi_power = False

        if (include_temp and temp_c is None) or (include_power and power_w is None):
            self._init_ohm_wmi()
            if self._wmi_ohm:
                # 3a. LHM/OHM GPU temperature
                if include_temp and temp_c is None:
                    try:
                        sensors = self._wmi_ohm.ExecQuery(
                            "SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='Temperature'"
                        )
                        for s in sensors:
                            identifier = str(getattr(s, 'Identifier', '')).lower()
                            if 'gpu' not in identifier or not self._gpu_sensor_matches(identifier, selected):
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
                if include_power and power_w is None and self._wmi_ohm:
                    try:
                        sensors = self._wmi_ohm.ExecQuery(
                            "SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='Power'"
                        )
                        for s in sensors:
                            identifier = str(getattr(s, 'Identifier', '')).lower()
                            if 'gpu' not in identifier or not self._gpu_sensor_matches(identifier, selected):
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
        # Total VRAM is a property of the card, not a reading, so it is fetched once and cached for
        # the life of the process - which is why asking for it here costs nothing on later polls.
        # It has to be its own reason to call nvidia-smi: this used to run only when temp or power
        # were needed FROM it, so with LibreHardwareMonitor supplying those, nvidia-smi never ran and
        # the total silently never arrived. Users saw VRAM as used-only with LHM open and used/total
        # with it closed, which is a bizarre thing to have to notice (#250).
        smi_pick: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
        if selected is None:
            need_smi_vram_total = self._nvidia_cache_vram_total is None
            if self._nvidia_smi_path and (need_smi_temp or need_smi_power or need_smi_vram_total):
                now_mono = time.monotonic()
                if (now_mono - self._nvidia_last_poll) >= self._NVIDIA_POLL_INTERVAL_SEC:
                    # Stamp first: a hung/failed call then waits the full interval before retry,
                    # instead of re-running (and re-stalling) every poll.
                    self._nvidia_last_poll = now_mono
                    try:
                        query_fields = "temperature.gpu,memory.total,power.draw"
                        output = self._run_child(
                            [self._nvidia_smi_path, f"--query-gpu={query_fields}", "--format=csv,noheader,nounits"],
                            constants.timeouts.NVIDIA_SMI_TIMEOUT_SEC)
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
        elif smi_matches and self._nvidia_smi_path:
            # A picked NVIDIA card: its OWN nvidia-smi line (by name), never the first line.
            smi_pick = self._nvidia_smi_for(selected, need_smi_temp or need_smi_power)
            if need_smi_temp and temp_c is None:
                temp_c = smi_pick[0]
            if need_smi_power and power_w is None:
                power_w = smi_pick[1]

        # Outside the gate on purpose: once the total is known it applies on every poll, whatever the
        # reason nvidia-smi did or did not run this time round.
        if selected is None:
            if vram_total is None and self._nvidia_cache_vram_total is not None:
                vram_total = self._nvidia_cache_vram_total

            # 4b. Integrated GPU: no adapter has dedicated memory, so "VRAM 0.0 GB" says nothing. Report
            # the shared system memory the iGPU actually uses instead (see _shared_vram_reading).
            shared = self._shared_vram_reading(shared_mib, dedicated_read_ok)
        else:
            # The picked adapter's own memory: nvidia-smi's total for the picked NVIDIA card (its own
            # line - 12282 MiB where DXGI says 12288), else its DXGI dedicated size (what Task Manager
            # shows), or - for an iGPU - its shared usage against its shared budget.
            shared = self._selected_shared_vram(selected, shared_mib, dedicated_read_ok)
            if shared is None and vram_used is not None:
                if smi_pick[2] is not None:
                    vram_total = smi_pick[2]
                elif selected.dedicated_video_bytes > 0:
                    vram_total = selected.dedicated_video_bytes / (1024.0 * 1024.0)
        if shared is not None:
            vram_used, vram_total = shared
            vram_is_shared = True

        # 5. RAPL PP1 fallback for Intel iGPU power (if no LHM/nvidia-smi power).
        # Init the PDH query FIRST (it's idempotent and sets _power_pp1_counter on first call) -
        # the old guard checked the counter before anything could ever set it, so this whole
        # fallback was dead code.
        # RAPL PP1 is the Intel iGPU's power plane: never an Arc's (or any discrete card's) reading.
        if include_power and power_w is None and (
                selected is None or (selected.vendor_id == constants.gpu.VENDOR_INTEL
                                     and gpu_adapters.is_integrated(selected))):
            try:
                self._init_power_query()
                if self._power_query and self._power_pp1_counter is not None:
                    win32pdh.CollectQueryData(self._power_query)
                    _, val = win32pdh.GetFormattedCounterValue(self._power_pp1_counter, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None and val > 0:
                        power_w = val / 1000.0  # mW to W
            except: pass

        # Clamp utilization to [0, 100] - a per-engine sum can momentarily read >100%.
        util_pct = max(constants.gpu.UTIL_MIN_PERCENT, min(constants.gpu.UTIL_MAX_PERCENT, util_pct))
        # LHM's HardwareId-scoped sensor group is independent evidence that a physical GPU
        # exists. Keeping the row visible with 0% utilization is intentional when PDH is
        # unavailable: temperature/power remain useful and are not fabricated.
        return GpuPollResult(util_pct, vram_used, vram_total, temp_c, power_w,
                             vram_is_shared=vram_is_shared,
                             present=self._gpu_engine_seen or self._lhm_gpu_seen)

    def _shared_vram_reading(self, shared_mib: Dict[str, float],
                             dedicated_read_ok: bool) -> Optional[Tuple[float, Optional[float]]]:
        """(used_mib, total_mib) of an integrated GPU's shared memory, or None to keep the dedicated
        readout exactly as before.

        None whenever there is any sign of dedicated memory or any doubt: the dedicated read failed,
        no shared reading, an NVIDIA driver (nvidia-smi), or - from the static DXGI facts - any real
        adapter with DedicatedVideoMemory >= DEDICATED_VIDEO_MEMORY_MIN_MIB, or no real adapter at
        all (Microsoft Basic Render only: VMs, servers, broken drivers keep today's hidden VRAM).
        Otherwise the real adapter (matched by LUID) using the most shared memory, against its DXGI
        SharedSystemMemory budget. Without DXGI: the usage latch and a 1 MiB floor (fallback).
        """
        if not dedicated_read_ok or not shared_mib or self._nvidia_smi_path:
            return None
        mib = 1024.0 * 1024.0
        adapters = self._gpu_adapters
        if adapters is not None:
            real = gpu_adapters.hardware_adapters(adapters)
            if not real or any(a.dedicated_video_bytes / mib >= constants.gpu.DEDICATED_VIDEO_MEMORY_MIN_MIB
                               for a in real):
                return None
            by_luid = {a.luid: a for a in real}
            best: Optional[Tuple[float, Optional[float]]] = None
            for inst, used in shared_mib.items():
                a = by_luid.get(gpu_adapters.pdh_instance_luid(inst))
                if a is not None and (best is None or used > best[0]):
                    best = (used, a.shared_system_bytes / mib if a.shared_system_bytes > 0 else None)
            if best is None:
                return None
            return best[0], best[1] if best[1] else self._half_ram_mib()
        # Fallback (no DXGI): usage latch + a floor that rules out a Basic-Render-only machine.
        if self._gpu_dedicated_seen:
            return None
        used = max(shared_mib.values())
        if used < constants.gpu.SHARED_USAGE_FALLBACK_MIN_MIB:
            return None
        return used, self._half_ram_mib()

    def _selected_shared_vram(self, adapter: gpu_adapters.AdapterInfo, shared_mib: Dict[str, float],
                              dedicated_read_ok: bool) -> Optional[Tuple[float, Optional[float]]]:
        """(used_mib, total_mib) of a PICKED integrated GPU's shared memory (#236), or None: the
        adapter has real dedicated memory, the dedicated read failed, or PDH has no shared reading
        for it. Same rules as _shared_vram_reading, scoped to the one adapter."""
        mib = 1024.0 * 1024.0
        if (not dedicated_read_ok
                or adapter.dedicated_video_bytes / mib >= constants.gpu.DEDICATED_VIDEO_MEMORY_MIN_MIB):
            return None
        mine = [v for inst, v in shared_mib.items() if gpu_adapters.pdh_instance_luid(inst) == adapter.luid]
        if not mine:
            return None
        total = adapter.shared_system_bytes / mib if adapter.shared_system_bytes > 0 else None
        return sum(mine), total if total else self._half_ram_mib()

    def _selected_gpu_adapter(self) -> Optional[gpu_adapters.AdapterInfo]:
        """The adapter picked in Settings > Hardware (#236), or None for Automatic. A picked GPU that
        is not in the machine right now (an unplugged eGPU) - or no DXGI facts - also reads as
        Automatic, logged once, so the readout never goes blank; it switches back when the GPU
        returns (G4 re-reads the adapter list when PDH names a new one)."""
        name = str(self.config.get('gpu_adapter', constants.gpu.ADAPTER_AUTO) or "").strip()
        if not name:
            return None
        adapter = gpu_adapters.find_adapter(self._gpu_adapters, name)
        if adapter is None:
            if self._gpu_missing_logged != name:
                self._gpu_missing_logged = name
                self.logger.info("Picked GPU %r is not present; showing the busiest GPU until it is.", name)
        else:
            self._gpu_missing_logged = None
        return adapter

    def _gpu_sensor_matches(self, identifier: str, adapter: Optional[gpu_adapters.AdapterInfo]) -> bool:
        """A WMI GPU sensor counts for a picked adapter only when its identifier names the same maker
        and - with another GPU of that maker in the machine - the same side of integrated / discrete.
        Intel's identifiers say so (/gpu-intel-integrated/); an AMD or NVIDIA pair cannot be told
        apart from the identifier, so it reads N/A rather than the other card's sensor."""
        if adapter is None:
            return True
        if not gpu_adapters.sensor_matches_vendor(identifier, adapter.vendor_id):
            return False
        if not gpu_adapters.same_maker_siblings(self._gpu_adapters, adapter):
            return True
        if adapter.vendor_id != constants.gpu.VENDOR_INTEL:
            return False
        return ("integrated" in identifier.lower()) == gpu_adapters.is_integrated(adapter)

    def _nvidia_smi_for(self, adapter: gpu_adapters.AdapterInfo, want_readings: bool = True
                        ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """(temp_c, power_w, total_mib) from the nvidia-smi line that IS the picked card (#236).

        Matched by name. One NVIDIA card in DXGI and one line: that line, whatever its name. Anything
        else (two identical cards, a name nvidia-smi spells differently on a multi-card box): all
        None - N/A, never another card's reading. Same slow sub-cadence and stamp-first rule as the
        Automatic query; cached per picked name."""
        name = adapter.name.strip().casefold()
        cached_name, cached = self._nvidia_pick_cache
        now = time.monotonic()
        if cached_name == name and (
                (now - self._nvidia_pick_last_poll) < self._NVIDIA_POLL_INTERVAL_SEC
                or (not want_readings and cached[2] is not None)):   # only the total wanted: known
            return cached
        self._nvidia_pick_last_poll = now
        result: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
        try:
            output = self._run_child(
                [self._nvidia_smi_path, "--query-gpu=name,temperature.gpu,memory.total,power.draw",
                 "--format=csv,noheader,nounits"], constants.timeouts.NVIDIA_SMI_TIMEOUT_SEC)
            rows = [[p.strip() for p in line.split(',')] for line in output.strip().splitlines() if line.strip()]
            mine = [r for r in rows if r and r[0].casefold() == name]
            nvidia_cards = [a for a in gpu_adapters.hardware_adapters(self._gpu_adapters or [])
                            if a.vendor_id == constants.gpu.VENDOR_NVIDIA]
            if not mine and len(rows) == 1 and len(nvidia_cards) == 1:
                mine = rows
            if len(mine) == 1 and len(mine[0]) >= 4:
                def num(text: str, lo: float, hi: float) -> Optional[float]:
                    try:
                        v = float(text)
                    except ValueError:
                        return None
                    return v if lo < v < hi else None
                r = mine[0]
                result = (num(r[1], 0.0, 150.0), num(r[3], 0.0, 1000.0), num(r[2], 0.0, float("inf")))
        except Exception as e:
            self.logger.debug("nvidia-smi query for the picked GPU failed: %s", e)
        self._nvidia_pick_cache = (name, result)
        return result

    def _refresh_adapters_on_luid_change(self, pdh_instances) -> None:
        """Re-read the DXGI adapter list when PDH's GPU set no longer matches it (rc.2 G4).

        The list is read at query init, and a query is rebuilt on resume and on a display change, but
        an eGPU hot-plugged without one would otherwise keep the old "no dedicated memory" verdict
        (shared-VRAM mode) until the next rebuild - or, unplugged, keep a stale dedicated one. A
        LUID in PDH the cache lacks, or a cached real GPU PDH no longer lists, triggers one re-read
        (~0.3 ms); the same LUID set is never checked twice, so a permanent mismatch costs nothing
        per poll."""
        if self._gpu_adapters is None:
            return   # DXGI unavailable: re-reading would not help, and the fallback latch applies
        luids = frozenset(x for x in (gpu_adapters.pdh_instance_luid(i) for i in pdh_instances) if x)
        if not luids or luids == self._gpu_pdh_luids_checked:
            return
        if self._adapters_cover(luids):
            self._gpu_pdh_luids_checked = luids
            self._gpu_luids_pending = None
            return
        g = constants.gpu
        now = time.monotonic()
        if luids != self._gpu_luids_pending:
            self._gpu_luids_pending, self._gpu_luids_tries, self._gpu_luids_retry_at = luids, 0, 0.0
        if now < self._gpu_luids_retry_at:
            return
        self._gpu_luids_tries += 1
        self._gpu_luids_retry_at = now + g.ADAPTER_RECHECK_SEC
        fresh = gpu_adapters.enumerate_adapters()
        if fresh is not None and fresh != self._gpu_adapters:
            self._gpu_adapters = fresh
            self.logger.info("GPU adapters changed: %s", [a.name for a in gpu_adapters.hardware_adapters(fresh)])
        # Marked checked only once resolved - or after the last bounded retry (a LUID DXGI never
        # lists), so a permanent mismatch stops costing anything.
        if (fresh is not None and self._adapters_cover(luids)) or self._gpu_luids_tries >= g.ADAPTER_RECHECK_MAX:
            self._gpu_pdh_luids_checked = luids
            self._gpu_luids_pending = None

    def _adapters_cover(self, luids: frozenset) -> bool:
        """The cached DXGI list knows every LUID PDH names, and PDH still lists every real GPU."""
        known = {a.luid for a in self._gpu_adapters or []}
        real = {a.luid for a in gpu_adapters.hardware_adapters(self._gpu_adapters or [])}
        return luids <= known and real <= luids

    @staticmethod
    def _half_ram_mib() -> Optional[float]:
        """Windows' default shared GPU memory budget: half of physical RAM, in MiB (None if unknown)."""
        try:
            budget = (psutil.virtual_memory().total / (1024.0 * 1024.0)
                      * constants.gpu.SHARED_BUDGET_FRACTION_OF_RAM)
        except Exception:
            return None
        return budget if budget > 0 else None

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
            for key in [k for k in self._thermal_first_raw if k[0] == "pdh"]:
                self._thermal_first_raw.pop(key, None)
                self._thermal_moved.discard(key)
                self._thermal_placeholder_logged.discard(key)

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
        # system_power must stay a non-negative draw ("watts consumed"): the poll returns a
        # SIGNED value (negative while charging), so a charge flow yields None here.
        watts = self._poll_battery_power()
        return watts if watts is not None and watts > 0 else None

    def _poll_battery_power(self) -> Optional[float]:
        """The battery flow in watts, SIGNED: discharge positive, charge negative; None when idle
        or unreadable. Summed over every pack (dual-battery laptops). Cached on a slow cadence
        (WMI is heavy), except that a change in the AC line - one cheap syscall - drops the
        cache, so unplugging flips the reading on the next poll instead of up to 8 s later."""
        from netspeedtray.utils import power_utils
        p = constants.hardware.power
        now = time.monotonic()
        ac_line = power_utils.ac_line_status()
        ac_changed = ac_line != self._battery_ac_line
        self._battery_ac_line = ac_line
        last_ts, last_val = self._battery_cache
        if not ac_changed and (now - last_ts) < self._battery_cadence_sec:
            return last_val
        if self._wmi_battery is False or now < self._battery_retry_at:
            self._battery_cache = (now, None)
            return None
        watts = None
        try:
            if self._wmi_battery is None:
                import wmi  # already a dependency (LHM temps)
                self._wmi_battery = wmi.WMI(namespace="root\\wmi")
            discharge_mw = charge_mw = 0
            for b in self._wmi_battery.BatteryStatus():
                # DischargeRate / ChargeRate are mW and non-zero only while that flow is real.
                # IMPLAUSIBLE_MW (constants/hardware.py) drops only the firmware garbage some
                # packs emit around an AC transition; real readings stay admissible.
                rate = getattr(b, "DischargeRate", 0) or 0
                if getattr(b, "Discharging", False) and 0 < rate < p.IMPLAUSIBLE_MW:
                    discharge_mw += rate
                c_rate = getattr(b, "ChargeRate", 0) or 0
                if getattr(b, "Charging", False) and 0 < c_rate < p.IMPLAUSIBLE_MW:
                    charge_mw += c_rate
            # While charging, the charge rate is reported SIGNED NEGATIVE (the hover card renders
            # "+38.2 W"). system_power stays a non-negative draw - the clamp in _poll_system_power
            # and the battery_power split in _collect_power_stats keep the sign out of history.
            if discharge_mw:
                watts = float(discharge_mw) / 1000.0
            elif charge_mw:
                watts = -float(charge_mw) / 1000.0
        except (AttributeError, ImportError):
            self._wmi_battery = False   # no wmi module / no BatteryStatus class: permanent, stop
        except Exception as e:
            # Transient (RPC unavailable right after resume, a WMI service restart): drop the
            # handle and try again after a backoff - never give up on the battery until restart.
            self.logger.debug("battery WMI poll failed, retrying in %.0f s: %s",
                              p.BATTERY_WMI_RETRY_SEC, e)
            self._wmi_battery = None
            self._battery_retry_at = now + p.BATTERY_WMI_RETRY_SEC
        self._battery_cache = (now, watts)
        return watts

    def _collect_power_stats(self, stats: Dict[str, Any], force_hw: bool) -> None:
        """Whole-system / battery power for one poll, into ``stats``.

        When the power tiles are on (or the Monitor forces collection): a true whole-system
        reading (RAPL PSYS, else the battery DischargeRate) goes to ``system_power`` - distinct
        from the CPU+GPU sum; emitted only when a real source is found. When only the hover's
        power display is on, battery-only polling is enough - skip RAPL, whose PDH handles
        exist for the Monitor's power tiles, not for a hover glance. The signed battery poll
        (negative while charging) still rides along on AC so the hover's charge rate (+X W)
        keeps working with the power tiles on too (#323 review 3): ``_poll_system_power`` is
        discharge-only (a charge flow yields None), and the poll is cached on a slow cadence,
        so the follow-up call is nearly free.

        A machine with no battery skips the hover's WMI polls entirely (one cheap
        GetSystemPowerStatus call says so): the section can never show there.
        """
        hover_battery = bool(self.config.get('power_display_enabled', False))
        if hover_battery:
            from netspeedtray.utils import power_utils
            hover_battery = power_utils.has_battery()
            if hover_battery:
                # Warm the projection's capacity cache HERE, on the monitor thread: the hover
                # card only reads the cache and must never run WMI on the GUI thread. A no-op
                # once a value is cached; a failure is retried after a backoff.
                power_utils.read_design_capacity_mwh()
        if self.config.get('show_hardware_power', False) or force_hw:
            sysp = self._poll_system_power()
            if sysp is not None:
                stats['system_power'] = sysp
            if hover_battery:
                bp = self._poll_battery_power()
                if bp is not None and bp < 0:
                    stats['battery_power'] = bp
        elif hover_battery:
            # Discharge lands in system_power (stat history + Monitor tile); the signed
            # charge value rides battery_power.
            sysp = self._poll_battery_power()
            if sysp is not None:
                if sysp < 0:
                    stats['battery_power'] = sysp
                else:
                    stats['system_power'] = sysp

    def _poll_cpu_power(self) -> Optional[float]:
        """
        Polls CPU power draw in watts, trying sources in order:
          1. LibreHardwareMonitor v0.9.5+ web server (all vendors, if enabled)
          2. PDH RAPL PKG (Intel - milliwatts, non-admin)
          3. LHM v0.9.4 / OHM WMI (all vendors, requires admin)
        """
        # 1. LHM 0.9.5+ HTTP source
        snapshot = self._lhm_http.get_snapshot()
        if snapshot and snapshot.cpu_power is not None:
            return snapshot.cpu_power

        # 2. PDH RAPL PKG
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

        # 3. LHM 0.9.4 / OHM WMI fallback
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
                # No namespace at all: tool not running, or LHM v0.9.5+ (which removed WMI and is
                # read over HTTP instead - this probe only runs when that read found nothing).
                self.logger.info(
                    "Hardware monitor: no CPU/GPU sensor source found (%s; no LHM web server at "
                    "%s:%d). Start LibreHardwareMonitor as Administrator; on v0.9.5+ also enable "
                    "Options > Remote Web Server (port %d).", "; ".join(probe_errors),
                    constants.lhm.HTTP_HOST, constants.lhm.HTTP_PORT, constants.lhm.HTTP_PORT
                )
        # Leave _wmi_ohm as None so we retry next poll (LHM may not be running yet)

    def _poll_cpu_temperature(self) -> Optional[float]:
        """
        Polls CPU temperature, trying sources in order:
          1. LibreHardwareMonitor v0.9.5+ web server (data.json over loopback HTTP, if enabled)
          2. LibreHardwareMonitor v0.9.4 / OpenHardwareMonitor WMI  (accurate CPU-die sensor)
          3. PDH Thermal Zone Information  (generic ACPI - often a motherboard/ambient zone)
          4. WMI MSAcpi_ThermalZoneTemperature  (legacy ACPI fallback)

        LHM/OHM is tried FIRST because its CPU-die sensor (CPU Package / Tctl/Tdie) is
        the accurate source. The generic ACPI "Thermal Zone" is frequently a
        motherboard/ambient sensor near room temperature; when it short-circuits ahead
        of LHM it pins the readout to a flat wrong value (issue #216: stuck at 27°C).
        The ACPI zones remain as fallbacks so machines without a hardware-monitor tool
        still get a reading.

        Note: Modern Intel/AMD CPUs often require a kernel-driver tool
        (LibreHardwareMonitor, HWiNFO64, etc.) - see the settings note.
        """
        # 1. LibreHardwareMonitor 0.9.5+ HTTP source.
        snapshot = self._lhm_http.get_snapshot()
        if snapshot and snapshot.cpu_temp is not None:
            return self._log_temp(
                snapshot.cpu_temp, "LHM HTTP", snapshot.cpu_temp_sensor or "CPU")

        # 2. LibreHardwareMonitor 0.9.4 / OpenHardwareMonitor WMI.
        self._init_ohm_wmi()
        if self._wmi_ohm:
            try:
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name='CPU Package'"
                )
                for s in sensors:
                    val = float(s.Value)
                    if 0.0 < val < 150.0:
                        return self._log_temp(val, "LHM/OHM", "CPU Package")
                # Some boards label it differently (AMD Ryzen exposes "Core (Tctl/Tdie)",
                # not "CPU Package"). Match on the LHM Identifier (/amdcpu/ or /intelcpu/),
                # which reliably scopes to the CPU regardless of the display name, and
                # broaden the name keywords to cover Ryzen's Tctl/Tdie/Tccd labels. (#148)
                sensors = self._wmi_ohm.ExecQuery(
                    "SELECT Value, Name, Identifier FROM Sensor WHERE SensorType='Temperature'"
                )
                best_val: Optional[float] = None
                best_name: str = ""
                for s in sensors:
                    name = str(getattr(s, 'Name', '')).upper()
                    ident = str(getattr(s, 'Identifier', '')).lower()
                    # The identifier is authoritative: reject other devices before the name
                    # keywords get a vote, or "GPU Core" matches on "CORE" (#237).
                    if any(marker in ident for marker in _NON_CPU_SENSOR_IDENTS):
                        continue
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
                    if 0.0 < val < 150.0 and (best_val is None or val > best_val):
                        best_val = val
                        best_name = str(getattr(s, 'Name', '')) or "CPU"
                if best_val is not None:
                    return self._log_temp(best_val, "LHM/OHM", best_name)
            except Exception as e:
                self.logger.debug("OHM/LHM CPU temp error: %s", e)
                self._wmi_ohm = None

        # 2. PDH Thermal Zone Information (generic ACPI; may be motherboard/ambient - #216)
        if win32pdh:
            if not self._thermal_query:
                self._init_thermal_query()
            if self._thermal_query and (self._thermal_hp_counters or self._thermal_counters):
                try:
                    win32pdh.CollectQueryData(self._thermal_query)
                    readings = []

                    # 2a. High Precision Temperature (preferred)
                    for handle in self._thermal_hp_counters:
                        try:
                            _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                            if val is not None:
                                # Standard: tenths of Kelvin → Celsius
                                celsius = (val / 10.0) - 273.15
                                if 0.0 < celsius < 150.0:
                                    if self._is_placeholder_zone(("pdh", handle), val):
                                        continue
                                    readings.append(celsius)
                                # Some OEMs (HP, Dell) report direct Celsius
                                elif 15.0 < val < 110.0:
                                    readings.append(val)
                        except: continue

                    # 2b. Standard Temperature counter (fallback)
                    if not readings:
                        for handle in self._thermal_counters:
                            try:
                                _, val = win32pdh.GetFormattedCounterValue(handle, win32pdh.PDH_FMT_DOUBLE)
                                if val is not None:
                                    celsius = (val / 10.0) - 273.15
                                    if 0.0 < celsius < 150.0:
                                        if self._is_placeholder_zone(("pdh", handle), val):
                                            continue
                                        readings.append(celsius)
                            except: continue

                    if readings:
                        return self._log_temp(max(readings), "PDH ACPI", "Thermal Zone")
                except Exception as e:
                    self.logger.debug("Thermal PDH polling error: %s", e)

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
            for idx, t in enumerate(temps):
                raw = t.CurrentTemperature
                # Standard ACPI: tenths of Kelvin (valid range ~2932-3932 for 20-120°C)
                celsius = (raw / 10.0) - 273.15
                if 0.0 < celsius < 150.0:
                    if self._is_placeholder_zone(("msacpi", idx), float(raw)):
                        continue
                    return self._log_temp(celsius, "MSAcpi", "Thermal Zone")
                # Some OEMs (HP, Dell, Lenovo) return direct Celsius instead
                if 15.0 < raw < 110.0:
                    self.logger.debug("ACPI temp raw=%s interpreted as direct Celsius", raw)
                    return self._log_temp(float(raw), "MSAcpi", "Thermal Zone (direct-C)")
        except Exception as e:
            self.logger.debug("CPU Temp WMI fallback error: %s", e)
            if "RPC server is unavailable" in str(e) or "0x800706ba" in str(e):
                self._wmi = None
        return None

    def _is_placeholder_zone(self, key: Any, raw: float) -> bool:
        """True when an ACPI thermal zone is firmware filling in a field rather than a sensor.

        #275's board answers exactly 290.0 K on every poll (shown as 17 °C); #237's answers 300.0 K
        (27 °C). Real zones report tenths of a kelvin - a Celsius-derived value lands on x.x5 - and
        they drift. So a zone is a placeholder while its reading is a whole ten-kelvin multiple
        (raw % 100 == 0 in tenths) AND it has never reported anything else. The first time a zone's
        value changes it is trusted for good, so a genuine coarse sensor that starts on a round
        value loses at most its first poll, and one that passes through a round value later loses
        nothing.
        """
        first = self._thermal_first_raw.setdefault(key, raw)
        if raw != first:
            self._thermal_moved.add(key)
        if key in self._thermal_moved or raw % 100 != 0:
            return False
        if key not in self._thermal_placeholder_logged:
            self._thermal_placeholder_logged.add(key)
            self.logger.info(
                "Ignoring ACPI thermal zone %s: it reports a fixed %.1f K (%.1f°C) on every poll - "
                "a firmware placeholder, not a sensor (#275). No CPU temperature will be shown "
                "unless a real source (e.g. LibreHardwareMonitor) is available.",
                key, raw / 10.0, raw / 10.0 - 273.15)
        return True

    def _log_temp(self, celsius: float, source: str, sensor: str) -> float:
        """Logs the selected CPU-temperature source once, and again only when it
        changes, so a wrong reading (e.g. #216) is self-diagnosing without flooding
        the per-second poll log. Returns the temperature unchanged."""
        signature = (source, sensor)
        if signature != self._last_temp_source_sig:
            self._last_temp_source_sig = signature
            self.logger.info("CPU temperature: %.1f°C via %s (sensor: %s).", celsius, source, sensor)
        return celsius

    def _update_lhm_notice(self, stats: Dict[str, Any], in_rdp: bool) -> None:
        """Emit ``lhm_not_detected`` once per session when temps/power are wanted but no sensor reads.

        Only a poll that the USER's settings made read temps/power counts: temperatures are shown
        next to CPU/GPU usage, so they need ``monitor_cpu_enabled`` / ``monitor_gpu_enabled`` as well
        as the temps/power switch. ``show_hardware_temps`` defaults to True while CPU/GPU monitoring
        defaults to off, so the switch alone says nothing. Through 2.1.7 the record_hardware_history
        gate made every default install poll CPU temps anyway (and find one, on most machines); once
        recording stopped being a reason to poll (#347), a default config read no sensor at all and
        the "Turn on temperatures" explainer appeared on every launch for a stat nobody had turned on.
        Polls forced by the open Monitor window do not count either - opening a window is not
        asking for temperatures on the taskbar."""
        if self._lhm_notice_emitted:
            return
        wants = (bool(self.config.get('show_hardware_temps', False))
                 or bool(self.config.get('show_hardware_power', False)))
        if not wants:
            return
        user_cpu, user_gpu = hardware_collection_gates(self.config, False)
        if not (user_cpu or (user_gpu and not in_rdp)):
            return
        self._lhm_check_polls += 1
        # Wait 5 polls (~5s) to give LHM time to be detected
        if self._lhm_check_polls >= 5:
            has_any_reading = any(stats.get(k) is not None for k in _SENSOR_STAT_KEYS)
            if not has_any_reading:
                self._lhm_notice_emitted = True
                self.lhm_not_detected.emit()

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

        # Initialize the COM apartment ONCE for this thread (H4). Was done per-poll inside
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
                    self._gpu_query_retry_after = 0.0
                    self._lhm_http.reset_backoff()   # temps/power just switched on: look for LHM now
                    self._cleanup_thermal_query()
                    self._cleanup_power_query()
                    self._wmi_ohm = None   # re-probe LHM/OHM on the next temp poll

                stats = {}

                # 1. Network (Always enabled for core functionality)
                network_counters = psutil.net_io_counters(pernic=True)
                if network_counters:
                    stats['network'] = network_counters
                    # When the counters were SAMPLED (monotonic, wall). The GUI thread may handle two
                    # samples closer together than they were taken; the odometer's sanity cap must be
                    # measured over the real sampling span, not the handling span (#13).
                    stats['network_sampled_at'] = (time.monotonic(), time.time())
                    # Link state for the v8 history (link_events, link speed/duplex), on a slow
                    # sub-cadence. Never fatal: a failing call costs the link record, not the poll.
                    now = time.monotonic()
                    if now - self._last_link_stats_poll >= constants.network.speed.LINK_STATS_POLL_INTERVAL_SEC:
                        self._last_link_stats_poll = now
                        try:
                            stats['network_if_stats'] = psutil.net_if_stats()
                        except Exception as e:
                            self.logger.debug("net_if_stats failed: %s", e)

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
                # Collect only what the user monitors (or the open Monitor window shows). This used to
                # OR in record_hardware_history, polling + recording CPU/GPU/RAM with monitoring off;
                # whether a sample is persisted is now the controller's call (hardware_persist_allowed).
                _collect_cpu, _collect_gpu = hardware_collection_gates(self.config, _force_hw)
                # RAM/VRAM follow their own switches too (G5), not only the CPU/GPU ones.
                _collect_ram, _collect_vram = memory_collection_gates(self.config, _force_hw)

                # 2. CPU / RAM (Optional)
                if _collect_cpu:
                    # non-blocking (percpu=False)
                    stats['cpu'] = psutil.cpu_percent(interval=None)
                    if self.config.get('show_hardware_temps', False) or _force_hw:
                        stats['cpu_temp'] = self._poll_cpu_temperature()
                    if self.config.get('show_hardware_power', False) or _force_hw:
                        stats['cpu_power'] = self._poll_cpu_power()

                if _collect_ram:
                    mem = psutil.virtual_memory()
                    stats['ram_used'] = mem.used / (1024**3) # GB
                    stats['ram_total'] = mem.total / (1024**3) # GB

                # 3. GPU / VRAM (Optional - skipped entirely in RDP sessions). VRAM alone still needs
                # the PDH GPU poll; temperature / power / utilization are only asked for with the GPU on.
                if (_collect_gpu or _collect_vram) and not _in_rdp:
                    try:
                        include_temp = _collect_gpu and (
                            bool(self.config.get('show_hardware_temps', False)) or _force_hw)
                        include_power = _collect_gpu and (
                            bool(self.config.get('show_hardware_power', False)) or _force_hw)
                        gpu = self._poll_gpu_hybrid(include_temp=include_temp, include_power=include_power)

                        if _collect_gpu:
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
                        if gpu.vram_is_shared and gpu.vram_used is not None:
                            # Only set for an iGPU, so a discrete GPU's stats dict is unchanged.
                            stats['vram_is_shared'] = True
                    except Exception as gpu_err:
                        self.logger.warning("GPU polling error (skipped, not counted against circuit breaker): %s", gpu_err)

                # Whole-system / battery power (see _collect_power_stats).
                self._collect_power_stats(stats, _force_hw)

                if stats:
                    self.stats_ready.emit(stats)

                # One-time LHM notice: temps/power wanted on a monitored metric, but no readings.
                self._update_lhm_notice(stats, _in_rdp)

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
        """Initialize the COM apartment ONCE for this thread (WMI/LHM access)."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

    def _cleanup_com(self) -> None:
        """Releases COM apartment initialized for WMI access."""
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def _run_child(self, argv, timeout: float) -> str:
        """Run a short-lived helper (nvidia-smi) and return its stdout, like check_output - but the
        process is held where ``stop()`` can kill it, so a quit never waits out (or orphans) a slow
        one. Raises on a non-zero exit, a timeout (the child is killed), or a kill from stop()."""
        with self._child_lock:
            if not self._is_running:
                raise RuntimeError("stopping")
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, encoding="utf-8",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._child = proc
        try:
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    # Bounded too: a grandchild that inherited the pipe (a launcher stub) keeps it
                    # open after the kill, and an unbounded read would then hang this thread.
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                raise
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, argv, out)
            return out
        finally:
            with self._child_lock:
                self._child = None

    def _kill_child(self) -> None:
        with self._child_lock:
            proc = self._child
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                self.logger.info("Stopped an in-flight helper process at quit.")
            except OSError:
                pass

    def stop(self) -> None:
        """Stops the monitoring loop: kills a helper process still running (nvidia-smi can take
        1.5 s, longer cold), waits a bounded time, and hands a thread that is still inside an
        uninterruptible call (WMI) to the thread reaper rather than letting quit abort on it."""
        self._is_running = False
        self._kill_child()
        self.wait(constants.timeouts.MONITOR_THREAD_STOP_WAIT_MS)
        from netspeedtray.core import thread_reaper
        if not thread_reaper.outlived(self, "The stats monitor thread"):
            self.logger.info("StatsMonitorThread stopped.")
