"""
One-time 2.0 welcome dialog — shown once after upgrading, gated on the
`first_run_v2_seen` config flag. Tells the user the widget is now embedded in the
taskbar; "What's new" opens the release page, "Got it" dismisses.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome


class WelcomeDialog(QDialog):
    """A calm one-screen welcome for the 2.0 taskbar-embed."""

    ACTION_GOT_IT = "got_it"
    ACTION_WHATS_NEW = "whats_new"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.action = self.ACTION_GOT_IT  # default if closed via X / Esc

        self.setWindowTitle(i18n.WELCOME_2_0_TITLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)

        title = QLabel(i18n.WELCOME_2_0_TITLE, self)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        body = QLabel(i18n.WELCOME_2_0_BODY, self)
        body.setWordWrap(True)
        body.setMinimumWidth(420)
        layout.addWidget(body)

        buttons = QHBoxLayout()
        whats_new_btn = QPushButton(i18n.WELCOME_2_0_WHATS_NEW_BUTTON, self)
        got_it_btn = QPushButton(i18n.WELCOME_2_0_GOT_IT_BUTTON, self)
        got_it_btn.setDefault(True)
        whats_new_btn.clicked.connect(lambda: self._choose(self.ACTION_WHATS_NEW))
        got_it_btn.clicked.connect(lambda: self._choose(self.ACTION_GOT_IT))
        buttons.addStretch(1)
        buttons.addWidget(whats_new_btn)
        buttons.addWidget(got_it_btn)
        layout.addLayout(buttons)

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
