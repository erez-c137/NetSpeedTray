"""
HardwareTab — the Monitor's Hardware tab: a live per-process CPU / RAM / GPU list.

Matplotlib-free by contract (its data is live per-process sampling, not a history chart — the
Overview's sparkline tiles and the Network graph cover history). Fed by HardwareFeed, which polls
only while this tab is visible and degrades gracefully under RDP.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout

from netspeedtray.views.monitor.hardware.list import HardwareBarList
from netspeedtray.views.monitor.hardware.feed import HardwareFeed


class HardwareTab(QWidget):
    """Per-process CPU/RAM/GPU list. Activates on show, idles on hide."""

    stat_type = "hardware"

    def __init__(self, main_widget, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._list = HardwareBarList(i18n)
        root.addWidget(self._list, 1)

        self._feed = HardwareFeed(self)
        self._feed.payload_ready.connect(self._list.set_payload)
        self._feed.unavailable.connect(self._list.set_unavailable)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            self._feed.start()
        except Exception:
            pass

    def hideEvent(self, event) -> None:
        try:
            self._feed.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def teardown(self) -> None:
        try:
            self._feed.teardown()
        except Exception:
            pass
