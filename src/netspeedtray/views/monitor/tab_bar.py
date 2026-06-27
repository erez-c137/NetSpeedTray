"""
FlatTabBar — the Monitor window's primary tab strip.

A Fluent "pivot": a flat row of checkable buttons where the active tab is marked by the accent
color + a 2px underline, not a heavy segmented pill — the premium Win11 idiom for top-level
horizontal tabs (Settings sub-pages, Edge). Deliberately NOT a QTabWidget: the Monitor keeps each
page lazy (a QStackedWidget swaps a cheap placeholder for the real, matplotlib-bearing page on
first activation), so the idle-RAM win survives. The bar holds only (tab_id, label) and emits the
selected stack index; it owns no page references and never imports matplotlib.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QButtonGroup

from netspeedtray.utils import styles as su


class FlatTabBar(QWidget):
    """A flat Fluent pivot tab strip. Emits ``tab_selected(index)`` — the stack index."""

    tab_selected = pyqtSignal(int)

    def __init__(self, tabs: List[Tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        """tabs: ordered ``(tab_id, label)`` pairs; a tab's index is its position in this list."""
        super().__init__(parent)
        self._buttons: Dict[str, QToolButton] = {}
        self._order: List[str] = [tab_id for tab_id, _ in tabs]

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for index, (tab_id, label) in enumerate(tabs):
            btn = QToolButton()
            btn.setText(str(label))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _checked=False, i=index: self.tab_selected.emit(i))
            self._group.addButton(btn)
            row.addWidget(btn)
            self._buttons[tab_id] = btn
        row.addStretch(1)

        if self._order:
            self._buttons[self._order[0]].setChecked(True)

        self._apply_style()

    def _apply_style(self) -> None:
        c = su.semantic_colors()
        accent = su.get_accent_color().name()
        self.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                color: {c['text_secondary']};
                border: none;
                border-bottom: 2px solid transparent;
                padding: 8px 14px 6px 14px;
                font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
                font-size: 14px;
            }}
            QToolButton:hover {{
                color: {c['text_primary']};
                background: {c['subtle_fill']};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QToolButton:checked {{
                color: {accent};
                border-bottom: 2px solid {accent};
                font-weight: 600;
            }}
        """)

    def setCurrentIndex(self, index: int) -> None:
        """Programmatically select a tab (also fires ``tab_selected`` so the window activates it)."""
        if 0 <= index < len(self._order):
            self._buttons[self._order[index]].setChecked(True)
            self.tab_selected.emit(index)

    def set_tab_visible(self, tab_id: str, visible: bool) -> None:
        """Show/hide a tab button (e.g. Hardware, only when hardware monitoring is enabled)."""
        btn = self._buttons.get(tab_id)
        if btn is not None:
            btn.setVisible(visible)
