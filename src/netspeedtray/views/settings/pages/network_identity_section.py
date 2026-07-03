""""Network identity" settings section (Network page, v2.1).

Surfaces the Wi-Fi band / network indicator on the taskbar widget:
  • show_network_identity - the header toggle; shows the band tag (2.4G/5G/6G) on the widget. The band
    is read locally with no Location permission; the SSID (a later add) needs Windows Location on.
  • band_display - how the band presents: Always (neutral), Color-coded (2.4G amber / 5G green /
    6G blue), or Alert only (show a red 2.4G ONLY when you've dropped to the slow band; clean otherwise).
"""
from typing import Any, Callable, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout

from netspeedtray.utils.components import SettingCard, SettingExpander, Win11ComboBox


class NetworkIdentitySettings(QWidget):
    """Header toggle = show_network_identity; a 'Show' picker chooses band / SSID / both, and a band
    display picker chooses how the band presents (always / color-coded / alert-only)."""

    _CONTENT = [
        ("band", "IDENTITY_MODE_BAND", "Band only (2.4G / 5G)"),
        ("ssid", "IDENTITY_MODE_SSID", "Network name (SSID)"),
        ("both", "IDENTITY_MODE_BOTH", "Name and band"),
    ]
    _MODES = [
        ("always", "BAND_DISPLAY_ALWAYS", "Always"),
        ("colored", "BAND_DISPLAY_COLORED", "Color-coded (2.4G / 5G / 6G)"),
        ("alert_only", "BAND_DISPLAY_ALERT_ONLY", "Alert only (warn on 2.4 GHz)"),
    ]

    def __init__(self, on_change: Callable[[], None], i18n=None) -> None:
        super().__init__()
        self.on_change = on_change
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._exp = SettingExpander(
            self._tr("NETWORK_IDENTITY_TITLE", "Network identity"),
            self._tr("NETWORK_IDENTITY_SUB",
                     "Show the Wi-Fi band (2.4G / 5G) on the widget - the thing Windows hides at a glance."),
            header_toggle=True, initial_on=False)
        self._exp.toggled.connect(lambda _on: self.on_change())
        body = self._exp.contentLayout()

        # What to show: band, the network name (SSID), or both. SSID is Location-gated (honest note).
        self._mode = Win11ComboBox()
        self._mode.setMinimumWidth(220)
        for value, key, default in self._CONTENT:
            self._mode.addItem(self._tr(key, default), userData=value)
        self._mode.currentIndexChanged.connect(lambda _i: self.on_change())
        body.addWidget(SettingCard(
            self._tr("IDENTITY_MODE_LABEL", "Show"),
            self._tr("IDENTITY_MODE_SUB",
                     "The band shows on every PC. The network name (SSID) needs Windows Location turned on."),
            control=self._mode))

        # How the band presents.
        self._display = Win11ComboBox()
        self._display.setMinimumWidth(220)
        for value, key, default in self._MODES:
            self._display.addItem(self._tr(key, default), userData=value)
        self._display.currentIndexChanged.connect(lambda _i: self.on_change())
        body.addWidget(SettingCard(
            self._tr("BAND_DISPLAY_LABEL", "Band display"),
            self._tr("BAND_DISPLAY_SUB", "Alert only keeps the widget clean and shows a red 2.4G only when it matters."),
            control=self._display))

        root.addWidget(self._exp)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    def load_settings(self, config: Dict[str, Any]) -> None:
        self._exp.setChecked(bool(config.get("show_network_identity", False)))
        mi = self._mode.findData(config.get("identity_mode", "band"))
        self._mode.setCurrentIndex(mi if mi >= 0 else 0)
        idx = self._display.findData(config.get("band_display", "always"))
        self._display.setCurrentIndex(idx if idx >= 0 else 0)

    def get_settings(self) -> Dict[str, Any]:
        return {
            "show_network_identity": self._exp.isChecked(),
            "identity_mode": self._mode.currentData() or "band",
            "band_display": self._display.currentData() or "always",
        }
