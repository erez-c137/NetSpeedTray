"""
"Connection" settings section — latency monitoring + advertised-plan speeds (Network page, 2.0 IA).

Surfaces three already-built-but-dormant features:
  • latency_enabled — ping + loss to the default gateway, and connection-drop events over time (on by
    default; LAN-only, never leaves the network).
  • latency_public_enabled / latency_public_host — the OPT-IN public anchor that measures true internet
    latency by pinging an external server (off by default — it's the one thing that leaves the LAN).
  • plan_down_mbps / plan_up_mbps — your advertised plan speeds, which drive the Statistics sheet's
    "% of time below the plan" figure (0 = off).
"""
from typing import Any, Callable, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSpinBox

from netspeedtray.utils.components import SettingCard, SettingExpander, Win11Toggle, Win11ComboBox


class ConnectionSettings(QWidget):
    """Latency (gateway always + opt-in public anchor) and advertised-plan-speed controls."""

    def __init__(self, on_change: Callable[[], None], i18n=None) -> None:
        super().__init__()
        self.on_change = on_change
        self._i18n = i18n

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # --- Latency (header toggle == latency_enabled; the public anchor is a nested opt-in) ---
        self._lat = SettingExpander(
            self._tr("CONN_LATENCY_TITLE", "Internet latency"),
            self._tr("CONN_LATENCY_SUB", "Ping + packet loss to your router, and connection drops over time"),
            header_toggle=True, initial_on=True)
        self._lat.toggled.connect(lambda _on: self.on_change())
        body = self._lat.contentLayout()

        self._public = Win11Toggle(initial_state=False)
        self._public.toggled.connect(lambda _v: self.on_change())
        body.addWidget(SettingCard(
            self._tr("CONN_PUBLIC_LABEL", "Also measure true internet latency"),
            self._tr("CONN_PUBLIC_SUB",
                     "Pings a public server — this leaves your local network, so it's off by default."),
            control=self._public))

        self._host = Win11ComboBox()
        self._host.setEditable(True)
        self._host.addItems(["1.1.1.1", "8.8.8.8", "9.9.9.9"])
        self._host.setMinimumWidth(160)
        self._host.currentTextChanged.connect(lambda _t: self.on_change())
        body.addWidget(SettingCard(self._tr("CONN_HOST_LABEL", "Internet test server"), control=self._host))
        root.addWidget(self._lat)

        # --- Advertised plan speed (drives the "% below plan" stat; 0 = off) ---
        self._plan = SettingExpander(
            self._tr("CONN_PLAN_TITLE", "Advertised plan speed"),
            self._tr("CONN_PLAN_SUB", "Flags time spent below your plan in the Statistics sheet (0 = off)."),
            expanded=True)
        pbody = self._plan.contentLayout()
        self._down = self._mbps_spin()
        self._up = self._mbps_spin()
        pbody.addWidget(SettingCard(self._tr("DOWNLOAD_LABEL", "Download"), control=self._down))
        pbody.addWidget(SettingCard(self._tr("UPLOAD_LABEL", "Upload"), control=self._up))
        root.addWidget(self._plan)

    def _mbps_spin(self) -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, 100000)
        s.setSingleStep(10)
        s.setSuffix(" Mbps")
        s.setSpecialValueText(self._tr("CONN_PLAN_OFF", "Off"))   # 0 reads "Off", not "0 Mbps"
        s.valueChanged.connect(lambda _v: self.on_change())
        return s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    def load_settings(self, config: Dict[str, Any]) -> None:
        self._lat.setChecked(bool(config.get("latency_enabled", True)))
        self._public.setChecked(bool(config.get("latency_public_enabled", False)))
        self._host.setCurrentText(str(config.get("latency_public_host", "1.1.1.1")))
        self._down.setValue(int(config.get("plan_down_mbps", 0) or 0))
        self._up.setValue(int(config.get("plan_up_mbps", 0) or 0))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "latency_enabled": self._lat.isChecked(),
            "latency_public_enabled": self._public.isChecked(),
            "latency_public_host": self._host.currentText().strip() or "1.1.1.1",
            "plan_down_mbps": int(self._down.value()),
            "plan_up_mbps": int(self._up.value()),
        }
