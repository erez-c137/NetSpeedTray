"""
UsageHistorySheet - "Usage by day / month / year" (#334), opened from the Overview's usage card.

The NetSpeedMonitor-style table a user asked for ("5 GB down, 7 GB up on 22 May"): Days | Months |
Years, newest first, with an Export CSV of exactly the rows shown, in bytes. Read-only.

Where the numbers come from (``core/usage_history.py`` has the rules):

* ``WidgetState.get_metered_daily_volume`` - the v8 reader's exact byte totals per LOCAL day (DST-safe),
  over the adapters the usage card counts, so the Today row is the card's Today. With a cap set the card
  shows the odometer for the period and never shows Today above it; the same caps apply here - Today
  (``clamp_day``), the current month / period row and its year (``clamp_current_period``).
* Months and Years are sums of those days. With a data cap that resets on a day other than the 1st,
  Months are the cap's billing periods, labelled as such.
* Rows holding history recorded before 2.2 (estimated from saved speeds) carry a ``~`` and the
  existing recorded-history footnote; the CSV says so per row.
* A footnote says what history cannot, per interface mode: the data-cap count also covers traffic
  while the app was closed, and in auto mode it meters only the adapter in use (history: every
  physical one), so it can read higher or lower.

The history read runs on a reader thread (the Stats-detail sheet's lifecycle: ``bind_reader_thread`` /
``stop_reader_thread``); a year of minute + hour rows is tens to hundreds of milliseconds.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6 import sip
from PyQt6.QtCore import QDate, QLocale, QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from netspeedtray.constants.styles import styles as tokens
from netspeedtray.core import usage_history as U
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Segmented
from netspeedtray.utils.helpers import format_data_size, format_decimal, reveal_in_explorer
from netspeedtray.views.monitor.graph_host import bind_reader_thread, stop_reader_thread

VIEW_DAYS, VIEW_MONTHS, VIEW_YEARS = "days", "months", "years"
_ESTIMATED_MARK = "~"
_PENDING_UPGRADE = "pending_upgrade"     # loader result: a 2.1 file waiting for its upgrade


def _load(ws):
    """The reader-thread read: day volumes, None (unreadable) or _PENDING_UPGRADE."""
    available = getattr(ws, "usage_history_available", None)
    if available is not None and not available():
        return _PENDING_UPGRADE
    return ws.get_metered_daily_volume(None, datetime.now())


def _tr(i18n, key: str, default: str) -> str:
    return str(getattr(i18n, key, default)) if i18n is not None else default


class _Loader(QObject):
    """One history read on its own QThread, then ends that thread (one shot)."""

    loaded = pyqtSignal(int, object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @pyqtSlot(int)
    def run(self, seq: int) -> None:
        try:
            result = self._fn()
        except Exception as e:
            logging.getLogger("NetSpeedTray.UsageHistorySheet").debug("load failed: %s", e)
            result = None
        self.loaded.emit(seq, result)
        thread = QThread.currentThread()
        if thread is not None:
            thread.quit()


# Every reader thread a sheet started, referenced until it has ended (a QThread destroyed while running
# aborts the process); finished entries are dropped on the next start. As stats_detail._live_readers.
_live_readers: List[Tuple[QThread, _Loader]] = []


def _keep_reader(thread: QThread, loader: _Loader) -> None:
    def running(t: QThread) -> bool:
        try:
            return not sip.isdeleted(t) and t.isRunning()
        except RuntimeError:
            return False
    _live_readers[:] = [(t, w) for t, w in _live_readers if running(t)]
    _live_readers.append((thread, loader))


class UsageHistorySheet(QDialog):
    """A modal, read-only table of bytes moved per day / month (or billing period) / year."""

    _load_requested = pyqtSignal(int)

    def __init__(self, widget_state, config: Dict[str, Any], i18n,
                 period_ceiling: Optional[Callable[[], Optional[Tuple[date, Tuple[float, float]]]]] = None,
                 parent=None) -> None:
        """``period_ceiling``: with a cap set, ``(period start, (up, down))`` - the odometer's count for
        the current period, which the usage card shows and never shows Today above - else None. Called
        on the GUI thread once the rows are in; Today, the current period row and its year are capped
        at it."""
        super().__init__(parent)
        self._ws = widget_state
        self._config = config
        self._i18n = i18n
        self._period_ceiling = period_ceiling
        self._ceiling: Optional[Tuple[date, Tuple[float, float]]] = None
        self.logger = logging.getLogger("NetSpeedTray.UsageHistorySheet")
        self._locale = QLocale(str(getattr(i18n, "language", "en_US") or "en_US"))
        self._reset_day = U.billing_reset_day(config)
        self._days: Optional[List[U.UsageRow]] = None     # None until loaded (or unreadable)
        self._view = VIEW_DAYS
        self._thread: Optional[QThread] = None
        self._loader: Optional[_Loader] = None
        self._thread_ident: Optional[int] = None
        self._load_seq = 0
        self._loaded = False
        self._closed = False

        self.setModal(True)
        self.setWindowTitle(self._tr("USAGE_HISTORY_TITLE", "Usage history"))
        self.setStyleSheet(su.dialog_style())
        self.setMinimumSize(520, 520)

        c = su.semantic_colors()
        self._c = c
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel(self._tr("USAGE_HISTORY_TITLE", "Usage history"))
        title.setFont(su.font(tokens.TYPE_TITLE))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        head.addWidget(title)
        head.addStretch(1)
        self._seg = Win11Segmented([(self._tr("USAGE_HISTORY_DAYS", "Days"), VIEW_DAYS),
                                    (self._tr("USAGE_HISTORY_MONTHS", "Months"), VIEW_MONTHS),
                                    (self._tr("USAGE_HISTORY_YEARS", "Years"), VIEW_YEARS)])
        self._seg.setValue(VIEW_DAYS)
        self._seg.valueChanged.connect(self._on_view_changed)
        head.addWidget(self._seg)
        root.addLayout(head)

        self._status = QLabel(self._tr("STATS_DETAIL_LOADING", "Reading history…"))
        self._status.setFont(su.font(tokens.TYPE_BODY))
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(self._status)

        self._table = self._build_table(c)
        self._table.setVisible(False)
        root.addWidget(self._table, 1)

        # Billing-period line (Months view with a cap reset day), then the honesty footnotes.
        self._period_note = self._caption(c)
        self._estimated_note = self._caption(c)
        self._estimated_note.setText(f"{_ESTIMATED_MARK} " + self._tr(
            "USAGE_RECORDED_HISTORY_NOTE",
            "From recorded history. Days before the 2.2 update are estimated from saved speeds."))
        self._footnote = self._caption(c)
        self._footnote.setText(self._footnote_text())
        self._cap_note = self._caption(c)
        self._cap_note.setText(self._tr(
            "USAGE_HISTORY_CAP_NOTE",
            "With a data cap set, today and the current period never show more than the data-cap count."))
        for w in (self._period_note, self._estimated_note, self._footnote, self._cap_note):
            w.setVisible(False)
            root.addWidget(w)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self._export_btn = QPushButton(self._tr("USAGE_HISTORY_EXPORT", "Export CSV…"))
        self._export_btn.setStyleSheet(su.button_style(accent=True))
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setEnabled(False)            # nothing to export until the rows are in
        self._export_btn.clicked.connect(self._export)
        foot.addWidget(self._export_btn)
        root.addLayout(foot)
        self._start_load()

    # ------------------------------------------------------------------ building
    def _footnote_text(self) -> str:
        """Honest per interface mode: in auto mode the odometer meters only the adapter in use while
        history covers every physical one, so the cap's count can read higher OR lower; in every other
        mode they count the same adapters and the cap's count can only be higher (closed-app traffic)."""
        if self._config.get("interface_mode", "auto") == "auto":
            return self._tr(
                "USAGE_HISTORY_FOOTNOTE_AUTO",
                "Recorded while NetSpeedTray was running, for all physical adapters. Your data-cap count "
                "can differ: in Auto mode it counts only the adapter in use, and it also counts traffic "
                "while the app was closed.")
        return self._tr(
            "USAGE_HISTORY_FOOTNOTE",
            "Recorded while NetSpeedTray was running, for the adapters your data usage counts. Your "
            "data-cap count can be higher: it also counts traffic while the app was closed.")

    def _caption(self, c) -> QLabel:
        lbl = QLabel("")
        lbl.setFont(su.font(tokens.TYPE_CAPTION))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        return lbl

    def _build_table(self, c) -> QTableWidget:
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels([self._tr("USAGE_HISTORY_COL_DATE", "Date"),
                                     self._tr("DOWNLOAD_LABEL", "Download"),
                                     self._tr("UPLOAD_LABEL", "Upload"),
                                     self._tr("USAGE_HISTORY_COL_TOTAL", "Total")])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setShowGrid(False)
        t.setWordWrap(False)
        t.setAlternatingRowColors(True)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.setFont(su.font(tokens.TYPE_BODY))
        hh = t.horizontalHeader()
        hh.setFont(su.font(tokens.TYPE_CAPTION))
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
        # The Monitor window's thin scrollbars (views/monitor/window.py) and a header that sits on the
        # card instead of the platform's light header strip.
        t.setStyleSheet(
            f"QTableWidget {{ background: {c['card_bg']}; alternate-background-color: {c['subtle_fill']};"
            f" color: {c['text_primary']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}"
            f"QTableWidget::item {{ padding: 4px 10px; border: none; }}"
            f"QTableWidget::item:selected {{ background: {c['card_stroke']}; color: {c['text_primary']}; }}"
            f"QHeaderView {{ background: transparent; border: none; }}"
            f"QHeaderView::section {{ background: {c['card_bg']}; color: {c['text_secondary']};"
            f" border: none; border-bottom: 1px solid {c['card_stroke']}; padding: 6px 10px; }}"
            f"QTableCornerButton::section {{ background: transparent; border: none; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}"
            f"QScrollBar::handle {{ background: {c['card_stroke']}; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical {{ min-height: 24px; margin: 1px 2px; }}"
            f"QScrollBar::handle:hover {{ background: {c['text_secondary']}; }}"
            f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}"
            f"QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}")
        return t

    # ------------------------------------------------------------------ loading (reader thread)
    def _start_load(self) -> None:
        self._load_seq += 1
        ws = self._ws
        self._thread = QThread()
        self._loader = _Loader(lambda: _load(ws))
        self._loader.moveToThread(self._thread)
        self._loader.loaded.connect(self._on_loaded)
        self._load_requested.connect(self._loader.run)
        bind_reader_thread(self._thread, self._ws, self._set_thread_ident)
        _keep_reader(self._thread, self._loader)
        self._thread.start()
        self._load_requested.emit(self._load_seq)

    def _set_thread_ident(self, ident: int) -> None:
        self._thread_ident = ident

    def is_loaded(self) -> bool:
        return self._loaded

    @pyqtSlot(int, object)
    def _on_loaded(self, seq: int, days) -> None:
        if self._closed or seq != self._load_seq or sip.isdeleted(self):
            return
        self._stop_loader()
        self.set_days(days)

    def set_days(self, days) -> None:
        """Fill the sheet from ``DayVolume`` entries (None = history could not be read;
        ``_PENDING_UPGRADE`` = a 2.1 file waiting for its upgrade)."""
        self._loaded = True
        if days == _PENDING_UPGRADE:
            self._days = None
            self._status.setText(self._tr(
                "USAGE_HISTORY_PENDING_UPGRADE",
                "Usage history will be available once your history has been upgraded to 2.2."))
            return
        if days is None:
            self._days = None
            self._status.setText(self._tr("USAGE_HISTORY_UNAVAILABLE",
                                          "Usage history can't be read right now. Try again in a moment."))
            return
        self._ceiling = None
        if self._period_ceiling is not None:
            try:
                self._ceiling = self._period_ceiling()
            except Exception as e:
                self.logger.debug("period ceiling unavailable: %s", e)
        today_cap = self._ceiling[1] if self._ceiling else None
        self._days = U.day_rows(U.clamp_day(list(days), date.today(), today_cap))
        self._render()

    def _rows(self) -> List[U.UsageRow]:
        days = self._days or []
        if self._view == VIEW_MONTHS:
            rows = U.month_rows(days, self._reset_day)
        elif self._view == VIEW_YEARS:
            rows = U.year_rows(days)
        else:
            return list(days)
        if self._ceiling:
            rows = U.clamp_current_period(rows, days, date.today(), *self._ceiling)
        return rows

    def _on_view_changed(self, value) -> None:
        self._view = value if value in (VIEW_DAYS, VIEW_MONTHS, VIEW_YEARS) else VIEW_DAYS
        if self._days is not None:
            self._render()

    def _render(self) -> None:
        rows = list(reversed(self._rows()))            # newest first
        empty = not rows
        self._status.setText(self._tr("USAGE_HISTORY_EMPTY", "No usage recorded yet.") if empty else "")
        self._status.setVisible(empty)
        self._table.setVisible(not empty)
        self._export_btn.setEnabled(not empty)
        self._footnote.setVisible(True)
        self._cap_note.setVisible(bool(self._ceiling))
        t = self._table
        t.setRowCount(len(rows))
        note = self._tr("USAGE_RECORDED_HISTORY_NOTE", "")
        num_align = Qt.AlignmentFlag.AlignTrailing | Qt.AlignmentFlag.AlignVCenter
        for i, r in enumerate(rows):
            cells = [self.row_label(r)] + [self._size(v, r.estimated)
                                           for v in (r.down_bytes, r.up_bytes, r.total_bytes)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(num_align)
                if r.estimated and note:
                    item.setToolTip(note)
                t.setItem(i, col, item)
        t.resizeRowsToContents()
        self._estimated_note.setVisible(any(r.estimated for r in rows))
        show_period = self._view == VIEW_MONTHS and bool(self._reset_day)
        if show_period:
            tmpl = self._tr("USAGE_HISTORY_PERIOD_NOTE",
                            "Months follow your data-cap billing period, which starts on day {day}.")
            try:
                self._period_note.setText(tmpl.format(day=self._reset_day))
            except Exception:
                self._period_note.setText(tmpl)
        self._period_note.setVisible(show_period)

    # ------------------------------------------------------------------ formatting
    def _size(self, value: float, estimated: bool) -> str:
        v, unit = format_data_size(float(value), self._i18n, precision=2)
        text = f"{format_decimal(v, self._i18n, 2)} {unit}"
        text = f"{_ESTIMATED_MARK}{text}" if estimated else text
        # RTL (he_IL): one left-to-right unit (LRE ... PDF, as connection_check.py), or the bidi
        # algorithm shows "~1.23 GB" as "GB 1.23~".
        return f"‪{text}‬" if getattr(self._i18n, "is_rtl", False) else text

    def _short_date(self, d: date) -> str:
        """The locale's short date with a four-digit year (``M/d/yy`` -> ``M/d/yyyy``)."""
        fmt = self._locale.dateFormat(QLocale.FormatType.ShortFormat)
        if "yyyy" not in fmt:
            fmt = fmt.replace("yy", "yyyy")
        return self._locale.toString(QDate(d.year, d.month, d.day), fmt)

    def row_label(self, r: U.UsageRow) -> str:
        if r.kind == U.KIND_DAY:
            qd = QDate(r.start.year, r.start.month, r.start.day)
            dow = self._locale.dayName(qd.dayOfWeek(), QLocale.FormatType.ShortFormat)
            return f"{dow} {self._short_date(r.start)}"
        if r.kind == U.KIND_MONTH:
            month = self._locale.standaloneMonthName(r.start.month, QLocale.FormatType.LongFormat)
            tmpl = self._tr("USAGE_HISTORY_MONTH_FORMAT", "{month} {year}")
            try:
                return tmpl.format(month=month, year=r.start.year)
            except Exception:
                return f"{month} {r.start.year}"
        if r.kind == U.KIND_PERIOD:
            return f"{self._short_date(r.start)} – {self._short_date(r.end)}"
        return str(r.start.year)

    # ------------------------------------------------------------------ export
    def _export(self) -> None:
        rows = self._rows()
        if not rows:
            return
        name = f"NetSpeedTray-usage-{self._view}-{date.today().isoformat()}.csv"
        path, _sel = QFileDialog.getSaveFileName(
            self, self._tr("STATS_DETAIL_EXPORT_SAVE", "Save export"), name, "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            U.write_csv(path, rows)
        except Exception as e:
            self.logger.error("Usage CSV export failed: %s", e, exc_info=True)
            QMessageBox.warning(self, self._tr("USAGE_HISTORY_TITLE", "Usage history"),
                                self._tr("STATS_DETAIL_EXPORT_FAIL", "Could not write the export:") + f"\n{e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle(self._tr("USAGE_HISTORY_TITLE", "Usage history"))
        box.setText(self._tr("STATS_DETAIL_EXPORT_OK", "Exported:") + f"\n{os.path.basename(path)}")
        open_btn = box.addButton(self._tr("STATS_DETAIL_OPEN_FOLDER", "Open folder"),
                                 QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            reveal_in_explorer(path)

    # ------------------------------------------------------------------ lifecycle
    def _stop_loader(self, interrupt_first: bool = False) -> None:
        thread, loader = self._thread, self._loader
        self._thread = self._loader = None
        if thread is None:
            return
        try:
            loader.loaded.disconnect(self._on_loaded)
            self._load_requested.disconnect(loader.run)
        except (TypeError, RuntimeError):
            pass
        if interrupt_first and thread.isRunning():
            interrupt = getattr(self._ws, "interrupt_reads", None)
            if interrupt is not None and self._thread_ident is not None:
                try:
                    interrupt(thread_id=self._thread_ident)
                except Exception:
                    pass
        if stop_reader_thread(thread, loader, self._ws, self._thread_ident, self.logger):
            loader.deleteLater()
            thread.deleteLater()

    def _close_loader(self) -> None:
        self._closed = True
        self._load_seq += 1
        self._stop_loader(interrupt_first=True)

    def done(self, r: int) -> None:  # Qt override
        self._close_loader()
        super().done(r)

    def exec(self) -> int:
        try:
            return super().exec()
        finally:
            self._close_loader()

    def _tr(self, key: str, default: str) -> str:
        return _tr(self._i18n, key, default)
