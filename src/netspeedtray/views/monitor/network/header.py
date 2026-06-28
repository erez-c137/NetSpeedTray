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
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QComboBox

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
        self.setStyleSheet(su.segmented_pills_style(su.is_dark_mode()))
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

    period_changed = pyqtSignal(int)     #: re-emits the pills' PERIOD_MAP index
    interface_changed = pyqtSignal(str)  #: NIC name, or "all"

    def __init__(self, i18n, initial_key: str = _DEFAULT_KEY, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 0, 4, 0)
        root.setSpacing(6)

        # Top row: the time-window label (so the totals self-describe) + the period pills.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._period_caption = QLabel(self._period_label(initial_key))
        self._period_caption.setFont(su.font(tokens.TYPE_CAPTION))
        self._period_caption.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        top.addWidget(self._period_caption, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self._pills = PeriodSegmentedControl(initial_key)
        self._pills.period_changed.connect(self.period_changed)
        top.addWidget(self._pills, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top)

        # Bottom row: the Download/Upload totals for that window.
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(28)
        self._down = self._stat_block(self._tr("DOWNLOAD_LABEL", "Download"), "↓", c)
        self._up = self._stat_block(self._tr("UPLOAD_LABEL", "Upload"), "↑", c)
        bottom.addLayout(self._down[0])
        bottom.addLayout(self._up[0])
        bottom.addStretch(1)
        # Per-NIC filter — scopes both the graph and these totals to one interface (or all).
        self._iface = QComboBox()
        self._iface.setMinimumWidth(150)
        self._iface.addItem(self._tr("ALL_INTERFACES_AGGREGATED_LABEL", "All Interfaces"), "all")
        self._iface.setStyleSheet(
            f"QComboBox {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: 4px; padding: 3px 8px; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {c['card_bg']}; color: {c['text_primary']};"
            f" selection-background-color: {c['accent']}; selection-color: white; outline: none; }}")
        self._iface.currentIndexChanged.connect(self._on_iface_changed)
        bottom.addWidget(self._iface, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(bottom)

    def _stat_block(self, label: str, glyph: str, c: dict) -> Tuple[QVBoxLayout, QLabel]:
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(su.font(tokens.TYPE_CAPTION))
        name.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        value = QLabel(f"{glyph} —")     # seed the populated shape so the first emit only swaps the number
        value.setFont(su.font(tokens.TYPE_SUBTITLE))
        value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        col.addWidget(name)
        col.addWidget(value)
        return col, value

    def _period_label(self, period_key: str) -> str:
        """Localized window name for the totals — reuses the existing TIMELINE_* i18n values
        (e.g. '24 Hours'), so no new translator keys."""
        return self._tr(period_key, period_key.replace("TIMELINE_", "").replace("_", " ").title())

    # --- inputs -----------------------------------------------------------------
    def set_totals(self, up_bytes: float, down_bytes: float, period_key: Optional[str] = None) -> None:
        dv, du = format_data_size(down_bytes, self._i18n, precision=1)
        uv, uu = format_data_size(up_bytes, self._i18n, precision=1)
        self._down[1].setText(f"↓ {self._fmt(dv)} {du}")
        self._up[1].setText(f"↑ {self._fmt(uv)} {uu}")
        if period_key:
            self._period_caption.setText(self._period_label(period_key))

    def set_period_key(self, period_key: str) -> None:
        self._pills.set_period_key(period_key, emit=False)
        self._period_caption.setText(self._period_label(period_key))

    def set_interfaces(self, names) -> None:
        """Populate the NIC dropdown ('All Interfaces' + each name), preserving the selection."""
        self._iface.blockSignals(True)
        prev = self._iface.currentData()
        self._iface.clear()
        self._iface.addItem(self._tr("ALL_INTERFACES_AGGREGATED_LABEL", "All Interfaces"), "all")
        for n in sorted(names or []):
            self._iface.addItem(n, n)
        idx = self._iface.findData(prev)
        if idx != -1:
            self._iface.setCurrentIndex(idx)
        self._iface.blockSignals(False)

    def _on_iface_changed(self, _idx: int) -> None:
        data = self._iface.currentData()
        self.interface_changed.emit(str(data) if data is not None else "all")

    def _fmt(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
