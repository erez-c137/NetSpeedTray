"""
NetworkHeader — the Monitor Network tab's header band: machine-wide Download/Upload totals for the
selected period, plus a segmented timeline control that drives the shared graph.

Standalone by design. It reuses only the pill *style* from utils.styles (matplotlib-free) and the
period keys from constants — never the graph package — so importing this module can't trip the
Monitor's matplotlib firewall, and the Monitor stays decoupled from the graph window it replaces.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_data_size

# (short label, period key) — mirrors the graph window's pills so the two surfaces feel identical.
_PILLS = [
    ("SESS", "TIMELINE_SESSION"),
    ("BOOT", "TIMELINE_SYSTEM_UPTIME"),
    ("24H", "TIMELINE_24_HOURS"),
    ("WEEK", "TIMELINE_WEEK"),
    ("MONTH", "TIMELINE_MONTH"),
    ("ALL", "TIMELINE_ALL"),
]
_DEFAULT_KEY = "TIMELINE_24_HOURS"


def _period_value(period_key: str) -> int:
    """period key -> the legacy PERIOD_MAP index GraphHost/coordinator speak."""
    for idx, key in constants.data.history_period.PERIOD_MAP.items():
        if key == period_key:
            return idx
    return 2


class PeriodSegmentedControl(QWidget):
    """Six exclusive pills (Session/Boot/24H/Week/Month/All) emitting the PERIOD_MAP index."""

    period_changed = pyqtSignal(int)

    def __init__(self, initial_key: str = _DEFAULT_KEY, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("timelinePills")
        lay = QHBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        self._buttons: Dict[str, QPushButton] = {}
        for i, (label, key) in enumerate(_PILLS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("period_key", key)
            btn.setObjectName(
                "pillFirst" if i == 0 else "pillLast" if i == len(_PILLS) - 1 else "pillMid")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(self._on_clicked)
            lay.addWidget(btn, 1)
            self._buttons[key] = btn
        self.setStyleSheet(su.timeline_pills_style())
        self.set_period_key(initial_key, emit=False)

    def _on_clicked(self) -> None:
        sender = self.sender()
        key = sender.property("period_key")
        for btn in self._buttons.values():
            btn.setChecked(btn is sender)
        self.period_changed.emit(_period_value(key))

    def set_period_key(self, period_key: str, emit: bool = False) -> None:
        """Select a pill (no signal unless ``emit``). Falls back to 24H for an unknown key."""
        target = self._buttons.get(period_key) or self._buttons[_DEFAULT_KEY]
        for btn in self._buttons.values():
            btn.blockSignals(True)
            btn.setChecked(btn is target)
            btn.blockSignals(False)
        if emit:
            self.period_changed.emit(_period_value(target.property("period_key")))


class NetworkHeader(QWidget):
    """Machine-wide Download/Upload totals for the active period + the timeline control."""

    period_changed = pyqtSignal(int)  #: re-emits the pills' PERIOD_MAP index

    def __init__(self, i18n, initial_key: str = _DEFAULT_KEY, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 0, 4, 0)
        root.setSpacing(24)
        self._down = self._stat_block(self._tr("DOWNLOAD_LABEL", "Download"), c)
        self._up = self._stat_block(self._tr("UPLOAD_LABEL", "Upload"), c)
        root.addLayout(self._down[0])
        root.addLayout(self._up[0])
        root.addStretch(1)
        self._pills = PeriodSegmentedControl(initial_key)
        self._pills.period_changed.connect(self.period_changed)
        root.addWidget(self._pills, 0, Qt.AlignmentFlag.AlignVCenter)

    def _stat_block(self, label: str, c: dict) -> Tuple[QVBoxLayout, QLabel]:
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(su.font(tokens.TYPE_CAPTION))
        name.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        value = QLabel("—")
        value.setFont(su.font(tokens.TYPE_SUBTITLE))
        value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        col.addWidget(name)
        col.addWidget(value)
        return col, value

    # --- inputs -----------------------------------------------------------------
    def set_totals(self, up_bytes: float, down_bytes: float, period_key: Optional[str] = None) -> None:
        dv, du = format_data_size(down_bytes, self._i18n, precision=1)
        uv, uu = format_data_size(up_bytes, self._i18n, precision=1)
        self._down[1].setText(f"↓ {self._fmt(dv)} {du}")
        self._up[1].setText(f"↑ {self._fmt(uv)} {uu}")

    def set_period_key(self, period_key: str) -> None:
        self._pills.set_period_key(period_key, emit=False)

    def _fmt(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
