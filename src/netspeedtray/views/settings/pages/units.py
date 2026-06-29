"""
Units and Interface Layout Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtWidgets import QWidget, QComboBox

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, Win11Segmented, SettingCard, Win11ComboBox
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

class UnitsPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self.i18n, key, default)) if self.i18n is not None else default

    def _setup_ui(self):
        # 2.0 IA: one Win11 Settings card per setting under light section captions (was QGroupBox grids).
        # Control objects + load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Data Format ---
        layout.addWidget(section_header(self._tr("DISPLAY_FORMAT_GROUP", "Data Format")))

        self.unit_type = Win11ComboBox()
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BITS_DECIMAL, "bits_decimal")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BITS_BINARY, "bits_binary")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BYTES_DECIMAL, "bytes_decimal")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BYTES_BINARY, "bytes_binary")
        self.unit_type.setMinimumWidth(180)
        self.unit_type.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.UNIT_TYPE_LABEL, control=self.unit_type))

        # Force MB Display — when on, the renderer forces MB output regardless of unit type.
        self.speed_display_mode = Win11Toggle(label_text="")
        self.speed_display_mode.toggled.connect(self._on_force_mb_toggled)
        layout.addWidget(SettingCard(self._tr("FORCE_MB_LABEL", "Force MB Display"),
                                     control=self.speed_display_mode))

        # Decimals — a 3-value enum {0,1,2}: a segmented control is the native Win11 idiom, NOT a
        # continuous slider (which implied a magnitude and showed a meaningless "1").
        self.decimal_places = Win11Segmented([("0", 0), ("1", 1), ("2", 2)])
        self.decimal_places.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.DECIMAL_PLACES_LABEL, control=self.decimal_places))

        # --- Interface Layout ---
        layout.addWidget(section_header(self._tr("INTERFACE_LAYOUT_GROUP", "Interface Layout")))

        # NOTE: the old "Text Alignment" (left/center/right) control was removed in 2.0 — it was wired
        # into config but never consulted by any drawText call (the widget is right-anchored to the tray
        # and left-aligns its content), so the three options did nothing. The orphan config key is left
        # in the schema for back-compat; nothing reads it.

        self.swap_upload_download = Win11Toggle(label_text="")
        self.swap_upload_download.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.SWAP_UPLOAD_DOWNLOAD_LABEL,
                                     control=self.swap_upload_download))

        self.hide_arrows = Win11Toggle(label_text="")
        self.hide_arrows.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HIDE_ARROWS_LABEL, control=self.hide_arrows))

        self.hide_unit_suffix = Win11Toggle(label_text="")
        self.hide_unit_suffix.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HIDE_UNIT_SUFFIX_LABEL, control=self.hide_unit_suffix))

        self.short_unit_labels = Win11Toggle(label_text="")
        self.short_unit_labels.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.SHORT_UNIT_LABELS_LABEL, control=self.short_unit_labels))

        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        # Unit Type
        ut = config.get("unit_type", "bytes_binary")
        idx = self.unit_type.findData(ut)
        if idx >= 0: self.unit_type.setCurrentIndex(idx)
        
        # Force MB toggle
        mode = config.get("speed_display_mode", "auto")
        is_forced_mb = True if mode == "always_mbps" else False
        self.speed_display_mode.setChecked(is_forced_mb)
        self.unit_type.setEnabled(True)
        
        # Others
        self.decimal_places.setValue(config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES))

        # Toggles
        self.swap_upload_download.setChecked(config.get("swap_upload_download", constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD))
        self.hide_arrows.setChecked(config.get("hide_arrows", False))
        self.hide_unit_suffix.setChecked(config.get("hide_unit_suffix", False))
        self.short_unit_labels.setChecked(config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS))
        

    def get_settings(self) -> Dict[str, Any]:
        speed_mode = "always_mbps" if self.speed_display_mode.isChecked() else "auto"
        unit_type = self.unit_type.currentData()

        return {
            "unit_type": unit_type,
            "speed_display_mode": speed_mode,
            "decimal_places": self.decimal_places.value(),
            "swap_upload_download": self.swap_upload_download.isChecked(),
            "hide_arrows": self.hide_arrows.isChecked(),
            "hide_unit_suffix": self.hide_unit_suffix.isChecked(),
            "short_unit_labels": self.short_unit_labels.isChecked()
        }

    def _on_force_mb_toggled(self, checked: bool) -> None:
        """Handler when the Force MB toggle changes."""
        # The toggle state is saved, and the rendering logic will handle the display.
        self.on_change()
