"""
Speed Color Coding Settings Page.
Handles network speed thresholds and color configuration.
"""
from typing import Dict, Any, Callable
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QDoubleSpinBox

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.components import Win11Toggle, SettingCard
from netspeedtray.utils.helpers import get_unit_labels_for_type
from netspeedtray.views.settings.pages._fluent import (
    section_header, page_layout, LastValidHex, ThemedColorPair, swatch_style)

class ColorsPage(QWidget):
    # High, the optional Medium (#9), Low - top to bottom, the order the bands ascend in reverse.
    BANDS = ("high_speed", "medium_speed", "low_speed")
    COLOR_KEYS = tuple(f"{band}_color_{suffix}" for band in BANDS
                       for suffix in constants.config.defaults.THEME_SUFFIXES)

    def __init__(self, i18n, on_change: Callable[[], None], color_dialog_callback: Callable[[str], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self.open_color_dialog = color_dialog_callback

        # Last complete #RRGGBB per colour field (S8): a half-typed hex is never applied or saved.
        d = constants.config.defaults
        self._hex = LastValidHex({k: d.DEFAULT_CONFIG[k] for k in self.COLOR_KEYS})
        self._swatches: Dict[str, tuple] = {}   # config key -> (swatch button, hex field)

        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: a Win11 Settings card per setting. The master toggle gates a container of
        # threshold + swatch cards (shown only when color coding is on). All control attribute
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

        # Thresholds + colors, two cards each (the speed at which to switch, then the color to use). Each
        # colour card holds a dark-taskbar and a light-taskbar swatch (the colour scheme, 2.2 rc.2).
        i18n = self.i18n
        strings = {   # key: (threshold label, threshold tooltip, colour label, colour tooltip)
            "high_speed": (i18n.HIGH_SPEED_THRESHOLD_LABEL, getattr(i18n, "HIGH_SPEED_THRESHOLD_TOOLTIP", ""),
                           i18n.HIGH_SPEED_COLOR_LABEL, getattr(i18n, "HIGH_SPEED_COLOR_TOOLTIP", "")),
            "medium_speed": (i18n.MEDIUM_SPEED_THRESHOLD_LABEL, getattr(i18n, "MEDIUM_SPEED_THRESHOLD_TOOLTIP", ""),
                             i18n.MEDIUM_SPEED_COLOR_LABEL, getattr(i18n, "MEDIUM_SPEED_COLOR_TOOLTIP", "")),
            "low_speed": (i18n.LOW_SPEED_THRESHOLD_LABEL, getattr(i18n, "LOW_SPEED_THRESHOLD_TOOLTIP", ""),
                          i18n.LOW_SPEED_COLOR_LABEL, getattr(i18n, "LOW_SPEED_COLOR_TOOLTIP", "")),
        }
        for key in self.BANDS:
            t_label, t_tip, c_label, c_tip = strings[key]
            spin = QDoubleSpinBox()
            spin.setRange(0, 10000)
            if key == "medium_speed":
                # Optional (#9): 0 reads "Off" - the same word the other optional thresholds use - and
                # keeps the three levels. Its colour card hides while it is off.
                spin.setSpecialValueText(self.i18n.CONN_PLAN_OFF)
                spin.valueChanged.connect(lambda _v: self._sync_medium_color_card())
            # Suffix is set per the active unit type in load_settings (PR #165, @rami123): the old static
            # " Mbps" was wrong whenever the widget was set to bytes or binary units (MB/s, Mibps, …).
            spin.setToolTip(t_tip)
            spin.setMinimumWidth(150)
            spin.valueChanged.connect(self.on_change)
            setattr(self, f"{key}_threshold", spin)
            cc_v_layout.addWidget(SettingCard(t_label, control=spin))

            pair = ThemedColorPair(self.i18n, f"{key}_color", c_label, self.open_color_dialog,
                                   self._on_hex_edited, c_tip)
            for ck, button in pair.buttons.items():
                setattr(self, f"{ck}_button", button)
                setattr(self, f"{ck}_input", pair.inputs[ck])
                self._swatches[ck] = (button, pair.inputs[ck])
            card = SettingCard(c_label, control=pair)
            setattr(self, f"{key}_color_card", card)
            cc_v_layout.addWidget(card)

        layout.addWidget(self.color_container)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        self.enable_colors.setChecked(bool(config.get("color_coding", False)))
        self.color_container.setVisible(self.enable_colors.isChecked())

        factory = constants.config.defaults.DEFAULT_CONFIG
        for key, (button, line) in self._swatches.items():
            c = str(config.get(key) or factory[key])
            button.setStyleSheet(swatch_style(c))
            line.setText(c)

        threshold_suffix = self._threshold_suffix(config)
        for key in self.BANDS:
            threshold = float(config.get(f"{key}_threshold", factory[f"{key}_threshold"]) or 0.0)
            threshold_spin = getattr(self, f"{key}_threshold")
            threshold_spin.setSuffix(threshold_suffix)
            threshold_spin.setValue(threshold)
        self._sync_medium_color_card()

    def set_unit_type(self, unit_type: str) -> None:
        """The data-format unit changed in this session: relabel the thresholds (S9 - they kept the
        unit the dialog opened with until the next open)."""
        suffix = self._threshold_suffix({"unit_type": unit_type})
        for key in self.BANDS:
            getattr(self, f"{key}_threshold").setSuffix(suffix)

    def _threshold_suffix(self, config: Dict[str, Any]) -> str:
        """The Mbps-position unit label for the active unit type (e.g. ' Mbps', ' MB/s', ' Mibps')."""
        unit_type = str(config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE))
        labels = get_unit_labels_for_type(self.i18n, unit_type, short_labels=False)
        return f" {labels[2]}"

    def get_settings(self) -> Dict[str, Any]:
        return {
            "color_coding": self.enable_colors.isChecked(),
            "high_speed_threshold": self.high_speed_threshold.value(),
            "medium_speed_threshold": self.medium_speed_threshold.value(),
            "low_speed_threshold": self.low_speed_threshold.value(),
            **{key: self._hex.get(key, line.text()) for key, (_b, line) in self._swatches.items()},
        }

    def _sync_medium_color_card(self) -> None:
        """The Medium colour only means something while the Medium threshold is on."""
        self.medium_speed_color_card.setVisible(self.medium_speed_threshold.value() > 0)

    def _on_hex_edited(self, key: str, text: str) -> None:
        """Only a complete #RRGGBB repaints the swatch and reaches the live preview (S8)."""
        if not self._hex.remember(key, text):
            return
        self._swatches[key][0].setStyleSheet(swatch_style(text.strip()))
        self.on_change()

    def _on_color_coding_toggled(self, checked: bool):
        self.color_container.setVisible(checked)
        self.on_change()

    def set_color_input(self, key: str, hex_code: str):
        """Show a picked colour in the field of config key ``key`` (e.g. ``high_speed_color_light``)."""
        if key in self._swatches:
            button, line = self._swatches[key]
            line.setText(hex_code)
            button.setStyleSheet(swatch_style(hex_code))
            self.on_change()
