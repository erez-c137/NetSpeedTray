"""
"Data usage" settings section - the data-cap feature's in-settings home (Network page, 2.0 IA).

A header-toggle SettingExpander (the master toggle == data_cap_enabled) over the cap / reset-day
/ count / alerts controls. Mirrors the interim tray DataCapDialog so both write the same keys.
"""
from typing import Any, Callable, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSpinBox

from netspeedtray.utils.components import SettingCard, SettingExpander, Win11Toggle, Win11Segmented


class DataCapSettings(QWidget):
    """The data-cap configuration block, embeddable on the Network settings page."""

    def __init__(self, on_change: Callable[[], None], i18n=None) -> None:
        super().__init__()
        self.on_change = on_change
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._expander = SettingExpander(
            self._tr("DATA_CAP_SECTION_TITLE", "Data usage"),
            self._tr("DATA_CAP_SECTION_SUBTITLE", "Warn me as I approach a monthly limit"),
            header_toggle=True, initial_on=False,
        )
        self._expander.toggled.connect(lambda _on: self.on_change())
        body = self._expander.contentLayout()

        self._cap = QSpinBox()
        self._cap.setRange(0, 1_000_000)
        self._cap.setSuffix(" GB")
        self._cap.valueChanged.connect(lambda _v: self.on_change())
        body.addWidget(SettingCard(self._tr("DATA_CAP_MONTHLY_CAP_LABEL", "Monthly cap"), control=self._cap))

        self._reset = QSpinBox()
        self._reset.setRange(1, 28)
        self._reset.valueChanged.connect(lambda _v: self.on_change())
        body.addWidget(SettingCard(self._tr("DATA_CAP_RESET_DAY_LABEL", "Reset day of month"),
                                   self._tr("DATA_CAP_RESET_DAY_SUBTITLE", "Your billing cycle's reset day (1-28)"),
                                   control=self._reset))

        self._count = Win11Segmented([
            (self._tr("DATA_CAP_COUNT_TOTAL", "Total"), "total"),
            (self._tr("DATA_CAP_COUNT_DOWN", "Down"), "download"),
            (self._tr("DATA_CAP_COUNT_UP", "Up"), "upload"),
        ])
        self._count.valueChanged.connect(lambda _v: self.on_change())
        body.addWidget(SettingCard(self._tr("DATA_CAP_COUNT_LABEL", "Count"), control=self._count))

        self._alerts = Win11Toggle(initial_state=True)
        self._alerts.toggled.connect(lambda _v: self.on_change())
        body.addWidget(SettingCard(self._tr("DATA_CAP_ALERT_LABEL", "Alert at 80% and 100%"), control=self._alerts))

        root.addWidget(self._expander)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    def load_settings(self, config: Dict[str, Any]) -> None:
        self._expander.setChecked(bool(config.get("data_cap_enabled", False)))
        self._cap.setValue(int(config.get("data_cap_gb", 0) or 0))
        self._reset.setValue(int(config.get("data_cap_reset_day", 1)))
        self._count.setValue(config.get("data_cap_count", "total"))
        self._alerts.setChecked(bool(config.get("data_cap_alert_enabled", True)))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "data_cap_enabled": self._expander.isChecked(),
            "data_cap_gb": float(self._cap.value()),
            "data_cap_reset_day": int(self._reset.value()),
            "data_cap_count": self._count.value() or "total",
            "data_cap_alert_enabled": self._alerts.isChecked(),
        }
