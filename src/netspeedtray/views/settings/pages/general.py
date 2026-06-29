"""
General Settings Page.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget, QComboBox

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.utils.components import Win11Slider, Win11Toggle, SettingCard, Win11ComboBox
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

class GeneralPage(QWidget):
    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: one Win11 Settings card per setting (title left, control right), grouped under light
        # section captions — replaces the old QGroupBox frames. All control objects + the load/get
        # wiring below are unchanged; only the containers became cards.
        layout = page_layout(self)

        # --- Language ---
        self.language_combo = Win11ComboBox()
        self.language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.language_combo.setMinimumWidth(220)
        for code, name in self.i18n.LANGUAGE_MAP.items():
            self.language_combo.addItem(name, userData=code)
        self.language_combo.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.LANGUAGE_LABEL, control=self.language_combo))

        # --- Update Rate ---
        layout.addWidget(section_header(self.i18n.UPDATE_RATE_GROUP_TITLE))
        # Slider range 0-4 = 5 discrete presets (0=SMART,1=FAST,2=BALANCED,3=EFFICIENT,4=POWER_SAVER).
        self.update_rate = Win11Slider(editable=False)
        self.update_rate.setRange(0, 4)
        self.update_rate.setSingleStep(1)
        self.update_rate.setMinimumWidth(260)
        self.update_rate.valueChanged.connect(self._on_update_rate_changed)
        layout.addWidget(SettingCard(self.i18n.UPDATE_INTERVAL_LABEL, control=self.update_rate))

        # --- Behavior ---
        # NOTE (2.0 IA): free-move, keep-visible-in-fullscreen and tray-offset moved to the new Widget
        # page (they're about the on-taskbar widget, not the app). General keeps app-level behaviour.
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        self.start_with_windows = Win11Toggle(label_text="")
        self.start_with_windows.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.START_WITH_WINDOWS_LABEL, control=self.start_with_windows))

        self.check_for_updates = Win11Toggle(label_text="")
        self.check_for_updates.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.CHECK_FOR_UPDATES_LABEL, control=self.check_for_updates))

        # Preferred Monitor (#72) — pin the widget to a specific display (default = primary).
        self.preferred_monitor_combo = Win11ComboBox()
        self.preferred_monitor_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preferred_monitor_combo.setMinimumWidth(220)
        self._populate_monitor_combo()
        self.preferred_monitor_combo.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.PREFERRED_MONITOR_LABEL, control=self.preferred_monitor_combo))

        layout.addStretch()

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
        self.check_for_updates.setChecked(config.get("check_for_updates", True))
        # free_move / keep_visible_fullscreen / tray_offset_x are loaded by the Widget page now.

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
            "start_with_windows": self.start_with_windows.isChecked(),
            "check_for_updates": self.check_for_updates.isChecked(),
            "preferred_monitor": self.preferred_monitor_combo.currentData(),
            # free_move / keep_visible_fullscreen / tray_offset_x now live on the Widget page (2.0 IA).
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

