"""
Settings Dialog Module for NetSpeedTray.

Provides the `SettingsDialog` class for configuring application settings with a modern,
Windows 11-inspired UI featuring a sidebar navigation and native-looking toggles/sliders.
Handles live updates to the parent widget via signals and throttling.
"""

from __future__ import annotations

from netspeedtray.core.controller import StatsController

import logging
import shutil
from typing import Any, Dict, List, Optional, TYPE_CHECKING

# Use TYPE_CHECKING to avoid circular import issues at runtime
if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QCloseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFileDialog, QFontDialog,
    QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget
)

# --- Custom Application Imports ---
from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.window_state import restore_window_position, save_window_position
from netspeedtray.utils.helpers import get_app_data_path, get_app_asset_path
from netspeedtray.utils.styles import is_dark_mode
from netspeedtray.utils.support_bundle import build_support_bundle
from netspeedtray.utils.dwm import apply_win11_chrome

# --- Settings Pages ---
from netspeedtray.views.settings.pages.units import UnitsPage
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.pages.interfaces import InterfacesPage
from netspeedtray.views.settings.pages.general import GeneralPage
from netspeedtray.views.settings.pages.appearance import AppearancePage
from netspeedtray.views.settings.pages.colors import ColorsPage
from netspeedtray.views.settings.pages.hardware import HardwarePage
from netspeedtray.views.settings.pages.advanced import AdvancedPage
from netspeedtray.views.widget.preview import PreviewWidget
from netspeedtray.constants.update_mode import UpdateMode


