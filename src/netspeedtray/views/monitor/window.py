"""
MonitorWindow - the unified Monitor (Overview / Network / Hardware).

The shell: native Win11 chrome, remembered geometry, a flat pivot tab-bar over a lazy
QStackedWidget. Each resource tab starts as a cheap placeholder; the real page (and, for chart
tabs, the matplotlib canvas via GraphHost) is built on first activation - so a glance at Overview
never imports matplotlib.

Import firewall: this module imports only Qt + app utils at module scope. The graph package is
imported lazily, inside the Network/Hardware tab factories, never here.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication, QLabel, QToolButton,
)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.dwm import apply_win11_chrome
from netspeedtray.utils.window_state import (
    restore_window_geometry, attach_geometry_memory, save_window_geometry,
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
        self._settings_flyout = None  # one reusable display-settings popup (not rebuilt per click)

        # Prefix the app name so the taskbar/Alt-Tab entry reads "NetSpeedTray Monitor" - consistent
        # with the Settings window ("NetSpeedTray Settings …").
        self.setWindowTitle(f"{constants.app.APP_NAME} {self._tr('MONITOR_WINDOW_TITLE', 'Monitor')}")
        # Default sized so the WHOLE wide Overview fits without scrolling (content ≈700px + chrome),
        # clamped to the screen so it never opens off-screen on a small display. The min is deliberately
        # small - every tab scrolls/reflows, so the window is safe at any size.
        self.setMinimumSize(660, 420)
        # Snug to the Overview's natural height (content ≈680 + chrome ≈90 → ~770; +30 breathing room),
        # so a fresh open shows the whole dashboard without scrolling AND without a big dead strip at the
        # bottom. Clamped to fit a 1080p screen. (Returning users keep their remembered size instead.)
        default_w, default_h = 960, 800
        scr = QApplication.primaryScreen()
        if scr is not None:
            avail = scr.availableGeometry()
            default_w = min(default_w, max(660, avail.width() - 60))
            default_h = min(default_h, max(420, avail.height() - 60))
        self.resize(default_w, default_h)
        self.setObjectName("monitorWindow")
        c = su.semantic_colors()
        self.setStyleSheet(f"#monitorWindow {{ background: {c['card_bg']}; }}")

        self._descriptors: List[LazyTabDescriptor] = self._build_descriptors()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header row: the pivot tab bar (left) + a gear that opens the live display-settings flyout.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 8, 0)
        header.setSpacing(0)
        # Segoe Fluent Icons per tab (Win10-safe MDL2 codepoints): Home / NetworkTower / DeveloperTools.
        self._tab_bar = FlatTabBar([(d.tab_id, d.label) for d in self._descriptors],
                                   icons={"overview": 0xE80F, "network": 0xEC05, "hardware": 0xEC7A})
        self._tab_bar.tab_selected.connect(self._on_tab_changed)
        header.addWidget(self._tab_bar)
        header.addStretch(1)
        self._gear = QToolButton()
        _gear_sp = self._gear.sizePolicy()
        _gear_sp.setRetainSizeWhenHidden(True)   # keep the header slot when hidden so hiding never reflows (#170)
        self._gear.setSizePolicy(_gear_sp)
        self._gear.setVisible(False)             # shown only on the Hardware tab (set in _on_tab_changed)
        self._gear.setText(chr(0xE713))   # Segoe Fluent Icons "Settings" - monochrome, obeys QSS colour
        self._gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gear.setToolTip(self._tr("MONITOR_SETTINGS_TIP", "Monitor display settings"))
        self._gear.setAccessibleName(self._tr("MONITOR_SETTINGS_TIP", "Monitor display settings"))
        self._gear.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {c['text_primary']};"
            f" font-family: 'Segoe Fluent Icons','Segoe MDL2 Assets'; font-size: 15px;"
            f" border: none; padding: 4px 6px; }} QToolButton:hover {{ color: {c['accent']}; }}"
            f" QToolButton:disabled {{ color: {c['text_secondary']}; }}")
        self._gear.clicked.connect(self._open_settings_flyout)
        header.addWidget(self._gear, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Seed the stack with cheap placeholders (indexes line up with descriptors).
        for i, d in enumerate(self._descriptors):
            d.stack_index = i
            self._stack.addWidget(self._placeholder())

        # Apply per-tab visibility. NOTE: the Hardware tab is intentionally ALWAYS visible - its
        # descriptor uses the default is_visible (lambda cfg: True) because the Monitor force-enables
        # hardware collection while it's open (see _build_descriptors). No tab is config-gated today.
        for d in self._descriptors:
            self._tab_bar.set_tab_visible(d.tab_id, d.is_visible(self.config))

        # Remembered geometry - position + SIZE + maximized state - multi-monitor safe, auto-saved on
        # move/resize/maximize.
        attach_geometry_memory(self, self._main_widget, _POS_KEY)
        if not restore_window_geometry(self, self.config, _POS_KEY):
            self._center_on_primary()

        # Open on the LAST tab the user left (if it's still visible), else the first visible one.
        saved_tab = self.config.get("monitor_active_tab")
        first = next((i for i, d in enumerate(self._descriptors)
                      if d.tab_id == saved_tab and d.is_visible(self.config)), None)
        if first is None:
            first = next((i for i, d in enumerate(self._descriptors) if d.is_visible(self.config)), 0)
        self._tab_bar.setCurrentIndex(first)

        # Apply the Win11 dark title bar + rounded corners BEFORE the caller's show(), so the window
        # never maps with the default light title bar and then flashes dark on the first frame. winId()
        # here forces the native handle to be realized early (it does NOT show the window). The showEvent
        # re-assert stays as an idempotent safety net (also covers an OS theme change between opens).
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    # ----------------------------------------------------------------- tabs

    def _build_descriptors(self) -> List[LazyTabDescriptor]:
        # The Monitor forces hardware collection while it's open (see _set_force_hardware), so the
        # Hardware tab always has data to show - it's a dedicated monitoring screen, not gated on
        # whether the taskbar widget happens to display hardware.
        return [
            LazyTabDescriptor("overview", self._tr("MONITOR_TAB_OVERVIEW", "Overview"),
                              factory=self._make_overview, needs_graph=False),
            LazyTabDescriptor("network", self._tr("MONITOR_TAB_NETWORK", "Network"),
                              factory=self._make_network, needs_graph=True),
            LazyTabDescriptor("hardware", self._tr("MONITOR_TAB_HARDWARE", "Hardware"),
                              factory=self._make_hardware, needs_graph=True),
        ]

    def select_tab(self, tab_id: str) -> None:
        """Programmatically switch tabs (e.g. an Overview hardware tile click drills into Hardware)."""
        for i, d in enumerate(self._descriptors):
            if d.tab_id == tab_id:
                self._tab_bar.setCurrentIndex(i)
                return

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
        # The display-settings gear only configures the Hardware graph, so it shows on the Hardware tab
        # and hides elsewhere (a visible-but-inert gear on Overview/Network confused users - #170). Its
        # layout slot is retained when hidden (setRetainSizeWhenHidden at construction), so hiding it
        # never reflows the header - which is what used to make it flicker in/out on pivot.
        is_hw = (d.tab_id == "hardware")
        self._gear.setVisible(is_hw)
        if not is_hw and self._settings_flyout is not None:
            self._settings_flyout.hide()
        # Remember the active tab so the Monitor reopens where the user left it.
        try:
            mgr = getattr(self._main_widget, "config_manager", None)
            if mgr is not None and self.config.get("monitor_active_tab") != d.tab_id:
                self.config["monitor_active_tab"] = d.tab_id
                mgr.save(self.config)
        except Exception:
            pass

    # --- tab factories (real pages, built lazily on first activation) ---

    def _make_overview(self) -> QWidget:
        from netspeedtray.views.monitor.overview.tab import OverviewTab
        return OverviewTab(self._main_widget, self.config, self.i18n, self)

    def _make_network(self) -> QWidget:
        from netspeedtray.views.monitor.network.tab import NetworkTab
        return NetworkTab(self._ensure_graph_host(), self._main_widget, self.config, self.i18n, self)

    def _ensure_graph_host(self):
        """The single shared graph engine, created lazily (matplotlib still doesn't load until a
        chart tab is actually shown - GraphHost.__init__ imports no graph package)."""
        if self._graph_host is None:
            from netspeedtray.views.monitor.graph_host import GraphHost
            self._graph_host = GraphHost(
                self._main_widget, self.config, self.i18n,
                session_start_time=getattr(self._main_widget, "session_start_time", None))
        return self._graph_host

    def _make_hardware(self) -> QWidget:
        from netspeedtray.views.monitor.hardware.tab import HardwareTab
        return HardwareTab(self._ensure_graph_host(), self._main_widget, self.config, self.i18n, self)

    # ----------------------------------------------------------------- settings flyout

    def _open_settings_flyout(self) -> None:
        """Open the live display-settings popup anchored under the gear (reused, not rebuilt per click)."""
        from PyQt6.QtGui import QGuiApplication
        from netspeedtray.views.monitor.settings_flyout import MonitorSettingsFlyout
        if self._settings_flyout is None:
            self._settings_flyout = MonitorSettingsFlyout(self._main_widget, self.config, self.i18n, self)
            self._settings_flyout.changed.connect(self._on_settings_changed)
        fly = self._settings_flyout
        fly.refresh()              # re-read the current config + theme into the controls
        fly.adjustSize()
        anchor = self._gear.mapToGlobal(self._gear.rect().bottomRight())
        x, y = anchor.x() - fly.width(), anchor.y() + 4
        scr = QGuiApplication.screenAt(anchor) or self.screen()
        if scr is not None:
            g = scr.availableGeometry()
            x = max(g.left(), min(x, g.right() - fly.width() + 1))
            if y + fly.height() > g.bottom():   # flip above the gear if it would overflow the bottom
                y = self._gear.mapToGlobal(self._gear.rect().topRight()).y() - fly.height() - 4
            y = max(g.top(), y)
        fly.move(x, y)
        fly.show()

    def _on_settings_changed(self) -> None:
        # Live-apply. The active page may need more than a re-render - a graph-mode change re-resolves
        # which stat is shown - so delegate to its on_settings_changed when it has one. Otherwise just
        # re-render the active graph (colour/legend/smoothing/axis). No-op until a chart tab built the host.
        idx = self._stack.currentIndex()
        page = self._descriptors[idx].page if 0 <= idx < len(self._descriptors) else None
        if page is not None and hasattr(page, "on_settings_changed"):
            try:
                page.on_settings_changed()
                return
            except Exception:
                pass
        if self._graph_host is not None:
            try:
                self._graph_host.update_graph(show_loading=False)
            except Exception:
                pass

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

    def _set_force_hardware(self, on: bool) -> None:
        """While the Monitor is open, force the stats thread to collect CPU/GPU/RAM/VRAM (+ temps)
        even when the taskbar widget has hardware monitoring off - this IS a dedicated monitoring
        screen, so it shows everything. Reverts to the widget's config when the Monitor closes."""
        try:
            self._main_widget.monitor_thread._force_hardware_collection = bool(on)
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_force_hardware(True)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._is_closing = True
        self._set_force_hardware(False)
        self.window_closed.emit()  # let the owner drop its ref before WA_DeleteOnClose destroys us
        try:
            save_window_geometry(self, self._main_widget, _POS_KEY)
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
