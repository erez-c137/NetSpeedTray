"""
Temperature onboarding dialog — shown once when the user turns on CPU/GPU temperatures (or
power) but no sensor source is detected. Instead of a silent blank readout, it explains why,
points one click at LibreHardwareMonitor, and is explicit that NetSpeedTray itself never runs
as admin and never ships a driver of its own.

Gated upstream (monitor thread emits at most once per session, only when temps/power are on)
and further suppressible via the "Don't remind me again" checkbox (config: temp_onboarding_dismissed).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome

# The download lives on the project's releases page; opened in the user's browser.
LHM_RELEASES_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases"


class TempOnboardingDialog(QDialog):
    """A calm, actionable explainer for enabling temperatures with no sensor source present."""

    ACTION_DISMISS = "dismiss"
    ACTION_GET_LHM = "get_lhm"

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

        # NOTE: English literals pending the single 2.0 i18n pass.
        self.setWindowTitle("Turn on temperatures")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)

        title = QLabel("CPU/GPU temperatures need a sensor source", self)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        body = QLabel(
            "Reading temperatures requires a small kernel-level sensor driver that Windows "
            "doesn't expose to normal apps. The free, open-source <b>LibreHardwareMonitor</b> "
            "(LHM) provides one and publishes the readings — NetSpeedTray reads them from there."
            "<br><br>"
            "• <b>NetSpeedTray itself never runs as admin</b> and never ships a driver of its own.<br>"
            "• NVIDIA GPU temperatures already work natively via <i>nvidia-smi</i> — no LHM needed.<br>"
            "• Once LHM is running, temperatures appear automatically."
            "<br><br>"
            "You can also just turn temperatures back off in Settings → Hardware.",
            self,
        )
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setMinimumWidth(440)
        layout.addWidget(body)

        self.dont_remind = QCheckBox("Don't remind me again", self)
        layout.addWidget(self.dont_remind)

        buttons = QHBoxLayout()
        get_lhm_btn = QPushButton("Get LibreHardwareMonitor", self)
        get_lhm_btn.setDefault(True)
        not_now_btn = QPushButton("Not now", self)
        get_lhm_btn.clicked.connect(lambda: self._choose(self.ACTION_GET_LHM))
        not_now_btn.clicked.connect(lambda: self._choose(self.ACTION_DISMISS))
        buttons.addStretch(1)
        buttons.addWidget(not_now_btn)
        buttons.addWidget(get_lhm_btn)
        layout.addLayout(buttons)

    def dismissed_forever(self) -> bool:
        return self.dont_remind.isChecked()

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
