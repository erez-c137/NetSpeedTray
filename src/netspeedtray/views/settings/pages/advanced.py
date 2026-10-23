"""
Advanced Settings Page (2.0 IA) - data history, app-behavior flags, and a fresh start.

The progressive-disclosure valve for genuinely low-frequency / destructive items:
data retention, hardware-history recording, "Delete all history", the app-wide reduce-motion
flag (also read by the Monitor window's hold-still logic), the opt-in local metrics endpoint
(Integrations, 2.2 scope B), and reset affordances.
"""
import logging
import os
from datetime import date
from pathlib import Path
from typing import Dict, Any, Callable, Optional

from PyQt6.QtCore import QObject, QStandardPaths, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QLabel, QPushButton, QGraphicsOpacityEffect, QSpinBox, QLineEdit,
                             QApplication, QFileDialog, QHBoxLayout, QMessageBox, QProgressBar)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, SettingCard, Win11ComboBox, ReflowRow
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils import helpers
from netspeedtray.views.settings.pages._fluent import section_header, page_layout


class _BackupSignals(QObject):
    """Carries the backup thread's progress and result to the GUI thread (queued: emitted elsewhere)."""
    progress = pyqtSignal(str)                 # history_backup.STEP_*
    finished = pyqtSignal(object, str)         # (BackupResult or None, BackupError code)


