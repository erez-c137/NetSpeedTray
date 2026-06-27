"""
Update-available dialog — shows the version delta + the GitHub release notes and
Download / Skip / Not Now.

The release body is rendered as INERT plain text via a read-only QPlainTextEdit
(never rich HTML), so nothing in the notes can execute or auto-open links. The same
dialog is intended to host the one-click update flow (progress + signature
verification) in a later phase; for now "Download" opens the release page.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome


class UpdateDialog(QDialog):
    """Update-available dialog with inert release notes and three actions."""

    ACTION_DISMISS = "dismiss"
    ACTION_DOWNLOAD = "download"
    ACTION_SKIP = "skip"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def __init__(self, i18n, current: str, latest: str, body: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.action = self.ACTION_DISMISS  # default if closed via X / Esc

        self.setWindowTitle(i18n.UPDATE_AVAILABLE_TITLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        header = QLabel(i18n.UPDATE_AVAILABLE_TEXT.format(current=current, latest=latest), self)
        header.setWordWrap(True)
        layout.addWidget(header)

        notes_text = (body or "").strip()
        if notes_text:
            layout.addWidget(QLabel(i18n.UPDATE_RELEASE_NOTES_LABEL.format(latest=latest), self))
            self.notes = QPlainTextEdit(notes_text, self)
            self.notes.setReadOnly(True)
            self.notes.setMinimumSize(480, 240)
            layout.addWidget(self.notes, 1)

        buttons = QHBoxLayout()
        skip_btn = QPushButton(i18n.UPDATE_SKIP_BUTTON, self)
        dismiss_btn = QPushButton(i18n.UPDATE_DISMISS_BUTTON, self)
        download_btn = QPushButton(i18n.UPDATE_DOWNLOAD_BUTTON, self)
        download_btn.setDefault(True)
        skip_btn.clicked.connect(lambda: self._choose(self.ACTION_SKIP))
        dismiss_btn.clicked.connect(lambda: self._choose(self.ACTION_DISMISS))
        download_btn.clicked.connect(lambda: self._choose(self.ACTION_DOWNLOAD))
        buttons.addWidget(skip_btn)
        buttons.addStretch(1)
        buttons.addWidget(dismiss_btn)
        buttons.addWidget(download_btn)
        layout.addLayout(buttons)

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
