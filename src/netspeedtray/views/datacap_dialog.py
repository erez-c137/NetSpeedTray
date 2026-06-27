"""
Data-cap settings dialog (interim).

A small, self-contained dialog to configure the data cap — enable, monthly cap, reset
day, what counts, and the 80%/100% alerts — opened from the tray menu. Built from the
new design-system primitives (a preview of the settings rework); when the full Fluent
settings rework lands, these controls move to a "Data usage" expander on the Network page
and this dialog can retire.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.components import SettingCard, Win11Toggle, Win11Segmented
from netspeedtray.utils.dwm import apply_win11_chrome


def _bytes_to_gb(b: float) -> float:
    return b / (1000 ** 3)


class DataCapDialog(QDialog):
    def __init__(self, config: Dict[str, Any], used_bytes: float = 0.0,
                 parent: Optional[QWidget] = None, i18n=None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self.setWindowTitle(self._tr("DATA_CAP_DIALOG_TITLE", "Data cap"))
        self.setStyleSheet(su.dialog_style())
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_M)
        root.setSpacing(tokens.SPACE_XS)

        title = QLabel(self._tr("DATA_CAP_DIALOG_TITLE", "Data cap"))
        title.setFont(su.font(tokens.TYPE_TITLE))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        root.addWidget(title)

        used = QLabel(self._tr("DATA_CAP_USED_TEMPLATE", "Used this period: {used:.1f} GB")
                      .format(used=_bytes_to_gb(used_bytes)))
        used.setFont(su.font(tokens.TYPE_CAPTION))
        used.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        root.addWidget(used)
        root.addSpacing(tokens.SPACE_S)

        self._enable = Win11Toggle(initial_state=bool(config.get("data_cap_enabled", False)))
        root.addWidget(SettingCard(self._tr("DATA_CAP_ENABLE_LABEL", "Enable data cap"),
                                   self._tr("DATA_CAP_SECTION_SUBTITLE", "Warn me as I approach a monthly limit"),
                                   control=self._enable))

        self._cap = QSpinBox()
        self._cap.setRange(0, 1_000_000)
        self._cap.setSuffix(" GB")
        self._cap.setValue(int(config.get("data_cap_gb", 0) or 0))
        root.addWidget(SettingCard(self._tr("DATA_CAP_MONTHLY_CAP_LABEL", "Monthly cap"), control=self._cap))

        self._reset = QSpinBox()
        self._reset.setRange(1, 28)
        self._reset.setValue(int(config.get("data_cap_reset_day", 1)))
        root.addWidget(SettingCard(self._tr("DATA_CAP_RESET_DAY_LABEL", "Reset day of month"),
                                   self._tr("DATA_CAP_RESET_DAY_SUBTITLE", "Your billing cycle's reset day (1-28)"),
                                   control=self._reset))

        self._count = Win11Segmented([
            (self._tr("DATA_CAP_COUNT_TOTAL", "Total"), "total"),
            (self._tr("DATA_CAP_COUNT_DOWN", "Down"), "download"),
            (self._tr("DATA_CAP_COUNT_UP", "Up"), "upload"),
        ])
        self._count.setValue(config.get("data_cap_count", "total"))
        root.addWidget(SettingCard(self._tr("DATA_CAP_COUNT_LABEL", "Count"), control=self._count))

        self._alerts = Win11Toggle(initial_state=bool(config.get("data_cap_alert_enabled", True)))
        root.addWidget(SettingCard(self._tr("DATA_CAP_ALERT_LABEL", "Alert at 80% and 100%"), control=self._alerts))

        root.addSpacing(tokens.SPACE_S)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(self._tr("DATA_CAP_CANCEL_BUTTON", "Cancel"))
        cancel.setStyleSheet(su.button_style())
        cancel.clicked.connect(self.reject)
        save = QPushButton(self._tr("DATA_CAP_SAVE_BUTTON", "Save"))
        save.setStyleSheet(su.button_style(accent=True))
        save.setDefault(True)
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

        self.setMinimumWidth(380)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def get_values(self) -> Dict[str, Any]:
        """The config updates chosen by the user."""
        return {
            "data_cap_enabled": self._enable.isChecked(),
            "data_cap_gb": float(self._cap.value()),
            "data_cap_reset_day": int(self._reset.value()),
            "data_cap_count": self._count.value() or "total",
            "data_cap_alert_enabled": self._alerts.isChecked(),
        }
