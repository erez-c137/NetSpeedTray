"""
MonitorWindow — the unified Monitor (Overview / Network / Hardware).

The shell: native Win11 chrome, remembered geometry, a flat pivot tab-bar over a lazy
QStackedWidget. Each resource tab starts as a cheap placeholder; the real page (and, for chart
tabs, the matplotlib canvas via GraphHost) is built on first activation — so a glance at Overview
never imports matplotlib.

Import firewall: this module imports only Qt + app utils at module scope. The graph package is
imported lazily, inside the Network/Hardware tab factories, never here.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QStackedWidget, QApplication, QLabel,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.dwm import apply_win11_chrome
from netspeedtray.utils.window_state import (
    restore_window_position, attach_position_memory, save_window_position,
)
from netspeedtray.views.monitor.tab_bar import FlatTabBar
from netspeedtray.views.monitor.lazy import LazyTabDescriptor

_POS_KEY = "monitor_window_pos"


class MonitorWindow(QWidget):
    """Top-level Monitor window. Created via the main widget; one per app (deleted on close)."""

    window_closed = pyqtSignal()  #: emitted in closeEvent so the owner clears its ref synchronously

    def __init__(self, main_widget, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._main_widget = main_widget
        self.config = config
        self.i18n = i18n
        self.logger = logging.getLogger("NetSpeedTray.MonitorWindow")
        self._is_closing = False
        self._graph_host = None  # one shared graph engine, built on first chart-tab activation

        self.setWindowTitle(self._tr("MONITOR_WINDOW_TITLE", "Monitor"))
        self.resize(900, 620)
        self.setMinimumSize(680, 440)
        self.setObjectName("monitorWindow")
        c = su.semantic_colors()
        self.setStyleSheet(f"#monitorWindow {{ background: {c['card_bg']}; }}")

        self._descriptors: List[LazyTabDescriptor] = self._build_descriptors()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tab_bar = FlatTabBar([(d.tab_id, d.label) for d in self._descriptors])
        self._tab_bar.tab_selected.connect(self._on_tab_changed)
        root.addWidget(self._tab_bar)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Seed the stack with cheap placeholders (indexes line up with descriptors).
        for i, d in enumerate(self._descriptors):
            d.stack_index = i
            self._stack.addWidget(self._placeholder())

        # Hardware tab is config-gated (hidden when no hardware monitoring is on).
        for d in self._descriptors:
            self._tab_bar.set_tab_visible(d.tab_id, d.is_visible(self.config))

        # Remembered geometry (multi-monitor safe) + auto-save on move.
        attach_position_memory(self, self._main_widget, _POS_KEY)
        if not restore_window_position(self, self.config, _POS_KEY):
            self._center_on_primary()

        # Open on the first visible tab (builds its page).
        first = next((i for i, d in enumerate(self._descriptors) if d.is_visible(self.config)), 0)
        self._tab_bar.setCurrentIndex(first)

    # ----------------------------------------------------------------- tabs

    def _build_descriptors(self) -> List[LazyTabDescriptor]:
        def hw_visible(cfg: Dict[str, Any]) -> bool:
            return bool(cfg.get("monitor_cpu_enabled") or cfg.get("monitor_gpu_enabled")
                        or cfg.get("monitor_ram_enabled") or cfg.get("monitor_vram_enabled"))
        return [
            LazyTabDescriptor("overview", self._tr("MONITOR_TAB_OVERVIEW", "Overview"),
                              factory=self._make_overview, needs_graph=False),
            LazyTabDescriptor("network", self._tr("MONITOR_TAB_NETWORK", "Network"),
                              factory=self._make_network, needs_graph=True),
            LazyTabDescriptor("hardware", self._tr("MONITOR_TAB_HARDWARE", "Hardware"),
                              factory=self._make_hardware, needs_graph=True, is_visible=hw_visible),
        ]

    def _on_tab_changed(self, index: int) -> None:
        if self._is_closing or not (0 <= index < len(self._descriptors)):
            return
        d = self._descriptors[index]
        if d.page is None:
            try:
                real = d.factory()
            except Exception as e:
                self.logger.error("Failed to build Monitor tab '%s': %s", d.tab_id, e, exc_info=True)
                return
            d.page = real
            old = self._stack.widget(index)
            self._stack.insertWidget(index, real)   # real now at `index`, old shifted to index+1
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.setCurrentIndex(index)

    # --- tab factories (real pages, built lazily on first activation) ---

    def _make_overview(self) -> QWidget:
        from netspeedtray.views.monitor.overview.tab import OverviewTab
        return OverviewTab(self._main_widget, self.config, self.i18n, self)

    def _make_network(self) -> QWidget:
        from netspeedtray.views.monitor.network.tab import NetworkTab
        return NetworkTab(self._ensure_graph_host(), self.config, self.i18n, self)

    def _ensure_graph_host(self):
        """The single shared graph engine, created lazily (matplotlib still doesn't load until a
        chart tab is actually shown — GraphHost.__init__ imports no graph package)."""
        if self._graph_host is None:
            from netspeedtray.views.monitor.graph_host import GraphHost
            self._graph_host = GraphHost(
                self._main_widget, self.config, self.i18n,
                session_start_time=getattr(self._main_widget, "session_start_time", None))
        return self._graph_host

    def _make_hardware(self) -> QWidget:
        from netspeedtray.views.monitor.hardware.tab import HardwareTab
        return HardwareTab(self._ensure_graph_host(), self._main_widget, self.config, self.i18n, self)

    # ----------------------------------------------------------------- helpers

    def _placeholder(self) -> QWidget:
        return QWidget()

    def _coming_soon(self, text: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {su.semantic_colors()['text_secondary']}; background: transparent;")
        lbl.setFont(su.font(tokens.TYPE_BODY))
        lay.addWidget(lbl)
        lay.addStretch(1)
        return w

    def _center_on_primary(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        g = screen.availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + (g.height() - self.height()) // 2)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self.i18n, key, default)) if self.i18n is not None else default

    # ----------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._is_closing = True
        self.window_closed.emit()  # let the owner drop its ref before WA_DeleteOnClose destroys us
        try:
            save_window_position(self, self._main_widget, _POS_KEY)
        except Exception:
            pass
        for d in self._descriptors:
            if d.page is not None and hasattr(d.page, "teardown"):
                try:
                    d.page.teardown()
                except Exception:
                    pass
        if self._graph_host is not None:
            try:
                self._graph_host.teardown()
            except Exception:
                pass
        event.accept()
