"""
General Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QComboBox, QLabel, QGridLayout

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
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
        
        # Slider range: 0-4 = 5 discrete presets mapped to update modes
        # 0=SMART, 1=FAST, 2=BALANCED, 3=EFFICIENT, 4=POWER_SAVER
        self.update_rate.setRange(0, 4)
        self.update_rate.setSingleStep(1)
        
        # Connect change signal: update textual label while dragging and propagate change
        self.update_rate.valueChanged.connect(self._on_update_rate_changed)

        update_group_layout.addWidget(QLabel(self.i18n.UPDATE_INTERVAL_LABEL))
        update_group_layout.addWidget(self.update_rate)
        layout.addWidget(update_group)

        # --- Options Group (Toggles) ---
        options_group = QGroupBox(self.i18n.OPTIONS_GROUP_TITLE)
        options_layout = QGridLayout(options_group)
        options_layout.setVerticalSpacing(10)
        options_layout.setHorizontalSpacing(8)

        sww_label = QLabel(self.i18n.START_WITH_WINDOWS_LABEL)
        self.start_with_windows = Win11Toggle(label_text="")
        self.start_with_windows.toggled.connect(self.on_change)

        options_layout.addWidget(sww_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.start_with_windows, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        fm_label = QLabel(self.i18n.FREE_MOVE_LABEL)
        self.free_move = Win11Toggle(label_text="")
        self.free_move.toggled.connect(self.on_change)
        
        options_layout.addWidget(fm_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.free_move, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        kvf_label = QLabel(self.i18n.KEEP_VISIBLE_FULLSCREEN_LABEL)
        self.keep_visible_fullscreen = Win11Toggle(label_text="")
        self.keep_visible_fullscreen.toggled.connect(self.on_change)

        options_layout.addWidget(kvf_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        options_layout.addWidget(self.keep_visible_fullscreen, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

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

        # Update Rate - Map config value to slider position (0-4)
        raw_rate = float(config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE))
        
        # Convert to slider position
        slider_position = self._rate_to_slider_position(raw_rate)
        self.update_rate.setValue(slider_position)
        self.update_rate.setValueText(self._format_update_rate_label(slider_position))
        
        # Other toggles
        self.start_with_windows.setChecked(is_startup_enabled)
        self.free_move.setChecked(config.get("free_move", False))
        self.keep_visible_fullscreen.setChecked(config.get("keep_visible_fullscreen", constants.config.defaults.DEFAULT_KEEP_VISIBLE_FULLSCREEN))

    def get_settings(self) -> Dict[str, Any]:
        # Get slider position and convert to update_rate value
        slider_position = self.update_rate.value()
        update_rate_value = self._slider_position_to_rate(slider_position)

        return {
            "language": self.language_combo.currentData(),
            "update_rate": update_rate_value,
            "free_move": self.free_move.isChecked(),
            "keep_visible_fullscreen": self.keep_visible_fullscreen.isChecked(),
            "start_with_windows": self.start_with_windows.isChecked() 
        }

    def _on_update_rate_changed(self, value: int) -> None:
        """Update the slider textual label in real time and propagate change."""
        try:
            label = self._format_update_rate_label(value)
            self.update_rate.setValueText(label)
        except Exception:
            pass
        # Propagate change notification
        self.on_change()
    
    def _format_update_rate_label(self, slider_position: int) -> str:
        """Format slider position as a human-readable label."""
        # Map slider position (0-4) to update mode label
        position_to_mode = {
            0: self.i18n.SMART_MODE_LABEL,
            1: f"{int(UpdateMode.AGGRESSIVE)}s" if not hasattr(self.i18n, 'UPDATE_MODE_AGGRESSIVE_LABEL') else self.i18n.UPDATE_MODE_AGGRESSIVE_LABEL,
            2: f"{int(UpdateMode.BALANCED)}s" if not hasattr(self.i18n, 'UPDATE_MODE_BALANCED_LABEL') else self.i18n.UPDATE_MODE_BALANCED_LABEL,
            3: f"{int(UpdateMode.EFFICIENT)}s" if not hasattr(self.i18n, 'UPDATE_MODE_EFFICIENT_LABEL') else self.i18n.UPDATE_MODE_EFFICIENT_LABEL,
            4: f"{int(UpdateMode.POWER_SAVER)}s" if not hasattr(self.i18n, 'UPDATE_MODE_POWER_SAVER_LABEL') else self.i18n.UPDATE_MODE_POWER_SAVER_LABEL,
        }
        return position_to_mode.get(slider_position, "Unknown")

    def _rate_to_slider_position(self, update_rate: float) -> int:
        """Convert update_rate value to slider position (0-4)."""
        # Map update rates to slider positions
        if update_rate < 0:  # SMART sentinel
            return 0
        elif abs(update_rate - UpdateMode.AGGRESSIVE) < 0.1:
            return 1
        elif abs(update_rate - UpdateMode.BALANCED) < 0.1:
            return 2
        elif abs(update_rate - UpdateMode.EFFICIENT) < 0.1:
            return 3
        elif abs(update_rate - UpdateMode.POWER_SAVER) < 0.1:
            return 4
        else:
            # Default to BALANCED if unrecognized
            return 2

    def _slider_position_to_rate(self, slider_position: int) -> float:
        """Convert slider position (0-4) to update_rate value."""
        position_to_rate = {
            0: UpdateMode.SMART,        # -1.0
            1: UpdateMode.AGGRESSIVE,   # 1.0
            2: UpdateMode.BALANCED,     # 2.0
            3: UpdateMode.EFFICIENT,    # 5.0
            4: UpdateMode.POWER_SAVER,  # 10.0
        }
        return position_to_rate.get(slider_position, UpdateMode.BALANCED)

