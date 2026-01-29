"""
Appearance Settings Page.
Handles fonts and color coding configuration.
"""
from typing import Dict, Any, Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QPushButton, QLineEdit, QGridLayout, QDoubleSpinBox
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider, Win11Toggle
from netspeedtray.constants import styles as style_constants

class AppearancePage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None], font_dialog_callback: Callable[[QFont], None], color_dialog_callback: Callable[[str], None]):
        """
        Args:
            i18n: Globalization strings.
            on_change: Callback for when any setting changes.
            font_dialog_callback: Callback to open a font dialog.
            color_dialog_callback: Callback to open a color dialog.
        """
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self.open_font_dialog = font_dialog_callback
        self.open_color_dialog = color_dialog_callback
        
        # State
        self.current_font = QFont()
        self.allowed_font_weights: List[int] = []
        self.font_weight_name_map: Dict[int, str] = {}
        
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Font Settings Group ---
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
        # Assuming style_utils was applied globally or passed? simpler to just set objects and let parent stylesheet handle it, 
        # but here we might miss specific button styling if not careful.
        # For now, we trust the parent Dialog's stylesheet cascades down to these widgets.
        self.font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_font))
        
        self.font_family_label = QLabel()
        self.font_family_label.setWordWrap(True)
        
        font_family_button_label_layout = QHBoxLayout()
        font_family_button_label_layout.addWidget(self.font_family_button)
        font_family_button_label_layout.addWidget(self.font_family_label, stretch=1)
        font_family_v_layout.addLayout(font_family_button_label_layout)
        font_family_color_layout.addWidget(font_family_widget, stretch=1)
        font_family_color_layout.addSpacing(20)

        # Font Color
        font_color_widget = QWidget()
        font_color_v_layout = QVBoxLayout(font_color_widget)
        font_color_v_layout.setContentsMargins(0,0,0,0)
        font_color_v_layout.setSpacing(4)
        font_color_v_layout.addWidget(QLabel(self.i18n.DEFAULT_COLOR_LABEL))
        
        default_color_h_layout = QHBoxLayout()
        default_color_h_layout.setSpacing(8)
        self.default_color_button = QPushButton()
        self.default_color_button.setObjectName("default_color") # For specific styling
        self.default_color_button.setToolTip(self.i18n.DEFAULT_COLOR_TOOLTIP)
        self.default_color_button.clicked.connect(lambda: self.open_color_dialog("default_color"))

        default_color_h_layout.addWidget(self.default_color_button)
        
        self.default_color_input = QLineEdit()
        self.default_color_input.setPlaceholderText("#FFFFFF")
        self.default_color_input.setMaxLength(7)
        self.default_color_input.setFixedWidth(80)
        self.default_color_input.textChanged.connect(lambda: self.on_change())
        
        default_color_h_layout.addWidget(self.default_color_input)
        default_color_h_layout.addStretch()
        
        font_color_v_layout.addLayout(default_color_h_layout)
        font_color_v_layout.addStretch()
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
        self.font_weight = Win11Slider(editable=False)
        self.font_weight.valueChanged.connect(self.on_change)
        font_layout.addWidget(self.font_weight)
        
        layout.addWidget(font_group)

        # --- Color Coding Group ---
        color_coding_group = QGroupBox(self.i18n.COLOR_CODING_GROUP)
        color_coding_main_layout = QGridLayout(color_coding_group)
        
        enable_colors_label = QLabel(self.i18n.ENABLE_COLOR_CODING_LABEL)
        self.enable_colors = Win11Toggle(label_text="")
        self.enable_colors.toggled.connect(self.on_change)
        
        color_coding_main_layout.addWidget(enable_colors_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        color_coding_main_layout.addWidget(self.enable_colors, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.color_container = QWidget()
        color_container_layout = QVBoxLayout(self.color_container)
        color_container_layout.setContentsMargins(0, 10, 0, 0)
        color_container_layout.setSpacing(8)

        # High Speed
        color_container_layout.addWidget(QLabel(self.i18n.HIGH_SPEED_THRESHOLD_LABEL))
        self.high_speed_threshold = QDoubleSpinBox()
        self.high_speed_threshold.setRange(
            float(constants.ui.sliders.SPEED_THRESHOLD_MIN_HIGH),
            10000.0 # Allow up to 10 Gbps
        )
        self.high_speed_threshold.setSingleStep(0.5)
        self.high_speed_threshold.setSuffix(" Mbps")
        self.high_speed_threshold.valueChanged.connect(self.on_change)
        color_container_layout.addWidget(self.high_speed_threshold)

        # Low Speed
        color_container_layout.addWidget(QLabel(self.i18n.LOW_SPEED_THRESHOLD_LABEL))
        self.low_speed_threshold = QDoubleSpinBox()
        self.low_speed_threshold.setRange(
            float(constants.ui.sliders.SPEED_THRESHOLD_MIN_LOW),
            10000.0
        )
        self.low_speed_threshold.setSingleStep(0.5)
        self.low_speed_threshold.setSuffix(" Mbps")
        self.low_speed_threshold.valueChanged.connect(self.on_change)
        color_container_layout.addWidget(self.low_speed_threshold)

        # High Speed Color
        color_container_layout.addWidget(QLabel(self.i18n.HIGH_SPEED_COLOR_LABEL))
        high_color_h_layout = QHBoxLayout()
        high_color_h_layout.setSpacing(8)
        self.high_speed_color_button = QPushButton()
        self.high_speed_color_button.setObjectName("high_speed_color")
        self.high_speed_color_button.setToolTip(self.i18n.HIGH_SPEED_COLOR_TOOLTIP)
        self.high_speed_color_button.clicked.connect(lambda: self.open_color_dialog("high_speed_color"))
        high_color_h_layout.addWidget(self.high_speed_color_button)
        
        self.high_speed_color_input = QLineEdit()
        self.high_speed_color_input.setPlaceholderText("#00FF00")
        self.high_speed_color_input.setMaxLength(7)
        self.high_speed_color_input.setFixedWidth(80)
        self.high_speed_color_input.textChanged.connect(lambda: self.on_change())
        
        high_color_h_layout.addWidget(self.high_speed_color_input)
        high_color_h_layout.addStretch()
        color_container_layout.addLayout(high_color_h_layout)

        # Low Speed Color
        color_container_layout.addWidget(QLabel(self.i18n.LOW_SPEED_COLOR_LABEL))
        low_color_h_layout = QHBoxLayout()
        low_color_h_layout.setSpacing(8)
        self.low_speed_color_button = QPushButton()
        self.low_speed_color_button.setObjectName("low_speed_color")
        self.low_speed_color_button.setToolTip(self.i18n.LOW_SPEED_COLOR_TOOLTIP)
        self.low_speed_color_button.clicked.connect(lambda: self.open_color_dialog("low_speed_color"))
        low_color_h_layout.addWidget(self.low_speed_color_button)
        
        self.low_speed_color_input = QLineEdit()
        self.low_speed_color_input.setPlaceholderText("#FFA500")
        self.low_speed_color_input.setMaxLength(7)
        self.low_speed_color_input.setFixedWidth(80)
        self.low_speed_color_input.textChanged.connect(lambda: self.on_change())

        low_color_h_layout.addWidget(self.low_speed_color_input)
        low_color_h_layout.addStretch()
        color_container_layout.addLayout(low_color_h_layout)

        color_coding_main_layout.addWidget(self.color_container, 1, 0, 1, 2) 
        layout.addWidget(color_coding_group)

        # --- Background Settings Group ---
        bg_group = QGroupBox(self.i18n.BACKGROUND_SETTINGS_GROUP_TITLE)
        bg_layout = QGridLayout(bg_group)
        
        # Background Color
        bg_layout.addWidget(QLabel(self.i18n.BACKGROUND_COLOR_LABEL), 0, 0)
        
        bg_color_h_layout = QHBoxLayout()
        bg_color_h_layout.setSpacing(8)
        self.bg_color_button = QPushButton()
        self.bg_color_button.setObjectName("background_color")
        self.bg_color_button.setToolTip(self.i18n.BACKGROUND_COLOR_TOOLTIP)
        self.bg_color_button.clicked.connect(lambda: self.open_color_dialog("background_color"))
        bg_color_h_layout.addWidget(self.bg_color_button)
        
        self.bg_color_input = QLineEdit()
        self.bg_color_input.setPlaceholderText("#000000")
        self.bg_color_input.setMaxLength(7)
        self.bg_color_input.setFixedWidth(80)
        self.bg_color_input.textChanged.connect(lambda: self.on_change())
        bg_color_h_layout.addWidget(self.bg_color_input)
        bg_color_h_layout.addStretch()
        bg_layout.addLayout(bg_color_h_layout, 0, 1)

        # Background Opacity
        bg_layout.addWidget(QLabel(self.i18n.BACKGROUND_OPACITY_LABEL), 1, 0)
        self.bg_opacity = Win11Slider(editable=True, suffix="%") # Editable slider for precision
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.valueChanged.connect(self.on_change)
        bg_layout.addWidget(self.bg_opacity, 1, 1)

        layout.addWidget(bg_group)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        # Font Family
        fam = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        self.current_font.setFamily(fam)
        self.font_family_label.setText(fam)
        self._update_font_weight_options(fam)

        # Font Size
        size = config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
        self.font_size.setValue(size)
        self.current_font.setPointSize(size)

        # Font Weight
        weight_str = config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
        weight_val = constants.fonts.WEIGHT_NORMAL
        if isinstance(weight_str, str):
            weight_val = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}.get(weight_str.lower(), QFont.Weight.Normal)
        elif isinstance(weight_str, int):
            weight_val = weight_str
        
        self.current_font.setWeight(weight_val)
        
        # Snap weight to allowed
        snapped_weight = self._snap_value_to_allowed(weight_val, self.allowed_font_weights)
        self.font_weight.setValue(snapped_weight)
        
        # Colors
        def set_color_ui(key, btn, inp):
            c = config.get(key, "#FFFFFF")
            btn.setStyleSheet(f"background-color: {c}; border: none;")
            inp.setText(c)
        
        set_color_ui("default_color", self.default_color_button, self.default_color_input)
        set_color_ui("high_speed_color", self.high_speed_color_button, self.high_speed_color_input)
        set_color_ui("low_speed_color", self.low_speed_color_button, self.low_speed_color_input)

        # Color Coding
        enabled = config.get("color_coding", constants.config.defaults.DEFAULT_COLOR_CODING)
        self.enable_colors.setChecked(enabled)
        self.color_container.setVisible(enabled)

        self.high_speed_threshold.setValue(config.get("high_speed_threshold", constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD))
        self.low_speed_threshold.setValue(config.get("low_speed_threshold", constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD))
        
        # Background
        set_color_ui("background_color", self.bg_color_button, self.bg_color_input)
        
        # Opacity defaults to 0 if not present which matches DEFAULT_BACKGROUND_OPACITY
        bg_opacity = float(config.get("background_opacity", constants.config.defaults.DEFAULT_BACKGROUND_OPACITY))
        # Handle cases where it might be stored as decimal 0.5 vs 50? No, standard is 0-100 int usually
        # But wait, RenderConfig converts it to float / 100.0. Configuration storage is INT 0-100.
        self.bg_opacity.setValue(int(bg_opacity))


    def get_settings(self) -> Dict[str, Any]:
        # Convert weight back to string/int logic if needed, or just store int
        # Implementation assumes storing int weight is fine now or mapped later
        weight_val = self.font_weight.value()
        
        return {
            "font_family": self.font_family_label.text(),
            "font_size": self.font_size.value(),
            "font_weight": weight_val,
            "default_color": self.default_color_input.text(),
            "color_coding": self.enable_colors.isChecked(),
            "high_speed_threshold": self.high_speed_threshold.value(),
            "low_speed_threshold": self.low_speed_threshold.value(),
            "high_speed_color": self.high_speed_color_input.text(),
            "low_speed_color": self.low_speed_color_input.text(),
            "background_color": self.bg_color_input.text(),
            "background_opacity": self.bg_opacity.value()
        }

    # Font Logic Helpers
    def _update_font_weight_options(self, font_family: str):
        style_strings = QFontDatabase.styles(font_family)
        raw_weights_to_styles: Dict[int, List[str]] = {}
        if style_strings:
            for style_name in style_strings:
                weight_val = QFontDatabase.weight(font_family, style_name)
                if weight_val > 0:
                    if weight_val not in raw_weights_to_styles:
                        raw_weights_to_styles[weight_val] = []
                    raw_weights_to_styles[weight_val].append(style_name)
        
        self.font_weight_name_map = {}
        if raw_weights_to_styles:
            for weight_val, style_names_for_weight in sorted(raw_weights_to_styles.items()):
                key_name = constants.fonts.WEIGHT_MAP.get(weight_val)
                if key_name:
                    display_name = getattr(self.i18n, key_name, key_name)
                else:
                    display_name = style_names_for_weight[0] # Simplification for brevity
                self.font_weight_name_map[weight_val] = display_name
        
        if not self.font_weight_name_map:
             for weight_val, key_name in constants.fonts.WEIGHT_MAP.items():
                self.font_weight_name_map[weight_val] = getattr(self.i18n, key_name, f"Weight {weight_val}")

        self.allowed_font_weights = sorted(list(self.font_weight_name_map.keys()))
        
        # Update slider range
        if self.allowed_font_weights:
            min_w, max_w = self.allowed_font_weights[0], self.allowed_font_weights[-1]
            self.font_weight.setRange(min_w, max_w if min_w != max_w else min_w + 1)
            self.font_weight.setEnabled(len(self.allowed_font_weights) > 1)
        else:
            self.font_weight.setEnabled(False)

    def _snap_value_to_allowed(self, value: int, allowed_values: List[int]) -> int:
        if not allowed_values: return value
        return min(allowed_values, key=lambda w: abs(w - value))

    def set_font_family(self, font: QFont):
        family = font.family()
        self.font_family_label.setText(family)
        self.current_font.setFamily(family)
        self._update_font_weight_options(family)
        # Reset weight to normal/default for new family or keep if possible
        self.on_change()

    def set_color_input(self, key: str, color_hex: str):
        if key == "default_color":
            self.default_color_input.setText(color_hex)
            self.default_color_button.setStyleSheet(f"background-color: {color_hex}; border: none;")
        elif key == "high_speed_color":
            self.high_speed_color_input.setText(color_hex)
            self.high_speed_color_button.setStyleSheet(f"background-color: {color_hex}; border: none;")
        elif key == "low_speed_color":
            self.low_speed_color_input.setText(color_hex)
            self.low_speed_color_button.setStyleSheet(f"background-color: {color_hex}; border: none;")
        elif key == "background_color":
            self.bg_color_input.setText(color_hex)
            self.bg_color_button.setStyleSheet(f"background-color: {color_hex}; border: none;")
        self.on_change()
