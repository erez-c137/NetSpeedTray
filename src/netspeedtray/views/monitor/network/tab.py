"""
NetworkTab — the Monitor's Network tab.

The header band (machine-wide Download/Upload totals for the selected period + a timeline control)
sits above the live history graph, which is hosted via the shared GraphHost — matplotlib loads on
first show, never before. Imports nothing from views.graph at module scope (the header is a
standalone, graph-free widget); the graph engine enters only when GraphHost.attach_to() runs on
showEvent. 5.1c will add the per-app AppBarList below a splitter.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout

from netspeedtray import constants
from netspeedtray.views.monitor.network.header import NetworkHeader


class NetworkTab(QWidget):
    """Header band + the shared GraphHost canvas for the "network" stat. Activates on show, idles on hide."""

    stat_type = "network"

    def __init__(self, graph_host, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._host = graph_host
        self._config = config
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header band — period totals + the timeline control that drives the shared graph.
        initial_key = constants.data.history_period.PERIOD_MAP.get(
            int(config.get("history_period_slider_value", 2)), "TIMELINE_24_HOURS")
        self._header = NetworkHeader(i18n, initial_key)
        self._header.period_changed.connect(self._host.set_period)
        self._host.network_totals_ready.connect(self._on_totals)
        root.addWidget(self._header)

        # The canvas mounts here when the tab becomes visible.
        self._plot_slot = QWidget()
        QVBoxLayout(self._plot_slot).setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._plot_slot, 1)

    def _on_totals(self, up_bytes: float, down_bytes: float, period_key: str) -> None:
        try:
            self._header.set_totals(up_bytes, down_bytes, period_key)
            self._header.set_period_key(period_key)  # keep pills in sync if the period changed elsewhere
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Mount the shared canvas into our slot + start live updates (lazy-loads the graph engine).
        try:
            self._host.attach_to(self._plot_slot.layout(), self.stat_type)
            self._host.start_realtime()
        except Exception:
            pass

    def hideEvent(self, event) -> None:
        # Pause live updates while another tab is showing (the host + canvas are reused, not torn down).
        try:
            self._host.stop_realtime()
        except Exception:
            pass
        super().hideEvent(event)

    def teardown(self) -> None:
        # The GraphHost is owned + fully torn down by the MonitorWindow; here we just stop our loop
        # and drop our cross-object signal so a late totals emit can't poke a deleted header.
        try:
            self._host.network_totals_ready.disconnect(self._on_totals)
        except Exception:
            pass
        try:
            self._host.stop_realtime()
        except Exception:
            pass