class SettingsDialog(QDialog):
    """
    Dialog window for configuring NetSpeedTray settings.

    Features sidebar navigation, live preview updates (throttled),
    and custom Win11-styled controls.
    """
    settings_changed = pyqtSignal(dict) #: Signal emitted when settings are changed (throttled).

    # Sidebar/stack page indices for deep-links (the tray "Hardware monitor" row jumps here).
    # Must stay in sync with the sidebar order in _setup_ui; test_settings_pages guards it so a
    # future page reshuffle trips a red test instead of silently opening the wrong page.
    PAGE_HARDWARE: int = 3

    def __init__(
        self,
        main_widget: "NetworkSpeedWidget",
        config: Dict[str, Any],
        version: str,
        i18n: constants.I18nStrings,
        available_interfaces: Optional[List[str]] = None,
        is_startup_enabled: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Initializes the settings dialog.
        """
        super().__init__(parent)
        self.parent_widget = main_widget
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")
        self.logger.debug("Initializing SettingsDialog...")

        self.config = config.copy() # Work on a copy to allow cancellation
        self.original_config = config.copy() # Keep original for rollback on reject
        self.version = version
        self.i18n = i18n
        self.initial_language = self.i18n.language
        self.available_interfaces = available_interfaces or []
        self.startup_enabled_initial_state = is_startup_enabled
        self._user_chose_default_color = False

        self._ui_setup_done = False
        
        # Timer for throttling live setting updates
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(constants.ui.dialogs.THROTTLE_INTERVAL_MS)
        self._update_timer.timeout.connect(self._emit_settings_changed_throttled)

        self.setWindowTitle(f"{constants.app.APP_NAME} {self.i18n.SETTINGS_WINDOW_TITLE} v{self.version}")
        
        try:
            icon_filename = getattr(constants.app, 'ICON_FILENAME', 'NetSpeedTray.ico')
            icon_path = get_app_asset_path(icon_filename)
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
            else:
                self.logger.warning(f"Icon file not found at {icon_path}")
        except Exception as e:
            self.logger.error(f"Error setting window icon: {e}", exc_info=True)
            
        # Apply the main dialog style from our style engine
        self.setStyleSheet(style_utils.dialog_style())

        # --- Initialization Steps ---
        self.setup_ui()
        self._init_ui_state()
        self._connect_signals()

        # Auto-size the dialog to the widest page's content so settings never
        # need a horizontal scrollbar. We measure each page's sizeHint with the
        # real on-machine fonts/DPI (which differ from any hardcoded width), then
        # add the sidebar, content margins, and room for a vertical scrollbar.
        margin = constants.layout.MAIN_MARGIN
        content_w = 450
        try:
            # minimumSizeHint is the floor below which the page can't shrink
            # without forcing a horizontal scrollbar; size to the widest page's
            # floor so no page ever scrolls sideways.
            page_widths = [
                self.stack.widget(i).widget().minimumSizeHint().width()
                for i in range(self.stack.count())
                if self.stack.widget(i).widget() is not None
            ]
            if page_widths:
                content_w = max(page_widths)
        except Exception:
            pass
        # +40 leaves room for the vertical scrollbar plus a little slack so the
        # horizontal scrollbar stays hidden even with slightly wider translations.
        # The final width is bounded by the screen below, so this can't run away.
        desired_w = constants.layout.SIDEBAR_WIDTH + (2 * margin) + content_w + 40

        self.setMinimumSize(min(640, desired_w), 400)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            width = min(desired_w, avail.width() - 80)
            height = min(700, avail.height() - 80)
            self.resize(max(640, width), max(450, height))
            # Restore the last-used position (clamped to this screen); else center.
            if not restore_window_position(self, self.config, "settings_window_pos"):
                self.move(
                    avail.center().x() - self.width() // 2,
                    avail.center().y() - self.height() // 2
                )
        else:
            self.resize(max(640, desired_w), 700)

        # NOTE: unlike the Graph / App Activity windows, the Settings dialog does NOT
        # auto-save its position on move. During a live preview it mutates the live
        # config in memory (handle_settings_changed save_to_disk=False), so a debounced
        # move-save would flush those un-applied edits to disk — which Cancel wouldn't
        # undo. The dialog is always closed via Save/Cancel/X, whose handlers persist
        # the position AFTER the apply/revert, so the close-path save is leak-free.

        self.logger.debug("SettingsDialog initialization completed.")


    def setup_ui(self) -> None:
        """Creates and arranges all UI elements within the dialog."""
        try:
            main_layout = QHBoxLayout(self)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

            # --- Sidebar ---
            sidebar_container = QWidget()
            sidebar_container.setObjectName("sidebarContainer")
            sidebar_layout = QVBoxLayout(sidebar_container)
            sidebar_layout.setContentsMargins(0,0,0,0)
            self.sidebar = QListWidget()
            self.sidebar.setFixedWidth(constants.layout.SIDEBAR_WIDTH)
            self.sidebar.setMinimumWidth(180)
            self.sidebar.setStyleSheet(style_utils.sidebar_style())
            self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.sidebar.addItems([
                self.i18n.GENERAL_SETTINGS_GROUP,
                self.i18n.APPEARANCE_SETTINGS_GROUP,
                self.i18n.COLOR_CODING_GROUP,
                self.i18n.HARDWARE_MONITORING_GROUP,
                self.i18n.UNITS_GROUP,
                self.i18n.NETWORK_INTERFACES_GROUP,
                self.i18n.ADVANCED_SETTINGS_GROUP,
            ])
            self.sidebar.setCurrentRow(0)
            sidebar_layout.addWidget(self.sidebar)
            main_layout.addWidget(sidebar_container)

            # --- Content Area ---
            content_widget = QWidget()
            content_widget.setObjectName("contentWidget")
            content_layout = QVBoxLayout(content_widget)
            content_layout.setContentsMargins(
                constants.layout.MAIN_MARGIN, constants.layout.MAIN_MARGIN,
                constants.layout.MAIN_MARGIN, constants.layout.MAIN_MARGIN
            )
            content_layout.setSpacing(constants.layout.MAIN_SPACING)

            self.stack = QStackedWidget()
            content_layout.addWidget(self.stack)

            # Instantiate Pages
            self.general_page = GeneralPage(self.i18n, self._schedule_settings_update)
            self.appearance_page = AppearancePage(
                self.i18n,
                self._schedule_settings_update,
                self._open_font_dialog,
                self._open_color_dialog
            )

            self.colors_page = ColorsPage(
                self.i18n,
                self._schedule_settings_update,
                self._open_color_dialog
            )

            self.hardware_page = HardwarePage(self.i18n, self._schedule_settings_update)
            self.units_page = UnitsPage(self.i18n, self._schedule_settings_update)
            self.interfaces_page = InterfacesPage(
                self.i18n,
                self.available_interfaces,
                self._schedule_settings_update
            )
            self.advanced_page = AdvancedPage(
                self.i18n,
                self._schedule_settings_update,
                reset_page_callback=self._reset_advanced_page,
                reset_all_callback=self._reset_all_to_defaults,
            )

            # Add pages wrapped in scroll areas (order matches sidebar)
            for page in [
                self.general_page,       # 0 - General
                self.appearance_page,    # 1 - Appearance
                self.colors_page,        # 2 - Color Coding
                self.hardware_page,      # 3 - Hardware
                self.units_page,         # 4 - Display
                self.interfaces_page,    # 5 - Interfaces
                self.advanced_page,      # 6 - Advanced
            ]:
                self.stack.addWidget(self._wrap_in_scroll(page))

            # --- Live preview strip (C5) — a faithful, inert render of the widget that
            # reflects the current settings as you change them, on a taskbar-like backdrop.
            # Uses the shared PreviewWidget keystone, so it matches the real widget exactly.
            try:
                preview_strip = QWidget()
                preview_strip.setObjectName("previewStrip")
                ps_layout = QHBoxLayout(preview_strip)
                ps_layout.setContentsMargins(12, 6, 12, 6)
                # Label the strip so it reads as a live preview of the taskbar widget, not a status bar.
                ps_label = QLabel(self.i18n.SETTINGS_PREVIEW_LABEL)
                ps_label.setStyleSheet(
                    f"color: {style_utils.semantic_colors()['text_secondary']}; background: transparent;")
                ps_layout.addWidget(ps_label)
                ps_layout.addStretch(1)
                self.preview_widget = PreviewWidget(self.config, self.i18n, width=300, height=40)
                ps_layout.addWidget(self.preview_widget)
                ps_layout.addStretch(1)
                # A subtle taskbar-ish backdrop so the preview reads as "on the taskbar".
                c = "#2b2b2b" if is_dark_mode() else "#e8e8e8"
                preview_strip.setStyleSheet(f"#previewStrip {{ background: {c}; border-radius: 6px; }}")
                content_layout.addWidget(preview_strip)
            except Exception as e:
                self.logger.error("Could not build settings live-preview: %s", e, exc_info=True)
                self.preview_widget = None

            # --- Bottom Buttons (Support Bundle / Cancel / Save) ---
            button_layout = QHBoxLayout()
            self.export_log_button = QPushButton(self.i18n.EXPORT_SUPPORT_BUNDLE_BUTTON)
            self.export_log_button.setStyleSheet(style_utils.button_style())
            self.export_log_button.setToolTip(self.i18n.EXPORT_SUPPORT_BUNDLE_TOOLTIP)
            self.export_log_button.clicked.connect(self.export_support_bundle)
            button_layout.addWidget(self.export_log_button)
            button_layout.addStretch()
            self.cancel_button = QPushButton(self.i18n.CANCEL_BUTTON)
            self.cancel_button.setStyleSheet(style_utils.button_style())
            self.save_button = QPushButton(self.i18n.SAVE_BUTTON)
            self.save_button.setStyleSheet(style_utils.button_style(accent=True))
            self.save_button.setDefault(True)
            button_layout.addWidget(self.cancel_button)
            button_layout.addWidget(self.save_button)
            content_layout.addLayout(button_layout)

            content_widget.setMinimumWidth(300) 
            main_layout.addWidget(content_widget, stretch=1)

            self.sidebar.currentRowChanged.connect(self._on_sidebar_selection_changed)
            self.cancel_button.clicked.connect(self._cancel_and_close)
            self.save_button.clicked.connect(self._save_and_close)

            self._ui_setup_done = True

            self.logger.debug(f"UI setup completed. Stack has {self.stack.count()} pages.")
        except Exception as e:
            self.logger.error(f"Error setting up UI: {e}", exc_info=True)
            QMessageBox.critical(
                self, self.i18n.ERROR_TITLE,
                self.i18n.ERROR_UI_SETUP_FAILED.format(error=str(e))
            )
            QTimer.singleShot(0, self.reject)

    @staticmethod
    def _wrap_in_scroll(page: QWidget) -> QScrollArea:
        """Wraps a settings page in a frameless, transparent scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(page)
        return scroll

    def _init_ui_state(self) -> None:
        """Loads current configuration into all UI elements and restores window position."""
        self.logger.debug("Initializing UI state from config...")
        try:
            self.general_page.load_settings(self.config, self.startup_enabled_initial_state)
            self.appearance_page.load_settings(self.config)
            self.colors_page.load_settings(self.config)
            self.hardware_page.load_settings(self.config)
            self.units_page.load_settings(self.config)
            self.interfaces_page.load_settings(self.config)
            self.advanced_page.load_settings(self.config)

            # Seed the live preview from the loaded config.
            self._update_preview(self.config)

            # Restore window position via the shared helper so reopen is multi-monitor
            # safe and consistent with the first-open path. This runs on every reopen
            # (reset_with_config -> _init_ui_state), where the __init__ auto-size restore
            # does not. The old hand-rolled clamp used self.screen(), which reports the
            # primary screen for a not-yet-shown window and yanked secondary-monitor
            # positions back to primary.
            restore_window_position(self, self.config, "settings_window_pos")

        except Exception as e:
             self.logger.error(f"Error initializing UI state: {e}", exc_info=True)

    def _connect_signals(self) -> None:
        """Connects additional global signals."""
        self.appearance_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.interfaces_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.hardware_page.layout_changed.connect(self._adjust_size_and_reposition)
        # When Force MB toggle is changed, ensure SMART update_rate is not selected
        try:
            self.units_page.speed_display_mode.toggled.connect(self._on_force_mb_toggled)
        except Exception:
            # Defensive: if widget API differs, ignore
            pass

    def _on_force_mb_toggled(self, checked: bool) -> None:
        """When Force MB is toggled, ensure update_rate SMART is not selected when Force MB is OFF.

        If the toggle is turned OFF (auto mode) and the update rate slider is on SMART (position 0),
        move it to AGGRESSIVE (1s) to prevent jitter. This enforces the UI-level rule immediately
        so users can't save an incompatible combination.
        """
        try:
            # If turned off, ensure GeneralPage slider isn't set to SMART
            if not checked and hasattr(self, 'general_page'):
                try:
                    if self.general_page.update_rate.value() == 0:
                        self.general_page.update_rate.setValue(1)
                        self.general_page.update_rate.setValueText(self.general_page._format_update_rate_label(1))
                        # Propagate change immediately
                        self._schedule_settings_update()
                except Exception:
                    pass
        except Exception:
            pass

    def _on_sidebar_selection_changed(self, row: int) -> None:
        """Handles sidebar row changes to switch the stacked page."""
        self.stack.setCurrentIndex(row)

    def navigate_to_page(self, index: int) -> None:
        """Select a settings page by sidebar index (e.g. deep-link to Hardware). Selecting the
        sidebar row drives the stacked page via _on_sidebar_selection_changed."""
        try:
            if self.sidebar is not None and 0 <= index < self.sidebar.count():
                self.sidebar.setCurrentRow(index)
        except Exception as e:
            self.logger.debug("navigate_to_page(%s) failed: %s", index, e)

    def _adjust_size_and_reposition(self) -> None:
        """Ensures dialog stays within screen bounds after layout changes."""
        screen = self.screen()
        if not screen:
            return
        avail = screen.availableGeometry()
        geo = self.geometry()
        x = max(avail.left(), min(geo.x(), avail.right() - geo.width()))
        y = max(avail.top(), min(geo.y(), avail.bottom() - geo.height()))
        if x != geo.x() or y != geo.y():
            self.move(x, y)

    def _schedule_settings_update(self) -> None:
        """Starts the throttle timer to emit settings_changed."""
        if not self._ui_setup_done: return
        self._update_timer.start()

    def _emit_settings_changed_throttled(self) -> None:
        """Emits the settings_changed signal with the current configuration."""
        current_settings = self.get_settings()
        self._update_preview(current_settings)
        self.settings_changed.emit(current_settings)

    def _update_preview(self, settings: Dict[str, Any]) -> None:
        """Refresh the live-preview strip to reflect the current (merged) settings."""
        pv = getattr(self, "preview_widget", None)
        if pv is None:
            return
        try:
            pv.set_config({**self.config, **(settings or {})})
        except Exception as e:
            self.logger.debug("preview update failed: %s", e)

    def get_settings(self) -> Dict[str, Any]:
        """Collects settings from all pages."""
        try:
            settings = self.config.copy()
            settings.update(self.general_page.get_settings())
            settings.update(self.appearance_page.get_settings())
            settings.update(self.colors_page.get_settings())
            settings.update(self.hardware_page.get_settings())
            settings.update(self.units_page.get_settings())
            settings.update(self.interfaces_page.get_settings())
            settings.update(self.advanced_page.get_settings())

            # Save current window position
            settings["settings_window_pos"] = {"x": self.pos().x(), "y": self.pos().y()}
            
            # Re-implement color logic check:
            if self._user_chose_default_color:
                settings["color_is_automatic"] = False
            else:
                 settings["color_is_automatic"] = self.original_config.get("color_is_automatic", True)
                 
            # UI-level rule: if Force MB is OFF (speed_display_mode == 'auto') then SMART
            # adaptive update mode is not allowed because it causes rapid unit flips.
            # Enforce at UI collection time by forcing to 1s (AGGRESSIVE) when needed.
            if settings.get("speed_display_mode") == "auto" and float(settings.get("update_rate", 1.0)) <= 0:
                self.logger.info("Force MB is off and SMART was selected; forcing update_rate to %ss", UpdateMode.AGGRESSIVE)
                settings["update_rate"] = float(UpdateMode.AGGRESSIVE)

            return settings
        except Exception as e:
            self.logger.error(f"Error collecting settings: {e}", exc_info=True)
            return {}

    # --- Callbacks ---

    def _open_font_dialog(self, initial_font: QFont, target: str = "main") -> None:
        font, ok = QFontDialog.getFont(initial_font, self)
        if ok:
            if target == "main":
                self.appearance_page.set_font_family(font)
            else:
                self.appearance_page.set_arrow_font_family(font)

    def _open_color_dialog(self, key_name: str) -> None:
        # Get current color from the correct page to set initial state
        if key_name in ["high_speed_color", "low_speed_color"]:
            current_settings = self.colors_page.get_settings()
        else:
            current_settings = self.appearance_page.get_settings()
            
        initial_hex = current_settings.get(key_name, "#FFFFFF")
        
        color = QColorDialog.getColor(QColor(initial_hex), self, self.i18n.SELECT_COLOR_TITLE)
        if color.isValid():
            new_hex = color.name().upper()
            if key_name in ["high_speed_color", "low_speed_color"]:
                self.colors_page.set_color_input(key_name.replace("_color", ""), new_hex)
            else:
                self.appearance_page.set_color_input(key_name.replace("_color", ""), new_hex)
            
            if key_name == "default_color":
                self._user_chose_default_color = True
            self._schedule_settings_update()

    def export_support_bundle(self) -> None:
        """Exports a sanitized bundle (logs + config + system info) for bug reports."""
        self.logger.info("Support bundle export requested.")
        try:
            default_name = (
                f"NetSpeedTray_Support_"
                f"{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            )
            from pathlib import Path
            default_dir = Path.home() / "Desktop"
            default_path = str(default_dir / default_name) if default_dir.exists() else default_name

            dest_path, _ = QFileDialog.getSaveFileName(
                self,
                self.i18n.EXPORT_SUPPORT_BUNDLE_TITLE,
                default_path,
                "Zip files (*.zip);;All Files (*)",
            )

            if not dest_path:
                return

            written = build_support_bundle(
                destination_zip=Path(dest_path),
                config=self.config,
            )
            self.logger.info("Support bundle written to %s", written)
            QMessageBox.information(
                self,
                self.i18n.SUCCESS_TITLE,
                self.i18n.SUPPORT_BUNDLE_SUCCESS_MESSAGE.format(file_path=str(written)),
            )
        except Exception as e:
            self.logger.error("Failed to export support bundle: %s", e, exc_info=True)
            QMessageBox.critical(
                self,
                self.i18n.ERROR_TITLE,
                self.i18n.SUPPORT_BUNDLE_ERROR_MESSAGE.format(error=str(e)),
            )

    def update_interface_list(self, new_interfaces: List[str]) -> None:
        """Updates the list of available network interfaces."""
        self.available_interfaces = new_interfaces
        if hasattr(self, 'interfaces_page'):
            self.interfaces_page.update_interface_list(new_interfaces)
            # Re-apply current selection
            self.interfaces_page.load_settings(self.get_settings())

    def reset_with_config(self, config: Dict[str, Any], is_startup_enabled: bool) -> None:
        """Resets the UI state with a new configuration dictionary."""
        self.config = config.copy()
        self.original_config = config.copy()
        self.startup_enabled_initial_state = is_startup_enabled
        self._user_chose_default_color = False
        self._init_ui_state()

    def _reset_advanced_page(self) -> None:
        """Reset only the Advanced page's settings to their defaults (C6)."""
        defaults = constants.config.defaults.DEFAULT_CONFIG
        cfg = self.config.copy()
        for k in self.advanced_page.get_settings():
            if k in defaults:
                cfg[k] = defaults[k]
        self.advanced_page.load_settings(cfg)
        self._schedule_settings_update()

    def _reset_all_to_defaults(self) -> None:
        """Reset every setting to factory defaults (after confirmation); history is kept (C6)."""
        resp = QMessageBox.question(
            self, self.i18n.RESET_ALL_CONFIRM_TITLE,
            self.i18n.RESET_ALL_CONFIRM_TEXT,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        defaults = dict(constants.config.defaults.DEFAULT_CONFIG)
        # Preserve non-settings state a reset shouldn't wipe (window positions, onboarding flags).
        for k in ("position_x", "position_y", "settings_window_pos", "graph_window_pos",
                  "app_activity_window_pos", "monitor_window_pos", "first_run_ever",
                  "first_run_v2_seen", "tooltip_hint_shown_count", "temp_onboarding_dismissed",
                  "usage_alert_state", "skipped_version"):
            if k in self.config:
                defaults[k] = self.config[k]
        self.reset_with_config(defaults, self.startup_enabled_initial_state)
        self._schedule_settings_update()

    def _save_and_close(self) -> None:
        """Saves settings and closes."""
        self.logger.debug("Save and close requested.")
        try:
            final_settings = self.get_settings()
            if not final_settings:
                self.logger.warning("Could not retrieve settings from pages.")
                return

            selected_language = final_settings.get("language")
            language_changed = selected_language and (selected_language != self.initial_language)

            # Apply settings to the main widget/application
            if hasattr(self.parent_widget, 'handle_settings_changed'):
                self.parent_widget.handle_settings_changed(final_settings, save_to_disk=True)
            
            # Determine startup change
            requested_startup = final_settings.get("start_with_windows", False)
            if requested_startup != self.startup_enabled_initial_state:
                 if hasattr(self.parent_widget, 'toggle_startup'):
                     self.parent_widget.toggle_startup(requested_startup)

            if language_changed:
                QMessageBox.information(
                    self, self.i18n.LANGUAGE_RESTART_TITLE, 
                    self.i18n.LANGUAGE_RESTART_MESSAGE
                )
                
            # Persist the position after the settings save so it can't be clobbered
            # by the saved config snapshot.
            save_window_position(self, self.parent_widget, "settings_window_pos")
            self.hide()
            self.logger.info("Settings saved and dialog hidden.")
        except Exception as e:
            self.logger.error(f"Failed to save settings: {e}", exc_info=True)
            QMessageBox.critical(
                self, self.i18n.ERROR_TITLE,
                f"{self.i18n.SETTINGS_ERROR_MESSAGE}\n\n{str(e)}"
            )

    def _cancel_and_close(self) -> None:
        """Reverts settings, then persists the window position, then closes."""
        if hasattr(self.parent_widget, 'handle_settings_changed'):
            self.parent_widget.handle_settings_changed(self.original_config, save_to_disk=False)
        # Save the position AFTER the revert: handle_settings_changed merges
        # original_config back into the live config, which would otherwise clobber
        # settings_window_pos with the value from when the dialog opened.
        save_window_position(self, self.parent_widget, "settings_window_pos")
        self.hide()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Native Win11 chrome (dark title bar + rounded corners), applied at
        # show-time so the native handle exists. Fail-safe: a silent no-op on
        # Windows 10 / older builds. Mica stays off until the dialog surface is
        # reworked translucent (see utils/dwm.py).
        try:
            apply_win11_chrome(int(self.winId()), dark=is_dark_mode())
        except Exception as e:
            self.logger.debug(f"Win11 chrome not applied: {e}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cancel_and_close()
        event.ignore()
