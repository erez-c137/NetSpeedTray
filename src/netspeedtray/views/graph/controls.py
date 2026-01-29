
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QLabel, QComboBox, 
    QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from netspeedtray import constants
from netspeedtray.utils import styles
from netspeedtray.utils.components import Win11Slider, Win11Toggle

class GraphSettingsPanel(QWidget):
    """
    The settings panel overlay for the Graph Window.
    Provides controls for History Period, Data Retention, Dark Mode, etc.
    """
    
    # Signals for changes
    interface_filter_changed = pyqtSignal(str)
    history_period_changed = pyqtSignal(int)   # Released (Update Data)
    history_period_changing = pyqtSignal(int)  # Dragging (Update Text)
    retention_changed = pyqtSignal(int)        # Released (Update Config)
    retention_changing = pyqtSignal(int)       # Dragging (Update Text)
    dark_mode_toggled = pyqtSignal(bool)
    live_update_toggled = pyqtSignal(bool)
    show_legend_toggled = pyqtSignal(bool)
    
    def __init__(self, parent=None, i18n=None, initial_state=None):
        super().__init__(parent)
        self.i18n = i18n
        self.initial_state = initial_state or {}
        
        # State placeholders
        self.interface_filter = None
        self.history_period_slider = None
        self.keep_data_slider = None
        self.dark_mode_toggle = None
        self.realtime_toggle = None
        self.show_legend_toggle = None
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("settingsPanel")
        
        # Setup UI
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setVerticalSpacing(15)
        layout.setHorizontalSpacing(10)
        
        # Title
        title_text = getattr(self.i18n, 'GRAPH_SETTINGS_LABEL', 'Graph Settings')
        title_label = QLabel(title_text)
        title_label.setObjectName("settingsTitleLabel")
        layout.addWidget(title_label, 0, 0, 1, 2)
        
        # Controls Container
        controls_container = QWidget()
        controls_container.setObjectName("controlsContainer")
        group_layout = QGridLayout(controls_container)
        group_layout.setContentsMargins(15, 15, 15, 15)
        group_layout.setVerticalSpacing(15)
        group_layout.setHorizontalSpacing(10)
        
        current_row = 0

        # 1. Interface Filter
        interface_label = QLabel(getattr(self.i18n, 'INTERFACE_LABEL', 'Interface'))
        self.interface_filter = QComboBox()
        self.interface_filter.addItem(getattr(self.i18n, 'ALL_INTERFACES_AGGREGATED_LABEL', 'All Interfaces'), "all")
        group_layout.addWidget(interface_label, current_row, 0, 1, 2)
        current_row += 1
        group_layout.addWidget(self.interface_filter, current_row, 0, 1, 2)
        current_row += 1

        # 2. History Period Slider
        history_label = QLabel(getattr(self.i18n, 'HISTORY_PERIOD_LABEL_NO_VALUE', 'Timeline'))
        initial_history_val = self.initial_state.get('history_period_value', 0)
        self.history_period_slider = Win11Slider(value=initial_history_val, editable=False)
        if hasattr(self.history_period_slider, 'slider'):
             self.history_period_slider.slider.setMinimum(0)
             self.history_period_slider.slider.setMaximum(len(constants.data.history_period.PERIOD_MAP) - 1)
        
        group_layout.addWidget(history_label, current_row, 0, 1, 2)
        current_row += 1
        group_layout.addWidget(self.history_period_slider, current_row, 0, 1, 2)
        current_row += 1

        # 3. Data Retention Slider
        retention_label = QLabel(getattr(self.i18n, 'DATA_RETENTION_LABEL_NO_VALUE', 'Data Retention'))
        retention_days = self.initial_state.get('retention_days', 30)
        retention_val = self._days_to_slider_value(retention_days)
        self.keep_data_slider = Win11Slider(value=retention_val, editable=False)
        if hasattr(self.keep_data_slider, 'slider'):
             self.keep_data_slider.slider.setMinimum(0)
             self.keep_data_slider.slider.setMaximum(len(constants.data.retention.DAYS_MAP) - 1)
             
        group_layout.addWidget(retention_label, current_row, 0, 1, 2)
        current_row += 1
        group_layout.addWidget(self.keep_data_slider, current_row, 0, 1, 2)
        current_row += 1

        # 4. Toggles
        # Dark Mode
        dm_label = QLabel(getattr(self.i18n, 'DARK_MODE_LABEL', 'Dark Mode'))
        is_dark = self.initial_state.get('is_dark_mode', True)
        self.dark_mode_toggle = Win11Toggle(initial_state=is_dark)
        group_layout.addWidget(dm_label, current_row, 0)
        group_layout.addWidget(self.dark_mode_toggle, current_row, 1, Qt.AlignmentFlag.AlignLeft)
        current_row += 1
        
        # Live Update
        lu_label = QLabel(getattr(self.i18n, 'LIVE_UPDATE_LABEL', 'Live Update'))
        is_live = self.initial_state.get('is_live_update_enabled', True)
        self.realtime_toggle = Win11Toggle(initial_state=is_live)
        group_layout.addWidget(lu_label, current_row, 0)
        group_layout.addWidget(self.realtime_toggle, current_row, 1, Qt.AlignmentFlag.AlignLeft)
        current_row += 1
        
        # Show Legend
        sl_label = QLabel(getattr(self.i18n, 'SHOW_LEGEND_LABEL', 'Show Legend'))
        show_legend = self.initial_state.get('show_legend', True)
        self.show_legend_toggle = Win11Toggle(initial_state=show_legend)
        group_layout.addWidget(sl_label, current_row, 0)
        group_layout.addWidget(self.show_legend_toggle, current_row, 1, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(controls_container, 1, 0, 1, 2)
        layout.setRowStretch(2, 1)
        
        self.setStyleSheet(styles.graph_settings_panel_style())

    def _connect_signals(self):
        # Interface Filter
        self.interface_filter.currentTextChanged.connect(self._on_interface_changed)
        
        # Sliders
        if hasattr(self.history_period_slider, 'slider'):
             self.history_period_slider.slider.valueChanged.connect(self.history_period_changing.emit)
             self.history_period_slider.slider.sliderReleased.connect(self._on_history_released)
        
        if hasattr(self.keep_data_slider, 'slider'):
             self.keep_data_slider.slider.valueChanged.connect(self.retention_changing.emit)
             self.keep_data_slider.slider.sliderReleased.connect(self._on_retention_released)

        # Toggles
        # Toggles
        self.dark_mode_toggle.toggled.connect(self.dark_mode_toggled.emit)
        self.realtime_toggle.toggled.connect(self.live_update_toggled.emit)
        self.show_legend_toggle.toggled.connect(self.show_legend_toggled.emit)
    
    def _on_history_released(self):
        val = self.history_period_slider.value()
        self.history_period_changed.emit(val)

    def _on_interface_changed(self, text):
        data = self.interface_filter.currentData()
        self.interface_filter_changed.emit(str(data))

    def _on_retention_released(self):
        val = self.keep_data_slider.value()
        days = constants.data.retention.DAYS_MAP.get(val, 30)
        self.retention_changed.emit(days)

    def populate_interfaces(self, distinct_interfaces):
        """Populates the interface filter with a list of strings."""
        self.interface_filter.blockSignals(True)
        current_data = self.interface_filter.currentData()
        self.interface_filter.clear()
        
        self.interface_filter.addItem(getattr(self.i18n, 'ALL_INTERFACES_AGGREGATED_LABEL', 'All Interfaces'), "all")
        
        if distinct_interfaces:
            for iface in sorted(distinct_interfaces):
                self.interface_filter.addItem(iface, iface)
        
        # Restore selection
        idx = self.interface_filter.findData(current_data)
        if idx != -1:
            self.interface_filter.setCurrentIndex(idx)
        
        self.interface_filter.blockSignals(False)

    def _days_to_slider_value(self, days):
        """Helper to find the slider index for a given number of days."""
        # constants.data.retention.DAYS_MAP is {index: days}
        # Invert it
        for idx, d in constants.data.retention.DAYS_MAP.items():
            if d == days:
                return idx
        return 3 # Default to 30 days (index 3 usually) if not found
