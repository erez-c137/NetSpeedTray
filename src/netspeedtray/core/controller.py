"""
Controller module for NetSpeedTray.

This module defines the StatsController, which manages system data acquisition,
including network speeds, CPU utilization, and GPU utilization.
"""

import logging
import time
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Tuple

from PyQt6.QtCore import pyqtSignal, QObject
import psutil

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.utils.config import stable_pseudonym
from netspeedtray.utils.network_utils import get_primary_interface_name
from netspeedtray.utils.interface_classifier import InterfaceClassifier, get_classifier

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.core.widget_state import WidgetState

logger = logging.getLogger("NetSpeedTray.StatsController")


# Which monitoring switches a persisted hardware stat_type depends on. A stat is written to the history
# DB only when the user's "Record hardware history" toggle is on AND every switch listed here is on -
# i.e. only for metrics the user actually chose to monitor. The Monitor window forces collection of
# everything while it is open (live tiles); that is display, not consent to record. RAM rides the CPU
# line (Settings greys RAM out with CPU off), hence both. Stats not listed here (the latency series)
# are not hardware and have their own switch, latency_enabled.
_HW_PERSIST_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "cpu": ("monitor_cpu_enabled",),
    "gpu": ("monitor_gpu_enabled",),
    "ram": ("monitor_cpu_enabled", "monitor_ram_enabled"),
    "cpu_temp": ("monitor_cpu_enabled", "show_hardware_temps"),
    "gpu_temp": ("monitor_gpu_enabled", "show_hardware_temps"),
    "cpu_power": ("monitor_cpu_enabled", "show_hardware_power"),
    "gpu_power": ("monitor_gpu_enabled", "show_hardware_power"),
}
_HW_POWER_TOTALS = ("system_power", "total_power")


def hardware_persist_allowed(stat_type: str, config: Dict[str, Any]) -> bool:
    """Whether a hardware sample of ``stat_type`` may be written to the history DB (2.2 item C).

    ``system_power`` / ``total_power`` need the power switch and at least one of CPU/GPU on; the
    caller additionally checks ``total_power``'s contributors (see handle_stats)."""
    if stat_type not in _HW_PERSIST_REQUIRES and stat_type not in _HW_POWER_TOTALS:
        return True
    if not config.get("record_hardware_history", True):
        return False
    if stat_type in _HW_POWER_TOTALS:
        return bool(config.get("show_hardware_power", False)) and bool(
            config.get("monitor_cpu_enabled", False) or config.get("monitor_gpu_enabled", False))
    return all(bool(config.get(k, False)) for k in _HW_PERSIST_REQUIRES[stat_type])


