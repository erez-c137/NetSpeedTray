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

# win32com.client (and wmi) load on first use, not here: see utils/com_utils.py (memory plan 2.5).
# _com_client() is the one seam - the LHM/OHM WMI namespace and the ACPI fallback call it.
from netspeedtray.utils.com_utils import import_wmi, win32com_client as _com_client

import subprocess
import shutil
from functools import lru_cache

from netspeedtray import constants
from netspeedtray.core import gpu_adapters
from netspeedtray.core.lhm_http import LhmGpu, LhmHttpClient, LhmSensor, LhmSnapshot, SourceOutage, gpu_groups
from netspeedtray.utils import secret_store
from netspeedtray.utils.rdp_utils import is_rdp_session
from netspeedtray.utils.network_identity_monitor import NetworkIdentityMonitor

logger = logging.getLogger("NetSpeedTray.StatsMonitorThread")

# LibreHardwareMonitor identifier prefixes that scope a sensor to something that is NOT the CPU.
# The CPU-temperature search falls back to matching sensor *names* (for boards that label the die
# sensor oddly - Ryzen's "Core (Tctl/Tdie)", super-I/O chips under /lpc/), and a name match must
# never out-vote an identifier that says the sensor belongs to another device: NVIDIA's sensor is
# literally named "GPU Core", which matched the "CORE" keyword, so on a hybrid laptop with no
# CPU-die sensor we reported the GPU's temperature as the CPU's (#237).
_NON_CPU_SENSOR_IDENTS: tuple = (
    "/gpu", "/nvme", "/hdd", "/ssd", "/psu", "/battery", "/network", "/ram",
)


# Which one-time sensor-source guidance lines this PROCESS has logged ("empty" = a namespace with 0
# sensors, "none" = no source at all). Module-level, not per thread: the monitor thread is rebuilt
# (settings changes, resume) and each rebuild used to repeat the same advice in the log.
_OHM_GUIDANCE_LOGGED: set = set()


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
    on and CPU (or GPU) off collected no memory data at all (rc.2 G5). Since 2.2.1 that is a real
    setup: RAM shows without CPU monitoring and VRAM without GPU monitoring (a memory-only row), and
    VRAM alone polls only the adapter-memory counters (see run). Recording keeps its own consent gate
    (controller.hardware_persist_allowed)."""
    collect_cpu, collect_gpu = hardware_collection_gates(config, force)
    return (collect_cpu or bool(config.get('monitor_ram_enabled', False)),
            collect_gpu or bool(config.get('monitor_vram_enabled', False)))


class GpuEngineUtilization(NamedTuple):
    """One `\\GPU Engine(*)\\Utilization Percentage` array, parsed ONCE per poll (2.2.1): the taskbar
    readout, the hover card's per-GPU rows and the Optimus gate all read it. Parsing it for each of
    them cost ~6 ms a pass on a big array (hundreds of processes x engines), three times a poll on a
    hybrid laptop - doubled at the 0.5 s rate. Built by parse_gpu_engine_utilization; the rules are
    aggregate_gpu_engine_utilization's."""
    per_adapter: Dict[Tuple[int, int], float]   # adapter LUID -> its busiest engine (processes summed)
    unparsed: Tuple[float, ...]                 # instances no engine can be read from: one engine each

    def busiest(self, luid: Optional[Tuple[int, int]] = None) -> float:
        """The busiest engine on any adapter (unparsed instances included), or on ``luid`` only."""
        if luid is not None:
            return self.per_adapter.get(tuple(luid), 0.0)
        return max(max(self.per_adapter.values(), default=0.0), max(self.unparsed, default=0.0))


