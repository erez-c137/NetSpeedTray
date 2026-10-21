"""
Advanced Settings Page (2.0 IA) - data history, app-behavior flags, and a fresh start.

The progressive-disclosure valve for genuinely low-frequency / destructive items:
data retention, hardware-history recording, "Delete all history", the app-wide reduce-motion
flag (also read by the Monitor window's hold-still logic), the opt-in local metrics endpoint
(Integrations, 2.2 scope B), and reset affordances.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QWidget, QLabel, QPushButton, QGraphicsOpacityEffect, QSpinBox, QLineEdit,
                             QApplication)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, SettingCard, Win11ComboBox, ReflowRow
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils import helpers
from netspeedtray.views.settings.pages._fluent import section_header, page_layout


class AdvancedPage(QWidget):
    """Retention + reduce-motion + reset (C6)."""

    def __init__(self, i18n, on_change: Callable[[], None],
                 reset_page_callback: Optional[Callable[[], None]] = None,
                 reset_all_callback: Optional[Callable[[], None]] = None,
                 delete_history_callback: Optional[Callable[[], None]] = None):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._reset_page_cb = reset_page_callback
        self._reset_all_cb = reset_all_callback
        self._delete_history_cb = delete_history_callback
        self._days_map = constants.data.retention.DAYS_MAP  # {0:1, ..., 6:365}
        self._setup_ui()

    def _setup_ui(self) -> None:
        # 2.0 IA: Win11 Settings cards under light section captions (was QGroupBox frames). Control
        # objects + load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Data ---
        layout.addWidget(section_header(self.i18n.ADVANCED_DATA_GROUP))
        self.keep_data = Win11ComboBox()
        for idx in sorted(self._days_map):
            days = self._days_map[idx]
            self.keep_data.addItem(self._retention_label(days), userData=days)
        self.keep_data.setMinimumWidth(180)
        self.keep_data.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ADVANCED_KEEP_HISTORY_LABEL, control=self.keep_data))

        # Privacy (2.2 item C): what gets recorded, and a way to erase it. Recording follows the
        # Hardware page's monitoring switches - this toggle can only narrow it, never widen it.
        self.record_hardware = Win11Toggle(label_text="")
        self.record_hardware.toggled.connect(self.on_change)
        self._record_card = SettingCard(self.i18n.RECORD_HARDWARE_HISTORY_LABEL,
                                        description=self.i18n.RECORD_HARDWARE_HISTORY_SUB,
                                        control=self.record_hardware)
        layout.addWidget(self._record_card)

        # An action, not a setting: runs right after its own confirmation (the dialog's Cancel does
        # not undo it), so it is not part of load/get_settings.
        self.delete_history_btn = QPushButton(self.i18n.DELETE_HISTORY_BUTTON)
        self.delete_history_btn.setStyleSheet(style_utils.button_style())
        if self._delete_history_cb:
            self.delete_history_btn.clicked.connect(self._delete_history_cb)
        layout.addWidget(SettingCard(self.i18n.DELETE_HISTORY_LABEL,
                                     description=self.i18n.DELETE_HISTORY_SUB,
                                     control=self.delete_history_btn))

        # --- Behavior ---
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        self.reduce_motion = Win11Toggle(label_text="")
        self.reduce_motion.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ADVANCED_REDUCE_MOTION_LABEL, control=self.reduce_motion))

        # Applies to the Settings dialog and the Monitor window together (#213).
        self.keep_windows_on_top = Win11Toggle(label_text="")
        self.keep_windows_on_top.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.KEEP_WINDOWS_ON_TOP_LABEL,
                                     control=self.keep_windows_on_top))

        # (Hover usage, hover tips and Pause-in-menu moved to General > Interaction, 2.2 IA pass: they
        # are everyday widget-interaction toggles, so "Reset this page" no longer covers them.)

        # --- Integrations (2.2 scope B): the opt-in local metrics endpoint ---
        self._build_integrations(layout)

        # --- Reset ---
        layout.addWidget(section_header(self.i18n.ADVANCED_RESET_GROUP))
        note = QLabel(self.i18n.ADVANCED_RESET_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {style_utils.semantic_colors()['text_secondary']};"
            f" background: transparent; padding: 0 2px;")
        layout.addWidget(note)
        self.reset_page_btn = QPushButton(self.i18n.ADVANCED_RESET_PAGE_BUTTON)
        self.reset_all_btn = QPushButton(self.i18n.ADVANCED_RESET_ALL_BUTTON)
        self.reset_page_btn.setStyleSheet(style_utils.button_style())
        self.reset_all_btn.setStyleSheet(style_utils.button_style())
        if self._reset_page_cb:
            self.reset_page_btn.clicked.connect(self._reset_page_cb)
        if self._reset_all_cb:
            self.reset_all_btn.clicked.connect(self._reset_all_cb)
        # Stacks the two buttons when the window is too narrow for both (es_ES needs ~500 px).
        layout.addWidget(ReflowRow([self.reset_page_btn, self.reset_all_btn]))

        layout.addStretch()

    def _build_integrations(self, layout) -> None:
        """Tier 1: the toggle (its description is the one-line privacy note). Tier 2, shown only while
        it is on: the port and the address to point a scraper at, with a copy button and the live
        status (listening, or why it could not start)."""
        m = constants.network.metrics
        layout.addWidget(section_header(self.i18n.INTEGRATIONS_GROUP))
        self.metrics_enabled = Win11Toggle(label_text="")
        self.metrics_enabled.toggled.connect(self._on_metrics_toggled)
        layout.addWidget(SettingCard(self.i18n.METRICS_ENDPOINT_LABEL,
                                     description=self.i18n.METRICS_ENDPOINT_SUB,
                                     control=self.metrics_enabled))

        self.metrics_port = QSpinBox()
        self.metrics_port.setRange(m.MIN_PORT, m.MAX_PORT)
        self.metrics_port.setValue(m.DEFAULT_PORT)
        self.metrics_port.setKeyboardTracking(False)   # apply on Enter / focus-out, not per keystroke
        self.metrics_port.setMinimumWidth(110)
        self.metrics_port.valueChanged.connect(self._on_metrics_port_changed)
        self._metrics_port_card = SettingCard(self.i18n.METRICS_PORT_LABEL, control=self.metrics_port)
        layout.addWidget(self._metrics_port_card)

        self.metrics_url = QLineEdit()
        self.metrics_url.setReadOnly(True)
        self.metrics_url.setProperty("fieldStyle", "input")   # the port spin box's look (dialog_style)
        # Wide enough for "http://127.0.0.1:65535/metrics"; ReflowRow stacks the Copy button under it
        # when the window is too narrow (no sideways scroll in any locale, #356).
        self.metrics_url.setMinimumWidth(self.metrics_url.fontMetrics().horizontalAdvance(
            "http://127.0.0.1:65535/metrics") + 24 + 16)   # + the input style's 8 px side padding
        self.metrics_copy_btn = QPushButton(self.i18n.METRICS_COPY_BUTTON)
        self.metrics_copy_btn.setStyleSheet(style_utils.button_style())
        self.metrics_copy_btn.clicked.connect(self._copy_metrics_url)
        url_row = ReflowRow([self.metrics_url, self.metrics_copy_btn])
        self._metrics_url_card = SettingCard(self.i18n.METRICS_URL_LABEL, description=" ", control=url_row)
        layout.addWidget(self._metrics_url_card)
        self._metrics_status = None
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(1500)
        self._copied_timer.timeout.connect(
            lambda: self.metrics_copy_btn.setText(self.i18n.METRICS_COPY_BUTTON))
        self._refresh_metrics_rows()

    def metrics_url_text(self) -> str:
        m = constants.network.metrics
        return f"http://{m.HOST}:{int(self.metrics_port.value())}{m.PATH}"

    def _on_metrics_toggled(self, _on: bool = False) -> None:
        self._refresh_metrics_rows()
        self.on_change()

    def _on_metrics_port_changed(self, _v: int = 0) -> None:
        self._refresh_metrics_rows()
        self.on_change()

    def _copy_metrics_url(self) -> None:
        try:
            QApplication.clipboard().setText(self.metrics_url_text())
            self.metrics_copy_btn.setText(self.i18n.METRICS_COPIED)
            self._copied_timer.start()
        except Exception:
            pass

    def set_metrics_endpoint_status(self, status) -> None:
        """The running endpoint's status (core.metrics_endpoint.EndpointStatus) or None. Shown under
        the address only when it describes the port currently in the field."""
        self._metrics_status = status
        self._refresh_metrics_rows()

    def metrics_status_text(self) -> str:
        m = constants.network.metrics
        st = self._metrics_status
        port = int(self.metrics_port.value())
        if not self.metrics_enabled.isChecked() or st is None or getattr(st, "port", None) != port:
            return ""
        if st.state == m.STATE_RUNNING:
            return self.i18n.METRICS_STATUS_RUNNING
        if st.state == m.STATE_ERROR:
            key = {"in_use": "METRICS_STATUS_IN_USE", "denied": "METRICS_STATUS_DENIED"}.get(
                st.error, "METRICS_STATUS_ERROR")
            return getattr(self.i18n, key).format(port=port)
        return ""

    def _refresh_metrics_rows(self) -> None:
        on = self.metrics_enabled.isChecked()
        self._metrics_port_card.setVisible(on)
        self._metrics_url_card.setVisible(on)
        self.metrics_url.setText(self.metrics_url_text())
        self._metrics_url_card.set_description(self.metrics_status_text() or " ")

    def set_hardware_monitoring_active(self, active: bool) -> None:
        """Grey the Record-hardware-history card out while nothing that records hardware is on (it
        would show On while recording nothing). Active means CPU or GPU monitoring, or the battery
        hover toggle - with the PR's defaults CPU/GPU monitoring is off, so the battery toggle is
        the thing that governs battery recording, and a user who switched it off could not switch
        it back on (#323 review). The user's choice is kept - only shown as unavailable, the same
        Win11 dependent-control look the Hardware page uses."""
        self._record_card.setEnabled(active)
        eff = self._record_card.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(self._record_card)
            self._record_card.setGraphicsEffect(eff)
        eff.setOpacity(1.0 if active else 0.4)
        self._record_card.set_description(self.i18n.RECORD_HARDWARE_HISTORY_SUB if active
                                          else self.i18n.RECORD_HARDWARE_HISTORY_INACTIVE_SUB)

    def _retention_label(self, days: int) -> str:
        return helpers.format_retention_label(days, self.i18n)

    def load_settings(self, config: Dict[str, Any]) -> None:
        days = int(config.get("keep_data", constants.config.defaults.DEFAULT_KEEP_DATA_DAYS))
        idx = self.keep_data.findData(days)
        if idx < 0:
            # snap to the closest available retention value
            idx = min(range(self.keep_data.count()),
                      key=lambda i: abs(int(self.keep_data.itemData(i)) - days))
        self.keep_data.setCurrentIndex(idx)
        self.record_hardware.setChecked(bool(config.get("record_hardware_history", True)))
        # The battery hover toggle (General > Interaction) governs battery recording, so it
        # keeps the Record-hardware-history card live on its own (#323 review).
        self.set_hardware_monitoring_active(bool(config.get("monitor_cpu_enabled", False))
                                            or bool(config.get("monitor_gpu_enabled", False))
                                            or bool(config.get("power_display_enabled", False)))
        self.reduce_motion.setChecked(bool(config.get("reduce_motion", False)))
        self.keep_windows_on_top.setChecked(bool(config.get("keep_windows_on_top", False)))
        m = constants.network.metrics
        try:
            port = int(config.get("metrics_endpoint_port", m.DEFAULT_PORT))
        except (TypeError, ValueError):
            port = m.DEFAULT_PORT
        self.metrics_port.blockSignals(True)
        self.metrics_port.setValue(port if m.MIN_PORT <= port <= m.MAX_PORT else m.DEFAULT_PORT)
        self.metrics_port.blockSignals(False)
        self.metrics_enabled.setChecked(bool(config.get("metrics_endpoint_enabled", False)))
        self._refresh_metrics_rows()

    def get_settings(self) -> Dict[str, Any]:
        return {
            "keep_data": int(self.keep_data.currentData()),
            "record_hardware_history": self.record_hardware.isChecked(),
            "reduce_motion": self.reduce_motion.isChecked(),
            "keep_windows_on_top": self.keep_windows_on_top.isChecked(),
            "metrics_endpoint_enabled": self.metrics_enabled.isChecked(),
            "metrics_endpoint_port": int(self.metrics_port.value()),
        }
