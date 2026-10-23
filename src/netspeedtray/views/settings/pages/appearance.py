"""
Appearance Settings Page.
Handles fonts, data format / layout, color coding, background and arrow styling (in that order).
The Mini Graph section moved to the Widget page in the 2.2 IA pass (pages/mini_graph_section.py).
"""
from typing import Dict, Any, Callable, List
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.components import Win11Slider, Win11Toggle, ArrowStylePicker, SettingCard
from netspeedtray.views.settings.pages._fluent import (
    section_header, page_layout, LastValidHex, ThemedColorPair, swatch_style)
from netspeedtray.views.settings.pages.units import UnitsPage
from netspeedtray.views.settings.pages.colors import ColorsPage


def _qt_family(family: str) -> str:
    """The family Qt knows a configured font name by. The default is the family "Segoe UI Variable"
    (7a) itself; a pre-release "Segoe UI Variable Text" (an optical size, not a family) resolves to it -
    QFontDatabase lists no styles for the optical size (the weight slider went dead and every Apply saved
    weight 400), and QFontDialog opened on Tahoma. Without the variable font (Windows 10) it is the
    Segoe UI the widget really draws. Every other name is returned unchanged."""
    try:
        from netspeedtray.utils.widget_renderer import make_widget_font
        return make_widget_font(family, 9, int(constants.fonts.WEIGHT_NORMAL)).family() or family
    except Exception:
        return family