def parse_gpu_engine_utilization(samples: Dict[Any, Any]) -> GpuEngineUtilization:
    """Parse a `\\GPU Engine(*)\\Utilization Percentage` array once - see
    aggregate_gpu_engine_utilization for the rules."""
    engines: Dict[Tuple[int, int, int, int], float] = {}
    unparsed: List[float] = []
    match_instance = constants.gpu.ENGINE_INSTANCE_PATTERN.match
    for instance, value in samples.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            continue
        match = match_instance(instance) if isinstance(instance, str) else None
        if not match:
            unparsed.append(0.0 + value)   # as the per-engine sum would hold it (-0.0 -> 0.0)
            continue
        luid_high, luid_low, phys, eng = match.groups()
        key = (int(luid_high, 16), int(luid_low, 16), int(phys), int(eng))
        engines[key] = engines.get(key, 0.0) + value
    per_adapter: Dict[Tuple[int, int], float] = {}
    for key, value in engines.items():
        per_adapter[key[:2]] = max(per_adapter.get(key[:2], 0.0), value)
    return GpuEngineUtilization(per_adapter, tuple(unparsed))


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

    One-shot form; a poll parses its array once (parse_gpu_engine_utilization) and reads that.
    """
    return parse_gpu_engine_utilization(samples).busiest(luid)


def per_adapter_engine_utilization(samples: Dict[Any, Any]) -> Dict[Tuple[int, int], float]:
    """{adapter LUID: its busiest engine's utilization} from the same array, by the same rules as
    aggregate_gpu_engine_utilization with a picked ``luid`` - each adapter's value is exactly what
    picking it would read (the hover card's per-GPU rows, #236). Unparseable instances belong to no
    adapter and are left out. Not clamped here. One-shot form, like aggregate_gpu_engine_utilization."""
    return dict(parse_gpu_engine_utilization(samples).per_adapter)


def _smi_number(parts: List[str], index: int, lo: float, hi: float) -> Optional[float]:
    """Field ``index`` of an nvidia-smi CSV line as a float strictly inside (lo, hi), else None
    ("[N/A]", "[Not Supported]", missing, out of range)."""
    try:
        value = float(parts[index].strip())
    except (IndexError, ValueError):
        return None
    return value if lo < value < hi else None


def _is_nvidia_group(gpu: LhmGpu) -> bool:
    return "nvidia" in f"{gpu.hardware_id} {gpu.hardware_name}".casefold()


def _is_intel_group(gpu: LhmGpu) -> bool:
    return "intel" in f"{gpu.hardware_id} {gpu.hardware_name}".casefold()


class GpuPollResult(NamedTuple):
    """Structured result from GPU polling, replacing opaque 4-tuple."""
    # None = no reading this poll (the first sample after a query rebuild): the readout is held.
    util: Optional[float] = 0.0
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    temp: Optional[float] = None
    power: Optional[float] = None
    # True when vram_used/vram_total are an integrated GPU's SHARED system memory (no adapter has
    # dedicated memory - see _poll_gpu_hybrid), not dedicated video memory.
    vram_is_shared: bool = False
    # A PDH engine instance or a hardware-scoped LHM GPU sensor proves presence.
    present: bool = False
    # A PICKED NVIDIA dGPU on a hybrid laptop (2.2.1): True = idle by the Optimus gate AND its temperature /
    # power are "—" because of that (_gpu_idle_for), False = not that, None = not judged / not that case.
    idle: Optional[bool] = None


class GpuCard(NamedTuple):
    """One GPU of a multi-GPU PC, as the hover card lists it (#236). Built by _gpu_cards from what
    the poll already read - never part of GpuPollResult, never persisted. None = no reading ("—")."""
    luid: Tuple[int, int]
    name: str                         # display_name, numbered when two cards share one
    integrated: bool
    util: Optional[float] = None
    vram_used_gib: Optional[float] = None
    vram_total_gib: Optional[float] = None
    vram_shared: bool = False         # an iGPU's shared system memory, not dedicated video memory
    temp: Optional[float] = None
    power: Optional[float] = None
    picked: bool = False              # the GPU picked in Settings: the one the taskbar shows
    # A hybrid laptop's NVIDIA dGPU (2.2.1, _gpu_idle_for): True = idle by the Optimus gate and its
    # temperature / power blank because of it, False = judged but not that (awake, or another source such
    # as LHM still reads it), None = not judged (another GPU, a desktop, temperature and power both off).
    idle: Optional[bool] = None


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
            
        # Stop flag as an Event (2.2.1): the loop waits ONCE per period on it instead of waking
        # every 0.1 s to look at a bool, and stop() still ends that wait at once. _is_running stays
        # the public face (a property over this event).
        self._stop_event = threading.Event()
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
        # Monotonic time before which a failed LHM/OHM WMI probe is not repeated (0.0 = probe now).
        self._ohm_retry_at: float = 0.0
        # Monotonic time of the first probe (None = none yet): the start of its every-poll grace.
        self._ohm_first_probe_at: Optional[float] = None
        # Monotonic time of the first failed re-probe of a connection that WAS working (None = not
        # lost): an outage longer than NOT_DETECTED_GRACE_SEC backs off like the never-found case.
        self._ohm_lost_probe_at: Optional[float] = None
        # One INFO line when a WMI connection that was working is dropped mid-session, one when it is
        # back (rate-limited if it flaps) - see SourceOutage.
        self._ohm_outage = SourceOutage(self.logger, "the LibreHardwareMonitor/OpenHardwareMonitor WMI source")
        # LHM 0.9.5+ removed its WMI provider; its built-in web server is the
        # primary sensor source. The client's short cache shares one data.json
        # response across CPU/GPU temperature and power reads in this poll.
        self._lhm_http = LhmHttpClient()
        # Settings > Hardware's port and optional credentials (G2/G3). The password lives in config only
        # as a DPAPI blob; it is decrypted here, on the polling thread, and only when the blob changes.
        self._lhm_password_blob: Optional[str] = None
        self._lhm_password: str = ""
        self._apply_lhm_settings()
        self._ohm_guidance_logged: bool = False  # One-time "no sensor source" guidance, logged only when NO namespace connects
        self._last_temp_source_sig: Optional[Tuple[str, str]] = None  # (source, sensor) of the last-logged CPU-temp read; re-logged only on change (#216)
        # ACPI thermal-zone placeholder detection (#237, #275): first raw value seen per zone, the
        # zones that have moved since (trusted for good), and the ones already logged as placeholders.
        self._thermal_first_raw: Dict[Any, float] = {}
        self._thermal_moved: set = set()
        self._thermal_placeholder_logged: set = set()
        self._lhm_notice_emitted: bool = False  # One-time notification flag
        self._lhm_check_since: Optional[float] = None  # monotonic ts of the first poll that wanted a sensor
        self._network_identity_monitor = NetworkIdentityMonitor()
        self._last_emitted_network_identity = None
        self._network_identity_update_requested = threading.Event()
        self._network_identity_update_lock = threading.Lock()
        self._com_initialized = False
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
        # Failure backoff, shared by both queries (one binary): failures in a row, and the earliest
        # monotonic time nvidia-smi may run again (0.0 = no backoff).
        self._nvidia_fail_streak: int = 0
        self._nvidia_retry_at: float = 0.0
        # Hybrid (Optimus) gate, 2.2.1: when our last nvidia-smi call ended, the last time the NVIDIA
        # dGPU showed engine activity of its own (None = never), nvidia-smi's cadence this poll, the
        # laptop-with-battery fact (cached once known), whether the gate was open on the last poll (the hover list follows
        # it), whether the dGPU's sensor is a known source that is just asleep (the LHM notice), the
        # length of the last PDH sample window, and the one-time "no utilization counter" log.
        self._nvidia_smi_ran_at: Optional[float] = None
        self._nvidia_active_at: Optional[float] = None
        # nvidia-smi's cadence this poll: _NVIDIA_POLL_INTERVAL_SEC, or the slower blind-fallback one on
        # battery (a hybrid whose GPU utilization cannot be read, owner decision 2026-11-11).
        self._nvidia_interval_now: float = self._NVIDIA_POLL_INTERVAL_SEC
        self._mobile_battery: Optional[bool] = None
        self._nvidia_gate_open: bool = True
        self._gpu_sensor_asleep: bool = False
        self._gpu_window_sec: float = 0.0
        self._nvidia_gate_blind_logged: bool = False
        # Whether the last DXGI list had an NVIDIA card - its going away drops nvidia-smi's total.
        self._nvidia_dxgi_seen: bool = False
        # monotonic time of the last \GPU Engine CollectQueryData: the next utilization sample covers
        # (this, next collection], which is how a sample that spans our own nvidia-smi call is told.
        self._gpu_collect_at: Optional[float] = None
        # The first utilization sample after a query (re)build covers only the moment between the
        # priming collection and the next one - a near-zero window whose value is noise (2.2.1). That
        # poll holds the readout (util None: the taskbar keeps its last value) and is not counted by
        # the Optimus gate. Set by _init_gpu_query, cleared by the next collection.
        self._gpu_util_settling: bool = False
        # The hover card's "Idle" tag (2.2.1): (the NVIDIA adapters the Optimus gate judged on the last
        # poll - empty = not judged, whether it found them idle with nvidia-smi healthy). Kept as is over
        # ONE poll whose engine array could not be read (the blind path), so a transient PDH failure
        # does not take the tag's row away from an open card and rebuild it twice.
        self._gpu_idle_gate: Tuple[frozenset, bool] = (frozenset(), False)
        self._gpu_idle_gate_kept: bool = False

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
        # What the last GPU poll read, kept for _gpu_cards (the hover card's per-GPU rows, #236) so the
        # list costs no extra PDH read and no subprocess: (util array, dedicated MiB by instance or
        # None, shared MiB by instance, LHM snapshot, picked adapter, its temp, its power).
        self._gpu_poll_inputs: Optional[tuple] = None
        # The WMI sensor groups the last poll built (LHM 0.9.4 / OHM), () when WMI was not asked.
        self._gpu_wmi_groups: Tuple[LhmGpu, ...] = ()
        # (vendor, integrated) per listed card, last logged - the list is logged once per change.
        self._gpu_cards_sig: Optional[tuple] = None
        # Set by update_config (GUI thread); the PDH handles are owned by run()'s thread, so the actual
        # cleanup/re-init is deferred to the loop (see update_config / run).
        self._hw_queries_dirty: bool = False
        # Whether the GPU query should carry the \GPU Engine(*) utilization wildcard - by far its most
        # expensive counter (one instance per process per engine). VRAM shown without GPU monitoring
        # (2.2.1) needs only the adapter-memory counters, so run() clears this then and the query is
        # rebuilt without it; _gpu_query_has_util records what the live query was built with.
        self._gpu_util_wanted: bool = True
        self._gpu_query_has_util: bool = True

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

    def _apply_lhm_settings(self) -> None:
        """Point the LHM client at the configured port, with the configured credentials. Runs at init
        and on run()'s thread after update_config - never concurrently with a poll."""
        L = constants.lhm
        try:
            port = int(self.config.get("lhm_port", L.HTTP_PORT))
        except (TypeError, ValueError):
            port = L.HTTP_PORT
        if not L.MIN_PORT <= port <= L.MAX_PORT:
            port = L.HTTP_PORT
        username = str(self.config.get("lhm_username", "") or "")
        blob = str(self.config.get("lhm_password_protected", "") or "")
        if blob != self._lhm_password_blob:
            self._lhm_password_blob = blob
            self._lhm_password = (secret_store.unprotect(blob) or "") if blob else ""
        self._lhm_http.configure(port, username, self._lhm_password)

    def _stop_flag(self) -> threading.Event:
        """The stop event, created on first use (tests build this thread with __new__)."""
        ev = self.__dict__.get("_stop_event")
        if ev is None:
            ev = self._stop_event = threading.Event()
        return ev

    @property
    def _is_running(self) -> bool:
        return not self._stop_flag().is_set()

    @_is_running.setter
    def _is_running(self, value: bool) -> None:
        # False from any thread (stop(), or a test inside a poll) also ends the wait in progress.
        if value:
            self._stop_flag().clear()
        else:
            self._stop_flag().set()

    def _wait_for_stop(self, seconds: float) -> bool:
        """Sleep up to ``seconds`` in a single wait; returns at once when stopping. True = stopping."""
        if seconds <= 0:
            return not self._is_running
        return self._stop_flag().wait(seconds)

    @property
    def lhm_status(self) -> str:
        """The LHM web-server client's last result (constants.lhm.STATUS_*), for Settings > Hardware."""
        return self._lhm_http.status

    @property
    def lhm_port(self) -> int:
        return self._lhm_http.port

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
            # Skipped for VRAM alone (2.2.1): memory needs only the adapter-memory counters below.
            self._gpu_query_has_util = bool(self._gpu_util_wanted)
            if self._gpu_query_has_util:
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
            self._gpu_collect_at = time.monotonic()
            self._gpu_util_settling = self._gpu_util_counter is not None
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
            self._gpu_collect_at = None
            self._gpu_util_settling = False
            # _gpu_engine_seen is deliberately NOT reset: a settings change or a resume rebuilds
            # the query, and forgetting that this machine has a GPU would blink the Monitor's GPU
            # tiles out until the next non-idle tick.

    def _poll_gpu_hybrid(self, include_temp: bool = True, include_power: bool = False,
                         memory_only: bool = False) -> GpuPollResult:
        """
        Collects GPU stats using a hybrid approach:
        - Utilization & VRAM via Universal PDH (all vendors)
        - Temperature via LHM web server (v0.9.5+) → LHM v0.9.4/OHM WMI (all vendors) → nvidia-smi (NVIDIA)
        - Power via LHM web server → LHM/OHM WMI (all vendors) → nvidia-smi (NVIDIA) → PDH RAPL PP1 (Intel iGPU)

        ``memory_only`` (VRAM shown without GPU monitoring, 2.2.1): never starts nvidia-smi - not even
        for the total VRAM. The total is then nvidia-smi's if an earlier poll already cached it, else
        DXGI's dedicated size (_dxgi_vram_total, any vendor; a picked card always used its own), so the
        readout keeps its "/12.0G" and the side bar's compact "VRAM / 57%" has a total.
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
        self._gpu_wmi_groups = ()

        # The utilization sample read below covers (window_start, this collection] (Optimus gate).
        window_start = self._gpu_collect_at
        # The first sample after a (re)build (see _gpu_util_settling): held, and not judged by the gate.
        settling = False
        if self._gpu_query:
            try:
                win32pdh.CollectQueryData(self._gpu_query)
                self._gpu_collect_at = time.monotonic()
                settling, self._gpu_util_settling = self._gpu_util_settling, False

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
        # Parsed once: the readout here, the Optimus gate below and the hover list (_gpu_cards) all
        # read this one structure (None = no array, or it could not be parsed).
        engine_util: Optional[GpuEngineUtilization] = None
        if util_arr is not None:
            try:
                engine_util = parse_gpu_engine_utilization(util_arr)
                util_pct = engine_util.busiest(selected.luid if selected is not None else None)
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
        # The sensor group (LHM web server or WMI) that supplied a reading. Once one has, every other
        # reading comes from that same card or is N/A - never another adapter's sensor (#236).
        source_gpu = None

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
            source_gpu = lhm_gpu
            if include_temp and lhm_gpu.temperature is not None:
                temp_c = lhm_gpu.temperature
                need_smi_temp = False
            if include_power and lhm_gpu.power is not None:
                power_w = lhm_gpu.power
                need_smi_power = False

        if (include_temp and temp_c is None) or (include_power and power_w is None):
            self._init_ohm_wmi()
            if self._wmi_ohm:
                # 3a/3b. LHM v0.9.4 / OHM WMI (all vendors). Grouped per GPU exactly like the web
                # server's tree: taking the first 'gpu' sensor of each type separately read an APU's
                # "GPU VR SoC" as the temperature beside the dGPU's power (#236).
                wmi_gpu = self._wmi_gpu_group(selected, source_gpu, include_temp and temp_c is None,
                                              include_power and power_w is None)
                if wmi_gpu is not None:
                    source_gpu = source_gpu or wmi_gpu
                    if include_temp and temp_c is None and wmi_gpu.temperature is not None:
                        temp_c = wmi_gpu.temperature
                        need_smi_temp = False
                        self.logger.debug("LHM/OHM GPU temp from sensor '%s': %.1f°C",
                                          wmi_gpu.temperature_sensor, temp_c)
                    if include_power and power_w is None and wmi_gpu.power is not None:
                        power_w = wmi_gpu.power
                        need_smi_power = False
                        self.logger.debug("LHM/OHM GPU power from sensor '%s': %.1fW",
                                          wmi_gpu.power_sensor, power_w)

        # nvidia-smi is NVIDIA's: in Automatic without an adapter, it only fills in for a sensor group
        # that IS the NVIDIA card (or when there is no sensor group at all).
        if source_gpu is not None and not _is_nvidia_group(source_gpu):
            need_smi_temp = need_smi_power = False

        # 4. nvidia-smi fallback for temp/power (vram_total comes as bonus). The subprocess
        #    runs on a slow sub-cadence; values are cached and reused on intervening polls so
        #    a synchronous (up to 1.5s) nvidia-smi call never stalls the network readout.
        # Total VRAM is a property of the card, not a reading, so it is fetched once and cached for
        # the life of the process - which is why asking for it here costs nothing on later polls.
        # It has to be its own reason to call nvidia-smi: this used to run only when temp or power
        # were needed FROM it, so with LibreHardwareMonitor supplying those, nvidia-smi never ran and
        # the total silently never arrived. Users saw VRAM as used-only with LHM open and used/total
        # with it closed, which is a bizarre thing to have to notice (#250).
        # Hybrid (Optimus) laptop, 2.2.1: nvidia-smi wakes a sleeping dGPU, so temperature / power are
        # asked only while it is awake anyway (_nvidia_awake) - asleep, they are N/A. Desktops and
        # NVIDIA-only laptops: unchanged.
        smi_pick: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
        hybrid = bool(self._nvidia_smi_path) and smi_matches and self._is_hybrid_laptop()
        gate_open = may_query = True
        gate_luids: List[Tuple[int, int]] = []   # the NVIDIA adapters the gate judged this poll
        self._nvidia_interval_now = self._NVIDIA_POLL_INTERVAL_SEC
        if hybrid:
            if self._gpu_util_counter is None or util_arr is None:
                # No utilization to judge by: fail OPEN - readings never go blank - but on battery at
                # a slower cadence (each call may wake the dGPU). Power source unknown: as on AC.
                if self._on_battery_power():
                    self._nvidia_interval_now = max(self._NVIDIA_POLL_INTERVAL_SEC,
                                                    constants.gpu.HYBRID_BLIND_BATTERY_POLL_SEC)
                # VRAM shown without GPU monitoring never starts nvidia-smi (and has no utilization
                # counter by design): the line would be false there, and the once-per-session flag
                # must stay unburned for when GPU monitoring is turned on (2.2.1 review).
                if not memory_only and not self._nvidia_gate_blind_logged:
                    self._nvidia_gate_blind_logged = True
                    self.logger.info("GPU utilization unavailable; nvidia-smi is polled without the "
                                     "sleep check on this hybrid PC (every %d s on battery).",
                                     int(constants.gpu.HYBRID_BLIND_BATTERY_POLL_SEC))
            elif include_temp or include_power:
                nvidia_luids = [a.luid for a in gpu_adapters.hardware_adapters(self._gpu_adapters or [])
                                if a.vendor_id == constants.gpu.VENDOR_NVIDIA] if selected is None else [selected.luid]
                gate_open, may_query = self._nvidia_awake(
                    engine_util.per_adapter if engine_util is not None else {}, nvidia_luids, window_start,
                    settling=settling)
                gate_luids = nvidia_luids
            # else: GPU temperature and power both off - nvidia-smi cannot be asked this poll (the total
            # is never a reason on a hybrid), so the gate is not judged (2.2.1). Nothing else reads it
            # then: the hover list drops temperature / power with the same switches, the LHM notice
            # needs one of them on, and the next poll that wants them judges its own sample first.
            if not gate_open:
                need_smi_temp = need_smi_power = False
                # Asleep: no reading is kept for later - not on the taskbar, not in the hover list.
                self._nvidia_cache_temp = self._nvidia_cache_power = None
                pick_name, pick = self._nvidia_pick_cache
                self._nvidia_pick_cache = (pick_name, (None, None, pick[2]))
        self._nvidia_gate_open = gate_open
        # A known sensor that is only asleep (no failure so far): not "no sensor" (the LHM notice).
        self._gpu_sensor_asleep = hybrid and not gate_open and self._nvidia_fail_streak == 0
        # Which cards that verdict is about - the hover card's "Idle" tag (2.2.1). A poll that could not
        # read the engine array keeps the last verdict once (see _gpu_idle_gate).
        blind_blip = (hybrid and self._gpu_util_counter is not None and util_arr is None
                      and (include_temp or include_power))
        if blind_blip and not self._gpu_idle_gate_kept:
            self._gpu_idle_gate_kept = True
        else:
            self._gpu_idle_gate_kept = False
            self._gpu_idle_gate = (frozenset(gate_luids), self._gpu_sensor_asleep)
        if selected is None:
            # On a hybrid the total alone is never a reason to call (owner decision 2026-11-11: zero
            # wakes at app start): DXGI's below until a temperature / power query brings nvidia-smi's.
            need_smi_vram_total = self._nvidia_cache_vram_total is None and not hybrid and not memory_only
            if self._nvidia_smi_path and (need_smi_temp or need_smi_power or need_smi_vram_total):
                now_mono = time.monotonic()
                if ((now_mono - self._nvidia_last_poll) >= self._nvidia_interval_now
                        and now_mono >= self._nvidia_retry_at and may_query):
                    # Stamp first: a hung/failed call then waits the full interval before retry,
                    # instead of re-running (and re-stalling) every poll.
                    self._nvidia_last_poll = now_mono
                    try:
                        query_fields = "temperature.gpu,memory.total,power.draw"
                        output = self._query_nvidia_smi(
                            [self._nvidia_smi_path, f"--query-gpu={query_fields}", "--format=csv,noheader,nounits"])
                        parts = output.strip().split('\n')[0].split(',')
                        # "[N/A]" or an out-of-range value clears the reading (N/A), never keeps the
                        # last good one; sanity ranges like every other temp / power path.
                        self._nvidia_cache_temp = _smi_number(parts, 0, 0.0, 150.0)
                        _total = _smi_number(parts, 1, 0.0, float("inf"))
                        if _total is not None:   # a card property: a blank field keeps the known total
                            self._nvidia_cache_vram_total = _total  # MiB
                        self._nvidia_cache_power = _smi_number(parts, 2, 0.0, 1000.0)
                    except Exception as e:
                        self.logger.debug("nvidia-smi temp/power query failed: %s", e)
                        # Sensor likely dropped out - invalidate the cache so readings fall back to
                        # N/A instead of freezing the last good value forever (the N/A design intent).
                        # The total is kept: it is a property of the card, not a reading.
                        self._nvidia_cache_temp = None
                        self._nvidia_cache_power = None

                # Apply the (possibly just-refreshed) cached values to whatever still needs a source.
                if need_smi_temp and temp_c is None and self._nvidia_cache_temp is not None:
                    temp_c = self._nvidia_cache_temp
                if need_smi_power and power_w is None and self._nvidia_cache_power is not None:
                    power_w = self._nvidia_cache_power
        elif smi_matches and self._nvidia_smi_path and not memory_only:
            # A picked NVIDIA card: its OWN nvidia-smi line (by name), never the first line. On a
            # hybrid, only while it is awake: asleep, its cached total (if any) and DXGI's below.
            if hybrid and not (need_smi_temp or need_smi_power):
                cached_name, cached = self._nvidia_pick_cache
                smi_pick = (None, None, cached[2] if cached_name == selected.name.strip().casefold() else None)
            else:
                smi_pick = self._nvidia_smi_for(selected, need_smi_temp or need_smi_power, may_query)
            if need_smi_temp and temp_c is None:
                temp_c = smi_pick[0]
            if need_smi_power and power_w is None:
                power_w = smi_pick[1]

        # Outside the gate on purpose: once the total is known it applies on every poll, whatever the
        # reason nvidia-smi did or did not run this time round.
        if selected is None:
            # The cached total survives a failed nvidia-smi call (2.2.1) - so it must not outlive the
            # card: an NVIDIA eGPU unplugged mid-session would otherwise leave its "/ 8.0 GB" on the
            # remaining GPU's memory. Dropped only when DXGI HAD an NVIDIA card and no longer does: a
            # card DXGI never lists (a TCC-mode compute GPU) keeps nvidia-smi's total, as before.
            if self._gpu_adapters is not None:
                nvidia_listed = any(a.vendor_id == constants.gpu.VENDOR_NVIDIA
                                    for a in gpu_adapters.hardware_adapters(self._gpu_adapters))
                if self._nvidia_dxgi_seen and not nvidia_listed:
                    self._nvidia_cache_vram_total = None
                self._nvidia_dxgi_seen = nvidia_listed
            if vram_total is None and self._nvidia_cache_vram_total is not None:
                vram_total = self._nvidia_cache_vram_total
            elif memory_only and vram_total is None:
                vram_total = self._dxgi_vram_total()   # VRAM alone: DXGI, any vendor, no subprocess
            elif vram_total is None and self._nvidia_smi_path:
                # nvidia-smi has not given a total (a hybrid's asleep dGPU, or nvidia-smi failing):
                # the one NVIDIA card's DXGI dedicated size - what Task Manager shows - not N/A.
                nvidia = [a for a in gpu_adapters.hardware_adapters(self._gpu_adapters or [])
                          if a.vendor_id == constants.gpu.VENDOR_NVIDIA]
                if len(nvidia) == 1 and nvidia[0].dedicated_video_bytes > 0:
                    vram_total = nvidia[0].dedicated_video_bytes / (1024.0 * 1024.0)

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
        # Without an adapter, it is not paired with another card's temperature either (#236).
        if selected is not None:
            rapl_matches = (selected.vendor_id == constants.gpu.VENDOR_INTEL
                            and gpu_adapters.is_integrated(selected))
        elif source_gpu is not None:
            rapl_matches = _is_intel_group(source_gpu)
        else:
            rapl_matches = temp_c is None   # a temperature here came from nvidia-smi: NVIDIA's
        rapl_w = None   # the Intel iGPU's RAPL PP1 reading, when it is what power_w shows
        if include_power and power_w is None and rapl_matches:
            try:
                self._init_power_query()
                if self._power_query and self._power_pp1_counter is not None:
                    win32pdh.CollectQueryData(self._power_query)
                    _, val = win32pdh.GetFormattedCounterValue(self._power_pp1_counter, win32pdh.PDH_FMT_DOUBLE)
                    if val is not None and val > 0:
                        power_w = rapl_w = val / 1000.0  # mW to W
            except: pass

        # Clamp utilization to [0, 100] - a per-engine sum can momentarily read >100%.
        util_pct = max(constants.gpu.UTIL_MIN_PERCENT, min(constants.gpu.UTIL_MAX_PERCENT, util_pct))
        # The first sample after a query rebuild is not a reading: held (None), never a blip (2.2.1).
        util: Optional[float] = None if settling else util_pct
        # The inputs, for the hover card's per-GPU list (_gpu_cards): read once here, reused there.
        self._gpu_poll_inputs = (engine_util, dedicated_mib if dedicated_read_ok else None, shared_mib,
                                 lhm_snapshot, selected, temp_c, power_w, rapl_w)
        # LHM's HardwareId-scoped sensor group is independent evidence that a physical GPU
        # exists. Keeping the row visible with 0% utilization is intentional when PDH is
        # unavailable: temperature/power remain useful and are not fabricated.
        return GpuPollResult(util, vram_used, vram_total, temp_c, power_w,
                             vram_is_shared=vram_is_shared,
                             present=self._gpu_engine_seen or self._lhm_gpu_seen,
                             idle=(self._gpu_idle_for(selected.luid, temp_c, power_w)
                                   if selected is not None else None))

    def _dxgi_vram_total(self) -> Optional[float]:
        """Total VRAM (MiB) from DXGI's static adapter facts, for VRAM shown without GPU monitoring in
        Automatic (no nvidia-smi then, and no cached total): what Task Manager shows as dedicated
        memory. With nvidia-smi on the box, its first NVIDIA card - the card nvidia-smi's total would
        have come from. Otherwise every discrete adapter, summed like the Automatic used value (AMD,
        Intel Arc, NVIDIA without nvidia-smi). None without DXGI facts or a discrete adapter: an
        integrated GPU keeps its shared-memory reading (_shared_vram_reading), a VM its used-only one.

        Only for memory_only: Automatic with the GPU monitored is pinned by test_gpu_automatic_golden
        (used-only on AMD / Intel Arc), and the widget shows that row's percent, not memory's."""
        real = gpu_adapters.hardware_adapters(self._gpu_adapters or [])
        if self._nvidia_smi_path:
            nvidia = [a for a in real
                      if a.vendor_id == constants.gpu.VENDOR_NVIDIA and a.dedicated_video_bytes > 0]
            if nvidia:
                return nvidia[0].dedicated_video_bytes / (1024.0 * 1024.0)
        discrete = [a for a in real if not gpu_adapters.is_integrated(a)]
        if not discrete:
            return None
        return sum(a.dedicated_video_bytes for a in discrete) / (1024.0 * 1024.0)

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

    def _gpu_cards(self, include_temp: bool, include_power: bool) -> Tuple[GpuCard, ...]:
        """Every real GPU, for the hover card's list (#236) - () unless the machine has two or more.

        Built from what the last _poll_gpu_hybrid read (no PDH read, no WMI query, never nvidia-smi):
        utilization per adapter (per_adapter_engine_utilization), its own dedicated memory - or, for an
        integrated GPU, its shared memory against its shared budget - and its own sensor group
        (LhmSnapshot.gpu_for: None when two cards cannot be told apart, so "—", never the other
        card's). The picked GPU's temperature / power are exactly what the taskbar shows. nvidia-smi
        readings are reused only from a cache that is the card's for sure (Automatic with ONE NVIDIA
        card, or the picked card's own line) and recent. Discrete before integrated, then DXGI order:
        a static order that never reshuffles at 1 Hz."""
        real = gpu_adapters.hardware_adapters(self._gpu_adapters or [])
        if len(real) < 2 or self._gpu_poll_inputs is None:
            return ()
        (engine_util, dedicated_mib, shared_mib, snapshot, selected, sel_temp, sel_power,
         rapl_w) = self._gpu_poll_inputs
        # Automatic showing RAPL PP1 as the GPU power: that IS the Intel iGPU's, so its row shows the
        # same value as the taskbar (one source of truth) - only when exactly one Intel iGPU exists.
        intel_igpus = [a for a in real if a.vendor_id == constants.gpu.VENDOR_INTEL
                       and gpu_adapters.is_integrated(a)]
        rapl_card = intel_igpus[0] if rapl_w is not None and selected is None and len(intel_igpus) == 1 else None
        cards = sorted(real, key=gpu_adapters.is_integrated)   # stable: DXGI order within each side
        utils = engine_util.per_adapter if engine_util is not None else None   # the poll's parse
        groups = snapshot.gpus if snapshot is not None and snapshot.gpus else self._gpu_wmi_groups
        sensors = LhmSnapshot(gpus=tuple(groups)) if groups else None
        nvidia = [a for a in real if a.vendor_id == constants.gpu.VENDOR_NVIDIA]
        now = time.monotonic()
        # Recent (a slow update rate stretches nvidia-smi's real cadence), and - on a hybrid - only
        # while the Optimus gate is open: the list never shows a reading the taskbar does not.
        fresh_window = constants.gpu.LIST_SMI_CACHE_MAX_AGE_POLLS * max(self._nvidia_interval_now,
                                                                        self._gpu_window_sec)
        auto_smi_fresh = (selected is None and len(nvidia) == 1 and self._nvidia_last_poll > 0.0
                          and self._nvidia_gate_open and now - self._nvidia_last_poll <= fresh_window)
        mib = 1024.0 * 1024.0
        out = []
        for a, name in zip(cards, gpu_adapters.numbered_display_names(c.name for c in cards)):
            integrated = gpu_adapters.is_integrated(a)
            picked = selected is not None and a.luid == selected.luid
            util = None
            if utils is not None:
                util = max(constants.gpu.UTIL_MIN_PERCENT,
                           min(constants.gpu.UTIL_MAX_PERCENT, utils.get(a.luid, 0.0)))

            # Memory: an iGPU's shared usage (no shared reading: "—" - its tiny dedicated carve-out
            # would read as a misleading "VRAM 0.0 / 0.1 GB"); any other GPU's own dedicated usage.
            used = total = None
            shared = integrated
            if integrated:
                mine_shared = [v for inst, v in (shared_mib or {}).items()
                               if gpu_adapters.pdh_instance_luid(inst) == a.luid]
                if mine_shared:
                    used = sum(mine_shared)
                    total = a.shared_system_bytes / mib if a.shared_system_bytes > 0 else self._half_ram_mib()
            elif dedicated_mib is not None:
                mine = [v for inst, v in dedicated_mib.items() if gpu_adapters.pdh_instance_luid(inst) == a.luid]
                if mine:
                    used = sum(mine)
                    smi_total = None
                    if a.vendor_id == constants.gpu.VENDOR_NVIDIA:
                        if picked and self._nvidia_pick_cache[0] == a.name.strip().casefold():
                            smi_total = self._nvidia_pick_cache[1][2]
                        elif selected is None and len(nvidia) == 1:
                            smi_total = self._nvidia_cache_vram_total
                    total = smi_total or (a.dedicated_video_bytes / mib if a.dedicated_video_bytes > 0 else None)

            temp = power = None
            if picked:
                temp, power = sel_temp, sel_power
            else:
                group = sensors.gpu_for(a.vendor_id, a.name, integrated) if sensors is not None else None
                if group is not None:
                    temp, power = group.temperature, group.power
                if a is rapl_card:
                    power = rapl_w
                if auto_smi_fresh and a is nvidia[0]:
                    temp = temp if temp is not None else self._nvidia_cache_temp
                    power = power if power is not None else self._nvidia_cache_power
            temp = temp if include_temp else None
            power = power if include_power else None
            out.append(GpuCard(
                luid=a.luid, name=name, integrated=integrated, util=util,
                vram_used_gib=used / 1024.0 if used is not None else None,
                vram_total_gib=total / 1024.0 if total else None, vram_shared=shared,
                temp=temp, power=power, picked=picked, idle=self._gpu_idle_for(a.luid, temp, power)))

        sig = tuple((hex(a.vendor_id), gpu_adapters.is_integrated(a)) for a in cards)
        if sig != self._gpu_cards_sig:
            self._gpu_cards_sig = sig
            # Vendors and kinds only - adapter names stay out of the log (the support bundle carries it).
            self.logger.info("Hover card GPU list: %d GPUs (%s)", len(sig), ", ".join(
                f"{v} {'integrated' if i else 'discrete'}" for v, i in sig))
        return tuple(out)

    def _wmi_gpu_group(self, adapter: Optional[gpu_adapters.AdapterInfo], same_as: Optional[LhmGpu],
                       want_temp: bool, want_power: bool) -> Optional[LhmGpu]:
        """LHM v0.9.4 / OHM WMI: the ONE GPU sensor group for ``adapter`` (or, without one, the
        best-ranked - discrete first), its temperature and power from that card only (#236). Only the
        sensor types still wanted are queried. ``same_as``: a group the web server already supplied -
        WMI may only complete that same card. A failed temperature query drops the connection
        (re-probed next poll) only when the power query failed too or was not asked; a temperature-only
        error keeps the power reading. A power-only error never drops it (as before)."""
        rows: List[LhmSensor] = []
        wanted = [t for t, want in (("Temperature", want_temp), ("Power", want_power)) if want]
        failed: List[str] = []
        for sensor_type in wanted:
            try:
                sensors = self._wmi_ohm.ExecQuery(
                    f"SELECT Value, Identifier, Name FROM Sensor WHERE SensorType='{sensor_type}'")
                for s in sensors:
                    identifier = str(getattr(s, 'Identifier', '')).lower()
                    if 'gpu' not in identifier or not self._gpu_sensor_matches(identifier, adapter):
                        continue
                    try:
                        value = float(s.Value)
                    except (TypeError, ValueError):
                        continue
                    rows.append(LhmSensor(name=str(getattr(s, 'Name', '') or ''),
                                          sensor_type=sensor_type.lower(), sensor_id=identifier,
                                          hardware_id="", hardware_name="", value=value))
            except Exception as e:
                failed.append(sensor_type)
                self.logger.debug("LHM/OHM GPU %s error: %s", sensor_type.lower(), e)
        if "Temperature" in failed and len(failed) == len(wanted):
            self._wmi_ohm = None
            self._ohm_outage.failed("GPU sensor query failed")
            return None
        groups = gpu_groups(rows)
        self._gpu_wmi_groups = groups
        if same_as is not None:
            card = same_as.hardware_id.casefold()
            return next((g for g in groups if g.hardware_id.casefold() == card), None)
        if not groups:
            self.logger.debug("LHM/OHM: no valid GPU temperature/power sensor found")
        return groups[0] if groups else None

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

    def _nvidia_smi_for(self, adapter: gpu_adapters.AdapterInfo, want_readings: bool = True,
                        may_query: bool = True) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """(temp_c, power_w, total_mib) from the nvidia-smi line that IS the picked card (#236).

        Matched by name. One NVIDIA card in DXGI and one line: that line, whatever its name. Anything
        else (two identical cards, a name nvidia-smi spells differently on a multi-card box): all
        None - N/A, never another card's reading. Same slow sub-cadence and stamp-first rule as the
        Automatic query; cached per picked name."""
        name = adapter.name.strip().casefold()
        cached_name, cached = self._nvidia_pick_cache
        now = time.monotonic()
        if cached_name == name and (
                (now - self._nvidia_pick_last_poll) < self._nvidia_interval_now
                or (not want_readings and cached[2] is not None)):   # only the total wanted: known
            return cached
        # Failing: backing off (see _query_nvidia_smi). Or, on a hybrid, this PDH sample spans our
        # last call (see _nvidia_awake): the cached reading, no new call.
        if now < self._nvidia_retry_at or not may_query:
            return cached if cached_name == name else (None, None, None)
        self._nvidia_pick_last_poll = now
        result: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)
        try:
            output = self._query_nvidia_smi(
                [self._nvidia_smi_path, "--query-gpu=name,temperature.gpu,memory.total,power.draw",
                 "--format=csv,noheader,nounits"])
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

    def _query_nvidia_smi(self, argv) -> str:
        """Run nvidia-smi through _run_child, with the failure backoff: the n-th failure in a row
        delays the next call by NVIDIA_SMI_BACKOFF_BASE_SEC * 2^(n-1) s (capped at ..._MAX_SEC) and
        is logged once per streak; a success ends the streak. Raises like _run_child."""
        g = constants.gpu
        try:
            output = self._run_child(argv, constants.timeouts.NVIDIA_SMI_TIMEOUT_SEC)
        except Exception as e:
            now = time.monotonic()
            self._nvidia_smi_ran_at = now
            self._nvidia_fail_streak += 1
            delay = min(g.NVIDIA_SMI_BACKOFF_MAX_SEC,
                        g.NVIDIA_SMI_BACKOFF_BASE_SEC * 2 ** min(self._nvidia_fail_streak - 1, 16))
            self._nvidia_retry_at = now + delay
            if self._nvidia_fail_streak == g.NVIDIA_SMI_BACKOFF_LOG_AFTER:
                self.logger.info("nvidia-smi failed %d times in a row (%s); backing off, up to %d s between tries.",
                                 self._nvidia_fail_streak, type(e).__name__, int(g.NVIDIA_SMI_BACKOFF_MAX_SEC))
            raise
        self._nvidia_smi_ran_at = time.monotonic()
        if self._nvidia_fail_streak >= g.NVIDIA_SMI_BACKOFF_LOG_AFTER:
            self.logger.info("nvidia-smi answers again.")
        self._nvidia_fail_streak = 0
        self._nvidia_retry_at = 0.0
        return output

    def _is_hybrid_laptop(self) -> bool:
        """A hybrid (Optimus) laptop, where an idle NVIDIA dGPU sleeps: an NVIDIA GPU and a GPU of
        another maker (DXGI facts) - on a laptop that one is the CPU's integrated GPU - on a PC the
        firmware calls mobile, with a battery. Not gpu_adapters.is_integrated: an AMD APU reports its
        512 MiB+ UMA frame buffer as dedicated memory, and Ryzen + GeForce is the commonest Optimus
        pair. Not a hybrid (nvidia-smi as before): a desktop - its dGPU never powers down, and a USB
        UPS that GetSystemPowerStatus reports as a battery does not make it a laptop (platform role) -
        or an NVIDIA GPU with a display on its own outputs, which keeps it awake anyway. The battery
        facts are cached once Windows can say; "unknown" is asked again on a later poll."""
        real = gpu_adapters.hardware_adapters(self._gpu_adapters or [])
        nvidia = [a for a in real if a.vendor_id == constants.gpu.VENDOR_NVIDIA]
        if not nvidia or len(nvidia) == len(real) or any(a.outputs > 0 for a in nvidia):
            return False
        if self._mobile_battery is None:
            try:
                from netspeedtray.utils import power_utils
                present = power_utils.battery_presence()
                if present is None:
                    return False
                self._mobile_battery = present and power_utils.platform_is_mobile() is not False
            except Exception as e:
                self.logger.debug("Battery check failed: %s", e)
                self._mobile_battery = False
        return self._mobile_battery

    def _on_battery_power(self) -> bool:
        """Running on battery right now (ACLineStatus 0). Unknown or unreadable: False - as on AC."""
        try:
            from netspeedtray.utils import power_utils
            return power_utils.ac_line_status() == 0
        except Exception:
            return False

    def _nvidia_awake(self, utils: Dict[Tuple[int, int], float], luids: List[Tuple[int, int]],
                      window_start: Optional[float], settling: bool = False) -> Tuple[bool, bool]:
        """(awake, may_query) for a hybrid's NVIDIA dGPU, from this tick's PDH engine utilization
        (PDH never wakes a GPU) - ``utils`` is the poll's parsed per-adapter busiest engine.

        A utilization sample covers (window_start, its collection]. A sample whose window holds our
        last nvidia-smi call is ambiguous - if querying a sleeping dGPU registers as engine activity,
        that is where it shows - so its activity is not counted, and no new call is made in it
        (may_query False: the cached reading is used). So nvidia-smi can never keep the dGPU awake by
        itself, at any update rate, and a busy dGPU is still read every other sample at worst.

        Awake: activity above HYBRID_DGPU_AWAKE_MIN_UTIL_PERCENT in an unambiguous sample within the
        hold - HYBRID_DGPU_AWAKE_HOLD_SEC, or HYBRID_DGPU_AWAKE_HOLD_WINDOWS sample windows at slow
        update rates, so one ambiguous sample in between never closes it.

        ``settling``: the first sample after a query (re)build - a near-zero window, so it is treated
        like an ambiguous one (not counted, no new call), and its length does not shrink the hold."""
        g = constants.gpu
        now = time.monotonic()
        ran = self._nvidia_smi_ran_at
        clean = not settling and (ran is None or (window_start is not None and window_start > ran))
        if not settling:
            self._gpu_window_sec = now - window_start if window_start is not None else 0.0
        if clean and any(utils.get(luid, 0.0) > g.HYBRID_DGPU_AWAKE_MIN_UTIL_PERCENT for luid in luids):
            self._nvidia_active_at = now
        hold = max(g.HYBRID_DGPU_AWAKE_HOLD_SEC, g.HYBRID_DGPU_AWAKE_HOLD_WINDOWS * self._gpu_window_sec)
        awake = self._nvidia_active_at is not None and now - self._nvidia_active_at <= hold
        return awake, clean

    def _gpu_idle_for(self, luid: Tuple[int, int], temp: Optional[float],
                      power: Optional[float]) -> Optional[bool]:
        """The hover card's "Idle" tag for one adapter (2.2.1), from its FINAL temperature / power.

        True only when the Optimus gate judged THIS card on the last poll, found it idle with nvidia-smi
        healthy (_gpu_sensor_asleep), and the readings the card shows are blank - so its "—" are the
        gate's doing. The gate only knows "no engine activity within the hold", which is why the tag says
        idle, not asleep. False when judged but that is not the case: awake, nvidia-smi failing, or
        another source (LHM / OHM) still reads it - a live 45 °C never sits next to "Idle". None when
        not judged at all (the row then has no tag)."""
        luids, idle = self._gpu_idle_gate
        if luid not in luids:
            return None
        return bool(idle) and temp is None and power is None

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
        if self._wmi_battery is None and not power_utils.has_battery():
            # No battery reported (a desktop - the power tiles poll this too): nothing to read, so
            # no WMI and the wmi module is never imported. Asked again on the next slow tick, so a
            # battery that appears later is still picked up.
            self._battery_cache = (now, None)
            return None
        watts = None
        try:
            if self._wmi_battery is None:
                self._wmi_battery = import_wmi().WMI(namespace="root\\wmi")
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
        Failures are NOT permanently cached, so the app picks up LHM/OHM if it starts after
        NetSpeedTray - but in the steady "never found" state a failed probe is not repeated for
        constants.lhm.WMI_PROBE_BACKOFF_SEC (2.2.1): it used to run on every poll, up to three times a
        poll, wherever no namespace exists (every NVIDIA desktop without LHM). Still every poll: for
        NOT_DETECTED_GRACE_SEC after the first probe (LHM's WMI coming up at login, or initialising
        with 0 sensors) and for NOT_DETECTED_GRACE_SEC after a connection that worked is lost (a
        one-poll hiccup recovers on the next poll, as before). A source lost for longer - LHM/OHM
        closed for good - backs off too: SourceOutage.lost stays True for the whole outage, and
        probing every poll then cost ~8-12 failed GetObject calls a second at the 0.5 s rate. Its
        recovery is still found within WMI_PROBE_BACKOFF_SEC, and SourceOutage still sees one failed()
        per backoff window, which its "still lost" line needs. A settings change (run) probes again at
        once.
        """
        if self._wmi_ohm is not None:
            return
        now = time.monotonic()
        if now < self._ohm_retry_at:
            return
        com_client = _com_client()
        if com_client is None:
            return
        if self._ohm_first_probe_at is None:
            self._ohm_first_probe_at = now
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
                obj = com_client.GetObject(f"winmgmts:{ns}")
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
                self._ohm_lost_probe_at = None
                # The first connection is INFO; a reconnect after a lost one is SourceOutage's
                # "is back" line, and a re-probe after a settings change is DEBUG.
                level = logging.INFO if self._ohm_outage.succeeded() else logging.DEBUG
                self.logger.log(level, "Hardware monitor: connected to %s (%d sensors).", ns, count)
                return
            except Exception as e:
                # A missing namespace (0x8004100e WBEM_E_INVALID_NAMESPACE) is normal when
                # that particular tool isn't the one running - keep it quiet here.
                tool = ns.rsplit("\\", 1)[-1]  # LibreHardwareMonitor / OpenHardwareMonitor
                probe_errors.append(f"{tool}: {e}")
                self.logger.debug("Hardware monitor: %s probe failed: %s", ns, e)
        # Nothing connected across all namespaces. A source that worked earlier is still (or newly)
        # lost: SourceOutage reports a folded outage that outlasts its quiet window (no-op otherwise).
        self._ohm_outage.failed("no LibreHardwareMonitor/OpenHardwareMonitor WMI namespace answered")
        # Back off unless inside the start-up grace or the first NOT_DETECTED_GRACE_SEC of an outage
        # of a source that worked (see the docstring).
        grace = constants.lhm.NOT_DETECTED_GRACE_SEC
        in_grace = now - self._ohm_first_probe_at < grace
        if self._ohm_outage.lost:
            if self._ohm_lost_probe_at is None:
                self._ohm_lost_probe_at = now
            in_grace = in_grace or now - self._ohm_lost_probe_at < grace
        else:
            self._ohm_lost_probe_at = None
        if not in_grace:
            self._ohm_retry_at = now + constants.lhm.WMI_PROBE_BACKOFF_SEC
        # Surface ONE actionable line, once.
        if not self._ohm_guidance_logged:
            self._ohm_guidance_logged = True
            kind = "empty" if saw_empty_namespace else "none"
            if kind in _OHM_GUIDANCE_LOGGED:
                self.logger.debug("Hardware monitor: no sensor source (guidance already logged).")
                return
            _OHM_GUIDANCE_LOGGED.add(kind)
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
                    self._lhm_http.host, self._lhm_http.port, self._lhm_http.port
                )
        # Leave _wmi_ohm as None so we retry after the backoff (LHM may not be running yet)

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
                self._ohm_outage.failed(f"CPU temperature query failed: {type(e).__name__}")

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
        com_client = _com_client()
        if com_client is None:
            return None
        try:
            if not self._wmi:
                try:
                    self._wmi = com_client.GetObject("winmgmts:\\\\.\\root\\wmi")
                except Exception:
                    self._wmi = com_client.GetObject("winmgmts:root\\wmi")
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
        # Give LHM time to be detected - by the clock, so the 0.5 s rate does not halve it (#456).
        now = time.monotonic()
        if self._lhm_check_since is None:
            self._lhm_check_since = now
        if now - self._lhm_check_since >= constants.lhm.NOT_DETECTED_GRACE_SEC:
            has_any_reading = any(stats.get(k) is not None for k in _SENSOR_STAT_KEYS)
            # A hybrid's sleeping dGPU (2.2.1 Optimus gate): nvidia-smi is a known source, just not
            # asked while the dGPU sleeps - not "no sensor". Re-checked on later polls.
            if not has_any_reading and user_gpu and self._gpu_sensor_asleep:
                return
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
                    self._apply_lhm_settings()       # a new LHM port / user name / password
                    self._lhm_http.reset_backoff()   # temps/power just switched on: look for LHM now
                    self._cleanup_thermal_query()
                    self._cleanup_power_query()
                    self._wmi_ohm = None   # re-probe LHM/OHM on the next temp poll
                    self._ohm_retry_at = 0.0   # now, not after a failed probe's backoff,
                    self._ohm_first_probe_at = None   # with a fresh every-poll grace
                    self._ohm_lost_probe_at = None

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

                # The event-driven cache does no Windows work on unchanged ticks.
                self._collect_network_identity(stats)

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

                # 3. GPU / VRAM (Optional - skipped entirely in RDP sessions). VRAM alone (2.2.1) polls
                # only the PDH adapter-memory counters: no engine-utilization wildcard, no temperature /
                # power sources, no nvidia-smi - those are only asked for with the GPU on.
                list_gpus = _collect_gpu and not _in_rdp and bool(self.config.get(
                    'hover_list_all_gpus', constants.config.defaults.DEFAULT_HOVER_LIST_ALL_GPUS))
                if not list_gpus:
                    stats['gpu_cards'] = ()   # GPU collection or the list switched off: clear it
                if (_collect_gpu or _collect_vram) and not _in_rdp:
                    try:
                        if self._gpu_query and self._gpu_query_has_util != _collect_gpu:
                            self._cleanup_gpu_query()          # rebuilt below with / without the wildcard
                            self._gpu_query_retry_after = 0.0
                        self._gpu_util_wanted = _collect_gpu
                        include_temp = _collect_gpu and (
                            bool(self.config.get('show_hardware_temps', False)) or _force_hw)
                        include_power = _collect_gpu and (
                            bool(self.config.get('show_hardware_power', False)) or _force_hw)
                        gpu = self._poll_gpu_hybrid(include_temp=include_temp, include_power=include_power,
                                                    memory_only=not _collect_gpu)

                        # util None = the first sample after a query rebuild: no key, so the taskbar
                        # and the history keep the last reading instead of a near-zero-window blip.
                        if _collect_gpu and gpu.util is not None:
                            stats['gpu'] = gpu.util
                            # The picked dGPU's "Idle" verdict for the single GPU row (2.2.1) - not on a
                            # held tick either: its gate verdict comes from the same sliver of a sample.
                            stats['gpu_idle'] = gpu.idle
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
                        # Every GPU of a multi-GPU PC, for the hover card's list (#236): built from
                        # this poll's reads, live display only - the controller never persists it.
                        # () = no list (one GPU). A failed poll sends no key at all, so a transient
                        # PDH/driver error keeps the list the card already shows.
                        # Not on a held poll either: its per-card utilization is the same noise, and no
                        # key keeps the list the card already shows.
                        if list_gpus and gpu.util is not None:
                            cards = self._gpu_cards(include_temp, include_power)
                            stats['gpu_cards'] = cards if len(cards) >= 2 else ()
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
                # A later collector may fail after identity collection but before stats_ready.
                # Resend on recovery so that a changed identity or resume request is not lost.
                self._last_emitted_network_identity = None
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
            # ONE wait per period (2.2.1): stop() sets the event, so a quit still ends it at once.
            # This replaced 0.1 s slices - ten wake-ups a second at any rate, for nothing.
            self._wait_for_stop(effective_interval)

        self._network_identity_monitor.close()
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
        """Use MTA for WMI and WinRT callbacks; this polling thread has no STA message pump."""
        self._com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            self._com_initialized = True
        except Exception as e:
            self.logger.debug("Monitor COM initialization unavailable: %s", e)

    def _cleanup_com(self) -> None:
        """Balance this worker's successful COM initialization only."""
        if self._com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception as e:
                self.logger.debug("Monitor COM cleanup failed: %s", e)
            finally:
                self._com_initialized = False

    def request_network_identity_update(self) -> None:
        """Ask the worker to resend its current identity after the live view resumes."""
        with self._network_identity_update_lock:
            self._network_identity_update_requested.set()

    def _collect_network_identity(self, stats: Dict[str, Any]) -> None:
        """Emit changed identities and one requested refresh, keeping WinRT on this worker."""
        if not self.config.get('show_network_identity', False):
            self._network_identity_monitor.close()
            self._last_emitted_network_identity = None
            return
        # Consume before reading so a resume request arriving during the query is retained.
        with self._network_identity_update_lock:
            force_update = self._network_identity_update_requested.is_set()
            self._network_identity_update_requested.clear()
        try:
            identity = self._network_identity_monitor.read(self.config.get('identity_mode', 'band'))
        except Exception:
            if force_update:
                self.request_network_identity_update()
            raise
        if force_update or identity != self._last_emitted_network_identity:
            stats['network_identity'] = identity
            self._last_emitted_network_identity = identity

    def _run_child(self, argv, timeout: float) -> str:
        """Run a short-lived helper (nvidia-smi) and return its stdout, like check_output - but the
        process is held where ``stop()`` can kill it, so a quit never waits out (or orphans) a slow
        one. Raises on a non-zero exit, a timeout (the child is killed), or a kill from stop()."""
        # Not in the app's folder (the inherited CWD): a child sitting there blocks a portable
        # update from renaming the folder for as long as it lives (helpers.neutral_cwd).
        from netspeedtray.utils.helpers import neutral_cwd
        with self._child_lock:
            if not self._is_running:
                raise RuntimeError("stopping")
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, encoding="utf-8", cwd=neutral_cwd(),
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
