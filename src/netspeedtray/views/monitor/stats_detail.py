"""
StatsDetailSheet - the "drill into the numbers" dialog behind every Overview card.

Clicking a hardware tile or the network hero opens this sheet for that metric, scoped to the Monitor's
current timeline window. It is the honest, professional face of the pro-stats work: the full
distribution (avg / median / 95th / 99th / peak / min / std dev), a peak-hour vs off-peak split, and -
where a threshold is configured - throttle-time (temp) or packet-loss (latency). It is also the home of
the .zip export (summary + raw CSV + JSON sidecar) and "copy these figures".

The honesty spine is enforced here too: percentiles render only when the WindowSummary is exact (raw
tier, <=24h); beyond that the rollup cells show an em-dash and a one-line note saying exact percentiles
need the last 24 hours. Every block carries the tier + coverage% + sample count, so a figure pasted into
an ISP ticket is always qualified by how much data backs it.

Its history reads (summaries, graph points, the hourly profile, drops) run on a reader thread, the same
QThread + bind_reader_thread/stop_reader_thread lifecycle as the Monitor's graph worker: on a big
database they took long enough to freeze the UI on every open. The sheet opens with a loading line and
fills in when the result lands; a result for a sheet that has since closed is dropped.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QMessageBox, QApplication,
)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed, get_machine_id, format_duration_short, reveal_in_explorer
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray.utils import summaries as S
from netspeedtray.utils import stats_exporter
from netspeedtray.views.monitor.graph_host import bind_reader_thread, stop_reader_thread


# latency RTT series -> the loss series of the same hop (latency_gw -> latency_gw_timeout, ...)
_LOSS_SERIES_FOR = constants.network.latency.LOSS_SERIES_FOR
_ANCHOR_KEY = "latency_anchor"
_GATEWAY_KEY = "latency_gw"


def _tr(i18n, key: str, default: str) -> str:
    return str(getattr(i18n, key, default)) if i18n is not None else default


def run_interactive_export(parent, widget_state, start: datetime, end: datetime, window_label: str,
                           config: Dict[str, Any], i18n, app_version: str = "") -> None:
    """Pick a save location, write the summary + raw + JSON export for [start,end] bundled into a single
    .zip, and confirm with an open-folder option. Shared by the Stats-detail sheet's Export button AND
    the Overview header's Export action, so "export the numbers" works identically from either point."""
    logger = logging.getLogger("NetSpeedTray.Export")
    machine = get_machine_id()[:8]
    period = re.sub(r"[^A-Za-z0-9]+", "-", window_label).strip("-").lower() or "window"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    basename = f"nst_export_{machine}_{period}_{ts}"
    zip_filter = f"{_tr(i18n, 'STATS_DETAIL_EXPORT_ZIP_LABEL', 'Zip archive')} (*.zip)"
    zip_path, _sel = QFileDialog.getSaveFileName(
        parent, _tr(i18n, "STATS_DETAIL_EXPORT_SAVE", "Save export"),
        f"{basename}.zip", zip_filter)
    if not zip_path:
        return
    if not zip_path.lower().endswith(".zip"):
        zip_path += ".zip"
    # Seconds per sample for coverage / volume math: the shared resolver, never a local `or 1.0`.
    poll = resolve_poll_interval_seconds(config.get("update_rate", 1.0))
    try:
        zip_path = stats_exporter.export_window_zip(
            widget_state, start, end, window_label, zip_path, basename,
            machine_id=machine, app_version=app_version, poll_interval=poll)
    except Exception as e:
        logger.error("Export failed: %s", e, exc_info=True)
        QMessageBox.warning(parent, _tr(i18n, "STATS_DETAIL_EXPORT", "Export"),
                            _tr(i18n, "STATS_DETAIL_EXPORT_FAIL", "Could not write the export:") + f"\n{e}")
        return
    box = QMessageBox(parent)
    box.setWindowTitle(_tr(i18n, "STATS_DETAIL_EXPORT", "Export"))
    box.setText(_tr(i18n, "STATS_DETAIL_EXPORT_OK", "Exported:") + f"\n{os.path.basename(zip_path)}")
    open_btn = box.addButton(_tr(i18n, "STATS_DETAIL_OPEN_FOLDER", "Open folder"),
                             QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is open_btn:
        reveal_in_explorer(zip_path)   # absolute-path explorer (CWD-hijack hardening), never raises


def _summarize(ws, key: str, kind: str, start: datetime, end: datetime, poll: float, logger):
    """(WindowSummary, raw (ts,value) pairs for context, loss_pct or None, loss WindowSummary or None,
    hourly {hour: average B/s} or None) for one series. Runs on the sheet's reader thread."""
    loss = None
    loss_summ = None
    hourly = None
    # Never wait on the DB write thread's flush barrier (up to 2 s per call - the download+upload sheet
    # froze the UI 2-4 s in 2.2.0-beta.1). The summaries above each series read without waiting too, so
    # the figures and the context line stay consistent; at most the last ~10 s batch is missing from a
    # window of hours.
    try:
        if kind == "net_down":
            summ = ws.summarize_network("download", start, end, None, poll)
            # Same no-wait read path as the summary (never the flush barrier).
            hourly = ws.get_network_hourly_profile("download", start, end)
            pairs = [(r[0], r[2]) for r in ws.get_speed_history(start, end, None, resolution='auto', wait_for_flush=False)]
        elif kind == "net_up":
            summ = ws.summarize_network("upload", start, end, None, poll)
            hourly = ws.get_network_hourly_profile("upload", start, end)
            pairs = [(r[0], r[1]) for r in ws.get_speed_history(start, end, None, resolution='auto', wait_for_flush=False)]
        else:
            summ = ws.summarize_hardware(key, start, end, poll)
            pairs = ws.get_hardware_history(key, start, end)
            if key in _LOSS_SERIES_FOR:
                loss_summ = ws.summarize_hardware(_LOSS_SERIES_FOR[key], start, end, poll)
                loss = round((loss_summ.avg or 0.0) * 100.0, 1) if loss_summ.count else None
    except Exception as e:
        logger.debug("summarize(%s) failed: %s", key, e)
        return S.summarize_raw([]), [], None, None, None
    return summ, pairs, loss, loss_summ, hourly


def _load_subjects(ws, subjects: List[Dict[str, Any]], start: datetime, end: datetime, poll: float,
                   logger) -> List[Dict[str, Any]]:
    """Every history read the sheet needs, one entry per subject (in order). Runs on the reader thread;
    the GUI thread only builds widgets from the result."""
    out = []
    for subj in subjects:
        key, kind = subj["key"], subj["kind"]
        summ, pairs, loss, loss_summ, hourly = _summarize(ws, key, kind, start, end, poll, logger)
        label = None   # None = the subject's own label
        # The internet anchor's RTT series only gets a row on a successful echo, so a fully blocked or
        # just-enabled anchor has loss rows and no RTT rows - keep that block (loss and drops, RTT cells
        # "-") instead of dropping it while the pill says 100% loss. Only when the anchor has neither
        # series does the block fall back to the router, under the router's own label.
        if key == _ANCHOR_KEY and summ.count == 0 and loss is None:
            g = _summarize(ws, _GATEWAY_KEY, kind, start, end, poll, logger)
            if g[0].count or g[2] is not None:
                key, (summ, pairs, loss, loss_summ, hourly) = _GATEWAY_KEY, g
                label = "router"
        # Drops come from the SAME hop as the block's RTTs; read only for a block that will show them.
        outage = None
        if key in _LOSS_SERIES_FOR and (summ.count or loss is not None):
            try:
                outage = S.outage_summary(ws.get_hardware_history(_LOSS_SERIES_FOR[key], start, end))
            except Exception:
                outage = None
        out.append({"key": key, "label": label, "summ": summ, "pairs": pairs, "loss": loss,
                    "loss_summ": loss_summ, "hourly": hourly, "outage": outage})
    return out


class _SheetLoader(QObject):
    """Runs one sheet's history reads on its own QThread, then ends that thread (one shot)."""

    loaded = pyqtSignal(int, object)   # (load sequence, _load_subjects result)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @pyqtSlot(int)
    def run(self, seq: int) -> None:
        try:
            result = self._fn()
        except Exception as e:   # _load_subjects already reads empty on failure; belt and braces
            logging.getLogger("NetSpeedTray.StatsDetailSheet").debug("load failed: %s", e)
            result = []
        self.loaded.emit(seq, result)
        thread = QThread.currentThread()
        if thread is not None:
            thread.quit()


# Every reader thread a sheet started, kept referenced until it has ended: a QThread destroyed while
# running aborts the process, and a sheet's Python wrapper can be collected mid-read (it is not always
# held by exec()). Finished entries are dropped on the next start.
_live_readers: List[Tuple[QThread, _SheetLoader]] = []


def _keep_reader(thread: QThread, loader: _SheetLoader) -> None:
    def running(t: QThread) -> bool:
        try:
            return not sip.isdeleted(t) and t.isRunning()
        except RuntimeError:
            return False
    _live_readers[:] = [(t, w) for t, w in _live_readers if running(t)]
    _live_readers.append((thread, loader))


class StatsDetailSheet(QDialog):
    """A modal sheet of honest distribution stats for one or more metrics over the selected window."""

    _load_requested = pyqtSignal(int)   # -> _SheetLoader.run on the reader thread

    def __init__(self, widget_state, subjects: List[Dict[str, Any]],
                 window: Tuple[datetime, datetime, str], config: Dict[str, Any], i18n,
                 app_version: str = "", parent=None) -> None:
        super().__init__(parent)
        self._ws = widget_state
        self._subjects = subjects          # [{"key","label","unit","kind"}], kind: net_down/net_up/hw
        self._start, self._end, self._win_label = window
        self._config = config
        self._i18n = i18n
        self._app_version = app_version
        self.logger = logging.getLogger("NetSpeedTray.StatsDetailSheet")
        # Seconds per sample for coverage / volume math: the shared resolver, never a local `or 1.0`.
        self._poll = resolve_poll_interval_seconds(config.get("update_rate", 1.0))
        self._copy_text_parts: List[str] = []
        self._thread: Optional[QThread] = None
        self._loader: Optional[_SheetLoader] = None
        self._thread_ident: Optional[int] = None
        self._load_seq = 0          # bumped on close: a result carrying an older number is dropped
        self._loaded = False
        self._closed = False

        self.setModal(True)
        self.setWindowTitle(self._tr("STATS_DETAIL_TITLE", "Statistics"))
        self.setStyleSheet(su.dialog_style())
        self.setMinimumWidth(440)

        c = su.semantic_colors()
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        primary = subjects[0]["label"] if subjects else ""
        title = QLabel(f"{primary} · {self._win_label}")
        title.setFont(su.font(tokens.TYPE_TITLE))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        root.addWidget(title)

        # Loading state: the blocks replace this line when the reader thread's result lands.
        self._c = c
        self._loading = QLabel(self._tr("STATS_DETAIL_LOADING", "Reading history…"))
        self._loading.setFont(su.font(tokens.TYPE_BODY))
        self._loading.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(self._loading)

        root.addSpacing(2)
        root.addLayout(self._build_footer(c))
        self._start_load()

    # ------------------------------------------------------------------ loading (reader thread)
    def _start_load(self) -> None:
        self._load_seq += 1
        fn = partial(_load_subjects, self._ws, list(self._subjects), self._start, self._end,
                     self._poll, self.logger)
        self._thread = QThread()
        self._loader = _SheetLoader(fn)
        self._loader.moveToThread(self._thread)
        self._loader.loaded.connect(self._on_loaded)          # queued: lands on the GUI thread
        self._load_requested.connect(self._loader.run)        # queued: runs on the reader thread
        # The reader thread's read connection is opened, and closed, on that thread.
        bind_reader_thread(self._thread, self._ws, self._set_thread_ident)
        _keep_reader(self._thread, self._loader)
        self._thread.start()
        self._load_requested.emit(self._load_seq)

    def _set_thread_ident(self, ident: int) -> None:
        self._thread_ident = ident   # runs on the reader thread (started, DirectConnection)

    def is_loaded(self) -> bool:
        return self._loaded

    @pyqtSlot(int, object)
    def _on_loaded(self, seq: int, results) -> None:
        # Stale: the sheet closed after this read began - never touch its widgets.
        if self._closed or seq != self._load_seq or sip.isdeleted(self):
            return
        self._stop_loader()
        root = self.layout()
        at = root.indexOf(self._loading)
        self._loading.hide()
        root.removeWidget(self._loading)
        self._loading.deleteLater()
        for subj, data in zip(self._subjects, results):
            block = self._build_block(subj, self._c, data)
            if block is not None:
                root.insertWidget(at, block)
                block.show()   # a layout only queues the show for a visible parent: size it now
                at += 1
        self._loaded = True
        self._copy_btn.setEnabled(True)
        root.activate()        # the new blocks' size now, not at the next LayoutRequest
        self.adjustSize()
        self._recentre()

    def _recentre(self) -> None:
        """The sheet was shown (and centred) at its small loading size and then grew downward from the
        same top-left - with three blocks its footer ended up below the screen. Re-centre it on its
        parent and keep it inside that screen's available area (top edge wins if it is taller)."""
        if not self.isVisible():
            return
        par = self.parentWidget()
        screen = (par.screen() if par is not None else None) or self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        fw, fh = frame.width(), frame.height()
        centre = par.window().frameGeometry().center() if par is not None else avail.center()
        x = max(avail.left(), min(centre.x() - fw // 2, avail.right() + 1 - fw))
        y = max(avail.top(), min(centre.y() - fh // 2, avail.bottom() + 1 - fh))
        self.move(x, y)

    def _stop_loader(self, interrupt_first: bool = False) -> None:
        """Stop + release the reader thread (bounded; see stop_reader_thread). Idempotent.
        ``interrupt_first``: the result is unwanted (the sheet is closing), so abort a running query
        straight away instead of first giving it GRAPH_THREAD_QUIT_WAIT_MS to finish."""
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
        self._load_seq += 1   # anything still in flight is now stale
        self._stop_loader(interrupt_first=True)

    def done(self, r: int) -> None:  # Qt override
        self._close_loader()
        super().done(r)

    def exec(self) -> int:
        # exec() can return without done() (app quit while the modal sheet runs): stop the reader thread
        # either way, so no running QThread outlives the sheet.
        try:
            return super().exec()
        finally:
            self._close_loader()

    # ------------------------------------------------------------------ blocks
    def _build_block(self, subj: Dict[str, Any], c: Dict[str, str],
                     data: Dict[str, Any]) -> Optional[QFrame]:
        """One subject's card from its _load_subjects entry (no DB access here: GUI thread)."""
        label, unit, kind = subj["label"], subj["unit"], subj["kind"]
        key, summ, pairs, loss = data["key"], data["summ"], data["pairs"], data["loss"]
        if data["label"] == "router":   # the anchor fell back to the gateway (see _load_subjects)
            label = self._tr("CHECK_CONN_ROUTER", "Router")
        loss_only = summ.count == 0 and key in _LOSS_SERIES_FOR and loss is not None

        # A secondary subject (e.g. CPU temperature when there's no sensor) is dropped when it has no
        # data, rather than showing an empty block - but the primary metric always renders.
        if summ.count == 0 and not subj.get("primary") and not loss_only:
            return None

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {c['card_bg']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: 8px; }}")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        # Header: metric label (when >1 subject) + coverage badge.
        head = QHBoxLayout()
        if len(self._subjects) > 1:
            ml = QLabel(label)
            ml.setFont(su.font(tokens.TYPE_BODY_STRONG))
            ml.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
            head.addWidget(ml)
        head.addStretch(1)
        badge = QLabel(self._coverage_text((data["loss_summ"] or summ) if loss_only else summ))
        badge.setFont(su.font(tokens.TYPE_CAPTION))
        badge.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        head.addWidget(badge)
        box.addLayout(head)

        if summ.count == 0 and not loss_only:
            empty = QLabel(self._tr("STATS_DETAIL_NO_DATA", "Not enough history for this period yet."))
            empty.setFont(su.font(tokens.TYPE_BODY))
            empty.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            box.addWidget(empty)
            return card

        # Distribution grid: a stat cell per figure. Percentiles only when exact (raw tier).
        fmt = self._formatter(kind, unit)
        cells = [
            (self._tr("STAT_CELL_AVG", "Average"), fmt(summ.avg)),
            (self._tr("STAT_CELL_PEAK", "Peak"), fmt(summ.max)),
            (self._tr("STAT_CELL_MEDIAN", "Median"), fmt(summ.p50) if summ.exact else "-"),
            (self._tr("STAT_CELL_P95", "95th pct"), fmt(summ.p95) if summ.exact else "-"),
            (self._tr("STAT_CELL_P99", "99th pct"), fmt(summ.p99) if summ.exact else "-"),
            (self._tr("STAT_CELL_MIN", "Min"), fmt(summ.min) if summ.exact else "-"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for i, (cap, val) in enumerate(cells):
            grid.addWidget(self._stat_cell(cap, val, c), i // 3, i % 3)
        box.addLayout(grid)

        # Plain-text copy buffer for this block.
        self._copy_text_parts.append(
            f"{label} - {self._win_label}  [{self._coverage_text(summ)}]\n  " +
            "  ".join(f"{cap}: {val}" for cap, val in cells))

        if not summ.exact and not loss_only:
            note = QLabel(self._tr("STATS_DETAIL_ROLLUP_NOTE",
                                   "Median and percentiles need the last 24 hours (per-second data)."))
            note.setFont(su.font(tokens.TYPE_CAPTION))
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            box.addWidget(note)

        ctx = self._context_line(key, kind, unit, pairs, loss, c, data)
        if ctx is not None:
            box.addWidget(ctx)
        return card

    def _stat_cell(self, caption: str, value: str, c: Dict[str, str]) -> QFrame:
        cell = QFrame()
        cell.setStyleSheet(f"QFrame {{ background: {c['subtle_fill']}; border-radius: 6px; }}")
        v = QVBoxLayout(cell)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(1)
        val = QLabel(value)
        val.setFont(su.font(tokens.TYPE_SUBTITLE))
        val.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        cap = QLabel(caption)
        cap.setFont(su.font(tokens.TYPE_CAPTION))
        cap.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        v.addWidget(val)
        v.addWidget(cap)
        return cell

    def _context_line(self, key: str, kind: str, unit: str, pairs, loss, c,
                      data: Dict[str, Any]) -> Optional[QLabel]:
        """Metric-specific extra: peak/off-peak (network), throttle-time (temp), packet-loss (latency)."""
        text = ""
        fmt = self._formatter(kind, unit)
        if kind in ("net_down", "net_up") and pairs:
            # The hourly AVERAGES from the reader - not ``pairs``, the graph's per-bucket peaks.
            po = S.peak_offpeak_from_profile(data["hourly"])
            # Only show the split when it's actually informative: a different busiest vs quietest hour
            # AND a meaningful spread (>15%). A flat metric shouldn't claim a "busiest hour".
            if (po and po["peak_hour"] != po["offpeak_hour"]
                    and po["peak_avg"] > po["offpeak_avg"] * 1.15):
                avg = self._tr('STAT_AVG_SHORT', 'avg')
                text = (f"{self._tr('STATS_DETAIL_BUSIEST', 'Busiest hour')} "
                        f"{int(po['peak_hour']):02d}:00 · {avg} {fmt(po['peak_avg'])}   "
                        f"{self._tr('STATS_DETAIL_QUIETEST', 'Quietest')} "
                        f"{int(po['offpeak_hour']):02d}:00 · {avg} {fmt(po['offpeak_avg'])}")
        elif key in ("cpu_temp", "gpu_temp"):
            thr = float(self._config.get("throttle_temp_c", 0) or 0)
            if thr > 0 and pairs:
                secs = S.time_above([v for _, v in pairs], thr, self._poll)
                if secs > 0:
                    text = (f"{self._tr('STATS_DETAIL_ABOVE', 'Above')} {thr:.0f}°C "
                            f"{self._tr('STATS_DETAIL_FOR', 'for')} {self._dur(secs)}")
        elif key in _LOSS_SERIES_FOR:
            # Loss and drops come from the SAME hop as the block's RTTs: the anchor's series when the
            # block is the public anchor, so "Internet" never shows router-only loss (2.2 D1/D3).
            bits = []
            if loss is not None and loss > 0:
                bits.append(f"{loss:.1f}% {self._tr('STATS_DETAIL_LOSS', 'packet loss')}")
            try:
                o = data["outage"]   # read on the reader thread (_load_subjects)
                if o and o["count"]:
                    last = o["last_start"]
                    lt = last.strftime("%H:%M") if hasattr(last, "strftime") else ""
                    drops = (f"{o['count']} {self._tr('STATS_DETAIL_DROPS', 'connection drops')}")
                    if lt:
                        drops += f" · {self._tr('STATS_DETAIL_LAST', 'last')} {lt}"
                    if o["total_down_seconds"] >= 1:
                        drops += f" · {self._dur(o['total_down_seconds'])} {self._tr('STATS_DETAIL_DOWN', 'down')}"
                    bits.append(drops)
            except Exception:
                pass
            text = "   ·   ".join(bits)

        # Network: % of time below the advertised plan, when configured.
        if kind in ("net_down", "net_up") and pairs:
            plan_key = "plan_down_mbps" if kind == "net_down" else "plan_up_mbps"
            plan = float(self._config.get(plan_key, 0) or 0)
            if plan > 0:
                thr_bps = plan * 1_000_000.0 / 8.0
                below = S.pct_below([v for _, v in pairs], thr_bps)
                if below is not None:
                    # One template, not two fragments glued around the number. English word order
                    # ("below the N Mbps plan") does not survive translation: Spanish and French
                    # were left with a dangling noun, and Russian said "тариф" twice. A template
                    # lets each language put the number and the noun where they belong.
                    extra = self._tr(
                        'STATS_DETAIL_BELOW_PLAN_TEMPLATE', '{pct}% of time below the {plan} Mbps plan'
                    ).format(pct=f"{below:.0f}", plan=f"{plan:.0f}")
                    text = f"{text}   ·   {extra}" if text else extra

        if not text:
            return None
        lbl = QLabel(text)
        lbl.setFont(su.font(tokens.TYPE_CAPTION))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._copy_text_parts.append(f"  {text}")
        return lbl

    # ------------------------------------------------------------------ footer / actions
    def _build_footer(self, c: Dict[str, str]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        copy_btn = QPushButton(self._tr("STATS_DETAIL_COPY", "Copy figures"))
        copy_btn.setStyleSheet(su.button_style(accent=False))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy)
        copy_btn.setEnabled(False)   # nothing to copy until the figures have loaded
        self._copy_btn = copy_btn
        export_btn = QPushButton(self._tr("STATS_DETAIL_EXPORT", "Export period (CSV)…"))
        export_btn.setStyleSheet(su.button_style(accent=True))
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export)
        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(export_btn)
        return row

    def _copy(self) -> None:
        QApplication.clipboard().setText("\n".join(self._copy_text_parts))

    def _export(self) -> None:
        run_interactive_export(self, self._ws, self._start, self._end, self._win_label,
                               self._config, self._i18n, self._app_version)

    # ------------------------------------------------------------------ formatting
    def _formatter(self, kind: str, unit: str):
        if kind in ("net_down", "net_up"):
            return lambda v: "-" if v is None else self._fmt_speed(float(v))
        if unit == "%":
            return lambda v: "-" if v is None else f"{float(v):.0f}%"
        if unit == "°C":
            return lambda v: "-" if v is None else f"{float(v):.0f}°C"
        if unit == "ms":
            return lambda v: "-" if v is None else f"{float(v):.0f} ms"
        if unit == "W":
            return lambda v: "-" if v is None else f"{float(v):.0f} W"
        return lambda v: "-" if v is None else f"{float(v):.1f} {unit}".strip()

    def _coverage_text(self, summ) -> str:
        tier_label = {"raw": self._tr("STATS_TIER_RAW", "per-second"),
                      "minute": self._tr("STATS_TIER_MINUTE", "per-minute"),
                      "hour": self._tr("STATS_TIER_HOUR", "per-hour")}.get(summ.tier, summ.tier)
        n = self._group_thousands(summ.count)
        return f"{tier_label} · {summ.coverage_pct:.0f}% · {n} {self._tr('STATS_SAMPLES', 'samples')}"

    def _group_thousands(self, n: int) -> str:
        return f"{int(n):,}".replace(",", self._tr("THOUSANDS_SEPARATOR", ","))

    def _dur(self, secs: float) -> str:
        return format_duration_short(secs, self._i18n)   # shared, localized (consolidates the old helpers)

    def _fmt_speed(self, bps: float) -> str:
        cfg = self._config
        return format_speed(
            bps, self._i18n,
            force_mega_unit=(cfg.get("speed_display_mode") == "always_mbps"),
            decimal_places=int(cfg.get("decimal_places", 1)),
            unit_type=cfg.get("unit_type", "bits_decimal"),
            short_labels=cfg.get("short_unit_labels", False),
        )

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
