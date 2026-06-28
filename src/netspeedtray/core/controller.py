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
from netspeedtray.utils.network_utils import get_primary_interface_name

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.core.widget_state import WidgetState

logger = logging.getLogger("NetSpeedTray.StatsController")


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


    def __init__(self, config: Dict[str, Any], widget_state: 'WidgetState') -> None:
        super().__init__()
        self.logger = logger
        self.config = config
        self.widget_state = widget_state
        self.view: Optional['NetworkSpeedWidget'] = None
        
        # Network specific state
        self.last_check_time: float = 0.0
        self.last_interface_counters: Dict[str, Any] = {}
        self.current_speed_data: Dict[str, Tuple[float, float]] = {}
        self.primary_interface: Optional[str] = None
        self.last_primary_check_time: float = 0.0
        self.repriming_needed: int = 0
        
        from collections import deque
        self.recent_speeds: Dict[str, deque] = {}

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
            
        self.logger.debug("View set and signals connected.")


    def handle_stats(self, stats: Dict[str, Any]) -> None:
        """
        Unified handler for all hardware statistics.
        """
        # 1. Handle Network
        if 'network' in stats:
            self._handle_network_counters(stats['network'])
            
        if 'cpu' in stats or 'gpu' in stats:
            cpu = stats.get('cpu')
            gpu = stats.get('gpu')
            ram_used = stats.get('ram_used')
            ram_total = stats.get('ram_total')
            vram_used = stats.get('vram_used')
            vram_total = stats.get('vram_total')

            # Emit signals for UI components
            if cpu is not None:
                self.cpu_usage_updated.emit(cpu)
                if self.widget_state:
                    self.widget_state.add_hardware_stat('cpu', cpu)
            if gpu is not None:
                self.gpu_usage_updated.emit(gpu)
                if self.widget_state:
                    self.widget_state.add_hardware_stat('gpu', gpu)
            # GPU presence (no-GPU boxes enumerate zero Engine counters): let the Monitor's at-a-glance
            # tiles hide the GPU rather than show a permanent 0%. Default stays True so a momentarily
            # idle GPU is never hidden — only a confirmed no-GPU poll flips it False.
            if 'gpu_present' in stats and self.view is not None:
                try:
                    self.view.gpu_present = bool(stats['gpu_present'])
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
                
            # 4. Handle RAM / VRAM Info
            if stats.get('ram_used') is not None and stats.get('ram_total') is not None:
                self.ram_info_updated.emit(stats['ram_used'], stats['ram_total'])
                
            if stats.get('vram_used') is not None:
                v_total = stats.get('vram_total')
                v_total_val = float(v_total) if v_total is not None else -1.0
                self.vram_info_updated.emit(stats['vram_used'], v_total_val)


    def _handle_network_counters(self, current_counters: Dict[str, Any]) -> None:
        """Processes raw network counters (logic moved from handle_network_counters)."""
        current_time = time.monotonic()
        
        if not current_counters:
            self.display_speed_updated.emit(0.0, 0.0)
            return

        if not self.last_interface_counters:
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            return

        time_diff = current_time - self.last_check_time
        update_interval = self.config.get("update_rate", 1.0)

        if time_diff < (update_interval * 0.5):
            return
            
        validity_threshold = max(10.0, update_interval * 5.0)

        if time_diff > validity_threshold:
            self.repriming_needed = 2
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self.display_speed_updated.emit(0.0, 0.0)
            return

        if self.repriming_needed > 0:
            self.last_check_time = current_time
            self.last_interface_counters = current_counters
            self.display_speed_updated.emit(0.0, 0.0)
            self.repriming_needed -= 1
            return

        self.current_speed_data.clear()
        # Exact per-interface byte deltas (pre-spike-filter) for the usage odometer —
        # the spike filter caps bursts for *display*, but a data cap must count them.
        byte_deltas: Dict[str, Tuple[float, float]] = {}

        for name, current in current_counters.items():
            last = self.last_interface_counters.get(name)
            if last:
                # Per-direction counter-reset/wrap guard: a reset in ONE direction must not
                # discard the OTHER direction's real bytes — they're independent counters.
                up_diff = current.bytes_sent - last.bytes_sent
                down_diff = current.bytes_recv - last.bytes_recv
                if up_diff < 0:
                    up_diff = 0
                if down_diff < 0:
                    down_diff = 0

                # The data-cap odometer counts REAL bytes transferred (post reset-clamp). The
                # rate ceiling + spike filter below are DISPLAY-only and must not drop them: a
                # small time_diff can inflate the *rate* past the ceiling while the bytes are real.
                byte_deltas[name] = (float(up_diff), float(down_diff))

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
                    
                    if recent_up_avg > 1000 and final_up_speed_bps > recent_up_avg * 5.0:
                        final_up_speed_bps = int(recent_up_avg * 2.0)
                    
                    if recent_down_avg > 1000 and final_down_speed_bps > recent_down_avg * 5.0:
                        final_down_speed_bps = int(recent_down_avg * 2.0)

                self.current_speed_data[name] = (final_up_speed_bps, final_down_speed_bps)
                self.recent_speeds[name].append((up_speed_bps, down_speed_bps))

        agg_upload, agg_download = self._aggregate_for_display(self.current_speed_data)

        if self.current_speed_data:
            if self.widget_state:
                self.widget_state.add_speed_data(self.current_speed_data, aggregated_up=agg_upload, aggregated_down=agg_download)
                # Feed the odometer the exact aggregated bytes transferred this poll
                # (same interface selection as the display, but raw deltas not rates).
                # resolve_primary=False reuses the primary already resolved above, so we
                # don't run the blocking routing lookup twice per poll.
                agg_up_bytes, agg_down_bytes = self._aggregate_for_display(byte_deltas, resolve_primary=False)
                self.widget_state.add_usage_bytes(agg_up_bytes, agg_down_bytes)

        upload_mbps = (agg_upload * 8) / 1_000_000
        download_mbps = (agg_download * 8) / 1_000_000
        
        self.display_speed_updated.emit(upload_mbps, download_mbps)

        self.last_check_time = current_time
        self.last_interface_counters = current_counters


    def get_active_interfaces(self) -> List[str]:
        """Returns active interface names."""
        if not self.current_speed_data:
            return []
        return [name for name, (up_speed, down_speed) in self.current_speed_data.items() if up_speed > 1.0 or down_speed > 1.0]


    def _aggregate_for_display(self, per_interface_speeds: Dict[str, Tuple[float, float]],
                               resolve_primary: bool = True) -> Tuple[float, float]:
        """Aggregates speeds based on mode. `resolve_primary=False` reuses the already-
        resolved primary interface (auto mode) without re-running the blocking routing
        lookup — used for the second (byte-delta) aggregation in the same poll."""
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
            exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            return total_up, total_down

        elif mode in ("all_virtual", "virtual"):
            # Sum every interface (physical + virtual). Both names are accepted: the settings
            # page persists "all_virtual"; "virtual" is the legacy/docs alias.
            return self._sum_all(per_interface_speeds)

        else:
            self.logger.warning("Unknown interface_mode %r; summing all interfaces as a fallback.", mode)
            return self._sum_all(per_interface_speeds)


    def _sum_all(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """Sums all speeds."""
        total_up = sum(up for up, down in per_interface_speeds.values())
        total_down = sum(down for up, down in per_interface_speeds.values())
        return total_up, total_down


    _PRIMARY_REFRESH_SEC: float = 15.0  # how often the routing lookup may actually run

    def _update_primary_interface_name(self) -> None:
        """Resolve the primary (routing) interface, CACHED so the blocking lookup
        (`get_primary_interface_name()` does a UDP connect + `net_if_addrs`) runs at most
        every _PRIMARY_REFRESH_SEC instead of every poll — keeping the GUI thread
        responsive in the default 'auto' mode (H1). A NIC change is picked up within the
        refresh window (the speed briefly attributes to the old primary)."""
        now = time.monotonic()
        if self.primary_interface is not None and (now - self.last_primary_check_time) < self._PRIMARY_REFRESH_SEC:
            return
        self.last_primary_check_time = now
        previous = self.primary_interface
        try:
            self.primary_interface = get_primary_interface_name()
        except Exception:
            self.primary_interface = None
        if previous != self.primary_interface:
            self.logger.info(
                "Primary network interface changed: %r -> %r", previous, self.primary_interface
            )


    def get_available_interfaces(self) -> List[str]:
        """Returns available interfaces for UI."""
        try:
            all_if = psutil.net_io_counters(pernic=True).keys()
            exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
            return sorted([n for n in all_if if not any(kw in n.lower() for kw in exclusions)])
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
                ):
                    if hasattr(self.view, slot):
                        sig.disconnect(getattr(self.view, slot))
            except (TypeError, RuntimeError):
                pass
            self.view = None
        self.last_interface_counters.clear()
