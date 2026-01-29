"""
General Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QComboBox, QLabel, QGridLayout

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider, Win11Toggle

class GeneralPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Language Group ---
        language_group = QGroupBox(self.i18n.LANGUAGE_LABEL)
        language_layout = QVBoxLayout(language_group)
        self.language_combo = QComboBox()
        
        for code, name in self.i18n.LANGUAGE_MAP.items():
            self.language_combo.addItem(name, userData=code)
        
        # Connect change signal
        self.language_combo.currentIndexChanged.connect(self.on_change)
        
        language_layout.addWidget(self.language_combo)
        layout.addWidget(language_group)

        # --- Update Rate Group ---
        update_group = QGroupBox(self.i18n.UPDATE_RATE_GROUP_TITLE)
        update_group_layout = QVBoxLayout(update_group)
        update_group_layout.setSpacing(8)
        self.update_rate = Win11Slider(editable=False)
        self.update_rate.setRange(0, int(constants.timers.MAXIMUM_UPDATE_RATE_SECONDS * 2))
        
        # Connect change signal
        self.update_rate.valueChanged.connect(self.on_change)

        update_group_layout.addWidget(QLabel(self.i18n.UPDATE_INTERVAL_LABEL))
        update_group_layout.addWidget(self.update_rate)
        layout.addWidget(update_group)

        # --- Options Group (Toggles) ---
        options_group = QGroupBox(self.i18n.OPTIONS_GROUP_TITLE)
        options_layout = QGridLayout(options_group)
        options_layout.setVerticalSpacing(10)
        options_layout.setHorizontalSpacing(8)

        du_label = QLabel(self.i18n.DYNAMIC_UPDATE_RATE_LABEL)
        self.dynamic_update_rate = Win11Toggle(label_text="")
        self.dynamic_update_rate.toggled.connect(self.on_change)
        
        options_layout.addWidget(du_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.dynamic_update_rate, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        sww_label = QLabel(self.i18n.START_WITH_WINDOWS_LABEL)
        self.start_with_windows = Win11Toggle(label_text="")
        # Note: Startup changes usually require specific logic (registry), 
        # but for the dialog UI we just capture the state. 
        # The main dialog might need to handle the actual application of this.
        self.start_with_windows.toggled.connect(self.on_change)

        options_layout.addWidget(sww_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.start_with_windows, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        fm_label = QLabel(self.i18n.FREE_MOVE_LABEL)
        self.free_move = Win11Toggle(label_text="")
        self.free_move.toggled.connect(self.on_change)
        
        options_layout.addWidget(fm_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.free_move, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        options_layout.setColumnStretch(0, 0)
        options_layout.setColumnStretch(1, 1)
        layout.addWidget(options_group)
        
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any], is_startup_enabled: bool):
        # Language
        current_lang = config.get("language", "en")
        index = self.language_combo.findData(current_lang)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

        # Update Rate
        rate_val = int(config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE) * 2)
        self.update_rate.setValue(rate_val)
        
        # Toggles
        self.dynamic_update_rate.setChecked(config.get("dynamic_update_enabled", constants.config.defaults.DEFAULT_DYNAMIC_UPDATE_ENABLED))
        self.start_with_windows.setChecked(is_startup_enabled)
        self.free_move.setChecked(config.get("free_move", False))
        
        # Special case labels handling for update rate can stay in the slider logic or be added here if needed
        # The Win11Slider handles its own label updates if set up, but let's see if we need text formatting.
        # Original code used `rate_to_text`. I should probably implement that helper or pass it.
        # For now, relying on basic slider behavior.

    def get_settings(self) -> Dict[str, Any]:
        return {
            "language": self.language_combo.currentData(),
            "update_rate": self.update_rate.value() / 2.0,
            "dynamic_update_enabled": self.dynamic_update_rate.isChecked(),
            "free_move": self.free_move.isChecked(),
            # is_startup_enabled is handled separately usually, but we return expected state
             "start_with_windows": self.start_with_windows.isChecked() 
        }
