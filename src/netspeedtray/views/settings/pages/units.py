"""
Units and Interface Layout Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtWidgets import QWidget

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, Win11Segmented, SettingCard, Win11ComboBox
from netspeedtray.utils.helpers import line_spacing_px
from netspeedtray.views.settings.pages._fluent import section_header, page_layout, as_int

class UnitsPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _tr(self, key: str, default: str) -> str:
        from netspeedtray.utils.helpers import tr
        return tr(self.i18n, key, default)

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

        # Force MB Display - when on, the renderer forces MB output regardless of unit type.
        self.speed_display_mode = Win11Toggle(label_text="")
        self.speed_display_mode.toggled.connect(self._on_force_mb_toggled)
        layout.addWidget(SettingCard(self._tr("FORCE_MB_LABEL", "Force MB Display"),
                                     control=self.speed_display_mode))

        # Decimals - a 3-value enum {0,1,2}: a segmented control is the native Win11 idiom, NOT a
        # continuous slider (which implied a magnitude and showed a meaningless "1").
        self.decimal_places = Win11Segmented([("0", 0), ("1", 1), ("2", 2)])
        self.decimal_places.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.DECIMAL_PLACES_LABEL, control=self.decimal_places))

        # --- Interface Layout ---
        layout.addWidget(section_header(self._tr("INTERFACE_LAYOUT_GROUP", "Interface Layout")))

        # NOTE: the old "Text Alignment" (left/center/right) control was removed in 2.0 - it was wired
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

        # Extra px between the upload/download rows on top of the font's line height; 1 = old fixed gap.
        # Shown as offsets from the default ("-4 .. -1, Default, +1 .. +4"); each segment's value is the
        # stored px (default + offset). Raw px made "0" look like the default while "+1" was it.
        defaults = constants.config.defaults
        # wrap: the localized "Default" segment makes the row ~460-510 px - the widest control in
        # Settings - so let it break onto two rows rather than raise the window's minimum width.
        self.line_spacing = Win11Segmented([(self._line_spacing_text(v), v)
                                            for v in range(defaults.LINE_SPACING_MIN, defaults.LINE_SPACING_MAX + 1)],
                                           wrap=True)
        self.line_spacing.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self._tr("LINE_SPACING_LABEL", "Line spacing"),
                                     description=self._tr("LINE_SPACING_OFFSET_DESCRIPTION",
                                                          "Gap between the upload and download rows, "
                                                          "in pixels from the default"),
                                     control=self.line_spacing))

        layout.addStretch()

    def _line_spacing_text(self, px: int) -> str:
        """A line-spacing segment's text: "Default" for the default gap, else the signed px offset
        from it ("-4" .. "+4")."""
        offset = px - constants.config.defaults.DEFAULT_LINE_SPACING
        if not offset:
            return self._tr("LINE_SPACING_DEFAULT_OPTION", "Default")
        # A right-to-left (Hebrew) segment read "1+" / "4-": the sign is direction-neutral and lands
        # on the far side of the digit. An LTR embedding (LRE ... PDF) keeps "+1" one left-to-right
        # unit (Qt's bidi, checked with QTextLayout). Not the LRI/PDI isolates: Segoe UI, the Hebrew UI
        # face, has no glyph for those, and Qt then maps every installed font file (~850) looking for
        # one. Only there: most CJK/Bengali UI faces lack even LRE/PDF. The segment's value is
        # separate from its text, so nothing maps the label back.
        text = f"{offset:+d}"
        return f"\u202a{text}\u202c" if getattr(self.i18n, "is_rtl", False) is True else text

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
        self.decimal_places.setValue(as_int(config.get("decimal_places"),
                                            constants.config.defaults.DEFAULT_DECIMAL_PLACES))

        # Toggles
        self.swap_upload_download.setChecked(config.get("swap_upload_download", constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD))
        self.hide_arrows.setChecked(config.get("hide_arrows", False))
        self.hide_unit_suffix.setChecked(config.get("hide_unit_suffix", False))
        self.short_unit_labels.setChecked(config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS))
        # The renderer's and the layout's rule (line_spacing_px), plus the range: the control has no
        # segment for a value outside it and would keep no value at all.
        defaults = constants.config.defaults
        spacing = line_spacing_px(config.get("line_spacing"))
        if not defaults.LINE_SPACING_MIN <= spacing <= defaults.LINE_SPACING_MAX:
            spacing = defaults.DEFAULT_LINE_SPACING
        self.line_spacing.setValue(spacing)


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
            "short_unit_labels": self.short_unit_labels.isChecked(),
            "line_spacing": self.line_spacing.value(),
        }

    def _on_force_mb_toggled(self, checked: bool) -> None:
        """Handler when the Force MB toggle changes."""
        # The toggle state is saved, and the rendering logic will handle the display.
        self.on_change()
