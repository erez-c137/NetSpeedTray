"""
Speed Color Coding Settings Page.
Handles network speed thresholds and color configuration.
"""
from typing import Dict, Any, Callable
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QDoubleSpinBox,
)

from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.components import Win11Toggle, SettingCard
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

class ColorsPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None], color_dialog_callback: Callable[[str], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self.open_color_dialog = color_dialog_callback
        
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: a Win11 Settings card per setting. The master toggle gates a container of
        # threshold + swatch cards (shown only when colour coding is on). All control attribute
        # names + the load/get wiring are unchanged.
        layout = page_layout(self)

        layout.addWidget(section_header(self.i18n.COLOR_CODING_GROUP))
        self.enable_colors = Win11Toggle(label_text="")
        self.enable_colors.toggled.connect(self._on_color_coding_toggled)
        layout.addWidget(SettingCard(self.i18n.ENABLE_COLOR_CODING_LABEL, control=self.enable_colors))

        self.color_container = QWidget()
        cc_v_layout = QVBoxLayout(self.color_container)
        cc_v_layout.setContentsMargins(0, 0, 0, 0)
        cc_v_layout.setSpacing(tokens.SPACE_XS)

        # Thresholds + colours, two cards each (the speed at which to switch, then the colour to use).
        for key, t_label, c_label in [
            ("high_speed", self.i18n.HIGH_SPEED_THRESHOLD_LABEL, self.i18n.HIGH_SPEED_COLOR_LABEL),
            ("low_speed", self.i18n.LOW_SPEED_THRESHOLD_LABEL, self.i18n.LOW_SPEED_COLOR_LABEL),
        ]:
            spin = QDoubleSpinBox()
            spin.setRange(0, 10000)
            spin.setSuffix(f" {self.i18n.MBITS_UNIT}")
            spin.setToolTip(getattr(self.i18n, f"{key.upper()}_THRESHOLD_TOOLTIP", ""))
            spin.setMinimumWidth(150)
            spin.valueChanged.connect(self.on_change)
            setattr(self, f"{key}_threshold", spin)
            cc_v_layout.addWidget(SettingCard(t_label, control=spin))

            btn = QPushButton()
            btn.setObjectName(f"{key}_color")
            btn.setFixedSize(30, 24)
            btn.setToolTip(getattr(self.i18n, f"{key.upper()}_COLOR_TOOLTIP", ""))
            btn.clicked.connect(lambda checked, k=f"{key}_color": self.open_color_dialog(k))
            inp = QLineEdit()
            inp.setMaxLength(7)
            inp.setFixedWidth(90)
            inp.textChanged.connect(lambda: self.on_change())
            setattr(self, f"{key}_color_button", btn)
            setattr(self, f"{key}_color_input", inp)
            swatch = QWidget()
            sh = QHBoxLayout(swatch)
            sh.setContentsMargins(0, 0, 0, 0)
            sh.setSpacing(8)
            sh.addWidget(btn)
            sh.addWidget(inp)
            cc_v_layout.addWidget(SettingCard(c_label, control=swatch))

        layout.addWidget(self.color_container)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        self.enable_colors.setChecked(bool(config.get("color_coding", False)))
        self.color_container.setVisible(self.enable_colors.isChecked())
        
        for key in ["high_speed", "low_speed"]:
            c = config.get(f"{key}_color", "#FFFFFF")
            getattr(self, f"{key}_color_button").setStyleSheet(f"background-color: {c}; border: none;")
            getattr(self, f"{key}_color_input").setText(c)
            
            threshold = float(config.get(f"{key}_threshold", 5.0 if "high" in key else 1.0))
            getattr(self, f"{key}_threshold").setValue(threshold)

    def get_settings(self) -> Dict[str, Any]:
        return {
            "color_coding": self.enable_colors.isChecked(),
            "high_speed_threshold": self.high_speed_threshold.value(),
            "low_speed_threshold": self.low_speed_threshold.value(),
            "high_speed_color": self.high_speed_color_input.text(),
            "low_speed_color": self.low_speed_color_input.text()
        }

    def _on_color_coding_toggled(self, checked: bool):
        self.color_container.setVisible(checked)
        self.on_change()

    def set_color_input(self, key: str, hex_code: str):
        if hasattr(self, f"{key}_color_input"):
            getattr(self, f"{key}_color_input").setText(hex_code)
            getattr(self, f"{key}_color_button").setStyleSheet(f"background-color: {hex_code}; border: none;")
            self.on_change()
