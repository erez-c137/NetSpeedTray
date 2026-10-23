"""
General Settings Page.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtWidgets import QWidget, QComboBox

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.utils.timer_utils import snap_update_rate
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
        # section captions - replaces the old QGroupBox frames. All control objects + the load/get
        # wiring below are unchanged; only the containers became cards.
        layout = page_layout(self)

        # --- Language ---
        self.language_combo = Win11ComboBox()
        self.language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.language_combo.setMinimumWidth(220)
        # First row = "Auto-detect (system)" (userData=None), so the default config `language: None`
        # round-trips. Without it, None couldn't be selected, the combo sat on row 0 (en_US), and
        # clicking Save without ever touching the dropdown silently switched non-English users to
        # English + popped a spurious "restart to apply language" dialog (#2).
        self.language_combo.addItem(
            getattr(self.i18n, "LANGUAGE_AUTO_DETECT", "Auto-detect (system)"), userData=None)
        for code, name in self.i18n.LANGUAGE_MAP.items():
            self.language_combo.addItem(name, userData=code)
        # Qt defaults to 10 visible rows; we ship 14 languages plus the auto-detect row, so the
        # last entries sat below the fold with no wheel scrolling in the popup (#237).
        self.language_combo.setMaxVisibleItems(self.language_combo.count())
        self.language_combo.currentIndexChanged.connect(self.on_change)

        # Name what auto-detect resolved to. Without it the row is unfalsifiable - a user whose
        # language silently failed to resolve sees a correct-looking "Auto-detect (system)" while
        # the app runs in English, which is exactly how #234 hid for a year.
        #
        # It goes in the card's description, NOT the combo row: the combo is pinned to 220px and
        # never grows to fit (AdjustToMinimumContentsLengthWithIcon with minimumContentsLength 0),
        # and the closed label is drawn via drawItemText, which hard-clips without even an
        # ellipsis. A composed row label measured 420-684px against ~178px of usable width, so it
        # was invisible in all 13 locales. The description word-wraps at full card width.
        detected_note = ""
        try:
            resolved_name = self.i18n.LANGUAGE_MAP.get(self.i18n.detect_system_language())
            if resolved_name:
                detected_note = getattr(
                    self.i18n, "LANGUAGE_DETECTED_NOTE", "Detected: {language}"
                ).format(language=resolved_name)
        except Exception:
            pass  # cosmetic only; never block the settings page over a label
        layout.addWidget(SettingCard(self.i18n.LANGUAGE_LABEL, description=detected_note,
                                     control=self.language_combo))

        # --- Update Rate ---
        layout.addWidget(section_header(self.i18n.UPDATE_RATE_GROUP_TITLE))
        # One slider position per preset: 0..3 = 1 / 2 / 5 / 10 s (UpdateMode.PRESETS order).
        self.update_rate = Win11Slider(editable=False)
        self.update_rate.setRange(0, len(UpdateMode.PRESETS) - 1)
        self.update_rate.setSingleStep(1)
        self.update_rate.setMinimumWidth(260)
        self.update_rate.valueChanged.connect(self._on_update_rate_changed)
        layout.addWidget(SettingCard(self.i18n.UPDATE_INTERVAL_LABEL, control=self.update_rate))

        # --- Behavior ---
        # NOTE (2.0 IA): free-move, keep-visible-in-fullscreen and tray-offset moved to the new Widget
        # page (they're about the on-taskbar widget, not the app). General keeps app-level behavior.
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        self.start_with_windows = Win11Toggle(label_text="")
        self.start_with_windows.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.START_WITH_WINDOWS_LABEL, control=self.start_with_windows))

        self.check_for_updates = Win11Toggle(label_text="")
        self.check_for_updates.toggled.connect(self.on_change)
        # IT's DisableUpdateCheck policy (U1) locks the toggle off and says why. The user's own
        # choice is kept untouched underneath (see get_settings), so lifting the policy restores it.
        # The card always carries the "managed" line; _apply_update_policy_lock shows it only while
        # the policy is set, and runs again on every load_settings (the dialog is cached).
        self._updates_policy_locked = False
        self._saved_check_for_updates = True
        self._updates_card = SettingCard(
            self.i18n.CHECK_FOR_UPDATES_LABEL,
            description=getattr(self.i18n, "UPDATE_CHECK_POLICY_MANAGED", "Managed by your organization"),
            control=self.check_for_updates)
        layout.addWidget(self._updates_card)
        self._apply_update_policy_lock()

        # (Preferred Monitor moved to the Widget page's Behavior section, 2.2 IA pass: it decides which
        # taskbar the widget docks to, and its dependent "Float on a display without a taskbar" is there.)

        # --- Interaction (community PR #165, @rami123) ---
        # What a double-click / middle-click on the taskbar widget does. Adapted to the 2.0 action
        # set (the old Graph / App-Activity windows are now the unified Monitor).
        layout.addWidget(section_header(getattr(self.i18n, "INTERACTION_GROUP_TITLE", "Interaction")))

        self.double_click_action = Win11ComboBox()
        self.double_click_action.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.double_click_action.setMinimumWidth(220)
        self._populate_click_action_combo(self.double_click_action)
        self.double_click_action.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(
            getattr(self.i18n, "DOUBLE_CLICK_ACTION_LABEL", "Double-click action"),
            control=self.double_click_action))

        self.middle_click_action = Win11ComboBox()
        self.middle_click_action.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.middle_click_action.setMinimumWidth(220)
        self._populate_click_action_combo(self.middle_click_action)
        self.middle_click_action.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(
            getattr(self.i18n, "MIDDLE_CLICK_ACTION_LABEL", "Middle-click action"),
            control=self.middle_click_action))

        # Moved from Advanced > Behavior (2.2 IA pass, change 3): everyday widget-interaction toggles,
        # not low-frequency / destructive ones. Pause-in-menu first - it is the right-click route to the
        # same "Pause / Resume" the two click lists above offer - then the hover card's two toggles.
        self.pause_in_menu = Win11Toggle(label_text="")
        self.pause_in_menu.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.PAUSE_IN_MENU_LABEL, control=self.pause_in_menu))

        self.show_usage_hover = Win11Toggle(label_text="")
        self.show_usage_hover.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HOVER_USAGE_CARD_LABEL, control=self.show_usage_hover))

        # Battery Hover Card (#323): the hover card's power section, opt-in.
        self.power_display = Win11Toggle(label_text="")
        self.power_display.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.POWER_HOVER_CARD_LABEL, control=self.power_display))

        self.show_hover_tips = Win11Toggle(label_text="")
        self.show_hover_tips.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HOVER_TIPS_LABEL, control=self.show_hover_tips))

        layout.addStretch()

    def _populate_click_action_combo(self, combo: Win11ComboBox) -> None:
        """Fill a click-action dropdown with the 2.0 action set (value stored as userData)."""
        defaults = constants.config.defaults
        actions = [
            (defaults.CLICK_ACTION_OPEN_MONITOR, getattr(self.i18n, "CLICK_ACTION_OPEN_MONITOR_LABEL", "Open Monitor")),
            (defaults.CLICK_ACTION_SETTINGS, getattr(self.i18n, "CLICK_ACTION_SETTINGS_LABEL", "Open Settings")),
            (defaults.CLICK_ACTION_PAUSE, getattr(self.i18n, "CLICK_ACTION_PAUSE_LABEL", "Pause / Resume")),
            (defaults.CLICK_ACTION_NONE, getattr(self.i18n, "CLICK_ACTION_NONE_LABEL", "Nothing")),
        ]
        for value, label in actions:
            combo.addItem(label, userData=value)

    def load_settings(self, config: Dict[str, Any], is_startup_enabled: bool):
        # Language - None means auto-detect (the first combo row); a real code selects its own row.
        current_lang = config.get("language")
        if current_lang is None:
            self.language_combo.setCurrentIndex(0)   # "Auto-detect (system)"
        else:
            index = self.language_combo.findData(current_lang)
            if index >= 0:
                self.language_combo.setCurrentIndex(index)

        # Update Rate - map the config value to its slider position (an off-preset value snaps to
        # the nearest preset, the same rule the config migration applies).
        slider_position = self._rate_to_slider_position(
            config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE))
        self.update_rate.setValue(slider_position)
        self.update_rate.setValueText(self._format_update_rate_label(slider_position))
        
        # Other toggles
        self.start_with_windows.setChecked(is_startup_enabled)
        self._apply_update_policy_lock()   # a policy added or removed since the dialog was built
        self._saved_check_for_updates = bool(config.get("check_for_updates", True))
        self.check_for_updates.setChecked(
            False if self._updates_policy_locked else self._saved_check_for_updates)
        # free_move / keep_visible_fullscreen / tray_offset_x are loaded by the Widget page now.
        # preferred_monitor is loaded by the Widget page now (2.2 IA pass).

        # Click actions (#165) - select the saved action, falling back to the first row.
        defaults = constants.config.defaults
        self._select_click_action(
            self.double_click_action, config.get("double_click_action", defaults.DEFAULT_DOUBLE_CLICK_ACTION))
        self._select_click_action(
            self.middle_click_action, config.get("middle_click_action", defaults.DEFAULT_MIDDLE_CLICK_ACTION))
        self.pause_in_menu.setChecked(bool(config.get("pause_in_menu", False)))
        self.show_usage_hover.setChecked(bool(config.get("show_usage_on_hover", True)))
        self.power_display.setChecked(bool(config.get("power_display_enabled", False)))
        self.show_hover_tips.setChecked(bool(config.get("show_hover_tips", True)))

    def _apply_update_policy_lock(self) -> None:
        """Re-read the DisableUpdateCheck policy (U1) and lock or unlock the toggle to match.
        setEnabled alone won't dim our fixed-color toggle (HardwarePage._set_card_enabled), so the
        toggle - only the toggle, so the "managed" line stays readable - gets an opacity effect."""
        from netspeedtray.core.update_checker import update_check_disabled_by_policy
        locked = update_check_disabled_by_policy()
        self._updates_policy_locked = locked
        self.check_for_updates.setEnabled(not locked)
        if locked:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            dim = QGraphicsOpacityEffect(self.check_for_updates)
            dim.setOpacity(0.4)
            self.check_for_updates.setGraphicsEffect(dim)
        else:
            self.check_for_updates.setGraphicsEffect(None)
        self._updates_card.setDescriptionVisible(locked)

    def _select_click_action(self, combo: Win11ComboBox, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def get_settings(self) -> Dict[str, Any]:
        # Get slider position and convert to update_rate value
        slider_position = self.update_rate.value()
        update_rate_value = self._slider_position_to_rate(slider_position)

        return {
            "language": self.language_combo.currentData(),
            "update_rate": update_rate_value,
            "start_with_windows": self.start_with_windows.isChecked(),
            # Under the policy the toggle shows "off" but the user's own setting is written back as-is.
            "check_for_updates": (self._saved_check_for_updates if self._updates_policy_locked
                                  else self.check_for_updates.isChecked()),
            "double_click_action": self.double_click_action.currentData(),
            "middle_click_action": self.middle_click_action.currentData(),
            "pause_in_menu": self.pause_in_menu.isChecked(),
            "show_usage_on_hover": self.show_usage_hover.isChecked(),
            "power_display_enabled": self.power_display.isChecked(),
            "show_hover_tips": self.show_hover_tips.isChecked(),
            # free_move / keep_visible_fullscreen / tray_offset_x (2.0 IA) and preferred_monitor
            # (2.2 IA pass) now live on the Widget page.
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
        keys = ("UPDATE_MODE_AGGRESSIVE_LABEL", "UPDATE_MODE_BALANCED_LABEL",
                "UPDATE_MODE_EFFICIENT_LABEL", "UPDATE_MODE_POWER_SAVER_LABEL")
        if not 0 <= slider_position < len(UpdateMode.PRESETS):
            return "Unknown"
        return getattr(self.i18n, keys[slider_position], f"{int(UpdateMode.PRESETS[slider_position])}s")

    def _rate_to_slider_position(self, update_rate: object) -> int:
        """Convert an update_rate value to its slider position (0-3), snapping to the nearest preset."""
        return UpdateMode.PRESETS.index(snap_update_rate(update_rate))

    def _slider_position_to_rate(self, slider_position: int) -> float:
        """Convert a slider position (0-3) to its update_rate preset in seconds."""
        if 0 <= slider_position < len(UpdateMode.PRESETS):
            return UpdateMode.PRESETS[slider_position]
        return constants.config.defaults.DEFAULT_UPDATE_RATE
