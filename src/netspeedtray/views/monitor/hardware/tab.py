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
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.views.monitor.hardware.list import HardwareBarList
from netspeedtray.views.monitor.hardware.feed import HardwareFeed
from netspeedtray.views.monitor.network.header import PeriodSegmentedControl


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

        # Period control — the graph's timeline (shared with Network: the Monitor has one window).
        # Without this the combined graph silently inherited Network's period with no affordance.
        self._period_key = constants.data.history_period.PERIOD_MAP.get(
            int(config.get("history_period_slider_value", 2)), "TIMELINE_24_HOURS")
        c = su.semantic_colors()
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 4, 0)
        self._caption = QLabel(self._period_label(self._period_key))
        self._caption.setFont(su.font(tokens.TYPE_CAPTION))
        self._caption.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        top.addWidget(self._caption, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self._pills = PeriodSegmentedControl(self._period_key)
        self._pills.period_changed.connect(self._host.set_period)
        self._pills.period_changed.connect(self._on_period)
        top.addWidget(self._pills, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._plot_slot = QWidget()           # the shared canvas mounts here on show
        QVBoxLayout(self._plot_slot).setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(self._plot_slot)
        self._list = HardwareBarList(i18n)
        splitter.addWidget(self._list)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([360, 235])         # graph-favoured, fits the default 620px window
        root.addWidget(splitter, 1)

        self._feed = HardwareFeed(self)
        self._feed.payload_ready.connect(self._list.set_payload)
        self._feed.unavailable.connect(self._list.set_unavailable)

    def _period_label(self, period_key: str) -> str:
        default = period_key.replace("TIMELINE_", "").replace("_", " ").title()
        return str(getattr(self._i18n, period_key, default))

    def _on_period(self, value: int) -> None:
        self._period_key = constants.data.history_period.PERIOD_MAP.get(int(value), "TIMELINE_24_HOURS")
        self._caption.setText(self._period_label(self._period_key))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The period is shared with Network — re-sync our pills + caption to the host's current value
        # so a change made on the Network tab is reflected here.
        try:
            pv = getattr(self._host, "_history_period_value", None)
            if pv is not None:
                key = constants.data.history_period.PERIOD_MAP.get(int(pv), self._period_key)
                self._period_key = key
                self._pills.set_period_key(key, emit=False)
                self._caption.setText(self._period_label(key))
        except Exception:
            pass
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
