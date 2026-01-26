"""
Troubleshooting Settings Page.
"""
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QPushButton

from netspeedtray import constants
from netspeedtray.utils import styles as style_utils

class TroubleshootingPage(QWidget):
    def __init__(self, i18n, on_export: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_export = on_export
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)
        
        troubleshooting_group = QGroupBox(self.i18n.TROUBLESHOOTING_GROUP)
        troubleshooting_layout = QVBoxLayout(troubleshooting_group)
        
        export_button = QPushButton(self.i18n.EXPORT_ERROR_LOG_BUTTON)
        export_button.setStyleSheet(style_utils.button_style())
        export_button.setToolTip(self.i18n.EXPORT_ERROR_LOG_TOOLTIP)
        export_button.clicked.connect(self.on_export)
        
        troubleshooting_layout.addWidget(export_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(troubleshooting_group)
        layout.addStretch()
