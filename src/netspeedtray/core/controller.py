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
    cpu_temp_updated = pyqtSignal(float)
    gpu_temp_updated = pyqtSignal(float)
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

        self.logger.debug("StatsController initialized.")


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
            cpu_temp = stats.get('cpu_temp')
            gpu_temp = stats.get('gpu_temp')
            ram_used = stats.get('ram_used')
            ram_total = stats.get('ram_total')
            vram_used = stats.get('vram_used')
            vram_total = stats.get('vram_total')
            
            # Emit signals for UI components
            if cpu is not None:
                self.cpu_usage_updated.emit(cpu)
            if gpu is not None:
                self.gpu_usage_updated.emit(gpu)
            if cpu_temp is not None:
                self.cpu_temp_updated.emit(cpu_temp)
            if self.widget_state:
                self.widget_state.add_hardware_stat('gpu', gpu_val)
            self.gpu_usage_updated.emit(gpu_val)
            if gpu_temp is not None:
                self.gpu_temp_updated.emit(gpu_temp)
                
            # Group VRAM with GPU
            if 'vram_used' in stats and 'vram_total' in stats:
                self.vram_info_updated.emit(stats['vram_used'], stats['vram_total'])


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

        for name, current in current_counters.items():
            last = self.last_interface_counters.get(name)
            if last:
                if current.bytes_sent < last.bytes_sent or current.bytes_recv < last.bytes_recv:
                    continue

                up_diff = current.bytes_sent - last.bytes_sent
                down_diff = current.bytes_recv - last.bytes_recv
                safe_time_diff = max(time_diff, constants.network.speed.MIN_TIME_DIFF)

                up_speed_bps = int(up_diff / safe_time_diff)
                down_speed_bps = int(down_diff / safe_time_diff)
                
                max_speed_bps = constants.network.interface.MAX_REASONABLE_SPEED_BPS
                
                try:
                    if_stats = psutil.net_if_stats()
                    if name in if_stats:
                        link_speed_mbps = if_stats[name].speed
                        if link_speed_mbps > 0:
                            max_speed_bps = int((link_speed_mbps * 1_000_000 / 8) * 1.05)
                except Exception:
                    pass

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


    def _aggregate_for_display(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """Aggregates speeds based on mode."""
        mode = self.config.get("interface_mode", "auto")

        if mode == "selected":
            selected = self.config.get("selected_interfaces", [])
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if name in selected)
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if name in selected)
            return total_up, total_down

        elif mode == "auto":
            self._update_primary_interface_name()
            return per_interface_speeds.get(self.primary_interface, (0.0, 0.0)) if self.primary_interface else (0.0, 0.0)

        elif mode == "all_physical":
            exclusions = self.config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS)
            total_up = sum(up for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            total_down = sum(down for name, (up, down) in per_interface_speeds.items() if not any(kw in name.lower() for kw in exclusions))
            return total_up, total_down

        else: # virtual or unknown
            return self._sum_all(per_interface_speeds)


    def _sum_all(self, per_interface_speeds: Dict[str, Tuple[float, float]]) -> Tuple[float, float]:
        """Sums all speeds."""
        total_up = sum(up for up, down in per_interface_speeds.values())
        total_down = sum(down for up, down in per_interface_speeds.values())
        return total_up, total_down


    def _update_primary_interface_name(self) -> None:
        """Updates primary interface."""
        try:
            self.primary_interface = get_primary_interface_name()
        except Exception:
            self.primary_interface = None


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
        """Cleanup resources."""
        if self.view:
            try:
                self.display_speed_updated.disconnect(self.view.update_display_speeds)
                if hasattr(self.view, 'update_cpu_usage'):
                    self.cpu_usage_updated.disconnect(self.view.update_cpu_usage)
                if hasattr(self.view, 'update_gpu_usage'):
                    self.gpu_usage_updated.disconnect(self.view.update_gpu_usage)
            except (TypeError, RuntimeError):
                pass
            self.view = None
        self.last_interface_counters.clear()