class AdvancedPage(QWidget):
    """Retention + reduce-motion + reset (C6)."""

    def __init__(self, i18n, on_change: Callable[[], None],
                 reset_page_callback: Optional[Callable[[], None]] = None,
                 reset_all_callback: Optional[Callable[[], None]] = None,
                 delete_history_callback: Optional[Callable[[], None]] = None,
                 history_path_callback: Optional[Callable[[], Optional[Path]]] = None,
                 notify_callback: Optional[Callable[[str, str, Optional[str]], None]] = None):
        """``history_path_callback``: the live history file (``WidgetState.history_db_path``), or None
        when there is none - read by "Back up history" (#334), which never writes to it.
        ``notify_callback(title, body, path_or_None)``: reports a backup that finishes while Settings is
        closed, without taking focus (the widget's notice flyout; ``path`` adds "Open folder")."""
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._reset_page_cb = reset_page_callback
        self._reset_all_cb = reset_all_callback
        self._delete_history_cb = delete_history_callback
        self._history_path_cb = history_path_callback
        self._notify_cb = notify_callback
        self._backup_thread = None      # the running backup's threading.Thread
        self._backup_signals = _BackupSignals(self)
        self._backup_signals.progress.connect(self._on_backup_progress)
        self._backup_signals.finished.connect(self._on_backup_finished)
        self.logger = logging.getLogger("NetSpeedTray.AdvancedPage")
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

        # #334: a consistent copy of the history file to a file the user picks (moving to a new PC).
        # Also an action: read-only on the live file, on a worker thread, progress in the card.
        self.backup_history_btn = QPushButton(self.i18n.BACKUP_HISTORY_BUTTON)
        self.backup_history_btn.setStyleSheet(style_utils.button_style())
        self.backup_history_btn.clicked.connect(self._on_backup_clicked)
        self.backup_history_btn.setEnabled(self._history_path_cb is not None)
        self._backup_busy = QProgressBar()
        self._backup_busy.setRange(0, 0)                 # indeterminate: VACUUM INTO reports no fraction
        self._backup_busy.setTextVisible(False)
        self._backup_busy.setFixedSize(96, 4)
        self._backup_busy.setVisible(False)
        backup_ctl = QWidget()
        row = QHBoxLayout(backup_ctl)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self._backup_busy)
        row.addWidget(self.backup_history_btn)
        self._backup_card = SettingCard(self.i18n.BACKUP_HISTORY_LABEL,
                                        description=self.i18n.BACKUP_HISTORY_SUB, control=backup_ctl)
        layout.addWidget(self._backup_card)

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

    # --- Back up history (#334) -------------------------------------------------------------------

    # BackupError.code -> the sentence shown under "Could not back up history:" (the raw detail is
    # logged, never shown: it holds full paths and English WinError text).
    _BACKUP_REASON_KEYS = {
        "refused": "BACKUP_HISTORY_ERR_REFUSED",
        "no_file": "BACKUP_HISTORY_NO_FILE",
        "read": "BACKUP_HISTORY_ERR_READ",
        "verify": "BACKUP_HISTORY_ERR_VERIFY",
        "io": "BACKUP_HISTORY_ERR_IO",
        "busy": "BACKUP_HISTORY_ERR_BUSY",
    }

    def backup_running(self) -> bool:
        return self._backup_thread is not None and self._backup_thread.is_alive()

    def _on_backup_clicked(self) -> None:
        from netspeedtray.core.db import history_backup
        if self.backup_running():
            return
        src = self._history_path_cb() if self._history_path_cb else None
        if src is None or not Path(src).is_file():
            self._report_backup(None, "no_file")
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or ""
        suggested = os.path.join(folder, history_backup.default_backup_name(date.today()))
        dest, _sel = QFileDialog.getSaveFileName(
            self, self.i18n.BACKUP_HISTORY_SAVE, suggested, f"{self.i18n.BACKUP_HISTORY_FILTER} (*.db)")
        if not dest:
            return
        if not dest.lower().endswith(".db"):
            dest += ".db"
        self.start_backup(Path(src), Path(dest))

    def start_backup(self, src: Path, dest: Path) -> None:
        """Run ``history_backup.backup_history`` on a daemon thread the app's quit can cancel
        (``history_backup.cancel_all`` from the widget's cleanup): it interrupts the copy, which then
        deletes its unfinished file. The live file is only ever read."""
        from netspeedtray.core.db import history_backup
        sig = self._backup_signals
        log = self.logger

        def emit(signal, *args) -> None:
            # Settings may be gone entirely by now (the page's signal object deleted): the backup still
            # finishes and logs its result; there is just nothing left to report it in.
            try:
                signal.emit(*args)
            except RuntimeError:
                pass

        def work() -> None:
            try:
                res = history_backup.backup_history(src, dest, progress=lambda s: emit(sig.progress, s))
            except history_backup.BackupError as e:
                emit(sig.finished, None, e.code)
            except Exception as e:      # never let the thread die silently: the card would stay busy
                log.error("History backup crashed: %s", e, exc_info=True)
                emit(sig.finished, None, history_backup.ERR_IO)
            else:
                emit(sig.finished, res, "")

        self.backup_history_btn.setEnabled(False)
        self._backup_busy.setVisible(True)
        self._backup_card.set_description(self.i18n.BACKUP_HISTORY_COPYING)
        self._backup_thread = history_backup.run_in_thread(work)

    def _on_backup_progress(self, step: str) -> None:
        from netspeedtray.core.db import history_backup
        self._backup_card.set_description(self.i18n.BACKUP_HISTORY_VERIFYING
                                          if step == history_backup.STEP_VERIFY
                                          else self.i18n.BACKUP_HISTORY_COPYING)

    def _on_backup_finished(self, result, code: str) -> None:
        self._backup_thread = None
        self._backup_busy.setVisible(False)
        self.backup_history_btn.setEnabled(True)
        self._backup_card.set_description(self.i18n.BACKUP_HISTORY_SUB)
        if result is None and code == "cancelled":
            return                      # the app is quitting: nothing to tell
        self._report_backup(str(result.path) if result is not None else None, code)

    def _report_backup(self, path: Optional[str], code: str) -> None:
        """Success (``path``) or failure (``code``). While Settings is on screen: a message box over it.
        Settings closed (it is hidden, not destroyed): the widget's notice flyout instead - an
        application-modal box with no visible parent would steal focus and block the Monitor."""
        if path is not None:
            title, body = self.i18n.BACKUP_HISTORY_LABEL, f"{self.i18n.BACKUP_HISTORY_DONE}\n{os.path.basename(path)}"
        else:
            reason = getattr(self.i18n, self._BACKUP_REASON_KEYS.get(code, "BACKUP_HISTORY_ERR_IO"))
            title, body = self.i18n.BACKUP_HISTORY_LABEL, f"{self.i18n.BACKUP_HISTORY_FAILED}\n{reason}"
        win = self.window()
        if win is None or not win.isVisible() or win.isMinimized():
            if self._notify_cb is not None:
                try:
                    self._notify_cb(title, body, path)
                except Exception as e:
                    self.logger.error("Could not show the backup result: %s", e, exc_info=True)
            return
        if path is None:
            QMessageBox.warning(self, title, body)
            return
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(body)
        open_btn = box.addButton(self.i18n.STATS_DETAIL_OPEN_FOLDER, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            helpers.reveal_in_explorer(path)

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
