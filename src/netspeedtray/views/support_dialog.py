"""
Support dialog - a calm, Windows 11-styled way to support the project.

Replaces the old plain QMessageBox: a dark, rounded dialog (native Win11 chrome via DWM) with the
app icon, a warm line, and one styled option row per support channel (each opens in the browser and
leaves the dialog open, so a user can star *and* sponsor). All copy reuses existing i18n keys.
"""
from __future__ import annotations

import webbrowser
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget

from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome

_SPONSORS_URL = "https://github.com/sponsors/erez-c137"
_KOFI_URL = "https://ko-fi.com/erezc137"
_BMC_URL = "https://buymeacoffee.com/erez.c137"
_STAR_URL = "https://github.com/erez-c137/NetSpeedTray"


class SupportDialog(QDialog):
    """The 'Support NetSpeedTray' popup, styled to match the rest of the 2.0 UI."""

    def __init__(self, i18n, parent: Optional[QWidget] = None, app_icon: Optional[QIcon] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._c = su.semantic_colors()
        c = self._c

        self.setWindowTitle(i18n.SUPPORT_DIALOG_TITLE)
        self.setStyleSheet(
            f"QDialog {{ background: {c['card_bg']}; }}"
            f" QLabel {{ color: {c['text_primary']}; background: transparent; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        # Header - app icon + title
        header = QHBoxLayout()
        header.setSpacing(12)
        if app_icon is not None and not app_icon.isNull():
            ic = QLabel(self)
            ic.setPixmap(app_icon.pixmap(QSize(40, 40)))
            header.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel(i18n.SUPPORT_DIALOG_TITLE, self)
        tf = title.font()
        tf.setPointSize(tf.pointSize() + 4)
        tf.setBold(True)
        title.setFont(tf)
        header.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        body = QLabel(i18n.SUPPORT_DIALOG_TEXT, self)
        body.setWordWrap(True)
        body.setMinimumWidth(430)
        body.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(body)

        # Option rows - heart = recurring sponsorship (accented), coffee = one-off tips, star = free.
        root.addWidget(self._option(i18n.SUPPORT_GITHUB_SPONSORS, 0xEB51, _SPONSORS_URL, accent=True))
        root.addWidget(self._option(i18n.SUPPORT_KOFI, 0xEC32, _KOFI_URL))
        root.addWidget(self._option(i18n.SUPPORT_BMC, 0xEC32, _BMC_URL))
        root.addWidget(self._option(i18n.SUPPORT_STAR_GITHUB, 0xE735, _STAR_URL))

        # Footer - Close
        foot = QHBoxLayout()
        foot.addStretch(1)
        close_btn = QPushButton(getattr(i18n, "CLOSE_BUTTON", "Close"), self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ padding: 7px 24px; border-radius: 6px; background: {c['subtle_fill']};"
            f" color: {c['text_primary']}; border: 1px solid {c['card_stroke']}; }}"
            f" QPushButton:hover {{ border-color: {c['accent']}; }}")
        close_btn.clicked.connect(self.accept)
        foot.addWidget(close_btn)
        root.addLayout(foot)

    def _option(self, text: str, codepoint: int, url: str, *, accent: bool = False) -> QPushButton:
        """A full-width, left-aligned Win11 'card' row: a Fluent glyph + the label, opens ``url``."""
        c = self._c
        btn = QPushButton("  " + text, self)
        btn.setIcon(su.fluent_icon(codepoint, 18, c['accent'] if accent else c['text_primary']))
        btn.setIconSize(QSize(18, 18))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        border = c['accent'] if accent else c['card_stroke']
        btn.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 11px 14px; border-radius: 7px; font-size: 10pt;"
            f" background: {c['subtle_fill']}; color: {c['text_primary']}; border: 1px solid {border}; }}"
            f" QPushButton:hover {{ border-color: {c['accent']}; background: {c['card_bg']}; }}")
        # Open the link but keep the dialog open, so a user can star AND tip.
        btn.clicked.connect(lambda: webbrowser.open(url))
        return btn

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass
