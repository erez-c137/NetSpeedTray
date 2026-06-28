"""
Appearance Settings Page.
Handles fonts and background settings.
"""
from typing import Dict, Any, Callable, List
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit

from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.components import Win11Slider, Win11Toggle, ArrowStylePicker, SettingCard
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

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
        
        # State - Arrow Font (Active family only, weight is fixed/ignored for symbols)
        self.current_arrow_font = QFont()
        
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: Win11 Settings cards under section captions (was QGroupBox frames). Compound controls
        # (font picker button + label, colour swatch + hex) are packed into a small composite docked on
        # the right of each card via _row(). All control objects + the load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Font ---
        layout.addWidget(section_header(self.i18n.FONT_SETTINGS_GROUP_TITLE))

        # Font family — the button opens the picker; the label shows (and stores) the current family.
        self.font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_font, "main"))
        self.font_family_label = QLabel()
        self.font_family_label.setMaximumWidth(180)
        layout.addWidget(SettingCard(self.i18n.FONT_FAMILY_LABEL,
                                     control=self._row(self.font_family_label, self.font_family_button)))

        # Default text colour (swatch + hex).
        self.default_color_button = QPushButton()
        self.default_color_button.setObjectName("default_color")
        self.default_color_button.setFixedSize(30, 24)
        self.default_color_button.setToolTip(self.i18n.DEFAULT_COLOR_TOOLTIP)
        self.default_color_button.clicked.connect(lambda: self.open_color_dialog("default_color"))
        self.default_color_input = QLineEdit()
        self.default_color_input.setPlaceholderText("#FFFFFF")
        self.default_color_input.setMaxLength(7)
        self.default_color_input.setFixedWidth(90)
        self.default_color_input.textChanged.connect(lambda: self.on_change())
        layout.addWidget(SettingCard(self.i18n.DEFAULT_COLOR_LABEL,
                                     control=self._row(self.default_color_button, self.default_color_input)))

        # Font size.
        self.font_size = Win11Slider(editable=False)
        self.font_size.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
        self.font_size.setMinimumWidth(260)
        self.font_size.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.FONT_SIZE_LABEL, control=self.font_size))

        # Font weight.
        self.font_weight = Win11Slider(editable=False, has_ticks=True)
        self.font_weight.setMinimumWidth(260)
        self.font_weight.valueChanged.connect(self._on_font_weight_changed)
        layout.addWidget(SettingCard(self.i18n.FONT_WEIGHT_LABEL, control=self.font_weight))

        # --- Arrow styling (merged from the old Arrows page) ---
        layout.addWidget(section_header(self.i18n.ARROW_STYLING_GROUP))
        # Arrow STYLE picker (#129) — curated glyph presets + Custom (full-width control). The default
        # "Classic" tracks the native locale arrow; the others set arrow_up/down_symbol overrides.
        self.arrow_style_picker = ArrowStylePicker(self.i18n)
        self.arrow_style_picker.changed.connect(self.on_change)
        layout.addWidget(self.arrow_style_picker)

        self.use_separate_arrow_font = Win11Toggle(label_text="")
        self.use_separate_arrow_font.toggled.connect(self._on_arrow_font_toggle)
        layout.addWidget(SettingCard(self.i18n.USE_CUSTOM_ARROW_FONT, control=self.use_separate_arrow_font))

        # The custom-arrow-font controls live in a container shown only when the toggle is on.
        self.arrow_font_container = QWidget()
        arrow_v_layout = QVBoxLayout(self.arrow_font_container)
        arrow_v_layout.setContentsMargins(0, 0, 0, 0)
        arrow_v_layout.setSpacing(6)
        self.arrow_font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.arrow_font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_arrow_font, "arrow"))
        self.arrow_font_family_label = QLabel()
        self.arrow_font_family_label.setMaximumWidth(180)
        arrow_v_layout.addWidget(SettingCard(
            self.i18n.FONT_FAMILY_LABEL,
            control=self._row(self.arrow_font_family_label, self.arrow_font_family_button)))
        self.arrow_font_size = Win11Slider(editable=False)
        self.arrow_font_size.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
        self.arrow_font_size.setMinimumWidth(260)
        self.arrow_font_size.valueChanged.connect(self.on_change)
        arrow_v_layout.addWidget(SettingCard(self.i18n.FONT_SIZE_LABEL, control=self.arrow_font_size))
        # Arrow Weight - REMOVED per user request (font support issues)
        layout.addWidget(self.arrow_font_container)

        # --- Background ---
        layout.addWidget(section_header(self.i18n.BACKGROUND_SETTINGS_GROUP_TITLE))
        self.background_color_button = QPushButton()
        self.background_color_button.setObjectName("background_color")
        self.background_color_button.setFixedSize(30, 24)
        self.background_color_button.clicked.connect(lambda: self.open_color_dialog("background_color"))
        self.background_color_input = QLineEdit()
        self.background_color_input.setMaxLength(7)
        self.background_color_input.setFixedWidth(90)
        self.background_color_input.textChanged.connect(lambda: self.on_change())
        layout.addWidget(SettingCard(
            self.i18n.BACKGROUND_COLOR_LABEL,
            control=self._row(self.background_color_button, self.background_color_input)))

        self.bg_opacity = Win11Slider(editable=True, suffix="%")
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.setMinimumWidth(260)
        self.bg_opacity.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.BACKGROUND_OPACITY_LABEL, control=self.bg_opacity))

        # --- Mini graph (absorbed from the old Graph page) ---
        layout.addWidget(section_header(self.i18n.MINI_GRAPH_SECTION_TITLE))
        self.enable_graph = Win11Toggle(label_text="")
        self.enable_graph.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ENABLE_GRAPH_LABEL, control=self.enable_graph))

        note = QLabel(self.i18n.GRAPH_NOTE_TEXT)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {style_utils.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(note)

        self.history_duration = Win11Slider(editable=False)
        hist_min, hist_max = constants.ui.history.HISTORY_MINUTES_RANGE
        self.history_duration.setRange(hist_min, hist_max)
        self.history_duration.setMinimumWidth(260)
        self.history_duration.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HISTORY_DURATION_LABEL, control=self.history_duration))

        self.graph_opacity = Win11Slider(editable=False)
        self.graph_opacity.setRange(constants.ui.sliders.OPACITY_MIN, constants.ui.sliders.OPACITY_MAX)
        self.graph_opacity.setMinimumWidth(260)
        self.graph_opacity.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.GRAPH_OPACITY_LABEL, control=self.graph_opacity))

        layout.addStretch()

    def _row(self, *widgets) -> QWidget:
        """Pack widgets into a compact horizontal composite for a SettingCard's right-docked control."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in widgets:
            h.addWidget(x)
        return w

    def load_settings(self, config: Dict[str, Any]):
        # Main Font
        fam = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        self.font_family_label.setText(fam)
        self.current_font.setFamily(fam)
        self.font_size.setValue(int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)))
        self._update_weight_options(fam)
        self._set_slider_weight(config.get("font_weight", constants.fonts.WEIGHT_DEMIBOLD))

        # Colors
        for key in ["default", "background"]:
            c = config.get(f"{key}_color", "#FFFFFF" if key == "default" else "#000000")
            getattr(self, f"{key}_color_button").setStyleSheet(f"background-color: {c}; border: none;")
            getattr(self, f"{key}_color_input").setText(c)
        
        self.bg_opacity.setValue(int(config.get("background_opacity", 0)))
        
        # Arrow style (glyph presets / custom)
        self.arrow_style_picker.set_values(
            config.get("arrow_up_symbol", ""), config.get("arrow_down_symbol", ""))

        # Arrow Fonts
        self.use_separate_arrow_font.setChecked(bool(config.get("use_separate_arrow_font", False)))
        a_fam = config.get("arrow_font_family", fam) # Fallback to main
        self.arrow_font_family_label.setText(a_fam)
        self.current_arrow_font.setFamily(a_fam)
        
        main_size = int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE))
        self.arrow_font_size.setValue(int(config.get("arrow_font_size", main_size)))
        
        # Arrow Weight ignored in UI
        
        self.arrow_font_container.setVisible(self.use_separate_arrow_font.isChecked())

        # Mini Graph
        self.enable_graph.setChecked(config.get("graph_enabled", True))
        self.history_duration.setValue(config.get("history_minutes", constants.config.defaults.DEFAULT_HISTORY_MINUTES))
        self.graph_opacity.setValue(config.get("graph_opacity", constants.config.defaults.DEFAULT_GRAPH_OPACITY))

    def get_settings(self) -> Dict[str, Any]:
        settings = {
            "font_family": self.font_family_label.text(),
            "font_size": int(self.font_size.value()),
            "font_weight": self.allowed_font_weights[self.font_weight.value()] if 0 <= self.font_weight.value() < len(self.allowed_font_weights) else 400,
            "default_color": self.default_color_input.text(),
            "background_color": self.background_color_input.text(),
            "background_opacity": self.bg_opacity.value(),
            # Arrow Config
            "use_separate_arrow_font": self.use_separate_arrow_font.isChecked(),
            "arrow_font_family": self.arrow_font_family_label.text(),
            "arrow_font_size": int(self.arrow_font_size.value()),
            "arrow_font_weight": constants.fonts.WEIGHT_DEMIBOLD, # Fixed default due to glyph fallback issues
            # Arrow style glyphs (#129) — empty == native locale default (Classic)
            **self.arrow_style_picker.get_values(),
            # Mini Graph
            "graph_enabled": self.enable_graph.isChecked(),
            "history_minutes": self.history_duration.value(),
            "graph_opacity": self.graph_opacity.value()
        }
        return settings

    def set_font_family(self, font: QFont):
        fam = font.family()
        self.font_family_label.setText(fam)
        self.current_font.setFamily(fam)
        self._update_weight_options(fam)
        self._set_slider_weight(self.current_font.weight())
        self.on_change()

    def set_arrow_font_family(self, font: QFont):
        fam = font.family()
        self.arrow_font_family_label.setText(fam)
        self.current_arrow_font.setFamily(fam)
        self.on_change()

    def set_color_input(self, key: str, hex_code: str):
        if hasattr(self, f"{key}_color_input"):
            getattr(self, f"{key}_color_input").setText(hex_code)
            getattr(self, f"{key}_color_button").setStyleSheet(f"background-color: {hex_code}; border: none;")
            self.on_change()

    def _on_font_weight_changed(self, idx: int):
        if 0 <= idx < len(self.allowed_font_weights):
            w = self.allowed_font_weights[idx]
            self.font_weight.setValueText(self.font_weight_name_map.get(w, str(w)))
        self.on_change()

    def _on_arrow_font_toggle(self, checked: bool):
        self.arrow_font_container.setVisible(checked)
        self.layout_changed.emit()
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
        
        target_list = self.allowed_font_weights
        target_slider = self.font_weight
        target_map = self.font_weight_name_map

        if not target_list: return
        best = min(target_list, key=lambda w: abs(w - target))
        idx = target_list.index(best)
        target_slider.setValue(idx)
        target_slider.setValueText(target_map.get(best, str(best)))
