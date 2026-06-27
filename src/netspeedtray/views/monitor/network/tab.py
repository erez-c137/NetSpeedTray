"""
NetworkTab — the Monitor's Network tab.

5.0: the live history graph, hosted via the shared GraphHost (matplotlib loads on first show, never
before). 5.1 adds the header band (machine-wide NIC totals + timeline control) and the per-app
AppBarList below a splitter. Imports nothing from views.graph at module scope — the graph enters
only when GraphHost.attach_to() runs on showEvent.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout


class NetworkTab(QWidget):
    """Hosts the shared GraphHost canvas for the "network" stat. Activates on show, idles on hide."""

    stat_type = "network"

    def __init__(self, graph_host, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._host = graph_host
        self._config = config
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # The canvas mounts here when the tab becomes visible.
        self._plot_slot = QWidget()
        QVBoxLayout(self._plot_slot).setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._plot_slot, 1)

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
        # The GraphHost is owned + fully torn down by the MonitorWindow; here we just stop our loop.
        try:
            self._host.stop_realtime()
        except Exception:
            pass
