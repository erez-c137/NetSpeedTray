"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QComboBox, QLabel, QGridLayout

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle

class HardwarePage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Hardware Monitoring Group ---
        hw_group = QGroupBox(self.i18n.HARDWARE_MONITORING_GROUP)
        hw_layout = QGridLayout(hw_group)
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

        hw_layout.addWidget(cpu_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_cpu, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(ram_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_ram, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        hw_layout.addWidget(gpu_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_gpu, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(vram_label, 3, 0, Qt.AlignmentFlag.AlignVCenter)
        hw_layout.addWidget(self.monitor_vram, 3, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(hw_group)

        # --- Display Mode Group ---
        display_group = QGroupBox(self.i18n.WIDGET_DISPLAY_MODE_LABEL)
        display_layout = QVBoxLayout(display_group)
        
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_NETWORK, userData="network_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CPU, userData="cpu_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_GPU, userData="gpu_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_COMBINED, userData="side_by_side")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CYCLE, userData="cycle")
        
        self.display_mode_combo.currentIndexChanged.connect(self.on_change)
        display_layout.addWidget(self.display_mode_combo)
        
        note_label = QLabel(self.i18n.HARDWARE_GRAPH_NOTE)
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: gray; font-size: 10px;")
        display_layout.addWidget(note_label)
        
        layout.addWidget(display_group)

        # --- Display Order Group ---
        order_group = QGroupBox(self.i18n.WIDGET_DISPLAY_ORDER_LABEL)
        order_layout = QGridLayout(order_group)
        
        self.pos_combos = []
        for i in range(3):
            label = QLabel(getattr(self.i18n, f"ORDER_POSITION_{i+1}"))
            combo = QComboBox()
            combo.addItem(self.i18n.ORDER_TYPE_NETWORK, userData="network")
            combo.addItem(self.i18n.ORDER_TYPE_CPU, userData="cpu")
            combo.addItem(self.i18n.ORDER_TYPE_GPU, userData="gpu")
            combo.addItem(self.i18n.ORDER_TYPE_NONE, userData="none")
            
            combo.currentIndexChanged.connect(self.on_change)
            order_layout.addWidget(label, i, 0)
            order_layout.addWidget(combo, i, 1)
            self.pos_combos.append(combo)
            
        layout.addWidget(order_group)

        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        self.monitor_cpu.setChecked(config.get("monitor_cpu_enabled", False))
        self.monitor_gpu.setChecked(config.get("monitor_gpu_enabled", False))
        self.monitor_ram.setChecked(config.get("monitor_ram_enabled", False))
        self.monitor_vram.setChecked(config.get("monitor_vram_enabled", False))
        
        mode = config.get("widget_display_mode", "network_only")
        # Handle 'combined' migration to 'side_by_side' if needed
        if mode == "combined": mode = "side_by_side"
        
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
            # Auto-switch to side_by_side if currently in network_only
            current_mode = self.display_mode_combo.currentData()
            if current_mode == "network_only":
                idx = self.display_mode_combo.findData("side_by_side")
                if idx >= 0:
                    self.display_mode_combo.setCurrentIndex(idx)
        
        # Trigger general change notification
        self.on_change()

    def get_settings(self) -> Dict[str, Any]:
        order = [c.currentData() for c in self.pos_combos]
        return {
            "monitor_cpu_enabled": self.monitor_cpu.isChecked(),
            "monitor_gpu_enabled": self.monitor_gpu.isChecked(),
            "monitor_ram_enabled": self.monitor_ram.isChecked(),
            "monitor_vram_enabled": self.monitor_vram.isChecked(),
            "widget_display_mode": self.display_mode_combo.currentData(),
            "widget_display_order": order
        }
