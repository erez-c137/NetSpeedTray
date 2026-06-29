"""
General Settings Page.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QComboBox, QLabel, QGridLayout, QSizePolicy

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.utils.components import Win11Slider, Win11Toggle

class GeneralPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Language Group ---
        language_group = QGroupBox(self.i18n.LANGUAGE_LABEL)
        language_layout = QVBoxLayout(language_group)
        self.language_combo = QComboBox()
        # Don't let a long item dictate the combo's minimum width (which would
        # push the page past the scroll viewport and add a horizontal scrollbar).
        # The layout stretches the combo to fill the available width regardless.
        self.language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

        for code, name in self.i18n.LANGUAGE_MAP.items():
            self.language_combo.addItem(name, userData=code)
        
        # Connect change signal
        self.language_combo.currentIndexChanged.connect(self.on_change)
        
        language_layout.addWidget(self.language_combo)
        layout.addWidget(language_group)

        # --- Update Rate Group ---
        update_group = QGroupBox(self.i18n.UPDATE_RATE_GROUP_TITLE)
        update_group_layout = QVBoxLayout(update_group)
        update_group_layout.setSpacing(8)
        self.update_rate = Win11Slider(editable=False)
        
        # Slider range: 0-4 = 5 discrete presets mapped to update modes
        # 0=SMART, 1=FAST, 2=BALANCED, 3=EFFICIENT, 4=POWER_SAVER
        self.update_rate.setRange(0, 4)
        self.update_rate.setSingleStep(1)
        
        # Connect change signal: update textual label while dragging and propagate change
        self.update_rate.valueChanged.connect(self._on_update_rate_changed)

        update_group_layout.addWidget(QLabel(self.i18n.UPDATE_INTERVAL_LABEL))
        update_group_layout.addWidget(self.update_rate)
        layout.addWidget(update_group)

        # --- Behavior Group (Toggles + Tray Offset) ---
        behavior_group = QGroupBox(self.i18n.BEHAVIOR_GROUP_TITLE)
        behavior_layout = QGridLayout(behavior_group)
        behavior_layout.setVerticalSpacing(10)
        behavior_layout.setHorizontalSpacing(8)

        sww_label = QLabel(self.i18n.START_WITH_WINDOWS_LABEL)
        self.start_with_windows = Win11Toggle(label_text="")
        self.start_with_windows.toggled.connect(self.on_change)

        behavior_layout.addWidget(sww_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        behavior_layout.addWidget(self.start_with_windows, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        fm_label = QLabel(self.i18n.FREE_MOVE_LABEL)
        self.free_move = Win11Toggle(label_text="")
        self.free_move.toggled.connect(self.on_change)

        behavior_layout.addWidget(fm_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
        behavior_layout.addWidget(self.free_move, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        kvf_label = QLabel(self.i18n.KEEP_VISIBLE_FULLSCREEN_LABEL)
        self.keep_visible_fullscreen = Win11Toggle(label_text="")
        self.keep_visible_fullscreen.toggled.connect(self.on_change)

        behavior_layout.addWidget(kvf_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
        behavior_layout.addWidget(self.keep_visible_fullscreen, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Tray Offset (absorbed from Display page)
        behavior_layout.addWidget(QLabel(self.i18n.TRAY_OFFSET_LABEL), 3, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tray_offset = Win11Slider()
        self.tray_offset.setRange(-50, 50)
        self.tray_offset.setInvertedAppearance(True)
        self.tray_offset.valueChanged.connect(self.on_change)
        behavior_layout.addWidget(self.tray_offset, 3, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        behavior_layout.addWidget(QLabel("Double Click"), 4, 0, Qt.AlignmentFlag.AlignVCenter)
        self.double_click_action = QComboBox()
        self._populate_click_action_combo(self.double_click_action)
        self.double_click_action.currentIndexChanged.connect(self.on_change)
        behavior_layout.addWidget(self.double_click_action, 4, 1, Qt.AlignmentFlag.AlignVCenter)

        behavior_layout.addWidget(QLabel("Middle Click"), 5, 0, Qt.AlignmentFlag.AlignVCenter)
        self.middle_click_action = QComboBox()
        self._populate_click_action_combo(self.middle_click_action)
        self.middle_click_action.currentIndexChanged.connect(self.on_change)
        behavior_layout.addWidget(self.middle_click_action, 5, 1, Qt.AlignmentFlag.AlignVCenter)

        cfu_label = QLabel(self.i18n.CHECK_FOR_UPDATES_LABEL)
        self.check_for_updates = Win11Toggle(label_text="")
        self.check_for_updates.toggled.connect(self.on_change)

        behavior_layout.addWidget(cfu_label, 6, 0, Qt.AlignmentFlag.AlignVCenter)
        behavior_layout.addWidget(self.check_for_updates, 6, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Preferred Monitor (#72) — lets users pin the widget to a specific
        # display in multi-monitor setups. Default (no selection) uses primary.
        behavior_layout.addWidget(QLabel(self.i18n.PREFERRED_MONITOR_LABEL), 7, 0, Qt.AlignmentFlag.AlignVCenter)
        self.preferred_monitor_combo = QComboBox()
        # Keep a long monitor label (e.g. "Monitor 1: 3413x1440 (primary)") from
        # inflating the combo's *minimum* width (which would force a horizontal
        # scrollbar), but let it expand to fill the column instead of collapsing
        # to a tiny stub — so drop the AlignLeft and give it an Expanding policy.
        self.preferred_monitor_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preferred_monitor_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._populate_monitor_combo()
        self.preferred_monitor_combo.currentIndexChanged.connect(self.on_change)
        behavior_layout.addWidget(self.preferred_monitor_combo, 7, 1, Qt.AlignmentFlag.AlignVCenter)

        behavior_layout.setColumnStretch(0, 0)
        behavior_layout.setColumnStretch(1, 1)
        layout.addWidget(behavior_group)

        layout.addStretch()

    def _populate_click_action_combo(self, combo: QComboBox) -> None:
        combo.addItem("Show Graph", constants.config.defaults.CLICK_ACTION_SHOW_GRAPH)
        combo.addItem("Show App Activity", constants.config.defaults.CLICK_ACTION_SHOW_APP_ACTIVITY)
        combo.addItem("Settings", constants.config.defaults.CLICK_ACTION_SETTINGS)

    def _populate_monitor_combo(self) -> None:
        """Fill the preferred-monitor dropdown with detected screens.

        First entry is always "Primary (auto)" mapped to None. Subsequent
        entries are real QScreens labeled with their resolution. We store
        the screen's name() as userData so reselection survives reboots,
        while showing a friendlier label.

        Called on construction AND on every showEvent so users who plug in
        a monitor after the app started still see it in the dropdown.
        """
        # Remember the current selection so we can preserve it across refresh.
        previous_selection = self.preferred_monitor_combo.currentData() if self.preferred_monitor_combo.count() else None

        # Temporarily disconnect to avoid firing on_change during the rebuild.
        try:
            self.preferred_monitor_combo.currentIndexChanged.disconnect(self.on_change)
        except (TypeError, RuntimeError):
            pass

        self.preferred_monitor_combo.clear()
        self.preferred_monitor_combo.addItem(self.i18n.PREFERRED_MONITOR_PRIMARY, userData=None)

        app = QGuiApplication.instance()
        if app is not None:
            primary = app.primaryScreen()
            for i, screen in enumerate(app.screens(), start=1):
                geom = screen.geometry()
                tag = " (primary)" if screen is primary else ""
                label = f"Monitor {i}: {geom.width()}x{geom.height()}{tag}"
                self.preferred_monitor_combo.addItem(label, userData=screen.name())

        # Restore previous selection if it still exists; otherwise fall back to "Primary (auto)".
        if previous_selection is not None:
            idx = self.preferred_monitor_combo.findData(previous_selection)
            if idx >= 0:
                self.preferred_monitor_combo.setCurrentIndex(idx)

        # Reconnect the change signal.
        self.preferred_monitor_combo.currentIndexChanged.connect(self.on_change)

    def showEvent(self, event):  # type: ignore[override]
        """Refresh the monitor dropdown whenever the page becomes visible.

        Catches the case where a user plugs/unplugs a monitor while the app
        is running, then opens Settings — the dropdown should reflect the
        current monitor topology, not the topology at app launch.
        """
        super().showEvent(event)
        if hasattr(self, "preferred_monitor_combo"):
            self._populate_monitor_combo()

    def load_settings(self, config: Dict[str, Any], is_startup_enabled: bool):
        # Language
        current_lang = config.get("language", "en")
        index = self.language_combo.findData(current_lang)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

        # Update Rate - Map config value to slider position (0-4)
        raw_rate = float(config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE))
        
        # Convert to slider position
        slider_position = self._rate_to_slider_position(raw_rate)
        self.update_rate.setValue(slider_position)
        self.update_rate.setValueText(self._format_update_rate_label(slider_position))
        
        # Other toggles
        self.start_with_windows.setChecked(is_startup_enabled)
        self.free_move.setChecked(config.get("free_move", False))
        self.keep_visible_fullscreen.setChecked(config.get("keep_visible_fullscreen", constants.config.defaults.DEFAULT_KEEP_VISIBLE_FULLSCREEN))
        self.check_for_updates.setChecked(config.get("check_for_updates", True))

        # Tray Offset
        self.tray_offset.setValue(config.get("tray_offset_x", 0))

        double_click_action = config.get("double_click_action", constants.config.defaults.DEFAULT_DOUBLE_CLICK_ACTION)
        idx = self.double_click_action.findData(double_click_action)
        self.double_click_action.setCurrentIndex(idx if idx >= 0 else 0)

        middle_click_action = config.get("middle_click_action", constants.config.defaults.DEFAULT_MIDDLE_CLICK_ACTION)
        idx = self.middle_click_action.findData(middle_click_action)
        self.middle_click_action.setCurrentIndex(idx if idx >= 0 else 2)

        # Preferred monitor (#72) — match by stored screen name.
        # If the saved monitor name doesn't match any detected screen (e.g.,
        # monitor was disconnected since last save), the combo just stays at
        # "Primary (auto)" and the runtime fallback in position_manager kicks in.
        preferred_name = config.get("preferred_monitor")
        idx = self.preferred_monitor_combo.findData(preferred_name)
        self.preferred_monitor_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def get_settings(self) -> Dict[str, Any]:
        # Get slider position and convert to update_rate value
        slider_position = self.update_rate.value()
        update_rate_value = self._slider_position_to_rate(slider_position)

        return {
            "language": self.language_combo.currentData(),
            "update_rate": update_rate_value,
            "free_move": self.free_move.isChecked(),
            "keep_visible_fullscreen": self.keep_visible_fullscreen.isChecked(),
            "start_with_windows": self.start_with_windows.isChecked(),
            "tray_offset_x": self.tray_offset.value(),
            "double_click_action": self.double_click_action.currentData(),
            "middle_click_action": self.middle_click_action.currentData(),
            "check_for_updates": self.check_for_updates.isChecked(),
            "preferred_monitor": self.preferred_monitor_combo.currentData(),
        }

    def _on_update_rate_changed(self, value: int) -> None:
        """Update the slider textual label in real time and propagate change."""
        try:
            label = self._format_update_rate_label(value)
            self.update_rate.setValueText(label)
        except Exception:
            pass
        # Propagate change notification
        self.on_change()
    
    def _format_update_rate_label(self, slider_position: int) -> str:
        """Format slider position as a human-readable label."""
        # Map slider position (0-4) to update mode label
        position_to_mode = {
            0: self.i18n.SMART_MODE_LABEL,
            1: f"{int(UpdateMode.AGGRESSIVE)}s" if not hasattr(self.i18n, 'UPDATE_MODE_AGGRESSIVE_LABEL') else self.i18n.UPDATE_MODE_AGGRESSIVE_LABEL,
            2: f"{int(UpdateMode.BALANCED)}s" if not hasattr(self.i18n, 'UPDATE_MODE_BALANCED_LABEL') else self.i18n.UPDATE_MODE_BALANCED_LABEL,
            3: f"{int(UpdateMode.EFFICIENT)}s" if not hasattr(self.i18n, 'UPDATE_MODE_EFFICIENT_LABEL') else self.i18n.UPDATE_MODE_EFFICIENT_LABEL,
            4: f"{int(UpdateMode.POWER_SAVER)}s" if not hasattr(self.i18n, 'UPDATE_MODE_POWER_SAVER_LABEL') else self.i18n.UPDATE_MODE_POWER_SAVER_LABEL,
        }
        return position_to_mode.get(slider_position, "Unknown")

    def _rate_to_slider_position(self, update_rate: float) -> int:
        """Convert update_rate value to slider position (0-4)."""
        # Map update rates to slider positions
        if update_rate < 0:  # SMART sentinel
            return 0
        elif abs(update_rate - UpdateMode.AGGRESSIVE) < 0.1:
            return 1
        elif abs(update_rate - UpdateMode.BALANCED) < 0.1:
            return 2
        elif abs(update_rate - UpdateMode.EFFICIENT) < 0.1:
            return 3
        elif abs(update_rate - UpdateMode.POWER_SAVER) < 0.1:
            return 4
        else:
            # Default to BALANCED if unrecognized
            return 2

    def _slider_position_to_rate(self, slider_position: int) -> float:
        """Convert slider position (0-4) to update_rate value."""
        position_to_rate = {
            0: UpdateMode.SMART,        # -1.0
            1: UpdateMode.AGGRESSIVE,   # 1.0
            2: UpdateMode.BALANCED,     # 2.0
            3: UpdateMode.EFFICIENT,    # 5.0
            4: UpdateMode.POWER_SAVER,  # 10.0
        }
        return position_to_rate.get(slider_position, UpdateMode.BALANCED)

