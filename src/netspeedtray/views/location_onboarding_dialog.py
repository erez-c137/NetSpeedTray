"""Location onboarding dialog - shown once when the user asks to display the Wi-Fi network name (SSID)
but Windows Location is off, so the WLAN API returns "access denied" instead of the name.

The whole point is TRUST: a network monitor asking to turn on "Location" looks alarming, so this
explains plainly that (a) it's a Windows privacy gate - not GPS, not tracking - and (b) NetSpeedTray
reads the name locally only, and never stores or transmits it. The band tag (2.4G / 5G) needs no
permission, so "just show the band" is always offered. One per session, and suppressible forever.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome

# Deep-link to Settings > Privacy & security > Location. Opened via QDesktopServices (handles the
# ms-settings: scheme on Windows).
LOCATION_SETTINGS_URI = "ms-settings:privacy-location"


class LocationOnboardingDialog(QDialog):
    """A calm, honest explainer for why the network name needs Windows Location - and what we don't do."""

    ACTION_DISMISS = "dismiss"
    ACTION_OPEN_LOCATION = "open_location"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.action = self.ACTION_DISMISS  # default if closed via X / Esc

        self.setWindowTitle(i18n.LOCATION_ONBOARDING_WINDOW_TITLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)

        title = QLabel(i18n.LOCATION_ONBOARDING_TITLE, self)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        body = QLabel(i18n.LOCATION_ONBOARDING_BODY, self)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setMinimumWidth(460)
        layout.addWidget(body)

        self.dont_remind = QCheckBox(i18n.LOCATION_ONBOARDING_DONT_REMIND, self)
        layout.addWidget(self.dont_remind)

        buttons = QHBoxLayout()
        open_btn = QPushButton(i18n.LOCATION_ONBOARDING_OPEN_BUTTON, self)
        open_btn.setDefault(True)
        band_btn = QPushButton(i18n.LOCATION_ONBOARDING_BAND_BUTTON, self)
        open_btn.clicked.connect(lambda: self._choose(self.ACTION_OPEN_LOCATION))
        band_btn.clicked.connect(lambda: self._choose(self.ACTION_DISMISS))
        buttons.addStretch(1)
        buttons.addWidget(band_btn)
        buttons.addWidget(open_btn)
        layout.addLayout(buttons)

    def dismissed_forever(self) -> bool:
        return self.dont_remind.isChecked()

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
