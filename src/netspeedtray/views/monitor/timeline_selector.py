"""
TimelineSelector — the Monitor's history-window dropdown.

A Win11 dropdown BUTTON (QToolButton + menu, with a "▾" chevron so it unmistakably reads as a
pulldown) for choosing the time window: Live/Session, 30m, 1h, 4h, 8h, 12h, 24h, 48h, Week, Month,
All, Since boot. It emits ``period_changed(index)`` with the PERIOD_MAP index — the same value
GraphHost.set_period expects — so it drops into the Overview and Network headers and drives the
existing DB read path. (Design panel: a dropdown beats pills once the range gets this granular.)
"""
from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QMenu

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


class TimelineSelector(QWidget):
    """Dropdown button of history windows; emits period_changed(PERIOD_MAP index)."""

    period_changed = pyqtSignal(int)

    # Display order by how a person reads time (NOT PERIOD_MAP index order).
    _ORDER = [0, 6, 7, 8, 9, 10, 2, 11, 3, 4, 5, 1]
    _CHEVRON = "▾"   # ▾ — a down triangle that renders in normal fonts (no asset, no font mixing)

    def __init__(self, i18n, current_index: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._index = int(current_index)
        c = su.semantic_colors()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._btn = QToolButton()
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._btn.setFont(su.font(tokens.TYPE_BODY))
        r = tokens.RADIUS_CONTROL
        self._btn.setStyleSheet(
            f"QToolButton {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: {r}px; padding: 4px 12px;"
            f" text-align: left; }}"
            f" QToolButton:hover {{ border-color: {c['accent']}; }}"
            f" QToolButton::menu-indicator {{ image: none; width: 0; }}")   # we draw our own ▾

        self._menu = QMenu(self._btn)
        self._menu.setStyleSheet(
            f"QMenu {{ background: {c['card_bg']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: 6px; padding: 4px; }}"
            f" QMenu::item {{ padding: 5px 28px 5px 12px; border-radius: 4px; }}"
            f" QMenu::item:selected {{ background: {c['accent']}; color: white; }}")
        self._actions: Dict[int, object] = {}
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        pmap = constants.data.history_period.PERIOD_MAP
        for idx in self._ORDER:
            key = pmap.get(idx)
            if not key:
                continue
            act = self._menu.addAction(self._label(key))
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, i=idx: self._select(i))
            group.addAction(act)
            self._actions[idx] = act
        self._btn.setMenu(self._menu)

        self.set_period_index(self._index, emit=False)
        lay.addWidget(self._btn)

    def _label(self, key: str) -> str:
        return str(getattr(self._i18n, key, key.replace("TIMELINE_", "").replace("_", " ").title()))

    def _period_key(self) -> str:
        return constants.data.history_period.PERIOD_MAP.get(self._index, "TIMELINE_24_HOURS")

    def _select(self, index: int) -> None:
        if index != self._index:
            self._index = int(index)
            self._sync()
            self.period_changed.emit(self._index)

    def _sync(self) -> None:
        self._btn.setText(f"{self._label(self._period_key())}    {self._CHEVRON}")
        act = self._actions.get(self._index)
        if act is not None:
            act.setChecked(True)

    def set_period_index(self, index: int, emit: bool = False) -> None:
        self._index = int(index)
        self._sync()
        if emit:
            self.period_changed.emit(self._index)

    def current_index(self) -> int:
        return self._index

    def current_label(self) -> str:
        """The human label for the active window (e.g. "Last 24 hours") — used by the Stats-detail
        sheet title and the export filename."""
        return self._label(self._period_key())