class StatsController(QObject):
    """
    Manages hardware data processing and UI dispatching.
    """
    # Signal for network speeds (aggregated upload/download in Mbps)
    display_speed_updated = pyqtSignal(float, float)
    
    # New signals for hardware utilization (%)
    cpu_usage_updated = pyqtSignal(float)
    gpu_usage_updated = pyqtSignal(float)
    # object (not float) so None can be forwarded when a sensor drops, letting the
    # widget clear a stale reading to "(N/A)" instead of freezing the last value.
    cpu_temp_updated = pyqtSignal(object)
    gpu_temp_updated = pyqtSignal(object)
    cpu_power_updated = pyqtSignal(object)
    gpu_power_updated = pyqtSignal(object)
    ram_info_updated = pyqtSignal(float, float) # (used, total) in GB
    vram_info_updated = pyqtSignal(float, float) # (used, total) in GB
    # Network identity (Wi-Fi band / SSID). object carries a NetworkIdentity (or None) so the widget
    # can render the band tag and detect the Location-gated SSID case. Sub-polled ~5s, not per tick.
    network_identity_updated = pyqtSignal(object)


    def __init__(self, config: Dict[str, Any], widget_state: 'WidgetState',
                 interface_classifier: Optional[InterfaceClassifier] = None) -> None:
        super().__init__()
        self.logger = logger
        self.config = config
        self.widget_state = widget_state
        # The ONE physical-vs-virtual answer, shared with the Settings picker (2.2 scope C).
        self.interface_classifier = interface_classifier or get_classifier()
        self.view: Optional['NetworkSpeedWidget'] = None
        
        # Network specific state
        self.last_check_time: float = 0.0
        self.last_interface_counters: Dict[str, Any] = {}
        self.current_speed_data: Dict[str, Tuple[float, float]] = {}
        # The last poll's rates as ONE immutable value (the metrics endpoint's source, read on the GUI
        # thread): (display per-interface, measured per-interface, display aggregate (up, down),
        # measured aggregate (up, down)), bytes/s. Replaced whole, never mutated; None before the first
        # rate. Zeroed whenever the widget is shown 0 (re-prime / gap), so it never outlives the display.
        self.live_rates: Optional[Tuple[Dict[str, Tuple[float, float]], Dict[str, Tuple[float, float]],
                                        Tuple[float, float], Tuple[float, float]]] = None
        self.primary_interface: Optional[str] = None
        self.last_primary_check_time: float = 0.0
        self.repriming_needed: int = 0
        # (monotonic, wall) at which last_interface_counters were SAMPLED - the odometer cap's clock.
        self._last_sample_time: Optional[Tuple[float, float]] = None
        self._odometer_cap_warned: bool = False
        # Launch reconciliation (once per session): the first counters seen, until reconciled.
        self._launch_counters: Optional[Tuple[Dict[str, Any], float]] = None
        self._launch_reconciled: bool = False
        # Auto mode: the last primary the routing lookup actually found (see _aggregate_for_odometer).
        self._last_known_primary: Optional[str] = None
        
        from collections import deque
        self.recent_speeds: Dict[str, deque] = {}
        # Per-interface [up, down] count of consecutive over-threshold polls. A SUSTAINED legitimate
        # ramp is then capped at most once (the first poll) instead of being held at 2x baseline for
        # many polls while the rolling average catches up (#5); a one-off counter glitch is still capped.
        self._spike_streak: Dict[str, list] = {}

        mode = self.config.get("interface_mode", "auto")
        self.logger.info("StatsController initialized (interface_mode=%s).", mode)


    def set_view(self, view: 'NetworkSpeedWidget') -> None:
        """Connects the controller to the main widget view."""
        self.view = view
        self.display_speed_updated.connect(self.view.update_display_speeds)
        
        # Connect new signals to view if methods exist
        # (We will add these methods to the widget later)
        if hasattr(self.view, 'update_cpu_usage'):
            self.cpu_usage_updated.connect(self.view.update_cpu_usage)
        if hasattr(self.view, 'update_gpu_usage'):
            self.gpu_usage_updated.connect(self.view.update_gpu_usage)
        if hasattr(self.view, 'update_cpu_temp'):
            self.cpu_temp_updated.connect(self.view.update_cpu_temp)
        if hasattr(self.view, 'update_gpu_temp'):
            self.gpu_temp_updated.connect(self.view.update_gpu_temp)
        if hasattr(self.view, 'update_cpu_power'):
            self.cpu_power_updated.connect(self.view.update_cpu_power)
        if hasattr(self.view, 'update_gpu_power'):
            self.gpu_power_updated.connect(self.view.update_gpu_power)
        if hasattr(self.view, 'update_ram_info'):
            self.ram_info_updated.connect(self.view.update_ram_info)
        if hasattr(self.view, 'update_vram_info'):
            self.vram_info_updated.connect(self.view.update_vram_info)
        if hasattr(self.view, 'update_network_identity'):
            self.network_identity_updated.connect(self.view.update_network_identity)

        self.logger.debug("View set and signals connected.")


    def handle_stats(self, stats: Dict[str, Any]) -> None:
        """
        Unified handler for all hardware statistics.
        """
        # 1. Handle Network
        if 'network' in stats:
            self._handle_network_counters(stats['network'], stats.get('network_sampled_at'),
                                          stats.get('network_if_stats'))

        # Forward the network identity (band/SSID) whenever the thread sub-polled it (present-key
        # emits, like temp/power). The payload is a NetworkIdentity or None.
        if 'network_identity' in stats:
            self.network_identity_updated.emit(stats['network_identity'])

        if 'cpu' in stats or 'gpu' in stats:
            cpu = stats.get('cpu')
            gpu = stats.get('gpu')
            ram_used = stats.get('ram_used')
            ram_total = stats.get('ram_total')
            vram_used = stats.get('vram_used')
            vram_total = stats.get('vram_total')

            # Emit signals for UI components
            def _keep(stat_type: str) -> bool:
                return hardware_persist_allowed(stat_type, self.config)

            if cpu is not None:
                self.cpu_usage_updated.emit(cpu)
                if self.widget_state:
                    self.widget_state.add_hardware_stat('cpu', cpu, persist=_keep('cpu'))
            if gpu is not None:
                self.gpu_usage_updated.emit(gpu)
                if self.widget_state:
                    self.widget_state.add_hardware_stat('gpu', gpu, persist=_keep('gpu'))
            # GPU presence (no-GPU boxes enumerate zero Engine counters): let the Monitor's at-a-glance
            # tiles hide the GPU rather than show a permanent 0%. LATCH it - only ever set True, never
            # back to False - so a transient empty-counter poll (re-init, driver hiccup) can't flicker
            # the tile on/off. A genuinely GPU-less box never sets it, so it stays hidden (default False).
            if stats.get('gpu_present') and self.view is not None:
                try:
                    self.view.gpu_present = True
                except Exception:
                    pass
            # Forward temp/power whenever the thread reported the key, even if the
            # value is None (sensor unavailable this poll). Emitting None lets the
            # widget clear a stale reading to "(N/A)" rather than freezing the last
            # good value forever. An absent key (feature disabled) emits nothing.
            if 'cpu_temp' in stats:
                self.cpu_temp_updated.emit(stats['cpu_temp'])
            if 'gpu_temp' in stats:
                self.gpu_temp_updated.emit(stats['gpu_temp'])
            if 'cpu_power' in stats:
                self.cpu_power_updated.emit(stats['cpu_power'])
            if 'gpu_power' in stats:
                self.gpu_power_updated.emit(stats['gpu_power'])

            # Persist temperature + power as their own (unclamped) hardware stat_types so the Monitor's
            # pro-stats (avg/max/p95, the Stats-detail sheet, export, throttle-time) have real history.
            # Total power = CPU + GPU watts (a "CPU+GPU power" figure, not whole-system draw).
            # Whole-system power (RAPL PSYS / battery) - a real source when present; stash it on the
            # widget so the Monitor can show a true "System N W" distinct from the CPU+GPU sum.
            if 'system_power' in stats and self.view is not None:
                try:
                    self.view.system_power = stats['system_power']
                except Exception:
                    pass
            if self.widget_state:
                for key in ('cpu_temp', 'gpu_temp', 'cpu_power', 'gpu_power', 'system_power'):
                    v = stats.get(key)
                    if v is not None and v > 0:
                        self.widget_state.add_hardware_stat(key, float(v), persist=_keep(key))
                cp, gp = stats.get('cpu_power'), stats.get('gpu_power')
                cp_on, gp_on = cp is not None and cp > 0, gp is not None and gp > 0
                if cp_on or gp_on:
                    # The sum may only be recorded when every reading IN it may be - otherwise a
                    # Monitor-forced GPU watt reading would leak into history through the total.
                    keep_total = (_keep('total_power') and (not cp_on or _keep('cpu_power'))
                                  and (not gp_on or _keep('gpu_power')))
                    self.widget_state.add_hardware_stat('total_power', float(cp or 0.0) + float(gp or 0.0),
                                                        persist=keep_total)

            # 4. Handle RAM / VRAM Info
            if stats.get('ram_used') is not None and stats.get('ram_total') is not None:
                self.ram_info_updated.emit(stats['ram_used'], stats['ram_total'])
                # Persist RAM% to the same 3-tier history as cpu/gpu (the pipeline is stat_type-generic)
                # so the Monitor can graph RAM over time and the Overview RAM sparkline is DB-backed.
                if self.widget_state and ram_total and ram_total > 0:
                    self.widget_state.add_hardware_stat('ram', (ram_used / ram_total) * 100.0,
                                                        persist=_keep('ram'))
                
            if stats.get('vram_used') is not None:
                v_total = stats.get('vram_total')
                v_total_val = float(v_total) if v_total is not None else -1.0
                self.vram_info_updated.emit(stats['vram_used'], v_total_val)


    def _handle_network_counters(self, current_counters: Dict[str, Any],
                                 sample_time: Optional[Tuple[float, float]] = None,
                                 if_stats: Optional[Dict[str, Any]] = None) -> None:
        """Processes raw network counters: the data-usage odometer and the v8 history first, then
        the rate display.

        `sample_time` is (time.monotonic(), time.time()) taken by the monitor thread right after it
        read the counters; it falls back to the handling time (direct callers, tests). `if_stats` is
        psutil.net_if_stats() when the monitor thread sampled it this poll (a slow sub-cadence).

        History (schema v8) stores the SAME exact byte deltas the odometer counts, per interface, over
        the measured sampling span - never a rate, so neither the spike filter nor the poll rate can
        reach it, and a gap's bytes are kept (the writer flags the row FLAG_GAP).

        Order matters. The odometer counts the exact counter delta of EVERY poll, BEFORE the gap /
        re-prime gate: a long gap (Modern Standby, the app suspended) used to discard the whole gap
        plus two more polls (data-usage accuracy audit, finding 1). Only the rate display re-primes -
        a rate averaged over a gap is meaningless, the bytes are not.
        """
        current_time = time.monotonic()
        sample_mono, sample_wall = sample_time if sample_time else (current_time, time.time())

        if not current_counters:
            self._set_live_rates_zero()
            self.display_speed_updated.emit(0.0, 0.0)
            return

        if not self.last_interface_counters:
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self._last_sample_time = (sample_mono, sample_wall)
            if self.widget_state:
                # No interval yet: this only primes the history writer's counter baselines.
                self.widget_state.add_net_sample(sample_wall, 1, {}, counters=current_counters,
                                                 if_stats=if_stats)
            if self._launch_counters is None and not self._launch_reconciled:
                self._launch_counters = (current_counters, sample_wall)
                self._maybe_reconcile_launch()
            return

        time_diff = current_time - self.last_check_time
        update_interval = self.config.get("update_rate", 1.0)

        if time_diff < (update_interval * 0.5):
            return

        # Resolve the primary (auto mode; cached, at most every 15 s) once for both aggregations.
        if self.config.get("interface_mode", "auto") == "auto":
            self._update_primary_interface_name()

        # --- 1. The odometer: exact bytes, every poll, gap or not -------------------------------
        self._maybe_reconcile_launch()   # no-op unless launch had to wait for the odometer to load
        last_mono, last_wall = self._last_sample_time or (self.last_check_time, sample_wall)
        # The cap's elapsed is the SAMPLING span, and the longer of the two clocks: the monotonic
        # clock need not advance across sleep, the wall clock can jump - either alone could clip
        # real bytes. A too-generous span only loosens the cap for that one poll.
        sample_elapsed = max(sample_mono - last_mono, sample_wall - last_wall, 0.0)
        byte_deltas = self._odometer_byte_deltas(current_counters, sample_elapsed)
        # Same interface selection as the display, but raw deltas, not rates. Gated on byte_deltas,
        # NOT on the display data: a rate over the ceiling is display-dropped, its bytes are real (#13).
        if byte_deltas and self.widget_state:
            agg_up_bytes, agg_down_bytes = self._aggregate_for_odometer(byte_deltas)
            # The window began at the previous sample: bytes are charged to the period it began in.
            self.widget_state.add_usage_bytes(agg_up_bytes, agg_down_bytes,
                                              counters=current_counters, sampled_at=sample_wall,
                                              window_start=last_wall, primary=self._odometer_primary())
        if self.widget_state:
            # v8 history: every interface's exact delta over the measured span (ending at this sample),
            # gap or not - the writer flags a long interval rather than dropping its bytes.
            self.widget_state.add_net_sample(sample_wall, sample_elapsed * 1000.0, byte_deltas,
                                             counters=current_counters, if_stats=if_stats)
        self._last_sample_time = (sample_mono, sample_wall)

        # --- 2. The rate display: re-prime across gaps ------------------------------------------
        _spd = constants.network.speed
        validity_threshold = max(_spd.RATE_VALIDITY_MIN_SECONDS, update_interval * _spd.RATE_VALIDITY_POLL_MULTIPLE)

        if time_diff > validity_threshold:
            self.repriming_needed = 2
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self._set_live_rates_zero()
            self.display_speed_updated.emit(0.0, 0.0)
            return

        if self.repriming_needed > 0:
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self._set_live_rates_zero()
            self.display_speed_updated.emit(0.0, 0.0)
            self.repriming_needed -= 1
            return

        self.current_speed_data.clear()
        # The MEASURED per-interface rates, before the spike filter. History stores these: the
        # filter's substitute (2x the rolling baseline) is a number nobody measured, and it fires
        # on exactly the bursts a user would later dispute (database-v8-final.md 0.1). The display
        # (current_speed_data -> widget text, mini graph, in-memory session deques) stays smoothed.
        measured_speed_data: Dict[str, Tuple[float, float]] = {}

        for name, current in current_counters.items():
            last = self.last_interface_counters.get(name)
            if last:
                # Per-direction counter-reset/wrap guard: a reset in ONE direction must not
                # discard the OTHER direction's real bytes - they're independent counters.
                up_diff = max(0, current.bytes_sent - last.bytes_sent)
                down_diff = max(0, current.bytes_recv - last.bytes_recv)

                safe_time_diff = max(time_diff, constants.network.speed.MIN_TIME_DIFF)
                up_speed_bps = int(up_diff / safe_time_diff)
                down_speed_bps = int(down_diff / safe_time_diff)

                max_speed_bps = constants.network.interface.MAX_REASONABLE_SPEED_BPS

                # NOTE: rely ONLY on this absolute ceiling; do not cap by
                # psutil.net_if_stats()[name].speed. On Windows that per-interface
                # link speed is unreliable for multi-gigabit / 10GbE adapters (often a
                # wrong or half rate), which silently dropped every real sample above it
                # and displayed a constant 0 (issue #154). Real counter glitches are
                # still caught here and by the rolling-average spike filter below.
                if up_speed_bps > max_speed_bps or down_speed_bps > max_speed_bps:
                    continue

                final_up_speed_bps = up_speed_bps
                final_down_speed_bps = down_speed_bps

                if name not in self.recent_speeds:
                    from collections import deque
                    self.recent_speeds[name] = deque(maxlen=20)

                recent_history = self.recent_speeds[name]
                if recent_history and len(recent_history) >= 5:
                    recent_ups = [s[0] for s in recent_history]
                    recent_downs = [s[1] for s in recent_history]

                    recent_up_avg = sum(sorted(recent_ups)[1:-1]) / max(1, len(recent_ups) - 2) if len(recent_ups) > 2 else sum(recent_ups) / len(recent_ups)
                    recent_down_avg = sum(sorted(recent_downs)[1:-1]) / max(1, len(recent_downs) - 2) if len(recent_downs) > 2 else sum(recent_downs) / len(recent_downs)

                    # Cap an ISOLATED over-threshold sample (a likely counter glitch), but let a
                    # SUSTAINED jump through after one poll - a 2nd consecutive over-threshold sample is
                    # a real ramp, not a glitch, and must not stay pinned at 2x baseline (#5).
                    streak = self._spike_streak.setdefault(name, [0, 0])
                    if recent_up_avg > 1000 and final_up_speed_bps > recent_up_avg * 5.0:
                        if streak[0] == 0:
                            final_up_speed_bps = int(recent_up_avg * 2.0)
                        streak[0] += 1
                    else:
                        streak[0] = 0

                    if recent_down_avg > 1000 and final_down_speed_bps > recent_down_avg * 5.0:
                        if streak[1] == 0:
                            final_down_speed_bps = int(recent_down_avg * 2.0)
                        streak[1] += 1
                    else:
                        streak[1] = 0

                self.current_speed_data[name] = (final_up_speed_bps, final_down_speed_bps)
                measured_speed_data[name] = (up_speed_bps, down_speed_bps)
                self.recent_speeds[name].append((up_speed_bps, down_speed_bps))

        agg_upload, agg_download = self._aggregate_for_display(self.current_speed_data, resolve_primary=False)
        self.live_rates = (dict(self.current_speed_data), measured_speed_data, (agg_upload, agg_download),
                           self._aggregate_for_display(measured_speed_data, resolve_primary=False))

        if self.current_speed_data and self.widget_state:
            self.widget_state.add_speed_data(self.current_speed_data, aggregated_up=agg_upload,
                                             aggregated_down=agg_download, persist_data=measured_speed_data)

        upload_mbps = (agg_upload * 8) / 1_000_000
        download_mbps = (agg_download * 8) / 1_000_000

        self.display_speed_updated.emit(upload_mbps, download_mbps)

        self.last_check_time = current_time
        self.last_interface_counters = current_counters


    def _set_live_rates_zero(self) -> None:
        """The widget is about to show 0 (re-prime / gap / no counters): so does the endpoint."""
        self.live_rates = ({}, {}, (0.0, 0.0), (0.0, 0.0))


    def monitored_interface_names(self, per_interface: Dict[str, Any]) -> set:
        """The interfaces of ``per_interface`` the current mode aggregates - the same selection as
        _aggregate_for_display, as names (the metrics endpoint's per-interface series)."""
        mode = self.config.get("interface_mode", "auto")
        names = set(per_interface)
        if mode == "selected":
            return names & set(self.config.get("selected_interfaces", []))
        if mode == "auto":
            return names & {self.primary_interface} if self.primary_interface else set()
        if mode == "all_physical":
            return names & self._physical_names(per_interface)
        return names


    def _odometer_byte_deltas(self, current_counters: Dict[str, Any],
                              elapsed_s: float) -> Dict[str, Tuple[float, float]]:
        """Exact per-interface byte deltas since the previous sample, for the usage odometer.

        Pre-spike-filter (the filter caps bursts for DISPLAY; a data cap must count them), with two
        guards: the per-direction negative-diff clamp (NICs restart their counters without a
        reboot - finding 6) and a ceiling of MAX_REASONABLE_SPEED_BPS x elapsed per direction, so
        one garbage counter jump cannot add unbounded phantom gigabytes (finding 4).
        """
        elapsed = max(elapsed_s, constants.network.speed.MIN_TIME_DIFF)
        deltas: Dict[str, Tuple[float, float]] = {}
        clipped = False
        for name, current in current_counters.items():
            last = self.last_interface_counters.get(name)
            if not last:
                continue
            up, c1 = usage_snapshot.cap_delta(float(max(0, current.bytes_sent - last.bytes_sent)), elapsed)
            down, c2 = usage_snapshot.cap_delta(float(max(0, current.bytes_recv - last.bytes_recv)), elapsed)
            clipped = clipped or c1 or c2
            deltas[name] = (up, down)
        if clipped:
            if not self._odometer_cap_warned:
                self._odometer_cap_warned = True
                self.logger.warning(
                    "Odometer sanity cap: a counter jump implied more than %.0f Gbps over %.1f s and "
                    "was clipped (a driver counter glitch). Logged once per session.",
                    constants.network.interface.MAX_REASONABLE_SPEED_BPS * 8 / 1e9, elapsed)
            else:
                self.logger.debug("Odometer sanity cap clipped another counter jump (%.1f s).", elapsed)
        return deltas


    def _odometer_primary(self) -> Optional[str]:
        """Auto mode: the interface the odometer meters this poll (persisted in the launch snapshot,
        so the next launch only recovers a window metered on the same adapter). None otherwise."""
        if self.config.get("interface_mode", "auto") != "auto":
            return None
        return self.primary_interface or self._last_known_primary


    def _aggregate_for_odometer(self, byte_deltas: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """The display's interface selection, with one difference in auto mode: when the routing
        lookup currently finds NO primary, fall back to the last one it did find. Waking from
        standby is exactly when the lookup fails (the route is not back yet) and exactly when the
        poll carries the whole standby window's bytes; attributing them to nothing would discard
        what the count-across-gaps fix just recovered. With truly no route, that adapter's delta is
        whatever it really moved - still this PC's traffic on the interface auto mode meters."""
        if (self.config.get("interface_mode", "auto") == "auto" and self.primary_interface is None
                and self._last_known_primary):
            return byte_deltas.get(self._last_known_primary, (0.0, 0.0))
        return self._aggregate_for_display(byte_deltas, resolve_primary=False)


    def _maybe_reconcile_launch(self) -> None:
        """Same-boot launch reconciliation (audit item 2), at most once per session.

        Adds the bytes that moved between the persisted counter snapshot and this session's FIRST
        counters - the traffic that happened while the app was not running. If the odometer has not
        loaded yet it keeps the launch counters and retries next poll: the live polls count from the
        launch counters onward, so reconciling against them later neither gaps nor double-counts.
        """
        if self._launch_reconciled or self._launch_counters is None or not self.widget_state:
            return
        snapshot = self.widget_state.get_usage_launch_snapshot()
        if snapshot is None:
            return  # odometer not loaded yet - retry next poll
        counters, sampled_at = self._launch_counters
        self._launch_reconciled = True
        self._launch_counters = None
        if not isinstance(snapshot, dict):
            return
        deltas, reason = usage_snapshot.reconcile_deltas(
            snapshot, counters, usage_snapshot.current_boot_time(), sampled_at)
        # Scope: only what the CURRENT interface mode meters, aggregated exactly like a live poll.
        if self.config.get("interface_mode", "auto") == "auto":
            self._update_primary_interface_name()
            current_primary = self._odometer_primary()
            if deltas and snapshot.get("primary") != current_primary:
                # Auto meters ONE interface. If it is not the one the snapshot was metered on, the
                # closed window's traffic may have gone over the old one - charging this adapter's
                # whole counter delta could overcount (traffic auto would never have metered).
                self.logger.info("Launch reconciliation skipped: the primary interface changed while "
                                 "the app was not running (%s -> %s).",
                                 stable_pseudonym(snapshot.get("primary")), stable_pseudonym(current_primary))
                deltas, reason = {}, "primary interface changed"
        up, down = self._aggregate_for_odometer(deltas) if deltas else (0.0, 0.0)
        # Always hand over the launch counters, even when nothing is recovered, so the snapshot the
        # odometer persists next matches the total it holds. The window began at the snapshot.
        self.widget_state.add_usage_bytes(up, down, counters=counters, sampled_at=sampled_at,
                                          window_start=snapshot.get("ts"), primary=self._odometer_primary())
        if up or down:
            self.logger.info("Launch reconciliation (%s): recovered %.1f MB up / %.1f MB down that "
                             "moved while the app was not running.", reason, up / 1e6, down / 1e6)
        elif snapshot:
            self.logger.info("Launch reconciliation: nothing recovered (%s).", reason)


    def get_active_interfaces(self) -> List[str]:
        """Returns active interface names."""
        if not self.current_speed_data:
            return []
        return [name for name, (up_speed, down_speed) in self.current_speed_data.items() if up_speed > 1.0 or down_speed > 1.0]


    def _aggregate_for_display(self, per_interface_speeds: Dict[str, Tuple[float, float]],
                               resolve_primary: bool = True) -> Tuple[float, float]:
        """Aggregates speeds based on mode. `resolve_primary=False` reuses the already-
        resolved primary interface (auto mode) without re-running the blocking routing
        lookup - used for the second (byte-delta) aggregation in the same poll."""
        mode = self.config.get("interface_mode", "auto")

        if mode == "selected":
            selected = self.config.get("selected_interfaces", [])
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if name in selected)
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if name in selected)
            return total_up, total_down

        elif mode == "auto":
            if resolve_primary:
                self._update_primary_interface_name()
            return per_interface_speeds.get(self.primary_interface, (0.0, 0.0)) if self.primary_interface else (0.0, 0.0)

        elif mode == "all_physical":
            names = self._physical_names(per_interface_speeds)
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if name in names)
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if name in names)
            return total_up, total_down

        elif mode in ("all_virtual", "virtual"):
            # Sum every interface (physical + virtual). Both names are accepted: the settings
            # page persists "all_virtual"; "virtual" is the legacy/docs alias.
            return self._sum_all(per_interface_speeds)

        else:
            self.logger.warning("Unknown interface_mode %r; summing all interfaces as a fallback.", mode)
            return self._sum_all(per_interface_speeds)


    def _interface_kind(self, name: str) -> str:
        exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
        return self.interface_classifier.kind(name, exclusions)

    def _physical_names(self, per_interface: Dict[str, Any]) -> set:
        """The interfaces all_physical sums. Classified by the shared adapter classifier (OS
        hardware flag + ifType, then names) - so a VPN / vEthernet / host-only adapter whose bytes
        already ride the real NIC is no longer counted twice. If NOTHING on the machine is physical
        (a VM guest NIC the OS doesn't flag as hardware) it falls back to every non-pseudo adapter,
        so the mode never reads zero forever - but never one KNOWN to be a VPN or tunnel (keyword,
        or tunnel/PPP/virtual ifType), so the fallback can't bring the double count back. The
        fallback looks at all live counters, so the display and the odometer aggregation of the
        same poll pick the same set."""
        iface = constants.network.interface
        live = set(per_interface) | set(self.last_interface_counters)
        physical = {n for n in live if self._interface_kind(n) == iface.KIND_PHYSICAL}
        if physical:
            return physical
        exclusions = self.config.get("excluded_interfaces", iface.DEFAULT_EXCLUSIONS)
        return {n for n in live if self.interface_classifier.fallback_eligible(n, exclusions)}

    def _sum_all(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """Sums all speeds."""
        total_up = sum(up for up, down in per_interface_speeds.values())
        total_down = sum(down for up, down in per_interface_speeds.values())
        return total_up, total_down


    _PRIMARY_REFRESH_SEC: float = 15.0  # how often the routing lookup may actually run

    def _update_primary_interface_name(self) -> None:
        """Resolve the primary (routing) interface, CACHED so the blocking lookup
        (`get_primary_interface_name()` does a UDP connect + `net_if_addrs`) runs at most
        every _PRIMARY_REFRESH_SEC instead of every poll - keeping the GUI thread
        responsive in the default 'auto' mode (H1). A NIC change is picked up within the
        refresh window (the speed briefly attributes to the old primary)."""
        now = time.monotonic()
        # Throttle on ELAPSED TIME ALONE. This used to also require `primary_interface is not None`,
        # which meant the cache stopped applying in exactly the case it was built for: with no route
        # (offline, VPN dropping, waking up), the lookup returns None, so the guard fell through and
        # the blocking UDP connect + net_if_addrs ran on EVERY poll instead of every 15s - and logged
        # a warning each time. One reporter's bundle was 137,497 of 139,638 lines from that single
        # call (#260). `last_primary_check_time` starts at 0.0, so the first lookup still runs at once.
        if (now - self.last_primary_check_time) < self._PRIMARY_REFRESH_SEC:
            return
        self.last_primary_check_time = now
        previous = self.primary_interface
        try:
            self.primary_interface = get_primary_interface_name()
        except Exception:
            self.primary_interface = None
        if self.primary_interface is not None:
            self._last_known_primary = self.primary_interface
        if previous != self.primary_interface:
            # Log a STABLE pseudonym, never the raw friendly name: users rename
            # adapters to personal/site labels and this INFO line ships in support
            # bundles (v2.1.5 item 7). Same hash ObfuscatingFormatter uses to scrub
            # historical lines, so old and new lines still correlate (#263).
            self.logger.info(
                "Primary network interface changed: %s -> %s",
                stable_pseudonym(previous), stable_pseudonym(self.primary_interface),
            )


    def current_primary_interface(self) -> Optional[str]:
        """The routing (internet-facing) interface, through the same 15 s cache auto mode uses.
        Resolves on first call in any mode, so the Settings picker can mark it."""
        self._update_primary_interface_name()
        return self.primary_interface

    def get_available_interfaces(self) -> List[str]:
        """Returns available interfaces for UI."""
        try:
            # Every live adapter except loopback / the IPv6 shims. VPNs and virtual adapters are
            # listed (the picker groups them under their own header) so selected mode can pick them.
            all_if = psutil.net_io_counters(pernic=True).keys()
            pseudo = constants.network.interface.KIND_PSEUDO
            return sorted([n for n in all_if if self._interface_kind(n) != pseudo])
        except Exception:
            return []


    def apply_config(self, config: Dict[str, Any]) -> None:
        """Applies configuration."""
        self.config = config.copy()


    def cleanup(self) -> None:
        """Cleanup resources: disconnect every signal wired in set_view()."""
        if self.view:
            try:
                self.display_speed_updated.disconnect(self.view.update_display_speeds)
                for sig, slot in (
                    (self.cpu_usage_updated, 'update_cpu_usage'),
                    (self.gpu_usage_updated, 'update_gpu_usage'),
                    (self.cpu_temp_updated, 'update_cpu_temp'),
                    (self.gpu_temp_updated, 'update_gpu_temp'),
                    (self.cpu_power_updated, 'update_cpu_power'),
                    (self.gpu_power_updated, 'update_gpu_power'),
                    (self.ram_info_updated, 'update_ram_info'),
                    (self.vram_info_updated, 'update_vram_info'),
                    (self.network_identity_updated, 'update_network_identity'),
                ):
                    if hasattr(self.view, slot):
                        sig.disconnect(getattr(self.view, slot))
            except (TypeError, RuntimeError):
                pass
            self.view = None
        self.last_interface_counters.clear()
