"""
NetworkTab — the Monitor's Network tab.

Layout: a header band (machine-wide Download/Upload totals for the selected period + a timeline
control) over a vertical splitter — the live history graph on top, a per-app connection list below.
The graph is hosted via the shared GraphHost (matplotlib loads on first show, never before); the
per-app list is fed by AppActivityFeed (a reused psutil connection sampler). Nothing here imports
views.graph at module scope — the header, list, and feed are all standalone, graph-free widgets;
the graph engine enters only when GraphHost.attach_to() runs on showEvent.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter

from netspeedtray import constants
from netspeedtray.views.monitor.network.header import NetworkHeader
from netspeedtray.views.monitor.network.app_list import AppBarList
from netspeedtray.views.monitor.network.app_feed import AppActivityFeed
from netspeedtray.views.monitor.network.detail import ConnectionDetailPanel


class NetworkTab(QWidget):
    """Header + graph (shared GraphHost) + per-app connection list. Activates on show, idles on hide."""

    stat_type = "network"

    def __init__(self, graph_host, main_widget, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._host = graph_host
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n

        # The header band owns the top of the tab (origin + fixed height shared with the other tabs);
        # all body content lives under it with the common side inset. Keeping the band flush at the top
        # of the stack cell is what aligns it with Overview/Hardware.
        _m = constants.layout.MONITOR_BODY_MARGIN
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header band — period totals + the timeline control that drives the shared graph.
        initial_key = constants.data.history_period.PERIOD_MAP.get(
            int(config.get("history_period_slider_value", 2)), "TIMELINE_24_HOURS")
        self._header = NetworkHeader(i18n, initial_key, graph_host=self._host)
        self._header.period_changed.connect(self._host.set_period)
        self._header.interface_changed.connect(self._host.set_interface_filter)
        self._host.network_totals_ready.connect(self._on_totals)
        # Populate the NIC dropdown from the unified (live + historical) interface list.
        try:
            self._header.set_interfaces(self._main_widget.get_unified_interface_list())
        except Exception:
            pass
        root.addWidget(self._header)

        body = QVBoxLayout()
        body.setContentsMargins(_m, 4, _m, _m)
        body.setSpacing(8)

        # Graph (top) + per-app list (bottom), user-resizable.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._plot_slot = QWidget()           # the shared canvas mounts here on show
        QVBoxLayout(self._plot_slot).setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(self._plot_slot)

        # Bottom = the app list beside an on-demand detail panel (hidden until a row is clicked).
        self._bottom = QSplitter(Qt.Orientation.Horizontal)
        self._bottom.setChildrenCollapsible(False)
        self._app_list = AppBarList(i18n)
        self._app_list.setMinimumWidth(300)    # 180 name + 64 count + spacing/margins + the bar
        self._bottom.addWidget(self._app_list)
        self._detail = ConnectionDetailPanel(i18n)
        self._detail.setMinimumWidth(240)
        self._detail.hide()                    # appears only when an app is selected
        self._bottom.addWidget(self._detail)
        self._app_list.row_selected.connect(self._on_row_selected)
        self._detail.closed.connect(self._on_detail_closed)
        splitter.addWidget(self._bottom)
        splitter.setStretchFactor(0, 3)        # graph gets the lion's share
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 280])          # ~6-7 rows so the user's apps aren't below the fold
        body.addWidget(splitter, 1)
        root.addLayout(body, 1)

        # Per-app connection feed (reused psutil sampler) — polls only while this tab is visible.
        self._feed = AppActivityFeed(self)
        self._feed.payload_ready.connect(self._on_payload)
        self._feed.unavailable.connect(self._app_list.set_unavailable)

    # --- per-app detail-on-demand ----------------------------------------------
    def _on_payload(self, payload: Dict[str, Any]) -> None:
        # Feed the list, then keep the open detail panel live: re-read the selected app from the fresh
        # payload (its connections change every tick) or, if it dropped off, show it as inactive.
        self._app_list.set_payload(payload)
        key = self._app_list.selected_key()
        # isHidden() (not isVisible()) tracks the panel's own open/closed state independent of whether
        # the Monitor window happens to be shown — so the live refresh is correct + unit-testable.
        if key and not self._detail.isHidden():
            row = self._app_list.get_row(key)
            self._detail.set_row(row) if row else self._detail.mark_inactive()

    def _on_row_selected(self, key: str) -> None:
        row = self._app_list.get_row(key)
        if row is None:
            return
        self._detail.set_row(row)
        if self._detail.isHidden():
            self._detail.show()
            self._bottom.setSizes([320, 300])   # reveal the detail beside the list (~half each)

    def _on_detail_closed(self) -> None:
        self._detail.hide()
        self._app_list.clear_selection()

    def _on_totals(self, up_bytes: float, down_bytes: float, period_key: str) -> None:
        # Update the numbers (+ the window label) only. The pills are the period's source of truth —
        # syncing them from a totals emit would let a late reply flip the user's selection.
        try:
            self._header.set_totals(up_bytes, down_bytes, period_key)
        except Exception:
            pass

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
        # Pause both feeds while another tab shows (host + canvas reused, not torn down).
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
        # The GraphHost is owned + torn down by the MonitorWindow; here we stop our loops, fully
        # tear down the per-app feed thread, and drop cross-object signals so a late emit can't poke
        # a deleted header and a stray pill click can't reach a closing host.
        try:
            self._host.network_totals_ready.disconnect(self._on_totals)
        except Exception:
            pass
        try:
            self._header.period_changed.disconnect(self._host.set_period)
        except Exception:
            pass
        try:
            self._header.interface_changed.disconnect(self._host.set_interface_filter)
        except Exception:
            pass
        try:
            self._host.stop_realtime()
        except Exception:
            pass
        try:
            self._feed.teardown()
        except Exception:
            pass
