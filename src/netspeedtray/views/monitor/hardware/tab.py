"""
HardwareTab — the Monitor's Hardware tab: a combined CPU+GPU history graph over a live per-process
CPU / RAM / GPU list (vertical splitter, mirroring the Network tab).

The graph is hosted by the shared GraphHost (stat "hwcombined" — CPU + GPU on one 0-100% axis,
vendor-coloured: CPU solid, GPU dashed). The per-process list is fed by HardwareFeed. The tab module
imports nothing from views.graph — the graph engine enters only via the shared host on showEvent.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter

from netspeedtray.views.monitor.hardware.list import HardwareBarList
from netspeedtray.views.monitor.hardware.feed import HardwareFeed


class HardwareTab(QWidget):
    """Combined CPU+GPU graph + per-process CPU/RAM/GPU list. Activates on show, idles on hide."""

    stat_type = "hwcombined"

    def __init__(self, graph_host, main_widget, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._host = graph_host
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._plot_slot = QWidget()           # the shared canvas mounts here on show
        QVBoxLayout(self._plot_slot).setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(self._plot_slot)
        self._list = HardwareBarList(i18n)
        splitter.addWidget(self._list)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 300])
        root.addWidget(splitter, 1)

        self._feed = HardwareFeed(self)
        self._feed.payload_ready.connect(self._list.set_payload)
        self._feed.unavailable.connect(self._list.set_unavailable)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            self._host.attach_to(self._plot_slot.layout(), self.stat_type)
            self._host.start_realtime()
        except Exception:
            pass
        try:
            self._feed.start()
        except Exception:
            pass

    def hideEvent(self, event) -> None:
        try:
            self._host.stop_realtime()
        except Exception:
            pass
        try:
            self._feed.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def teardown(self) -> None:
        # GraphHost is owned + torn down by the MonitorWindow; we stop our loops + tear down the feed.
        try:
            self._host.stop_realtime()
        except Exception:
            pass
        try:
            self._feed.teardown()
        except Exception:
            pass
