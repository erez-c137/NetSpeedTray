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
from PyQt6.QtGui import QColor, QFont, QIcon, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFileDialog, QFontDialog,
    QHBoxLayout, QListWidget, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget
)

# --- Custom Application Imports ---
from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.helpers import get_app_data_path, get_app_asset_path
from netspeedtray.utils.styles import is_dark_mode

# --- Settings Pages ---
from netspeedtray.views.settings.pages.units import UnitsPage
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.pages.interfaces import InterfacesPage
from netspeedtray.views.settings.pages.general import GeneralPage
from netspeedtray.views.settings.pages.appearance import AppearancePage
from netspeedtray.views.settings.pages.colors import ColorsPage
from netspeedtray.views.settings.pages.hardware import HardwarePage
from netspeedtray.constants.update_mode import UpdateMode


class SettingsDialog(QDialog):
    """
    Dialog window for configuring NetSpeedTray settings.

    Features sidebar navigation, live preview updates (throttled),
    and custom Win11-styled controls.
    """
    settings_changed = pyqtSignal(dict) #: Signal emitted when settings are changed (throttled).

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

        # Fixed dialog size — pages scroll if taller than available space
        self.setMinimumSize(550, 400)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            initial_h = min(600, avail.height() - 80)
            self.resize(650, max(450, initial_h))
            # Center on available geometry (excludes taskbar)
            self.move(
                avail.center().x() - self.width() // 2,
                avail.center().y() - self.height() // 2
            )
        else:
            self.resize(650, 560)

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

            # Add pages wrapped in scroll areas (order matches sidebar)
            for page in [
                self.general_page,       # 0 - General
                self.appearance_page,    # 1 - Appearance
                self.colors_page,        # 2 - Color Coding
                self.hardware_page,      # 3 - Hardware
                self.units_page,         # 4 - Display
                self.interfaces_page,    # 5 - Interfaces
            ]:
                self.stack.addWidget(self._wrap_in_scroll(page))

            # --- Bottom Buttons (Export Log / Cancel / Save) ---
            button_layout = QHBoxLayout()
            self.export_log_button = QPushButton(self.i18n.EXPORT_ERROR_LOG_BUTTON)
            self.export_log_button.setStyleSheet(style_utils.button_style())
            self.export_log_button.setToolTip(self.i18n.EXPORT_ERROR_LOG_TOOLTIP)
            self.export_log_button.clicked.connect(self.export_error_log)
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
            
            # Restore window position if saved (validate against available geometry)
            saved_pos = self.config.get("settings_window_pos")
            if saved_pos and isinstance(saved_pos, dict):
                 x, y = saved_pos.get("x"), saved_pos.get("y")
                 if x is not None and y is not None:
                     screen = self.screen() or QApplication.primaryScreen()
                     if screen:
                         avail = screen.availableGeometry()
                         x = max(avail.left(), min(x, avail.right() - self.width()))
                         y = max(avail.top(), min(y, avail.bottom() - self.height()))
                     self.move(x, y)
                     self.logger.debug(f"Restored Settings Dialog position to ({x}, {y})")

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
        self.settings_changed.emit(current_settings)

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
        
        color = QColorDialog.getColor(QColor(initial_hex), self, "Select Color")
        if color.isValid():
            new_hex = color.name().upper()
            if key_name in ["high_speed_color", "low_speed_color"]:
                self.colors_page.set_color_input(key_name.replace("_color", ""), new_hex)
            else:
                self.appearance_page.set_color_input(key_name.replace("_color", ""), new_hex)
            
            if key_name == "default_color":
                self._user_chose_default_color = True
            self._schedule_settings_update()

    def export_error_log(self) -> None:
        """Exports the application log file to a user-selected location."""
        self.logger.info("Export error log requested.")
        try:
            log_filename = getattr(constants.logs, 'ERROR_LOG_FILENAME', constants.logs.LOG_FILENAME)
            source_path = get_app_data_path() / log_filename

            if not source_path.exists():
                QMessageBox.warning(self, self.i18n.ERROR_TITLE, "Log file not found.")
                return

            dest_path, _ = QFileDialog.getSaveFileName(
                self, self.i18n.EXPORT_ERROR_LOG_TITLE, log_filename, "Log Files (*.log);;All Files (*)"
            )

            if dest_path:
                shutil.copy2(source_path, dest_path)
                QMessageBox.information(self, self.i18n.SUCCESS_TITLE, f"Log exported to {dest_path}")
                self.logger.info(f"Log exported to {dest_path}")
        except Exception as e:
            self.logger.error(f"Failed to export log: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Failed to export log: {str(e)}")

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
                
            self.hide()
            self.logger.info("Settings saved and dialog hidden.")
        except Exception as e:
            self.logger.error(f"Failed to save settings: {e}", exc_info=True)
            QMessageBox.critical(
                self, self.i18n.ERROR_TITLE,
                f"{self.i18n.SETTINGS_ERROR_MESSAGE}\n\n{str(e)}"
            )

    def _cancel_and_close(self) -> None:
        """Reverts and closes."""
        if hasattr(self.parent_widget, 'handle_settings_changed'):
            self.parent_widget.handle_settings_changed(self.original_config, save_to_disk=False)
        self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cancel_and_close()
        event.ignore()
