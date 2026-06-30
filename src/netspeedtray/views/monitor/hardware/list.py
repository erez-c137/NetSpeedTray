"""
HardwareBarList — the per-process CPU / RAM / GPU list for the Monitor's Hardware tab.

One row per program: name, a CPU bar (relative to the busiest process so the heaviest reads clearly),
and precise CPU% / Memory / GPU% figures. Rows are keyed by program identity and updated in place;
ordering is by CPU% (busiest first) — like Task Manager, where the busy process leading the list is
the point. Reuses the connection list's _ActivityBar; matplotlib-free.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_data_size, format_decimal
from netspeedtray.views.monitor.network.app_list import _ActivityBar

_NAME_W = 168
_CPU_W = 50
_RAM_W = 88
_GPU_W = 52
_ACTIVE_CPU = 1.0      # %, below this a process is treated as idle (dimmed)
# Floor the CPU-bar denominator so a quiet desktop (busiest ~9%) doesn't draw that 9% as a FULL bar.
# Below this the bar scales against the floor (honest "barely busy"); above it, relative-to-busiest.
_BAR_FLOOR_PCT = 30.0
_GPU_SHOW_MIN = 0.5    # render "—" below this so the one GPU-using process isn't lost in a wall of 0%


def _is_busy(row: Dict[str, Any]) -> bool:
    return float(row.get("cpu_pct", 0.0)) >= _ACTIVE_CPU or float(row.get("gpu_pct", 0.0)) >= _ACTIVE_CPU


class HardwareRow(QFrame):
    """name | CPU bar | CPU% | Memory | GPU%."""

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(10)

        self._name = QLabel("")
        self._name.setFont(su.font(tokens.TYPE_BODY))
        self._name.setFixedWidth(_NAME_W)

        self._bar = _ActivityBar()

        self._cpu = self._num(_CPU_W, c)
        self._ram = self._num(_RAM_W, c)
        self._gpu = self._num(_GPU_W, c)

        lay.addWidget(self._name)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._cpu)
        lay.addWidget(self._ram)
        lay.addWidget(self._gpu)

    def _num(self, width: int, c: dict) -> QLabel:
        lbl = QLabel("—")
        lbl.setFont(su.font(tokens.TYPE_BODY))
        lbl.setFixedWidth(width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        return lbl

    def update_row(self, row: Dict[str, Any], max_cpu: float, gpu_available: bool) -> None:
        name = str(row.get("display_name", "—"))
        cpu = float(row.get("cpu_pct", 0.0))
        rss = int(row.get("rss_bytes", 0))
        gpu = float(row.get("gpu_pct", 0.0))
        busy = _is_busy(row)

        c = su.semantic_colors()
        text = c["text_primary"] if busy else c["text_secondary"]
        fm = QFontMetrics(self._name.font())
        self._name.setText(fm.elidedText(name, Qt.TextElideMode.ElideRight, _NAME_W))
        # Emphasise/recede the WHOLE row together (name + numbers), not just the name, so a busy row
        # doesn't read as half-disabled. Re-resolved each tick -> picks up a theme switch.
        for lbl in (self._name, self._cpu, self._ram, self._gpu):
            lbl.setStyleSheet(f"color: {text}; background: transparent;")

        # CPU bar is RELATIVE to the busiest process (the heavy hitter should lead) but the
        # denominator is floored so an idle desktop's small leader isn't drawn as a full bar; the
        # number column carries the absolute value.
        self._bar.set_value((cpu / max(max_cpu, _BAR_FLOOR_PCT)) if max_cpu > 0 else 0.0, busy)
        self._cpu.setText(f"{cpu:.0f}%")
        rv, ru = format_data_size(rss, self._i18n, precision=1)
        rv_s = format_decimal(rv, self._i18n, 1)   # locale separator (matches the Overview/UsageTile)
        self._ram.setText(f"{rv_s} {ru}")
        self._gpu.setText(f"{gpu:.0f}%" if (gpu_available and gpu >= _GPU_SHOW_MIN) else "—")

        summary = f"{name} — CPU {cpu:.0f}% · RAM {rv_s} {ru} · GPU {gpu:.0f}%"
        self.setToolTip(summary)
        self.setAccessibleName(summary)   # tooltips aren't exposed to screen readers; accessible name is


class HardwareBarList(QWidget):
    """Header + summary + a scrollable, in-place-updated per-process CPU/RAM/GPU list."""

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._rows: Dict[str, HardwareRow] = {}
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # The three caps mirror the row's fixed-width number trio (widths _CPU_W/_RAM_W/_GPU_W +
        # spacing 10 + margins 8) so they line up over the columns; keep them in sync if either moves.
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 0)
        head.setSpacing(10)
        proc = self._cap(self._tr("APP_ACTIVITY_PROCESS_HEADER", "Process"), c)
        head.addWidget(proc)
        head.addStretch(1)
        head.addWidget(self._cap_w(self._tr("ORDER_TYPE_CPU", "CPU"), _CPU_W, c))
        head.addWidget(self._cap_w(self._tr("MEMORY_HEADER", "Memory"), _RAM_W, c))
        head.addWidget(self._cap_w(self._tr("ORDER_TYPE_GPU", "GPU"), _GPU_W, c))
        root.addLayout(head)

        self._summary = QLabel(self._tr("HARDWARE_LOADING_MESSAGE", "Reading processes…"))
        self._summary.setFont(su.font(tokens.TYPE_CAPTION))
        self._summary.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._summary.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._summary)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._list_layout = QVBoxLayout(self._content)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

    def set_payload(self, payload: Dict[str, Any]) -> None:
        rows: List[Dict[str, Any]] = payload.get("rows", []) if isinstance(payload, dict) else []
        if not rows:
            self._summary.setText(self._tr("HARDWARE_NO_DATA_MESSAGE", "No process data."))
            return
        gpu_available = bool(payload.get("gpu_available", False))
        max_cpu = max((float(r.get("cpu_pct", 0.0)) for r in rows), default=0.0)

        # Freeze the row ORDER while the pointer is over the list — CPU% is volatile, and a busiest-
        # first re-sort every 2s would slide the row you're reading out from under the cursor. Values
        # still update live in place; membership/order resettle on the next tick after the mouse leaves.
        if self._scroll.viewport().underMouse():
            for r in rows:
                w = self._rows.get(str(r.get("identity_key", r.get("display_name", ""))))
                if w is not None:
                    w.update_row(r, max_cpu, gpu_available)
            self._summary.setText(self._summary_text(payload))
            return

        seen = set()
        ordered: List[HardwareRow] = []
        for r in rows:
            key = str(r.get("identity_key", r.get("display_name", "")))
            seen.add(key)
            w = self._rows.get(key)
            if w is None:
                w = HardwareRow(self._i18n)
                self._rows[key] = w
            w.update_row(r, max_cpu, gpu_available)
            ordered.append(w)
        for i in reversed(range(self._list_layout.count())):
            if self._list_layout.itemAt(i).widget() is not None:
                self._list_layout.takeAt(i)
        for key in [k for k in self._rows if k not in seen]:
            self._rows.pop(key).deleteLater()
        for j, w in enumerate(ordered):
            self._list_layout.insertWidget(j, w)
        self._summary.setText(self._summary_text(payload))

    def set_unavailable(self, reason: str) -> None:
        if reason == "rdp":
            self._summary.setText(self._tr(
                "APP_ACTIVITY_RDP_UNAVAILABLE_MESSAGE",
                "Per-process monitoring is unavailable in Remote Desktop sessions."))
        else:
            self._summary.setText(self._tr("HARDWARE_NO_DATA_MESSAGE", "No process data."))

    def _summary_text(self, payload: Dict[str, Any]) -> str:
        rv, ru = format_data_size(int(payload.get("total_rss_bytes", 0)), self._i18n, precision=1)
        tmpl = self._tr("HARDWARE_SUMMARY_TEMPLATE",
                        "{procs} processes · CPU {cpu}% · RAM {ram} {ram_unit} · Updated {updated_at}")
        try:
            return tmpl.format(
                procs=int(payload.get("proc_count", 0)),
                cpu=format_decimal(float(payload.get("total_cpu_pct", 0.0)), self._i18n, 0),  # plain token, no spec (#10)
                ram=format_decimal(rv, self._i18n, 1), ram_unit=ru,   # locale decimal separator
                updated_at=payload.get("updated_at", "--:--:--"))
        except Exception:
            return ""

    def _cap(self, text: str, c: dict) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(su.font(tokens.TYPE_CAPTION))
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        return lbl

    def _cap_w(self, text: str, width: int, c: dict) -> QLabel:
        lbl = self._cap(text, c)
        lbl.setFixedWidth(width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
