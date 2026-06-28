"""
TimelineSelector — the Monitor's history-window dropdown.

A single Win11 combo that scopes a Monitor surface to a time window, offering the full granular range
(Live/Session, 30m, 1h, 4h, 8h, 12h, 24h, 48h, Week, Month, All, Since boot). It emits
``period_changed(index)`` with the PERIOD_MAP index — the same value GraphHost.set_period expects — so
it drops into the Overview and Network headers in place of the segmented pills while still driving the
existing DB read path. The design panel chose a dropdown over pills because the granular set is too
many options for a comfortable segmented row.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox

from netspeedtray import constants
from netspeedtray.utils import styles as su


class TimelineSelector(QWidget):
    """Dropdown of history windows; emits period_changed(PERIOD_MAP index)."""

    period_changed = pyqtSignal(int)

    # Display order by how a person reads time (NOT PERIOD_MAP index order): live, then short→long, then
    # the special "since boot" anchor last.
    _ORDER = [0, 6, 7, 8, 9, 10, 2, 11, 3, 4, 5, 1]

    def __init__(self, i18n, current_index: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._combo.setStyleSheet(
            f"QComboBox {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: 4px; padding: 3px 8px; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {c['card_bg']}; color: {c['text_primary']};"
            f" selection-background-color: {c['accent']}; selection-color: white; outline: none; }}")

        pmap = constants.data.history_period.PERIOD_MAP
        for idx in self._ORDER:
            key = pmap.get(idx)
            if key:
                self._combo.addItem(self._label(key), idx)
        self.set_period_index(current_index, emit=False)
        self._combo.currentIndexChanged.connect(self._on_changed)
        lay.addWidget(self._combo)

    def _label(self, key: str) -> str:
        return str(getattr(self._i18n, key, key.replace("TIMELINE_", "").replace("_", " ").title()))

    def _on_changed(self, _combo_idx: int) -> None:
        idx = self._combo.currentData()
        if idx is not None:
            self.period_changed.emit(int(idx))

    def set_period_index(self, index: int, emit: bool = False) -> None:
        """Select the item whose PERIOD_MAP index is ``index`` (no-op if not present)."""
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == index:
                if not emit:
                    self._combo.blockSignals(True)
                self._combo.setCurrentIndex(i)
                if not emit:
                    self._combo.blockSignals(False)
                return

    def current_index(self) -> int:
        d = self._combo.currentData()
        return int(d) if d is not None else 2
