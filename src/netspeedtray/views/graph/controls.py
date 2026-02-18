
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QLabel, QComboBox, 
    QPushButton, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from netspeedtray import constants
from netspeedtray.utils import styles
from netspeedtray.utils.components import Win11Slider, Win11Toggle


class TimelinePills(QWidget):
    """
    Segmented control for timeline selection.
    Replaces the slider with instant-access buttons (Fitts's Law optimization).
    """
    
    timeline_changed = pyqtSignal(str)  # Emits period key directly
    
    # 6-pill design: SESS, BOOT, 24H, WEEK, MONTH, ALL
    PERIODS = [
        ("SESS", "TIMELINE_SESSION"),
        ("BOOT", "TIMELINE_SYSTEM_UPTIME"),
        ("24H", "TIMELINE_24_HOURS"),
        ("WEEK", "TIMELINE_WEEK"),
        ("MONTH", "TIMELINE_MONTH"),
        ("ALL", "TIMELINE_ALL"),
    ]
    
    def __init__(self, initial_period_key: str = "TIMELINE_24_HOURS", parent=None):
        super().__init__(parent)
        self.setObjectName("timelinePills")
        
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._buttons = {}
        for i, (label, key) in enumerate(self.PERIODS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("period_key", key)
            
            # Explicit object names for CSS targeting (Qt selectors like :first-child are unreliable)
            if i == 0:
                btn.setObjectName("pillFirst")
            elif i == len(self.PERIODS) - 1:
                btn.setObjectName("pillLast")
            else:
                btn.setObjectName("pillMid")
                
            btn.clicked.connect(self._on_pill_clicked)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            
            # Add with stretch=1 to ensure all pills are equal width
            layout.addWidget(btn, 1)
            self._buttons[key] = btn

        
        # Apply styling
        self.setStyleSheet(styles.timeline_pills_style())
        
        # Set initial selection
        if initial_period_key in self._buttons:
            self._buttons[initial_period_key].setChecked(True)
        else:
            self._buttons["TIMELINE_24_HOURS"].setChecked(True)
    
    def _on_pill_clicked(self):
        sender = self.sender()
        key = sender.property("period_key")
        
        # Exclusive selection
        for btn in self._buttons.values():
            btn.setChecked(btn == sender)
        
        self.timeline_changed.emit(key)
    
    def set_period(self, period_key: str):
        """Programmatically set the active pill without emitting signal."""
        print(f"DEBUG: TimelinePills.set_period('{period_key}') called.")
        found = False
        if period_key in self._buttons:
            found = True
            for btn in self._buttons.values():
                is_target = (btn == self._buttons[period_key])
                btn.blockSignals(True)
                btn.setChecked(is_target)
                print(f"DEBUG: Setting {btn.text()} checked={is_target}")
                btn.blockSignals(False)
        else:
            print(f"DEBUG: period_key '{period_key}' NOT FOUND in buttons: {list(self._buttons.keys())}")
    
    def current_period_key(self) -> str:
        """Returns the currently selected period key."""
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return "TIMELINE_24_HOURS"


class GraphSettingsPanel(QWidget):
    """
    The settings panel overlay for the Graph Window.
    Provides controls for History Period, Data Retention, Dark Mode, etc.
    """
    
    # Signals for changes
    interface_filter_changed = pyqtSignal(str)
    timeline_changed = pyqtSignal(str)        # NEW: Emits period key directly
    retention_changed = pyqtSignal(int)        # Released (Update Config)
    retention_changing = pyqtSignal(int)       # Dragging (Update Text)
    dark_mode_toggled = pyqtSignal(bool)
    live_update_toggled = pyqtSignal(bool)
    show_loading_toggled = pyqtSignal(bool)
    
    # DEPRECATED: Kept for backwards compatibility during transition
    history_period_changed = pyqtSignal(int)
    history_period_changing = pyqtSignal(int)
    
    def __init__(self, parent=None, i18n=None, initial_state=None):
        super().__init__(parent)
        self.i18n = i18n
        self.initial_state = initial_state or {}
        
        # State placeholders
        self.interface_filter = None
        self.timeline_pills = None  # NEW: Replaces history_period_slider
        self.history_period_slider = None  # DEPRECATED: Kept for compatibility
        self.keep_data_slider = None
        self.dark_mode_toggle = None
        self.realtime_toggle = None
        self.show_loading_toggle = None
        
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
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setVerticalSpacing(10) # Reduced from 15 for better density
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

        # 2. Timeline Pills (replaces History Period Slider)
        timeline_label = QLabel(getattr(self.i18n, 'HISTORY_PERIOD_LABEL_NO_VALUE', 'Timeline'))
        initial_period_key = self._get_initial_period_key()
        self.timeline_pills = TimelinePills(initial_period_key=initial_period_key)
        
        group_layout.addWidget(timeline_label, current_row, 0, 1, 2)
        current_row += 1
        group_layout.addWidget(self.timeline_pills, current_row, 0, 1, 2)
        current_row += 1

        # 3. Data Retention Slider
        self.retention_label = QLabel(getattr(self.i18n, 'DATA_RETENTION_LABEL_NO_VALUE', 'Data Retention'))
        
        retention_days = self.initial_state.get('retention_days', 30)
        retention_val = self._days_to_slider_value(retention_days)
        self.keep_data_slider = Win11Slider(value=retention_val, editable=False, auto_update_label=False)
        self.keep_data_slider.setMinimumWidth(280)
        
        if hasattr(self.keep_data_slider, 'slider'):
             self.keep_data_slider.slider.setMinimum(0)
             self.keep_data_slider.slider.setMaximum(len(constants.data.retention.DAYS_MAP) - 1)
             
        group_layout.addWidget(self.retention_label, current_row, 0, 1, 2)
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
        
        # Show Loading Status (Hidden for now)
        # ls_label = QLabel(getattr(self.i18n, 'SHOW_LOADING_LABEL', 'Show Status'))
        show_loading = self.initial_state.get('show_loading', True)
        self.show_loading_toggle = Win11Toggle(initial_state=show_loading)
        # group_layout.addWidget(ls_label, current_row, 0)
        # group_layout.addWidget(self.show_loading_toggle, current_row, 1, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(controls_container, 1, 0, 1, 2)
        layout.setRowStretch(2, 1)
        
        self.setStyleSheet(styles.graph_settings_panel_style())
        
        # NEW: Initialize text with size if available
        retention_size = self.initial_state.get('db_size_mb')
        self.update_retention_text(retention_days, retention_size)

    def update_retention_text(self, days, db_size_mb=None):
        """Updates the retention value in the slider's integrated label."""
        text = self.i18n.DAYS_TEMPLATE.format(days=days)
        # Only show DB size when 365 days (ALL) is selected as requested
        if days >= 365 and db_size_mb is not None:
             # Rounded to nearest MB
             rounded_mb = int(round(db_size_mb))
             text += f" ({rounded_mb} {self.i18n.MB_UNIT})"
        
        if hasattr(self, 'keep_data_slider'):
            self.keep_data_slider.setValueText(text)

    def update_db_size_text(self, size_mb):
        """Convenience to update just size, keeping current days."""
        days = 30
        if hasattr(self, 'keep_data_slider'):
            val = self.keep_data_slider.value()
            days = constants.data.retention.DAYS_MAP.get(val, 30)
        self.update_retention_text(days, size_mb)

    def _get_initial_period_key(self) -> str:
        """Get the initial period key from initial_state."""
        # Try to get from stored period key first
        stored_key = self.initial_state.get('history_period_key')
        if stored_key:
            return stored_key
        
        # Fall back to slider value -> key conversion
        slider_val = self.initial_state.get('history_period_value', 2)  # Default to 24H (index 2)
        return constants.data.history_period.PERIOD_MAP.get(slider_val, "TIMELINE_24_HOURS")

    def _connect_signals(self):
        # Interface Filter
        self.interface_filter.currentTextChanged.connect(self._on_interface_changed)
        
        # Timeline Pills
        self.timeline_pills.timeline_changed.connect(self._on_timeline_changed)
        
        # Data Retention Slider
        if hasattr(self.keep_data_slider, 'slider'):
             self.keep_data_slider.slider.valueChanged.connect(self._on_retention_slider_changed)
             self.keep_data_slider.slider.sliderReleased.connect(self._on_retention_released)

        # Toggles
        self.dark_mode_toggle.toggled.connect(self.dark_mode_toggled.emit)
        self.realtime_toggle.toggled.connect(self.live_update_toggled.emit)
        self.show_loading_toggle.toggled.connect(self.show_loading_toggled.emit)
    
    def _on_timeline_changed(self, period_key: str):
        """Handle timeline pill selection."""
        self.timeline_changed.emit(period_key)
        
        # SENIOR FIX: The legacy signal 'history_period_changed' is now handled 
        # by emitting 'timeline_changed' and having the Window map the index.
        # This prevents double-update triggers from a single click.


    def _on_interface_changed(self, text):
        data = self.interface_filter.currentData()
        self.interface_filter_changed.emit(str(data))

    def _on_retention_slider_changed(self, val):
        days = constants.data.retention.DAYS_MAP.get(val, 30)
        self.retention_changing.emit(days)

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

