"""
Appearance Settings Page.
Handles fonts and background settings.
"""
from typing import Dict, Any, Callable, List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QPushButton, QLineEdit
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider

class AppearancePage(QWidget):
    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None], font_dialog_callback: Callable[[QFont, str], None], color_dialog_callback: Callable[[str], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self.open_font_dialog = font_dialog_callback
        self.open_color_dialog = color_dialog_callback
        
        # State - Main Font
        self.current_font = QFont()
        self.allowed_font_weights: List[int] = []
        self.font_weight_name_map: Dict[int, str] = {}
        
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Main Font Settings Group ---
        font_group = QGroupBox(self.i18n.FONT_SETTINGS_GROUP_TITLE)
        font_layout = QVBoxLayout(font_group)
        font_layout.setSpacing(8)

        font_family_color_layout = QHBoxLayout()
        font_family_widget = QWidget()
        font_family_v_layout = QVBoxLayout(font_family_widget)
        font_family_v_layout.setContentsMargins(0,0,0,0)
        font_family_v_layout.setSpacing(4)
        font_family_v_layout.addWidget(QLabel(self.i18n.FONT_FAMILY_LABEL))
        
        self.font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_font, "main"))
        
        self.font_family_label = QLabel()
        self.font_family_label.setWordWrap(True)
        
        font_family_button_label_layout = QHBoxLayout()
        font_family_button_label_layout.addWidget(self.font_family_button)
        font_family_button_label_layout.addWidget(self.font_family_label, stretch=1)
        font_family_v_layout.addLayout(font_family_button_label_layout)
        font_family_color_layout.addWidget(font_family_widget, stretch=1)
        font_family_color_layout.addSpacing(20)

        # Font Color (Default)
        font_color_widget = QWidget()
        font_color_v_layout = QVBoxLayout(font_color_widget)
        font_color_v_layout.setContentsMargins(0,0,0,0)
        font_color_v_layout.setSpacing(4)
        font_color_v_layout.addWidget(QLabel(self.i18n.DEFAULT_COLOR_LABEL))
        
        default_color_h_layout = QHBoxLayout()
        self.default_color_button = QPushButton()
        self.default_color_button.setObjectName("default_color")
        self.default_color_button.setToolTip(self.i18n.DEFAULT_COLOR_TOOLTIP)
        self.default_color_button.clicked.connect(lambda: self.open_color_dialog("default_color"))
        default_color_h_layout.addWidget(self.default_color_button)
        
        self.default_color_input = QLineEdit()
        self.default_color_input.setPlaceholderText("#FFFFFF")
        self.default_color_input.setMaxLength(7)
        self.default_color_input.setFixedWidth(80)
        self.default_color_input.textChanged.connect(lambda: self.on_change())
        default_color_h_layout.addWidget(self.default_color_input)
        
        font_color_v_layout.addLayout(default_color_h_layout)
        font_family_color_layout.addWidget(font_color_widget)
        font_layout.addLayout(font_family_color_layout)

        # Font Size
        font_layout.addWidget(QLabel(self.i18n.FONT_SIZE_LABEL))
        self.font_size = Win11Slider(editable=False)
        self.font_size.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
        self.font_size.valueChanged.connect(self.on_change)
        font_layout.addWidget(self.font_size)

        # Font Weight
        font_layout.addWidget(QLabel(self.i18n.FONT_WEIGHT_LABEL))
        self.font_weight = Win11Slider(editable=False, has_ticks=True)
        self.font_weight.valueChanged.connect(self._on_font_weight_changed)
        font_layout.addWidget(self.font_weight)
        
        layout.addWidget(font_group)

        # --- Background Settings ---
        bg_group = QGroupBox(self.i18n.BACKGROUND_SETTINGS_GROUP_TITLE)
        bg_main_layout = QVBoxLayout(bg_group)
        
        bg_color_h = QHBoxLayout()
        bg_color_h.addWidget(QLabel(self.i18n.BACKGROUND_COLOR_LABEL))
        self.background_color_button = QPushButton()
        self.background_color_button.setObjectName("background_color")
        self.background_color_button.clicked.connect(lambda: self.open_color_dialog("background_color"))
        self.background_color_input = QLineEdit()
        self.background_color_input.setMaxLength(7)
        self.background_color_input.setFixedWidth(80)
        self.background_color_input.textChanged.connect(lambda: self.on_change())
        bg_color_h.addWidget(self.background_color_button)
        bg_color_h.addWidget(self.background_color_input)
        bg_color_h.addStretch()
        bg_main_layout.addLayout(bg_color_h)

        bg_main_layout.addWidget(QLabel(self.i18n.BACKGROUND_OPACITY_LABEL))
        self.bg_opacity = Win11Slider(editable=True, suffix="%")
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.valueChanged.connect(self.on_change)
        bg_main_layout.addWidget(self.bg_opacity)

        layout.addWidget(bg_group)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        fam = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        self.font_family_label.setText(fam)
        self.current_font.setFamily(fam)
        self.font_size.setValue(int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)))
        self._update_weight_options(fam)
        self._set_slider_weight(config.get("font_weight", constants.fonts.WEIGHT_DEMIBOLD))

        for key in ["default", "background"]:
            c = config.get(f"{key}_color", "#FFFFFF" if key == "default" else "#000000")
            getattr(self, f"{key}_color_button").setStyleSheet(f"background-color: {c}; border: none;")
            getattr(self, f"{key}_color_input").setText(c)
        
        self.bg_opacity.setValue(int(config.get("background_opacity", 0)))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "font_family": self.font_family_label.text(),
            "font_size": int(self.font_size.value()),
            "font_weight": self.allowed_font_weights[self.font_weight.value()] if 0 <= self.font_weight.value() < len(self.allowed_font_weights) else 400,
            "default_color": self.default_color_input.text(),
            "background_color": self.background_color_input.text(),
            "background_opacity": self.bg_opacity.value()
        }

    def set_font_family(self, font: QFont):
        fam = font.family()
        self.font_family_label.setText(fam)
        self.current_font.setFamily(fam)
        self._update_weight_options(fam)
        self._set_slider_weight(self.current_font.weight())
        self.on_change()

    def set_color_input(self, key: str, hex_code: str):
        if hasattr(self, f"{key}_input"):
            getattr(self, f"{key}_input").setText(hex_code)
            getattr(self, f"{key}_button").setStyleSheet(f"background-color: {hex_code}; border: none;")
            self.on_change()

    def _on_font_weight_changed(self, idx: int):
        if 0 <= idx < len(self.allowed_font_weights):
            w = self.allowed_font_weights[idx]
            self.font_weight.setValueText(self.font_weight_name_map.get(w, str(w)))
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
        self.allowed_font_weights = weights
        self.font_weight_name_map = name_map
        self.font_weight.setRange(0, max(0, len(weights) - 1))

    def _set_slider_weight(self, weight: Any):
        try:
            target = int(weight)
        except:
            target = 400
        if not self.allowed_font_weights: return
        best = min(self.allowed_font_weights, key=lambda w: abs(w - target))
        idx = self.allowed_font_weights.index(best)
        self.font_weight.setValue(idx)
        self.font_weight.setValueText(self.font_weight_name_map.get(best, str(best)))