class AppearancePage(QWidget):
    layout_changed = pyqtSignal()

    # The colour keys this page's own fields hold (the embedded Color coding section has its own).
    THEMED_STEMS = ("default_color", "arrow_up_color", "arrow_down_color")
    COLOR_KEYS = tuple(f"{stem}_{suffix}" for stem in THEMED_STEMS
                       for suffix in constants.config.defaults.THEME_SUFFIXES) + ("background_color",)

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
        
        # Last complete #RRGGBB per colour field (S8): a half-typed hex is never applied or saved.
        # The text and arrow colours are themed pairs (<stem>_dark / <stem>_light, 2.2 rc.2).
        d = constants.config.defaults
        self._hex = LastValidHex({k: d.DEFAULT_CONFIG[k] for k in self.COLOR_KEYS})
        # Every colour field on this page (the Color coding section keeps its own): key -> (swatch, hex).
        self._swatches: Dict[str, tuple] = {}

        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: Win11 Settings cards under section captions (was QGroupBox frames). Compound controls
        # (font picker button + label, color swatch + hex) are packed into a small composite docked on
        # the right of each card via _row(). All control objects + the load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Font ---
        layout.addWidget(section_header(self.i18n.FONT_SETTINGS_GROUP_TITLE))

        # Font family - the button opens the picker; the label shows (and stores) the current family.
        self.font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.font_family_button.clicked.connect(lambda: self.open_font_dialog(self.current_font, "main"))
        self.font_family_label = QLabel()
        self.font_family_label.setMaximumWidth(180)
        layout.addWidget(SettingCard(self.i18n.FONT_FAMILY_LABEL,
                                     control=self._row(self.font_family_label, self.font_family_button)))

        # Default text color: a dark-taskbar and a light-taskbar swatch (the colour scheme, #332). The
        # note under it is the page's one explanation of the moon / sun pairs on every colour row.
        layout.addWidget(SettingCard(self.i18n.DEFAULT_COLOR_LABEL,
                                     control=self._themed_pair("default_color", self.i18n.DEFAULT_COLOR_LABEL,
                                                               self.i18n.DEFAULT_COLOR_TOOLTIP)))
        self.themed_colors_note = QLabel(self.i18n.THEMED_COLORS_NOTE)
        self.themed_colors_note.setWordWrap(True)
        self.themed_colors_note.setStyleSheet(
            f"color: {style_utils.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(self.themed_colors_note)

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

        # --- Data format + Interface layout (absorbed from the old Display/Units page) + Color coding
        # (absorbed from the old Color Coding page), 2.0 IA. Reused as-is (their own tested control groups
        # + load/get); the dialog keeps references to them for the Force-MB rule + the high/low
        # color-picker routing. They sit right under Font (2.2 IA pass): units / decimals are the most
        # asked-for formatting settings and were three screens down.
        self.units_section = UnitsPage(self.i18n, self.on_change)
        layout.addWidget(self.units_section)
        self.colors_section = ColorsPage(self.i18n, self.on_change, self.open_color_dialog)
        layout.addWidget(self.colors_section)

        # --- Background ---
        layout.addWidget(section_header(self.i18n.BACKGROUND_SETTINGS_GROUP_TITLE))
        self.background_color_button = QPushButton()
        self.background_color_button.setObjectName("background_color")
        self.background_color_button.setFixedSize(36, 26)
        self.background_color_button.clicked.connect(lambda: self.open_color_dialog("background_color"))
        self.background_color_input = QLineEdit()
        self.background_color_input.setMaxLength(7)
        self.background_color_input.setFixedWidth(90)
        self.background_color_input.textChanged.connect(lambda t: self._on_hex_edited("background_color", t))
        self._swatches["background_color"] = (self.background_color_button, self.background_color_input)
        layout.addWidget(SettingCard(
            self.i18n.BACKGROUND_COLOR_LABEL,
            control=self._row(self.background_color_button, self.background_color_input)))

        self.bg_opacity = Win11Slider(editable=True, suffix="%")
        self.bg_opacity.setRange(0, 100)
        self.bg_opacity.setMinimumWidth(260)
        self.bg_opacity.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.BACKGROUND_OPACITY_LABEL, control=self.bg_opacity))

        # --- Arrow styling (merged from the old Arrows page) ---
        layout.addWidget(section_header(self.i18n.ARROW_STYLING_GROUP))
        # Arrow STYLE picker (#129) - curated glyph presets + Custom (full-width control). The default
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
        arrow_v_layout.setSpacing(tokens.SPACE_XS)   # match the page card gap so all card spacing is equal
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

        # Per-direction arrow color (#168). Off by default: the arrow shares the speed text's pen,
        # so with color-coding on the whole line bands together as one signal. Turning this on
        # splits it into two color languages (direction vs magnitude) - defensible, but a real
        # design change, so nobody's widget shifts on upgrade.
        self.use_custom_arrow_colors = Win11Toggle(label_text="")
        self.use_custom_arrow_colors.toggled.connect(self._on_arrow_colors_toggle)
        layout.addWidget(SettingCard(self.i18n.USE_CUSTOM_ARROW_COLORS,
                                     control=self.use_custom_arrow_colors))

        self.arrow_colors_container = QWidget()
        arrow_c_layout = QVBoxLayout(self.arrow_colors_container)
        arrow_c_layout.setContentsMargins(0, 0, 0, 0)
        arrow_c_layout.setSpacing(tokens.SPACE_XS)
        for stem, label in (("arrow_up_color", self.i18n.ARROW_UP_COLOR_LABEL),
                            ("arrow_down_color", self.i18n.ARROW_DOWN_COLOR_LABEL)):
            arrow_c_layout.addWidget(SettingCard(label, control=self._themed_pair(stem, label)))
        layout.addWidget(self.arrow_colors_container)

        layout.addStretch()

    def _themed_pair(self, stem: str, title: str, tooltip: str = "") -> ThemedColorPair:
        """A themed colour row's control. Its widgets are reachable as ``<key>_button`` / ``<key>_input``
        (e.g. ``default_color_light_input``), and SettingsDialog routes a picked colour back through
        set_color_input(key) - the full config key."""
        pair = ThemedColorPair(self.i18n, stem, title, self.open_color_dialog, self._on_hex_edited, tooltip)
        for key, button in pair.buttons.items():
            setattr(self, f"{key}_button", button)
            setattr(self, f"{key}_input", pair.inputs[key])
            self._swatches[key] = (button, pair.inputs[key])
        return pair

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
        self.current_font.setFamily(_qt_family(fam))
        self.font_size.setValue(int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)))
        self._update_weight_options(fam)
        self._set_slider_weight(config.get("font_weight", constants.fonts.WEIGHT_DEMIBOLD))

        # Colors (each themed key and the background) - a plain loop, no theme state (2.2 rc.2).
        factory = constants.config.defaults.DEFAULT_CONFIG
        for key, (button, line) in self._swatches.items():
            c = str(config.get(key) or factory[key])
            button.setStyleSheet(swatch_style(c))
            line.setText(c)

        
        self.bg_opacity.setValue(int(config.get("background_opacity", 0)))
        
        # Arrow style (glyph presets / custom)
        self.arrow_style_picker.set_values(
            config.get("arrow_up_symbol", ""), config.get("arrow_down_symbol", ""))

        # Arrow Fonts
        self.use_separate_arrow_font.setChecked(bool(config.get("use_separate_arrow_font", False)))
        a_fam = config.get("arrow_font_family", fam) # Fallback to main
        self.arrow_font_family_label.setText(a_fam)
        self.current_arrow_font.setFamily(_qt_family(a_fam))
        
        main_size = int(config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE))
        self.arrow_font_size.setValue(int(config.get("arrow_font_size", main_size)))
        
        # Arrow Weight ignored in UI
        
        self.arrow_font_container.setVisible(self.use_separate_arrow_font.isChecked())

        self.use_custom_arrow_colors.setChecked(bool(config.get("use_custom_arrow_colors", False)))
        self.arrow_colors_container.setVisible(self.use_custom_arrow_colors.isChecked())

        # Absorbed sections (units / color coding).
        self.units_section.load_settings(config)
        self.colors_section.load_settings(config)

    def get_settings(self) -> Dict[str, Any]:
        settings = {
            "font_family": self.font_family_label.text(),
            "font_size": int(self.font_size.value()),
            "font_weight": self.allowed_font_weights[self.font_weight.value()] if 0 <= self.font_weight.value() < len(self.allowed_font_weights) else 400,
            # The themed text / arrow colours (both halves of each pair) and the background.
            **{key: self._hex.get(key, line.text()) for key, (_b, line) in self._swatches.items()},
            "background_opacity": self.bg_opacity.value(),
            # Arrow Config
            "use_custom_arrow_colors": self.use_custom_arrow_colors.isChecked(),
            "use_separate_arrow_font": self.use_separate_arrow_font.isChecked(),
            "arrow_font_family": self.arrow_font_family_label.text(),
            "arrow_font_size": int(self.arrow_font_size.value()),
            "arrow_font_weight": constants.fonts.WEIGHT_DEMIBOLD, # Fixed default due to glyph fallback issues
            # Arrow style glyphs (#129) - empty == native locale default (Classic)
            **self.arrow_style_picker.get_values(),
            # Mini Graph keys moved with the section to the Widget page (mini_graph_section.py).
        }
        # Merge the absorbed sections' keys (units / color coding).
        settings.update(self.units_section.get_settings())
        settings.update(self.colors_section.get_settings())
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
        """Show a picked colour in the field of config key ``key`` (e.g. ``arrow_down_color_light``)."""
        if key in self._swatches:
            button, line = self._swatches[key]
            line.setText(hex_code)
            button.setStyleSheet(swatch_style(hex_code))
            self.on_change()

    def _on_hex_edited(self, key: str, text: str) -> None:
        """A colour hex field changed: only a complete #RRGGBB repaints the swatch and reaches the live
        preview; the keystrokes in between change nothing (S8)."""
        if not self._hex.remember(key, text):
            return
        self._swatches[key][0].setStyleSheet(swatch_style(text.strip()))
        self.on_change()

    def _on_font_weight_changed(self, idx: int):
        if 0 <= idx < len(self.allowed_font_weights):
            w = self.allowed_font_weights[idx]
            self.font_weight.setValueText(self.font_weight_name_map.get(w, str(w)))
        self.on_change()

    def _on_arrow_colors_toggle(self, checked: bool):
        self.arrow_colors_container.setVisible(checked)
        self.on_change()

    def _on_arrow_font_toggle(self, checked: bool):
        self.arrow_font_container.setVisible(checked)
        self.layout_changed.emit()
        self.on_change()

    def _update_weight_options(self, family: str):
        family = _qt_family(family)
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
