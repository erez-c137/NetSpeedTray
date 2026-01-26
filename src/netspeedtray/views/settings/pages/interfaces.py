"""
Interfaces Settings Page.
"""
from typing import Dict, Any, Callable, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QLabel, QRadioButton, QScrollArea
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle
from netspeedtray.constants import styles as style_constants
from netspeedtray.utils import styles as style_utils

class InterfacesPage(QWidget):
    def __init__(self, i18n, available_interfaces: List[str], on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.available_interfaces = available_interfaces
        self.on_change = on_change
        self.interface_checkboxes: Dict[str, Win11Toggle] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        interfaces_group = QGroupBox(self.i18n.NETWORK_INTERFACES_GROUP)
        interfaces_layout = QVBoxLayout(interfaces_group)
        interfaces_layout.setSpacing(constants.layout.HORIZONTAL_SPACING_MEDIUM)

        interfaces_layout.addWidget(QLabel(self.i18n.MONITORING_MODE_LABEL))
        self.auto_interface_radio = QRadioButton(self.i18n.MONITORING_MODE_AUTO)
        self.all_physical_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_PHYSICAL)
        self.all_virtual_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_VIRTUAL)
        self.selected_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_SELECTED)
        
        # Tooltips
        self.auto_interface_radio.setToolTip(self.i18n.MONITORING_MODE_AUTO_TOOLTIP)
        self.all_physical_interfaces_radio.setToolTip(self.i18n.MONITORING_MODE_PHYSICAL_TOOLTIP)
        self.all_virtual_interfaces_radio.setToolTip(self.i18n.MONITORING_MODE_VIRTUAL_TOOLTIP)
        self.selected_interfaces_radio.setToolTip(self.i18n.MONITORING_MODE_SELECTED_TOOLTIP)
        
        # Connect signals
        for radio in [self.auto_interface_radio, self.all_physical_interfaces_radio, 
                      self.all_virtual_interfaces_radio, self.selected_interfaces_radio]:
            radio.toggled.connect(lambda c: c and self.on_change())
            interfaces_layout.addWidget(radio)

        self.interface_scroll = QScrollArea()
        self.interface_scroll.setWidgetResizable(True)
        self.interface_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        interfaces_container = QWidget()
        self.interfaces_container_layout = QVBoxLayout(interfaces_container)
        self.interfaces_container_layout.setSpacing(constants.layout.VERTICAL_SPACING)

        self._populate_interface_list()

        interfaces_container.setLayout(self.interfaces_container_layout)
        self.interface_scroll.setWidget(interfaces_container)

        interfaces_layout.addWidget(self.interface_scroll)
        layout.addWidget(interfaces_group)
        layout.addStretch()

    def _populate_interface_list(self):
        # Clear existing
        while self.interfaces_container_layout.count():
            child = self.interfaces_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.interface_checkboxes.clear()

        if self.available_interfaces:
            for iface in sorted(self.available_interfaces):
                checkbox = Win11Toggle(iface)
                self.interface_checkboxes[iface] = checkbox
                checkbox.toggled.connect(self.on_change)
                self.interfaces_container_layout.addWidget(checkbox)
        else:
            no_iface_label = QLabel(self.i18n.NO_INTERFACES_FOUND)
            is_dark = style_utils.is_dark_mode()
            subtle_color = style_constants.SUBTLE_TEXT_COLOR_DARK if is_dark else style_constants.SUBTLE_TEXT_COLOR_LIGHT
            no_iface_label.setStyleSheet(f"color: {subtle_color}; font-style: italic;")
            self.interfaces_container_layout.addWidget(no_iface_label)
        
        self.interfaces_container_layout.addStretch()

    def update_interface_list(self, new_interfaces: List[str]):
        self.available_interfaces = new_interfaces or []
        self._populate_interface_list()
        # Note: calling code needs to re-apply selection state (load_settings) after update if needed

    def load_settings(self, config: Dict[str, Any]):
        mode = config.get("monitoring_mode", "auto")
        if mode == "auto":
            self.auto_interface_radio.setChecked(True)
        elif mode == "all_physical":
            self.all_physical_interfaces_radio.setChecked(True)
        elif mode == "all_virtual":
            self.all_virtual_interfaces_radio.setChecked(True)
        elif mode == "selected":
            self.selected_interfaces_radio.setChecked(True)
        
        selected_list = config.get("selected_interfaces", [])
        for iface, checkbox in self.interface_checkboxes.items():
            if iface in selected_list:
                checkbox.setChecked(True)
            else:
                checkbox.setChecked(False)

    def get_settings(self) -> Dict[str, Any]:
        mode = "auto"
        if self.all_physical_interfaces_radio.isChecked(): mode = "all_physical"
        elif self.all_virtual_interfaces_radio.isChecked(): mode = "all_virtual"
        elif self.selected_interfaces_radio.isChecked(): mode = "selected"
        
        selected = [iface for iface, cb in self.interface_checkboxes.items() if cb.isChecked()]
        
        return {
            "monitoring_mode": mode,
            "selected_interfaces": selected
        }
