"""
Per-application network activity window.

This window is opened on demand from the tray menu and shows active processes
with network connections, connection endpoints, and live usage estimates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QIcon, QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from netspeedtray import constants
from netspeedtray.utils import helpers
from netspeedtray.views.app_activity.worker import AppActivityWorker


class AppActivityWindow(QWidget):
    """Window that presents live per-process network activity details."""

    request_sample = pyqtSignal()
    window_closed = pyqtSignal()

    POLL_INTERVAL_MS = 1000

    def __init__(self, main_widget: QWidget | None, parent: QWidget | None = None, i18n=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.Window, True)

        self._main_widget = main_widget
        self.i18n = i18n
        self.logger = logging.getLogger("NetSpeedTray.AppActivityWindow")

        self._is_closing = False
        self._rows: List[Dict[str, Any]] = []

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self.POLL_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.request_sample.emit)

        self._build_ui()
        self._apply_icon()
        self._position_window()
        self._init_worker_thread()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{constants.app.APP_NAME} - {getattr(self.i18n, 'APP_USAGE_TAB_LABEL', 'App Usage')}")
        self.resize(980, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.summary_label = QLabel("Loading application activity...", self)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.summary_label)

        self.hint_label = QLabel(
            "Per-app speed is estimated from process I/O deltas while connections are active.",
            self,
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #808080;")
        root.addWidget(self.hint_label)

        self.table = QTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            [
                "Process",
                "PID",
                getattr(self.i18n, "DOWNLOAD_LABEL", "Download"),
                getattr(self.i18n, "UPLOAD_LABEL", "Upload"),
                "Connections",
                "Endpoints",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._update_details_for_selection)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        details_title = QLabel("Connection Details", self)
        root.addWidget(details_title)

        self.details_box = QPlainTextEdit(self)
        self.details_box.setReadOnly(True)
        self.details_box.setMinimumHeight(140)
        root.addWidget(self.details_box)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.refresh_button = QPushButton("Refresh", self)
        self.close_button = QPushButton("Close", self)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        root.addLayout(button_row)

        self.refresh_button.clicked.connect(self.request_sample.emit)
        self.close_button.clicked.connect(self.close)

    def _apply_icon(self) -> None:
        try:
            icon_path = helpers.get_app_asset_path(constants.app.ICON_FILENAME)
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
        except Exception as exc:
            self.logger.debug("Could not apply app icon: %s", exc)

    def _position_window(self) -> None:
        screen = None
        if self._main_widget is not None and hasattr(self._main_widget, "screen"):
            screen = self._main_widget.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        geom = screen.availableGeometry()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y() + (geom.height() - self.height()) // 2
        self.move(x, y)

    def _init_worker_thread(self) -> None:
        self.worker_thread = QThread(self)
        self.worker = AppActivityWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.data_ready.connect(self._on_data_ready)
        self.worker.error.connect(self._on_worker_error)
        self.request_sample.connect(self.worker.sample)
        self.worker_thread.start()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        QTimer.singleShot(0, self.request_sample.emit)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._is_closing = True
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        if hasattr(self, "worker_thread"):
            self.worker_thread.quit()
            self.worker_thread.wait(800)
        self.window_closed.emit()
        event.accept()

    def _on_data_ready(self, payload: Dict[str, Any]) -> None:
        if self._is_closing:
            return

        rows = payload.get("rows", [])
        self._rows = rows
        self._render_rows(rows)

        if rows:
            download_text = self._format_speed(float(payload.get("total_down_bps", 0.0)))
            upload_text = self._format_speed(float(payload.get("total_up_bps", 0.0)))
            updated_at = payload.get("updated_at", "--:--:--")
            self.summary_label.setText(
                f"Live now: {len(rows)} apps | Download {download_text} | Upload {upload_text} | Updated {updated_at}"
            )
            if self.table.rowCount() > 0 and not self.table.selectionModel().selectedRows():
                self.table.selectRow(0)
        else:
            empty_text = getattr(self.i18n, "NO_APP_DATA_MESSAGE", "No application usage data available.")
            self.summary_label.setText(empty_text)
            self.details_box.setPlainText(empty_text)

    def _on_worker_error(self, error_text: str) -> None:
        self.logger.error("App activity worker error: %s", error_text)
        message = getattr(self.i18n, "APP_USAGE_ERROR_MESSAGE", "Failed to load app usage data.")
        self.summary_label.setText(f"{message} ({error_text})")

    def _render_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            process_name = str(row.get("process_name", "Unknown"))
            pid = int(row.get("pid", 0))
            download_text = self._format_speed(float(row.get("download_bps", 0.0)))
            upload_text = self._format_speed(float(row.get("upload_bps", 0.0)))
            connections = str(int(row.get("connection_count", 0)))
            preview = str(row.get("endpoint_preview", "-"))

            row_values = [process_name, str(pid), download_text, upload_text, connections, preview]
            for col, value in enumerate(row_values):
                item = QTableWidgetItem(value)
                if col in (1, 2, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, col, item)

    def _update_details_for_selection(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        row_index = selected_rows[0].row()
        if row_index < 0 or row_index >= len(self._rows):
            return

        row = self._rows[row_index]
        process_name = str(row.get("process_name", "Unknown"))
        pid = int(row.get("pid", 0))
        endpoints = row.get("endpoints", [])
        if endpoints:
            detail_lines = [f"{idx + 1}. {endpoint}" for idx, endpoint in enumerate(endpoints)]
            details = "\n".join(detail_lines)
        else:
            details = "No connection details available."

        self.details_box.setPlainText(f"{process_name} (PID {pid})\n\n{details}")

    def _format_speed(self, speed_bps: float) -> str:
        if self.i18n is None:
            return f"{speed_bps:.0f} B/s"

        config = getattr(self._main_widget, "config", {}) if self._main_widget is not None else {}
        force_mega = str(config.get("speed_display_mode", "auto")) == "always_mbps"
        unit_type = str(config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE))
        short_labels = bool(config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS))
        decimal_places = int(config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES))
        decimal_places = max(0, min(2, decimal_places))

        return helpers.format_speed(
            speed_bps,
            self.i18n,
            force_mega_unit=force_mega,
            decimal_places=decimal_places,
            unit_type=unit_type,
            short_labels=short_labels,
        )
