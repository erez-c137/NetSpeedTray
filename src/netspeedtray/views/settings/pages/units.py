"""
Units and Interface Layout Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QLabel, QGridLayout, QComboBox

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider, Win11Toggle

class UnitsPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Group 1: Data Format ---
        format_group = QGroupBox(getattr(self.i18n, 'DISPLAY_FORMAT_GROUP', "Data Format"))
        format_layout = QGridLayout(format_group)
        format_layout.setVerticalSpacing(10)
        format_layout.setHorizontalSpacing(15)
        format_row = 0

        # Unit Type
        format_layout.addWidget(QLabel(self.i18n.UNIT_TYPE_LABEL), format_row, 0, Qt.AlignmentFlag.AlignVCenter)
        self.unit_type = QComboBox()
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BITS_DECIMAL, "bits_decimal")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BITS_BINARY, "bits_binary")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BYTES_DECIMAL, "bytes_decimal")
        self.unit_type.addItem(self.i18n.UNIT_TYPE_BYTES_BINARY, "bytes_binary")
        self.unit_type.setMinimumWidth(120)
        self.unit_type.currentIndexChanged.connect(self.on_change)
        
        format_layout.addWidget(self.unit_type, format_row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        format_row += 1

        # Scaling (Speed Display Mode)
        format_layout.addWidget(QLabel(getattr(self.i18n, 'SCALING_LABEL', "Unit Scaling")), format_row, 0, Qt.AlignmentFlag.AlignVCenter)
        self.speed_display_mode = Win11Slider(editable=False)
        self.speed_display_mode.setRange(0, 1)
        self.speed_display_mode.valueChanged.connect(self.on_change)
        format_layout.addWidget(self.speed_display_mode, format_row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        format_row += 1

        # Decimals
        format_layout.addWidget(QLabel(self.i18n.DECIMAL_PLACES_LABEL), format_row, 0, Qt.AlignmentFlag.AlignVCenter)
        self.decimal_places = Win11Slider(editable=False)
        self.decimal_places.setRange(0, 2)
        self.decimal_places.valueChanged.connect(self.on_change)
        format_layout.addWidget(self.decimal_places, format_row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        format_layout.setColumnStretch(0, 1)
        format_layout.setColumnStretch(1, 0)
        layout.addWidget(format_group)

        # --- Group 2: Interface Layout ---
        layout_group = QGroupBox(getattr(self.i18n, 'INTERFACE_LAYOUT_GROUP', "Interface Layout"))
        layout_gl = QGridLayout(layout_group)
        layout_gl.setVerticalSpacing(10)
        layout_gl.setHorizontalSpacing(15)
        l_row = 0

        # Text Alignment
        layout_gl.addWidget(QLabel(self.i18n.TEXT_ALIGNMENT_LABEL), l_row, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text_alignment = Win11Slider(editable=False)
        self.text_alignment.setRange(0, 2)
        self.text_alignment.valueChanged.connect(self.on_change)
        layout_gl.addWidget(self.text_alignment, l_row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        l_row += 1

        # Toggles Helper
        def add_toggle_row(label_text, toggle_widget, row_idx):
            layout_gl.addWidget(QLabel(label_text), row_idx, 0, Qt.AlignmentFlag.AlignVCenter)
            layout_gl.addWidget(toggle_widget, row_idx, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            toggle_widget.toggled.connect(self.on_change)

        # Swap Order
        self.swap_upload_download = Win11Toggle(label_text="")
        add_toggle_row(self.i18n.SWAP_UPLOAD_DOWNLOAD_LABEL, self.swap_upload_download, l_row)
        l_row += 1

        # Hide Arrows
        self.hide_arrows = Win11Toggle(label_text="")
        add_toggle_row(self.i18n.HIDE_ARROWS_LABEL, self.hide_arrows, l_row)
        l_row += 1

        # Hide Units
        self.hide_unit_suffix = Win11Toggle(label_text="")
        add_toggle_row(self.i18n.HIDE_UNIT_SUFFIX_LABEL, self.hide_unit_suffix, l_row)
        l_row += 1

        # Short Unit Labels
        self.short_unit_labels = Win11Toggle(label_text="")
        add_toggle_row(self.i18n.SHORT_UNIT_LABELS_LABEL, self.short_unit_labels, l_row)

        layout_gl.setColumnStretch(0, 1)
        layout_gl.setColumnStretch(1, 0)
        layout.addWidget(layout_group)

        # --- Group 3: Positioning ---
        pos_group = QGroupBox(getattr(self.i18n, 'POSITION_GROUP', "Positioning"))
        pos_layout = QGridLayout(pos_group)
        
        pos_layout.addWidget(QLabel(self.i18n.TRAY_OFFSET_LABEL), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tray_offset = Win11Slider()
        self.tray_offset.setRange(0, 50)
        self.tray_offset.valueChanged.connect(self.on_change)
        pos_layout.addWidget(self.tray_offset, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        pos_layout.setColumnStretch(0, 1)
        pos_layout.setColumnStretch(1, 0)
        layout.addWidget(pos_group)

        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        # Unit Type
        ut = config.get("unit_type", "bytes_binary")
        idx = self.unit_type.findData(ut)
        if idx >= 0: self.unit_type.setCurrentIndex(idx)
        
        # Scaling
        # Mapping: auto=0, always_mbps=1
        mode = config.get("speed_display_mode", "auto")
        val = 1 if mode == "always_mbps" else 0
        self.speed_display_mode.setValue(val)
        
        # Others
        self.decimal_places.setValue(config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES))
        
        # Alignment
        align = config.get("text_alignment", "left")
        align_map = {"left": 0, "center": 1, "right": 2}
        self.text_alignment.setValue(align_map.get(align, 0))
        
        # Toggles
        self.swap_upload_download.setChecked(config.get("swap_upload_download", constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD))
        self.hide_arrows.setChecked(config.get("hide_arrows", False))
        self.hide_unit_suffix.setChecked(config.get("hide_unit_suffix", False))
        self.short_unit_labels.setChecked(config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS))
        
        # Offset
        self.tray_offset.setValue(config.get("tray_offset_x", 0))

    def get_settings(self) -> Dict[str, Any]:
        # Inverse mapping
        speed_mode = "always_mbps" if self.speed_display_mode.value() == 1 else "auto"
        align_map_inv = {0: "left", 1: "center", 2: "right"}
        align = align_map_inv.get(self.text_alignment.value(), "left")
        
        return {
            "unit_type": self.unit_type.currentData(),
            "speed_display_mode": speed_mode,
            "decimal_places": self.decimal_places.value(),
            "text_alignment": align,
            "swap_upload_download": self.swap_upload_download.isChecked(),
            "hide_arrows": self.hide_arrows.isChecked(),
            "hide_unit_suffix": self.hide_unit_suffix.isChecked(),
            "short_unit_labels": self.short_unit_labels.isChecked(),
            "tray_offset_x": self.tray_offset.value()
        }
