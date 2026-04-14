"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLabel, QGridLayout

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, CollapsibleSection

class HardwarePage(QWidget):
    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Hardware Monitoring Section (expanded by default) ---
        hw_section = CollapsibleSection(self.i18n.HARDWARE_MONITORING_GROUP, expanded=True)
        hw_section.toggled.connect(lambda: self.layout_changed.emit())
        hw_layout = QGridLayout()
        hw_layout.setVerticalSpacing(10)
        hw_layout.setHorizontalSpacing(8)

        cpu_label = QLabel(self.i18n.MONITOR_CPU_LABEL)
        self.monitor_cpu = Win11Toggle(label_text="")
        self.monitor_cpu.toggled.connect(self._on_monitor_toggled)

        gpu_label = QLabel(self.i18n.MONITOR_GPU_LABEL)
        self.monitor_gpu = Win11Toggle(label_text="")
        self.monitor_gpu.toggled.connect(self._on_monitor_toggled)

        ram_label = QLabel(self.i18n.MONITOR_RAM_LABEL)
        self.monitor_ram = Win11Toggle(label_text="")
        self.monitor_ram.toggled.connect(self.on_change)

        vram_label = QLabel(self.i18n.MONITOR_VRAM_LABEL)
        self.monitor_vram = Win11Toggle(label_text="")
        self.monitor_vram.toggled.connect(self.on_change)

        temps_label = QLabel(self.i18n.SHOW_HARDWARE_TEMPS_LABEL)
        self.show_temps = Win11Toggle(label_text="")
        self.show_temps.toggled.connect(self.on_change)

        power_label = QLabel(self.i18n.SHOW_HARDWARE_POWER_LABEL)
        self.show_power = Win11Toggle(label_text="")
        self.show_power.toggled.connect(self.on_change)

        temps_note = QLabel(self.i18n.HARDWARE_TEMPS_LIMITATION_NOTE)
        temps_note.setWordWrap(True)
        temps_note.setStyleSheet("color: gray; font-size: 10px;")

        hw_layout.addWidget(cpu_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_cpu, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(ram_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_ram, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(gpu_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_gpu, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(vram_label, 3, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_vram, 3, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(temps_label, 4, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.show_temps, 4, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(power_label, 5, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.show_power, 5, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(temps_note, 6, 0, 1, 2)

        style_label = QLabel(self.i18n.HARDWARE_INDICATOR_STYLE_LABEL)
        self.label_style = QComboBox()
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_COLORED_ICONS, userData="icons_colored")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_MONOCHROME_ICONS, userData="icons_monochrome")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_TEXT_LABELS, userData="text")
        self.label_style.currentIndexChanged.connect(self.on_change)

        hw_layout.addWidget(style_label, 7, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.label_style, 7, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        hw_section.contentLayout().addLayout(hw_layout)
        layout.addWidget(hw_section)

        # --- Widget Display Mode Section (collapsed by default) ---
        display_section = CollapsibleSection(self.i18n.WIDGET_DISPLAY_MODE_LABEL, expanded=False)
        display_section.toggled.connect(lambda: self.layout_changed.emit())

        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_NETWORK, userData="network_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_COMBINED, userData="side_by_side")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_STACKED_COLUMN, userData="side_by_stack")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CYCLE, userData="cycle")
        self.display_mode_combo.currentIndexChanged.connect(self.on_change)
        display_section.contentLayout().addWidget(self.display_mode_combo)

        note_label = QLabel(self.i18n.HARDWARE_GRAPH_NOTE)
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: gray; font-size: 10px;")
        display_section.contentLayout().addWidget(note_label)

        layout.addWidget(display_section)

        # --- Display Order Section (collapsed by default) ---
        order_section = CollapsibleSection(self.i18n.WIDGET_DISPLAY_ORDER_LABEL, expanded=False)
        order_section.toggled.connect(lambda: self.layout_changed.emit())
        order_layout = QGridLayout()

        self.pos_combos = []
        for i in range(3):
            label = QLabel(getattr(self.i18n, f"ORDER_POSITION_{i+1}"))
            combo = QComboBox()
            combo.addItem(self.i18n.ORDER_TYPE_NETWORK, userData="network")
            combo.addItem(self.i18n.ORDER_TYPE_CPU, userData="cpu")
            combo.addItem(self.i18n.ORDER_TYPE_GPU, userData="gpu")
            combo.addItem(self.i18n.ORDER_TYPE_NONE, userData="none")

            combo.currentIndexChanged.connect(lambda _, idx=i: self._on_pos_changed(idx))
            order_layout.addWidget(label, i, 0)
            order_layout.addWidget(combo, i, 1)
            self.pos_combos.append(combo)

        order_section.contentLayout().addLayout(order_layout)
        layout.addWidget(order_section)

        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        # Block signals to prevent setChecked from triggering _on_monitor_toggled
        # which would auto-switch the display mode dropdown unexpectedly during load.
        self.monitor_cpu.blockSignals(True)
        self.monitor_gpu.blockSignals(True)
        self.monitor_ram.blockSignals(True)
        self.monitor_vram.blockSignals(True)
        self.show_temps.blockSignals(True)
        self.show_power.blockSignals(True)

        self.monitor_cpu.setChecked(config.get("monitor_cpu_enabled", False))
        self.monitor_gpu.setChecked(config.get("monitor_gpu_enabled", False))
        self.monitor_ram.setChecked(config.get("monitor_ram_enabled", False))
        self.monitor_vram.setChecked(config.get("monitor_vram_enabled", False))
        self.show_temps.setChecked(config.get("show_hardware_temps", False))
        self.show_power.setChecked(config.get("show_hardware_power", False))

        style_val = config.get("hardware_label_style", "icons_colored")
        style_idx = self.label_style.findData(style_val)
        if style_idx >= 0:
            self.label_style.setCurrentIndex(style_idx)

        self.monitor_cpu.blockSignals(False)
        self.monitor_gpu.blockSignals(False)
        self.monitor_ram.blockSignals(False)
        self.monitor_vram.blockSignals(False)
        self.show_temps.blockSignals(False)
        self.show_power.blockSignals(False)
        
        mode = config.get("widget_display_mode", "network_only")
        if mode == "side_by_side" and config.get("stack_hardware_stats", False):
            mode = "side_by_stack"
            
        index = self.display_mode_combo.findData(mode)
        if index >= 0:
            self.display_mode_combo.setCurrentIndex(index)
            
        order = config.get("widget_display_order", ["network", "cpu", "gpu"])
        for i, combo in enumerate(self.pos_combos):
            val = order[i] if i < len(order) else "none"
            idx = combo.findData(val)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _on_monitor_toggled(self, checked: bool):
        """Handle monitor toggling with auto-mode switching."""
        if checked:
            current_mode = self.display_mode_combo.currentData()
            if current_mode == "network_only":
                idx = self.display_mode_combo.findData("side_by_side")
                if idx >= 0:
                    self.display_mode_combo.setCurrentIndex(idx)
        
        self.on_change()

    def _on_pos_changed(self, combo_index: int):
        """Prevents duplicate positional items by auto-swapping with the absent item."""
        values = [c.currentData() for c in self.pos_combos]
        new_val = values[combo_index]
        if new_val == "none":
            self.on_change()
            return
            
        for i in range(3):
            if i != combo_index and values[i] == new_val:
                used = set(values)
                missing = {"network", "cpu", "gpu"} - used
                if missing:
                    next_item = list(missing)[0]
                    idx = self.pos_combos[i].findData(next_item)
                    if idx >= 0:
                        self.pos_combos[i].blockSignals(True)
                        self.pos_combos[i].setCurrentIndex(idx)
                        self.pos_combos[i].blockSignals(False)
                break
        self.on_change()

    def get_settings(self) -> Dict[str, Any]:
        order = [c.currentData() for c in self.pos_combos]
        mode = self.display_mode_combo.currentData()
        
        return {
            "monitor_cpu_enabled": self.monitor_cpu.isChecked(),
            "monitor_gpu_enabled": self.monitor_gpu.isChecked(),
            "monitor_ram_enabled": self.monitor_ram.isChecked(),
            "monitor_vram_enabled": self.monitor_vram.isChecked(),
            "show_hardware_temps": self.show_temps.isChecked(),
            "show_hardware_power": self.show_power.isChecked(),
            "hardware_label_style": self.label_style.currentData(),
            "stack_hardware_stats": mode == "side_by_stack",
            "widget_display_mode": "side_by_side" if mode == "side_by_stack" else mode,
            "widget_display_order": order
        }
