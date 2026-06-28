"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QComboBox, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Toggle, Win11Slider, SettingCard, SettingExpander
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

class HardwarePage(QWidget):
    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: the primary monitoring toggles are Win11 Settings cards under a plain section caption;
        # the lower-frequency groups (display mode / order / load colours) stay behind Fluent expanders.
        # All control objects + the load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Hardware monitoring (primary — always visible) ---
        layout.addWidget(section_header(self.i18n.HARDWARE_MONITORING_GROUP))

        self.monitor_cpu = Win11Toggle(label_text="")
        self.monitor_cpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_CPU_LABEL, control=self.monitor_cpu))

        self.monitor_ram = Win11Toggle(label_text="")
        self.monitor_ram.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.MONITOR_RAM_LABEL, control=self.monitor_ram))

        self.monitor_gpu = Win11Toggle(label_text="")
        self.monitor_gpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_GPU_LABEL, control=self.monitor_gpu))

        self.monitor_vram = Win11Toggle(label_text="")
        self.monitor_vram.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.MONITOR_VRAM_LABEL, control=self.monitor_vram))

        self.show_temps = Win11Toggle(label_text="")
        self.show_temps.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.SHOW_HARDWARE_TEMPS_LABEL, control=self.show_temps))

        self.show_power = Win11Toggle(label_text="")
        self.show_power.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.SHOW_HARDWARE_POWER_LABEL, control=self.show_power))

        temps_note = QLabel(self.i18n.HARDWARE_TEMPS_LIMITATION_NOTE)
        temps_note.setWordWrap(True)
        temps_note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(temps_note)

        self.label_style = QComboBox()
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_COLORED_ICONS, userData="icons_colored")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_MONOCHROME_ICONS, userData="icons_monochrome")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_TEXT_LABELS, userData="text")
        self.label_style.setMinimumWidth(200)
        self.label_style.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HARDWARE_INDICATOR_STYLE_LABEL, control=self.label_style))

        # Throttle temperature (Statistics): flags time spent at/above this temp in the Stats-detail
        # sheet so thermal throttling is provable (0 = off). Only meaningful when temps are collected.
        self.throttle_temp = Win11Slider(editable=True, suffix="°C")
        self.throttle_temp.setRange(0, 130)
        self.throttle_temp.setMinimumWidth(260)
        self.throttle_temp.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(str(getattr(self.i18n, "HW_THROTTLE_LABEL", "Throttle temp (stats)")),
                                     control=self.throttle_temp))

        # --- Widget Display Mode (advanced — collapsible) ---
        display_section = SettingExpander(self.i18n.WIDGET_DISPLAY_MODE_LABEL, expanded=False)
        display_section.expandedChanged.connect(lambda _on: self.layout_changed.emit())
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_NETWORK, userData="network_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_COMBINED, userData="side_by_side")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_STACKED_COLUMN, userData="side_by_stack")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CYCLE, userData="cycle")
        self.display_mode_combo.currentIndexChanged.connect(self.on_change)
        display_section.contentLayout().addWidget(self.display_mode_combo)
        note_label = QLabel(self.i18n.HARDWARE_GRAPH_NOTE)
        note_label.setWordWrap(True)
        note_label.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        display_section.contentLayout().addWidget(note_label)
        layout.addWidget(display_section)

        # --- Display Order (advanced — collapsible) ---
        order_section = SettingExpander(self.i18n.WIDGET_DISPLAY_ORDER_LABEL, expanded=False)
        order_section.expandedChanged.connect(lambda _on: self.layout_changed.emit())
        self.pos_combos = []
        for i in range(3):
            combo = QComboBox()
            combo.addItem(self.i18n.ORDER_TYPE_NETWORK, userData="network")
            combo.addItem(self.i18n.ORDER_TYPE_CPU, userData="cpu")
            combo.addItem(self.i18n.ORDER_TYPE_GPU, userData="gpu")
            combo.addItem(self.i18n.ORDER_TYPE_NONE, userData="none")
            combo.setMinimumWidth(160)
            combo.currentIndexChanged.connect(lambda _, idx=i: self._on_pos_changed(idx))
            order_section.contentLayout().addWidget(
                SettingCard(getattr(self.i18n, f"ORDER_POSITION_{i+1}"), control=combo))
            self.pos_combos.append(combo)
        layout.addWidget(order_section)

        # --- Color-code by load (advanced — collapsible): thresholds for tinting CPU/GPU % by load.
        load_section = SettingExpander(self.i18n.HARDWARE_LOAD_COLOR_SECTION, expanded=False)
        load_section.expandedChanged.connect(lambda _on: self.layout_changed.emit())

        def _make_load_slider() -> Win11Slider:
            s = Win11Slider(editable=True, suffix="%")
            s.setRange(0, 100)
            s.setMinimumWidth(240)
            s.valueChanged.connect(self.on_change)
            return s

        self.cpu_load_high = _make_load_slider()
        self.cpu_load_low = _make_load_slider()
        self.gpu_load_high = _make_load_slider()
        self.gpu_load_low = _make_load_slider()
        for label, widget in [
            (self.i18n.HARDWARE_CPU_HIGH_LOAD_LABEL, self.cpu_load_high),
            (self.i18n.HARDWARE_CPU_LOW_LOAD_LABEL, self.cpu_load_low),
            (self.i18n.HARDWARE_GPU_HIGH_LOAD_LABEL, self.gpu_load_high),
            (self.i18n.HARDWARE_GPU_LOW_LOAD_LABEL, self.gpu_load_low),
        ]:
            load_section.contentLayout().addWidget(SettingCard(label, control=widget))
        layout.addWidget(load_section)

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

        self.throttle_temp.setValue(int(config.get("throttle_temp_c", 0) or 0))

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

        d = constants.config.defaults
        self.cpu_load_high.setValue(int(config.get("cpu_load_high_threshold", d.DEFAULT_CPU_LOAD_HIGH_THRESHOLD)))
        self.cpu_load_low.setValue(int(config.get("cpu_load_low_threshold", d.DEFAULT_CPU_LOAD_LOW_THRESHOLD)))
        self.gpu_load_high.setValue(int(config.get("gpu_load_high_threshold", d.DEFAULT_GPU_LOAD_HIGH_THRESHOLD)))
        self.gpu_load_low.setValue(int(config.get("gpu_load_low_threshold", d.DEFAULT_GPU_LOAD_LOW_THRESHOLD)))

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
            "widget_display_order": order,
            "cpu_load_high_threshold": float(self.cpu_load_high.value()),
            "cpu_load_low_threshold": float(self.cpu_load_low.value()),
            "gpu_load_high_threshold": float(self.gpu_load_high.value()),
            "gpu_load_low_threshold": float(self.gpu_load_low.value()),
            "throttle_temp_c": int(self.throttle_temp.value()),
        }
