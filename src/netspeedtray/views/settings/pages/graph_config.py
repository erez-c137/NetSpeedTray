"""
Graph Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QLabel, QGridLayout

from netspeedtray import constants
from netspeedtray.utils.components import Win11Slider, Win11Toggle
from netspeedtray.constants import styles as style_constants
from netspeedtray.utils import styles as style_utils

class GraphPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        graph_group = QGroupBox(self.i18n.MINI_GRAPH_SETTINGS_GROUP)
        graph_layout = QGridLayout(graph_group)
        graph_layout.setVerticalSpacing(10)
        graph_layout.setHorizontalSpacing(8)

        enable_graph_label = QLabel(self.i18n.ENABLE_GRAPH_LABEL)
        self.enable_graph = Win11Toggle(label_text="")
        self.enable_graph.toggled.connect(self.on_change)
        
        graph_layout.addWidget(enable_graph_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        graph_layout.addWidget(self.enable_graph, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        note = QLabel(self.i18n.GRAPH_NOTE_TEXT)
        note.setWordWrap(True)
        is_dark = style_utils.is_dark_mode()
        subtle_color = style_constants.SUBTLE_TEXT_COLOR_DARK if is_dark else style_constants.SUBTLE_TEXT_COLOR_LIGHT
        note.setStyleSheet(f"font-size: {constants.fonts.NOTE_FONT_SIZE}pt; color: {subtle_color};")
        graph_layout.addWidget(note, 1, 0, 1, 2)

        graph_layout.addWidget(QLabel(self.i18n.HISTORY_DURATION_LABEL), 2, 0, 1, 2)
        self.history_duration = Win11Slider(editable=False)
        hist_min, hist_max = constants.ui.history.HISTORY_MINUTES_RANGE
        self.history_duration.setRange(hist_min, hist_max)
        self.history_duration.valueChanged.connect(self.on_change)
        graph_layout.addWidget(self.history_duration, 3, 0, 1, 2)

        graph_layout.addWidget(QLabel(self.i18n.GRAPH_OPACITY_LABEL), 4, 0, 1, 2)
        self.graph_opacity = Win11Slider(editable=False)
        self.graph_opacity.setRange(constants.ui.sliders.OPACITY_MIN, constants.ui.sliders.OPACITY_MAX)
        self.graph_opacity.valueChanged.connect(self.on_change)
        graph_layout.addWidget(self.graph_opacity, 5, 0, 1, 2)

        layout.addWidget(graph_group)
        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        self.enable_graph.setChecked(config.get("graph_enabled", True))
        self.history_duration.setValue(config.get("history_minutes", constants.config.defaults.DEFAULT_HISTORY_MINUTES))
        self.graph_opacity.setValue(config.get("graph_opacity", constants.config.defaults.DEFAULT_GRAPH_OPACITY))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "graph_enabled": self.enable_graph.isChecked(),
            "history_minutes": self.history_duration.value(),
            "graph_opacity": self.graph_opacity.value()
        }
