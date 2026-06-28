"""
HardwareTab — the Monitor's Hardware tab: a CPU+GPU history graph over a live per-process
CPU / RAM / GPU list (vertical splitter, mirroring the Network tab).

The graph is hosted by the shared GraphHost. Its layout follows the ``monitor_hw_graph_mode`` setting:
  • combined — CPU + GPU on one 0-100% axis (CPU solid, GPU dashed), vendor-coloured.  [default]
  • separate — CPU and GPU on two stacked axes, each vendor-coloured solid.
  • toggle   — one stat at a time (CPU **or** GPU), chosen by an in-header CPU|GPU switch.
Smoothing and the fixed/auto y-axis are read from config by the host's ``_hw_styles``. The per-process
list is fed by HardwareFeed. The tab imports nothing from views.graph — the graph engine enters only
via the shared host on showEvent.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.components import Win11Segmented
from netspeedtray.views.monitor.hardware.list import HardwareBarList
from netspeedtray.views.monitor.hardware.feed import HardwareFeed
from netspeedtray.views.monitor.network.header import PeriodSegmentedControl


class HardwareTab(QWidget):
    """CPU+GPU graph (mode-configurable) + per-process CPU/RAM/GPU list. Activates on show, idles on hide."""

    stat_type = "hwcombined"   # default; the live stat is resolved from monitor_hw_graph_mode

    def __init__(self, graph_host, main_widget, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._host = graph_host
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n
        # Which single stat the "toggle" mode shows (sticky for the window's lifetime).
        self._toggle_role = "cpu"

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
        # CPU|GPU switch — only meaningful (and only shown) in "toggle" mode.
        self._cpu_gpu = Win11Segmented([
            (str(getattr(i18n, "ORDER_TYPE_CPU", "CPU")), "cpu"),
            (str(getattr(i18n, "ORDER_TYPE_GPU", "GPU")), "gpu"),
        ])
        self._cpu_gpu.setValue(self._toggle_role)
        self._cpu_gpu.valueChanged.connect(self._on_cpu_gpu_toggle)
        self._cpu_gpu.setVisible(False)
        top.addWidget(self._cpu_gpu, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addSpacing(8)
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

    # --- graph mode (combined / separate / toggle) ----------------------------

    def _resolve_stat(self) -> str:
        """Map the configured graph mode to the host stat type."""
        mode = str(self._config.get("monitor_hw_graph_mode", "combined"))
        if mode == "separate":
            return "hwseparate"
        if mode == "toggle":
            return self._toggle_role   # "cpu" or "gpu"
        return "hwcombined"

    def _apply_graph(self, *, first_mount: bool = False) -> None:
        """Show the right stat for the current mode. On first mount we attach (reparent the shared
        canvas); afterwards we switch in place (set_stat) or just re-render (colours/legend/smoothing)."""
        stat = self._resolve_stat()
        self._cpu_gpu.setVisible(str(self._config.get("monitor_hw_graph_mode", "combined")) == "toggle")
        try:
            if first_mount:
                self._host.attach_to(self._plot_slot.layout(), stat)
                self._host.start_realtime()
            elif stat != getattr(self._host, "_current_stat", None):
                self._host.set_stat(stat)
            else:
                self._host.update_graph(show_loading=False)
        except Exception:
            pass

    def _on_cpu_gpu_toggle(self, role) -> None:
        self._toggle_role = str(role)
        self._apply_graph()

    def on_settings_changed(self) -> None:
        """Called by the Monitor window when the display-settings flyout changes anything (mode,
        colours, legend, smoothing, axis). Re-resolve the mode + re-render."""
        self._apply_graph()

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
        self._apply_graph(first_mount=True)
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
