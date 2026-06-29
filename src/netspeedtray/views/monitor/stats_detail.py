"""
StatsDetailSheet — the "drill into the numbers" dialog behind every Overview card.

Clicking a hardware tile or the network hero opens this sheet for that metric, scoped to the Monitor's
current timeline window. It is the honest, professional face of the pro-stats work: the full
distribution (avg / median / 95th / 99th / peak / min / std dev), a peak-hour vs off-peak split, and —
where a threshold is configured — throttle-time (temp) or packet-loss (latency). It is also the home of
the .zip export (summary + raw CSV + JSON sidecar) and "copy these figures".

The honesty spine is enforced here too: percentiles render only when the WindowSummary is exact (raw
tier, <=24h); beyond that the rollup cells show an em-dash and a one-line note saying exact percentiles
need the last 24 hours. Every block carries the tier + coverage% + sample count, so a figure pasted into
an ISP ticket is always qualified by how much data backs it.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QMessageBox, QApplication,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed, get_machine_id, format_duration_short
from netspeedtray.utils import summaries as S
from netspeedtray.utils import stats_exporter


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
    poll = float(config.get("update_rate", 1.0) or 1.0)
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
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(zip_path)])
        except Exception:
            pass


class StatsDetailSheet(QDialog):
    """A modal sheet of honest distribution stats for one or more metrics over the selected window."""

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
        self._poll = float(config.get("update_rate", 1.0) or 1.0)
        self._copy_text_parts: List[str] = []

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

        for subj in subjects:
            block = self._build_block(subj, c)
            if block is not None:
                root.addWidget(block)

        root.addSpacing(2)
        root.addLayout(self._build_footer(c))

    # ------------------------------------------------------------------ blocks
    def _build_block(self, subj: Dict[str, Any], c: Dict[str, str]) -> Optional[QFrame]:
        key, label, unit, kind = subj["key"], subj["label"], subj["unit"], subj["kind"]
        summ, pairs, loss = self._summarize(key, kind)

        # A secondary subject (e.g. CPU temperature when there's no sensor) is dropped when it has no
        # data, rather than showing an empty block — but the primary metric always renders.
        if summ.count == 0 and not subj.get("primary"):
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
        badge = QLabel(self._coverage_text(summ))
        badge.setFont(su.font(tokens.TYPE_CAPTION))
        badge.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        head.addWidget(badge)
        box.addLayout(head)

        if summ.count == 0:
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
            (self._tr("STAT_CELL_MEDIAN", "Median"), fmt(summ.p50) if summ.exact else "—"),
            (self._tr("STAT_CELL_P95", "95th pct"), fmt(summ.p95) if summ.exact else "—"),
            (self._tr("STAT_CELL_P99", "99th pct"), fmt(summ.p99) if summ.exact else "—"),
            (self._tr("STAT_CELL_MIN", "Min"), fmt(summ.min) if summ.exact else "—"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for i, (cap, val) in enumerate(cells):
            grid.addWidget(self._stat_cell(cap, val, c), i // 3, i % 3)
        box.addLayout(grid)

        # Plain-text copy buffer for this block.
        self._copy_text_parts.append(
            f"{label} — {self._win_label}  [{self._coverage_text(summ)}]\n  " +
            "  ".join(f"{cap}: {val}" for cap, val in cells))

        if not summ.exact:
            note = QLabel(self._tr("STATS_DETAIL_ROLLUP_NOTE",
                                   "Median and percentiles need the last 24 hours (per-second data)."))
            note.setFont(su.font(tokens.TYPE_CAPTION))
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            box.addWidget(note)

        ctx = self._context_line(key, kind, unit, pairs, loss, c)
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

    def _context_line(self, key: str, kind: str, unit: str, pairs, loss, c) -> Optional[QLabel]:
        """Metric-specific extra: peak/off-peak (network), throttle-time (temp), packet-loss (latency)."""
        text = ""
        fmt = self._formatter(kind, unit)
        if kind in ("net_down", "net_up") and pairs:
            po = S.peak_offpeak(pairs)
            # Only show the split when it's actually informative: a different busiest vs quietest hour
            # AND a meaningful spread (>15%). A flat metric shouldn't claim a "busiest hour".
            if (po and po["peak_hour"] != po["offpeak_hour"]
                    and po["peak_avg"] > po["offpeak_avg"] * 1.15):
                text = (f"{self._tr('STATS_DETAIL_BUSIEST', 'Busiest hour')} "
                        f"{int(po['peak_hour']):02d}:00 · {fmt(po['peak_avg'])}   "
                        f"{self._tr('STATS_DETAIL_QUIETEST', 'Quietest')} "
                        f"{int(po['offpeak_hour']):02d}:00 · {fmt(po['offpeak_avg'])}")
        elif key in ("cpu_temp", "gpu_temp"):
            thr = float(self._config.get("throttle_temp_c", 0) or 0)
            if thr > 0 and pairs:
                secs = S.time_above([v for _, v in pairs], thr, self._poll)
                if secs > 0:
                    text = (f"{self._tr('STATS_DETAIL_ABOVE', 'Above')} {thr:.0f}°C "
                            f"{self._tr('STATS_DETAIL_FOR', 'for')} {self._dur(secs)}")
        elif key == "latency_gw":
            bits = []
            if loss is not None and loss > 0:
                bits.append(f"{loss:.1f}% {self._tr('STATS_DETAIL_LOSS', 'packet loss')}")
            try:
                o = S.outage_summary(self._ws.get_hardware_history("latency_gw_timeout",
                                                                   self._start, self._end))
                if o["count"]:
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
                    extra = (f"{below:.0f}% {self._tr('STATS_DETAIL_BELOW_PLAN', 'of time below the')} "
                             f"{plan:.0f} Mbps {self._tr('STATS_DETAIL_PLAN', 'plan')}")
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

    # ------------------------------------------------------------------ data
    def _summarize(self, key: str, kind: str):
        """(WindowSummary, raw (ts,value) pairs for context, loss_pct or None)."""
        ws = self._ws
        loss = None
        try:
            if kind == "net_down":
                summ = ws.summarize_network("download", self._start, self._end, None, self._poll)
                pairs = [(r[0], r[2]) for r in ws.get_speed_history(self._start, self._end, None, resolution='auto')]
            elif kind == "net_up":
                summ = ws.summarize_network("upload", self._start, self._end, None, self._poll)
                pairs = [(r[0], r[1]) for r in ws.get_speed_history(self._start, self._end, None, resolution='auto')]
            else:
                summ = ws.summarize_hardware(key, self._start, self._end, self._poll)
                pairs = ws.get_hardware_history(key, self._start, self._end)
                if key == "latency_gw":
                    ls = ws.summarize_hardware("latency_gw_timeout", self._start, self._end, self._poll)
                    loss = round((ls.avg or 0.0) * 100.0, 1) if ls.count else None
        except Exception as e:
            self.logger.debug("summarize(%s) failed: %s", key, e)
            return S.summarize_raw([]), [], None
        return summ, pairs, loss

    # ------------------------------------------------------------------ formatting
    def _formatter(self, kind: str, unit: str):
        if kind in ("net_down", "net_up"):
            return lambda v: "—" if v is None else self._fmt_speed(float(v))
        if unit == "%":
            return lambda v: "—" if v is None else f"{float(v):.0f}%"
        if unit == "°C":
            return lambda v: "—" if v is None else f"{float(v):.0f}°C"
        if unit == "ms":
            return lambda v: "—" if v is None else f"{float(v):.0f} ms"
        if unit == "W":
            return lambda v: "—" if v is None else f"{float(v):.0f} W"
        return lambda v: "—" if v is None else f"{float(v):.1f} {unit}".strip()

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
