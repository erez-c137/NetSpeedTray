"""
A small frameless callout ("flyout") anchored near a point on screen.

Used for the first-run "unfold the knife" hint — a calm, non-modal card that points at
the widget and names the features most users never find (graphs, per-app activity,
CPU/GPU/temps). It does NOT use a system-tray icon (NetSpeedTray deliberately has none),
and doubles as the no-dependency fallback notification surface for data-cap alerts when
native Windows toasts aren't available.

Frameless + translucent + always-on-top + shown without stealing focus. Auto-dismisses
after a timeout (skipped when the OS / config asks to reduce motion is irrelevant here —
it's a timeout, not an animation). Built on the design tokens so it matches the app.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


class Flyout(QWidget):
    """A frameless, auto-dismissing callout. Call show_at(point) to display it."""
    dismissed = pyqtSignal()
    action_clicked = pyqtSignal()

    def __init__(self, title: str, message: str, action_text: Optional[str] = None,
                 auto_dismiss_ms: int = 12000, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # never steal focus
        self._auto_dismiss_ms = auto_dismiss_ms

        c = su.semantic_colors()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)  # room so the card's shadow/border isn't clipped

        card = QFrame()
        card.setObjectName("flyoutCard")
        card.setStyleSheet(
            f"QFrame#flyoutCard {{ background-color: {c['card_bg']}; "
            f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M)
        cl.setSpacing(tokens.SPACE_S)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel(title)
        title_lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title_lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        header.addWidget(title_lbl, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text_secondary']}; background: transparent; border: none; }}"
            f"QPushButton:hover {{ color: {c['text_primary']}; }}"
        )
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        cl.addLayout(header)

        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setMinimumWidth(280)
        msg.setMaximumWidth(360)
        msg.setFont(su.font(tokens.TYPE_BODY))
        msg.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        cl.addWidget(msg)

        if action_text:
            row = QHBoxLayout()
            row.addStretch(1)
            act = QPushButton(action_text)
            act.setCursor(Qt.CursorShape.PointingHandCursor)
            accent = su.get_accent_color().name()
            act.setStyleSheet(
                f"QPushButton {{ background-color: {accent}; color: white; border: none; "
                f"border-radius: {tokens.RADIUS_CONTROL}px; padding: 4px 14px; }}"
            )
            act.clicked.connect(self._on_action)
            row.addWidget(act)
            cl.addLayout(row)

        outer.addWidget(card)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    def show_at(self, point: QPoint) -> None:
        """Show the flyout with its bottom-right near `point` (e.g. the widget), nudged
        on-screen. Starts the auto-dismiss timer."""
        self.adjustSize()
        sz = self.size()
        x = point.x() - sz.width()
        y = point.y() - sz.height()
        if x < 0:
            x = max(0, point.x())
        if y < 0:
            y = max(0, point.y())
        self.move(x, y)
        self.show()
        if self._auto_dismiss_ms > 0:
            self._timer.start(self._auto_dismiss_ms)

    def _on_action(self) -> None:
        self.action_clicked.emit()
        self._dismiss()

    def _dismiss(self) -> None:
        self._timer.stop()
        self.dismissed.emit()
        self.close()
