"""
Arrow Styling Settings Page.
Handles arrow-specific font and visibility configuration.
"""
from typing import Dict, Any, Callable, List
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QPushButton
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider, Win11Toggle

class ArrowsPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None], font_dialog_callback: Callable[[QFont, str], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self.open_font_dialog = font_dialog_callback
        
        self.current_arrow_font = QFont()
        self.allowed_arrow_font_weights: List[int] = []
        self.arrow_font_weight_name_map: Dict[int, str] = {}
        
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Arrow Styling Group ---
        arrow_group = QGroupBox(self.i18n.ARROW_STYLING_GROUP)
        arrow_layout = QVBoxLayout(arrow_group)
        arrow_layout.setSpacing(12)
        
        self.use_separate_arrow_font = Win11Toggle(label_text=self.i18n.USE_CUSTOM_ARROW_FONT)
        self.use_separate_arrow_font.toggled.connect(self._on_arrow_font_toggle)
        arrow_layout.addWidget(self.use_separate_arrow_font)

        self.arrow_font_container = QWidget()
        arrow_v_layout = QVBoxLayout(self.arrow_font_container)
        arrow_v_layout.setContentsMargins(0,0,0,0)
        arrow_v_layout.setSpacing(8)

        # Family
        arrow_v_layout.addWidget(QLabel(self.i18n.FONT_FAMILY_LABEL))
        arrow_family_h_layout = QHBoxLayout()
        self.arrow_font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.arrow_font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_arrow_font, "arrow"))
        self.arrow_font_family_label = QLabel()
        arrow_family_h_layout.addWidget(self.arrow_font_family_button)
        arrow_family_h_layout.addWidget(self.arrow_font_family_label, stretch=1)
        arrow_v_layout.addLayout(arrow_family_h_layout)

        # Size
        arrow_v_layout.addWidget(QLabel(self.i18n.FONT_SIZE_LABEL))
        self.arrow_font_size = Win11Slider(editable=False)
        self.arrow_font_size.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
        self.arrow_font_size.valueChanged.connect(self.on_change)
        arrow_v_layout.addWidget(self.arrow_font_size)

        # Weight
        arrow_v_layout.addWidget(QLabel(self.i18n.FONT_WEIGHT_LABEL))
        self.arrow_font_weight = Win11Slider(editable=False, has_ticks=True)
        self.arrow_font_weight.valueChanged.connect(self._on_arrow_font_weight_changed)
        arrow_v_layout.addWidget(self.arrow_font_weight)

        arrow_layout.addWidget(self.arrow_font_container)
        layout.addWidget(arrow_group)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        self.use_separate_arrow_font.setChecked(bool(config.get("use_separate_arrow_font", False)))
        
        # Fallback to main font if not set
        main_fam = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        a_fam = config.get("arrow_font_family", main_fam)
        
        self.arrow_font_family_label.setText(a_fam)
        self.current_arrow_font.setFamily(a_fam)
        
        main_size = int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE))
        self.arrow_font_size.setValue(int(config.get("arrow_font_size", main_size)))
        
        self._update_weight_options(a_fam)
        self._set_slider_weight(config.get("arrow_font_weight", constants.fonts.WEIGHT_DEMIBOLD))
        
        self.arrow_font_container.setVisible(self.use_separate_arrow_font.isChecked())

    def get_settings(self) -> Dict[str, Any]:
        return {
            "use_separate_arrow_font": self.use_separate_arrow_font.isChecked(),
            "arrow_font_family": self.arrow_font_family_label.text(),
            "arrow_font_size": int(self.arrow_font_size.value()),
            "arrow_font_weight": self.allowed_arrow_font_weights[self.arrow_font_weight.value()] if 0 <= self.arrow_font_weight.value() < len(self.allowed_arrow_font_weights) else 400
        }

    def set_arrow_font_family(self, font: QFont):
        fam = font.family()
        self.arrow_font_family_label.setText(fam)
        self.current_arrow_font.setFamily(fam)
        self._update_weight_options(fam)
        self._set_slider_weight(self.current_arrow_font.weight())
        self.on_change()

    def _on_arrow_font_weight_changed(self, idx: int):
        if 0 <= idx < len(self.allowed_arrow_font_weights):
            w = self.allowed_arrow_font_weights[idx]
            self.arrow_font_weight.setValueText(self.arrow_font_weight_name_map.get(w, str(w)))
        self.on_change()

    def _on_arrow_font_toggle(self, checked: bool):
        self.arrow_font_container.setVisible(checked)
        self.on_change()

    def _update_weight_options(self, family: str):
        styles = QFontDatabase.styles(family)
        weights = []
        name_map = {}
        for s in styles:
            w = QFontDatabase.weight(family, s)
            if w <= 0: continue
            
            if w not in weights:
                weights.append(w)
            
            key_name = constants.fonts.WEIGHT_MAP.get(w)
            if key_name:
                display_name = getattr(self.i18n, key_name, key_name)
            else:
                display_name = s
                
            current_name = name_map.get(w, "")
            is_new_better = not current_name or ("italic" in current_name.lower() and "italic" not in display_name.lower())
            
            if is_new_better:
                name_map[w] = display_name
                
        weights.sort()
        self.allowed_arrow_font_weights = weights
        self.arrow_font_weight_name_map = name_map
        self.arrow_font_weight.setRange(0, max(0, len(weights) - 1))

    def _set_slider_weight(self, weight: Any):
        try:
            target = int(weight)
        except:
            target = 400
        if not self.allowed_arrow_font_weights: return
        best = min(self.allowed_arrow_font_weights, key=lambda w: abs(w - target))
        idx = self.allowed_arrow_font_weights.index(best)
        self.arrow_font_weight.setValue(idx)
        self.arrow_font_weight.setValueText(self.arrow_font_weight_name_map.get(best, str(best)))
