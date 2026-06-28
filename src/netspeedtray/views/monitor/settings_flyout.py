"""
MonitorSettingsFlyout — the in-window "gear" panel for the Monitor's display options.

A Fluent flyout (Qt.Popup: closes on click-outside) anchored under the gear. ONE instance is reused
across opens (the window caches it); ``refresh()`` re-reads the current config/theme into the controls
before each show. Changes apply LIVE — each control writes its config key (persisted via the widget's
config_controller) and emits ``changed`` so the Monitor re-renders the active graph. Matplotlib-free.

6.2a covers the combined-graph essentials (CPU/GPU line colours with a vendor "Auto" reset, and the
legend toggle). 6.2b/6.2c add the graph mode, smoothing, axis, cadence, theme and open-on-startup
here and on a mirrored Settings → Monitor page.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.components import ColorField, Win11Toggle
from netspeedtray.utils import hardware_vendors as hv


class MonitorSettingsFlyout(QFrame):
    """Live display-options popup for the Monitor window (reused across opens)."""

    changed = pyqtSignal()   #: a setting changed — the Monitor should re-render its active graph

    def __init__(self, main_widget, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self._mw = main_widget
        self._config = config
        self._i18n = i18n
        # Vendor-hue lookup MUST track the graph's own theme (config['dark_mode']), the same bool
        # GraphHost._hw_styles uses — NOT the OS theme — or the "Auto" swatch would advertise one
        # shade while the graph draws its sibling.
        self._dark = bool(config.get("dark_mode", True))
        c = su.semantic_colors()

        self.setObjectName("monSettings")
        self.setStyleSheet(
            f"#monSettings {{ background: {c['card_bg']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(6)

        root.addWidget(self._heading(self._tr("MONITOR_SETTINGS_GRAPH", "Graph"), c))
        scope = QLabel(self._tr("MONITOR_SETTINGS_SCOPE", "Applies to the Hardware graph"))
        scope.setFont(su.font(tokens.TYPE_CAPTION))
        scope.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(scope)
        root.addSpacing(6)

        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(12)

        line = self._tr("MONITOR_LINE", "line")
        self._cpu = ColorField(config.get("monitor_cpu_graph_color") or hv.default_color("cpu", self._dark))
        self._cpu.colorChanged.connect(lambda h: self._set("monitor_cpu_graph_color", h))
        grid.addWidget(self._label(f"{self._tr('ORDER_TYPE_CPU', 'CPU')} {line}", c), 0, 0)
        grid.addWidget(self._cpu, 0, 1)
        grid.addWidget(self._auto_btn(lambda: self._reset("monitor_cpu_graph_color", "cpu", self._cpu), c), 0, 2)

        self._gpu = ColorField(config.get("monitor_gpu_graph_color") or hv.default_color("gpu", self._dark))
        self._gpu.colorChanged.connect(lambda h: self._set("monitor_gpu_graph_color", h))
        grid.addWidget(self._label(f"{self._tr('ORDER_TYPE_GPU', 'GPU')} {line}", c), 1, 0)
        grid.addWidget(self._gpu, 1, 1)
        grid.addWidget(self._auto_btn(lambda: self._reset("monitor_gpu_graph_color", "gpu", self._gpu), c), 1, 2)

        # Legend toggle shares the Auto buttons' right edge (col 2) for one Fluent alignment guide.
        grid.addWidget(self._label(self._tr("MONITOR_SHOW_LEGEND", "Show legend"), c), 2, 0)
        self._legend = Win11Toggle("", bool(config.get("monitor_graph_legend", True)))
        self._legend.toggled.connect(lambda on: self._set("monitor_graph_legend", bool(on)))
        grid.addWidget(self._legend, 2, 2, Qt.AlignmentFlag.AlignRight)
        root.addLayout(grid)

        self.setFixedWidth(272)

    # --- live state ------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the current config + theme into the colour swatches (the cached flyout may have
        been built under a now-changed theme). The legend toggle is flyout-owned, so it can't stale."""
        self._dark = bool(self._config.get("dark_mode", True))
        for field, key, role in ((self._cpu, "monitor_cpu_graph_color", "cpu"),
                                  (self._gpu, "monitor_gpu_graph_color", "gpu")):
            field.blockSignals(True)
            field.setColor(self._config.get(key) or hv.default_color(role, self._dark))
            field.blockSignals(False)

    # --- helpers ---------------------------------------------------------------
    def _heading(self, text: str, c: dict) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
        lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        return lbl

    def _label(self, text: str, c: dict) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(su.font(tokens.TYPE_BODY))
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        return lbl

    def _auto_btn(self, fn, c: dict) -> QPushButton:
        b = QPushButton(self._tr("MONITOR_COLOR_AUTO", "Auto"))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(self._tr("MONITOR_COLOR_AUTO_TIP", "Reset to the auto-detected vendor colour"))
        # Legible at rest (text_primary), accent on hover — not "disabled-until-hovered".
        b.setStyleSheet(
            f"QPushButton {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" padding: 3px 10px; }} QPushButton:hover {{ color: {c['accent']}; border-color: {c['accent']}; }}")
        b.clicked.connect(fn)
        return b

    def _set(self, key: str, value) -> None:
        self._config[key] = value   # belt-and-suspenders: in-memory update even if persistence below fails
        try:
            self._mw.config_controller.update_config({key: value}, apply_and_repaint=False)
        except Exception:
            pass
        self.changed.emit()

    def _reset(self, key: str, role: str, field: ColorField) -> None:
        field.blockSignals(True)
        field.setColor(hv.default_color(role, self._dark))   # show the vendor hue
        field.blockSignals(False)
        self._set(key, None)                                 # but store None == "auto"

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
