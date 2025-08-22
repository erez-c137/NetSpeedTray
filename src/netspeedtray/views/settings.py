"""
Settings Dialog Module for NetSpeedTray.

Provides the `SettingsDialog` class for configuring application settings with a modern,
Windows 11-inspired UI featuring a sidebar navigation and native-looking toggles/sliders.
Handles live updates to the parent widget via signals and throttling.
"""

from __future__ import annotations

import logging
import os
import shutil
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

# Use TYPE_CHECKING to avoid circular import issues at runtime
if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QPoint
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDialog, QFileDialog, QFontDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget, QRadioButton
)

from netspeedtray.utils.taskbar_utils import get_taskbar_info
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.helpers import get_app_data_path, get_app_asset_path
from netspeedtray.utils.styles import dialog_style, sidebar_style, button_style, color_button_style
from netspeedtray.utils.components import Win11Toggle, Win11Slider

from netspeedtray import constants

class SettingsDialog(QWidget):
    """
    Dialog window for configuring NetSpeedTray settings.

    Features sidebar navigation, live preview updates (throttled),
    and custom Win11-styled controls.
    """
    settings_changed = pyqtSignal(dict) #: Signal emitted when settings are changed (throttled).

    # Mappings for config strings to slider integer values
    SPEED_DISPLAY_MODE_MAP = {"auto": 0, "always_mbps": 1}
    TEXT_ALIGNMENT_MAP = {"left": 0, "center": 1, "right": 2}

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

        Args:
            parent: The parent NetworkSpeedWidget instance.
            config: The current application configuration dictionary.
            version: The application version string.
            i18n: The internationalization strings instance.
            available_interfaces: List of network interface names detected by the system.
            is_startup_enabled: The current status of the 'start with windows' setting.
        """
    
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.parent_widget = main_widget
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")
        self.logger.debug("Initializing SettingsDialog...")

        # Inverse mappings for saving config
        self.SPEED_DISPLAY_MODE_MAP_INV = {v: k for k, v in self.SPEED_DISPLAY_MODE_MAP.items()}
        self.TEXT_ALIGNMENT_MAP_INV = {v: k for k, v in self.TEXT_ALIGNMENT_MAP.items()}
        self.config = config.copy() # Work on a copy to allow cancellation
        self.original_config = config.copy() # Keep original for rollback on reject
        self.version = version
        self.i18n = i18n
        self.initial_language = self.i18n.language
        self.available_interfaces = available_interfaces or []
        self.startup_enabled_initial_state = is_startup_enabled

        # For dynamic font weight handling
        self.allowed_font_weights: List[int] = []  # Sorted list of numerical weights for the current font
        self.font_weight_name_map: Dict[int, str] = {} # Maps numerical weight to its display name

        self._ui_setup_done = False # Flag to prevent accessing UI elements before setup
        self._pending_update = False # Flag for throttling signal emissions

        # Timer for throttling live setting updates sent to the main widget
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(constants.ui.dialogs.THROTTLE_INTERVAL_MS)
        self._update_timer.timeout.connect(self._emit_settings_changed_throttled)

        self.setWindowTitle(f"{constants.app.APP_NAME} {self.i18n.SETTINGS_WINDOW_TITLE} v{self.version}")
        
        # Set window icon using the same helper as other windows
        try:
            icon_filename = getattr(constants.app, 'ICON_FILENAME', 'NetSpeedTray.ico')
            icon_path = get_app_asset_path(icon_filename)
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
            else:
                self.logger.warning(f"Icon file not found at {icon_path}")
        except Exception as e:
            self.logger.error(f"Error setting window icon: {e}", exc_info=True)
            
        self.setStyleSheet(dialog_style())

        # --- Initialization Steps ---
        self.setup_ui() # Build the UI elements
        self._init_ui_state() # Populate UI elements with initial config values
        self._connect_signals() # Connect UI element signals to handlers

        # Center the dialog on the screen
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            screen_center = screen.availableGeometry().center()
            dialog_center = self.rect().center()
            self.move(screen_center - dialog_center)

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
            sidebar_container.setContentsMargins(0, 0, 0, 0)
            sidebar_layout = QVBoxLayout(sidebar_container)
            sidebar_layout.setContentsMargins(0, 0, 0, 0)
            sidebar_layout.setSpacing(0)

            self.sidebar = QListWidget()
            self.sidebar.setFixedWidth(220)
            self.sidebar.setStyleSheet(sidebar_style())
            self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.sidebar.setContentsMargins(0, 0, 0, 0)
            self.sidebar.addItems([
                self.i18n.GENERAL_SETTINGS_GROUP,
                self.i18n.APPEARANCE_SETTINGS_GROUP,
                self.i18n.MINI_GRAPH_SETTINGS_GROUP,
                self.i18n.UNITS_GROUP,
                self.i18n.NETWORK_INTERFACES_GROUP,
                self.i18n.TROUBLESHOOTING_GROUP
            ])
            self.sidebar.setCurrentRow(0)
            sidebar_layout.addWidget(self.sidebar)
            main_layout.addWidget(sidebar_container)

            # --- Content Area ---
            content_widget = QWidget()
            content_widget.setObjectName("contentWidget")
            content_widget.setContentsMargins(0, 0, 0, 0)
            content_layout = QVBoxLayout(content_widget)
            content_layout.setContentsMargins(10, 20, 30, 20)
            content_layout.setSpacing(20)

            self.stack = QStackedWidget()
            self.stack.setContentsMargins(0, 0, 0, 0)
            content_layout.addWidget(self.stack, stretch=1)

            while self.stack.count() > 0:
                widget_to_remove = self.stack.widget(0)
                self.stack.removeWidget(widget_to_remove)
                widget_to_remove.deleteLater()

            self._setup_general_page()
            self._setup_appearance_page()
            self._setup_graph_page()
            self._setup_units_page()
            self._setup_interfaces_page()
            self._setup_troubleshooting_page()

            # --- Bottom Buttons (Save/Cancel) ---
            button_layout = QHBoxLayout()
            button_layout.setContentsMargins(0, 10, 0, 0)
            button_layout.addStretch()
            self.cancel_button = QPushButton(self.i18n.CANCEL_BUTTON)
            self.cancel_button.setStyleSheet(button_style())
            self.save_button = QPushButton(self.i18n.SAVE_BUTTON)
            self.save_button.setStyleSheet(button_style(accent=True))
            self.save_button.setDefault(True)
            button_layout.addWidget(self.cancel_button)
            button_layout.addWidget(self.save_button)
            content_layout.addLayout(button_layout)

            main_layout.addWidget(content_widget, stretch=1)

            self.sidebar.currentRowChanged.connect(self._on_sidebar_selection_changed)
            self.cancel_button.clicked.connect(self._cancel_and_close)
            self.save_button.clicked.connect(self._save_and_close)

            self._ui_setup_done = True
            self.setMinimumSize(600, 500) # Adjusted height slightly for font weight text
            self.adjustSize()

            self.logger.debug(f"UI setup completed. Stack has {self.stack.count()} pages.")
        except Exception as e:
            self.logger.error(f"Error setting up UI: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.i18n.ERROR_TITLE,
                self.i18n.ERROR_UI_SETUP_FAILED.format(error=str(e))
            )
            QTimer.singleShot(0, self.reject)

    # --- Page Setup Methods (_setup_*) ---

    def _setup_general_page(self) -> None:
        self.logger.debug("Setting up General page")
        try:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(15) # Spacing between QGroupBoxes

            # --- Language Group ---
            language_group = QGroupBox(self.i18n.LANGUAGE_LABEL)
            language_layout = QVBoxLayout(language_group)
            self.language_combo = QComboBox()
            
            # Populate the language dropdown from the i18n constants
            for code, name in self.i18n.LANGUAGE_MAP.items():
                self.language_combo.addItem(name, userData=code)
            
            language_layout.addWidget(self.language_combo)
            page_layout.addWidget(language_group)

            # --- Update Rate Group ---
            update_group = QGroupBox(self.i18n.UPDATE_RATE_GROUP_TITLE)
            update_group_layout = QVBoxLayout(update_group)
            update_group_layout.setSpacing(8)
            self.update_rate = Win11Slider()
            self.update_rate.setRange(0, int(constants.timers.MAXIMUM_UPDATE_RATE_SECONDS * 2))
            update_group_layout.addWidget(QLabel(self.i18n.UPDATE_INTERVAL_LABEL))
            update_group_layout.addWidget(self.update_rate)
            page_layout.addWidget(update_group)

            # --- Options Group (Toggles) ---
            options_group = QGroupBox(self.i18n.OPTIONS_GROUP_TITLE)
            options_layout = QGridLayout(options_group)
            options_layout.setVerticalSpacing(10)
            options_layout.setHorizontalSpacing(8)

            du_label = QLabel(self.i18n.DYNAMIC_UPDATE_RATE_LABEL)
            self.dynamic_update_rate = Win11Toggle(label_text="")
            options_layout.addWidget(du_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            options_layout.addWidget(self.dynamic_update_rate, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            sww_label = QLabel(self.i18n.START_WITH_WINDOWS_LABEL)
            self.start_with_windows = Win11Toggle(label_text="")
            options_layout.addWidget(sww_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
            options_layout.addWidget(self.start_with_windows, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
            fm_label = QLabel(self.i18n.FREE_MOVE_LABEL)
            self.free_move = Win11Toggle(label_text="")
            options_layout.addWidget(fm_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
            options_layout.addWidget(self.free_move, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            options_layout.setColumnStretch(0, 0)
            options_layout.setColumnStretch(1, 1)
            options_layout.setRowStretch(3, 1)
            page_layout.addWidget(options_group)

            page_layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up General page: {e}", exc_info=True)


    def _on_free_move_toggled(self, checked: bool) -> None:
        """
        Handles the "Free Move" toggle event. If unchecked, snaps the widget
        back to its default position.
        """
        self.logger.debug(f"Free Move toggled to: {checked}. Scheduling update.")
        self._schedule_settings_update()


    def _setup_appearance_page(self) -> None:
        self.logger.debug("Setting up Appearance page")
        try:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(15)

            # --- Font Settings Group ---
            font_group = QGroupBox(self.i18n.FONT_SETTINGS_GROUP_TITLE)
            font_layout = QVBoxLayout(font_group)
            font_layout.setSpacing(8)

            font_family_color_layout = QHBoxLayout()
            font_family_widget = QWidget()
            font_family_v_layout = QVBoxLayout(font_family_widget)
            font_family_v_layout.setContentsMargins(0,0,0,0)
            font_family_v_layout.setSpacing(4)
            font_family_v_layout.addWidget(QLabel(self.i18n.FONT_FAMILY_LABEL))
            self.font_family_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
            self.font_family_button.setStyleSheet(button_style())
            self.font_family_label = QLabel()
            self.font_family_label.setWordWrap(True)
            font_family_button_label_layout = QHBoxLayout()
            font_family_button_label_layout.addWidget(self.font_family_button)
            font_family_button_label_layout.addWidget(self.font_family_label, stretch=1)
            font_family_v_layout.addLayout(font_family_button_label_layout)
            font_family_color_layout.addWidget(font_family_widget, stretch=1)
            font_family_color_layout.addSpacing(20)

            font_color_widget = QWidget()
            font_color_v_layout = QVBoxLayout(font_color_widget)
            font_color_v_layout.setContentsMargins(0,0,0,0)
            font_color_v_layout.setSpacing(4)
            font_color_v_layout.addWidget(QLabel(self.i18n.DEFAULT_COLOR_LABEL))
            self.default_color_button = QPushButton()
            self.default_color_button.setObjectName("default_color")
            self.default_color_button.setToolTip(self.i18n.DEFAULT_COLOR_TOOLTIP)
            font_color_v_layout.addWidget(self.default_color_button, alignment=Qt.AlignmentFlag.AlignLeft)
            font_color_v_layout.addStretch()
            font_family_color_layout.addWidget(font_color_widget)
            font_layout.addLayout(font_family_color_layout)

            font_layout.addWidget(QLabel(self.i18n.FONT_SIZE_LABEL))
            self.font_size = Win11Slider()
            self.font_size.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
            font_layout.addWidget(self.font_size)

            font_layout.addWidget(QLabel(self.i18n.FONT_WEIGHT_LABEL))
            self.font_weight = Win11Slider()
            font_layout.addWidget(self.font_weight)
            page_layout.addWidget(font_group)

            # --- MOVED: Color Coding Group ---
            color_coding_group = QGroupBox(self.i18n.COLOR_CODING_GROUP)
            color_coding_main_layout = QGridLayout(color_coding_group)
            color_coding_main_layout.setVerticalSpacing(10)
            color_coding_main_layout.setHorizontalSpacing(8)
            
            enable_colors_label = QLabel(self.i18n.ENABLE_COLOR_CODING_LABEL)
            self.enable_colors = Win11Toggle(label_text="")
            color_coding_main_layout.addWidget(enable_colors_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            color_coding_main_layout.addWidget(self.enable_colors, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            self.color_container = QWidget()
            color_container_layout = QVBoxLayout(self.color_container)
            color_container_layout.setContentsMargins(0, 10, 0, 0)
            color_container_layout.setSpacing(8)

            color_container_layout.addWidget(QLabel(self.i18n.HIGH_SPEED_THRESHOLD_LABEL))
            self.high_speed_threshold = Win11Slider()
            self.high_speed_threshold.setRange(
                int(constants.ui.sliders.SPEED_THRESHOLD_MIN_HIGH),
                int(constants.ui.sliders.SPEED_THRESHOLD_MAX_HIGH)
            )
            color_container_layout.addWidget(self.high_speed_threshold)

            color_container_layout.addWidget(QLabel(self.i18n.LOW_SPEED_THRESHOLD_LABEL))
            self.low_speed_threshold = Win11Slider()
            self.low_speed_threshold.setRange(
                int(constants.ui.sliders.SPEED_THRESHOLD_MIN_LOW),
                int(constants.ui.sliders.SPEED_THRESHOLD_MAX_LOW)
            )
            color_container_layout.addWidget(self.low_speed_threshold)

            color_container_layout.addWidget(QLabel(self.i18n.HIGH_SPEED_COLOR_LABEL))
            self.high_speed_color_button = QPushButton()
            self.high_speed_color_button.setObjectName("high_speed_color")
            self.high_speed_color_button.setToolTip(self.i18n.HIGH_SPEED_COLOR_TOOLTIP)
            color_container_layout.addWidget(self.high_speed_color_button, alignment=Qt.AlignmentFlag.AlignLeft)

            color_container_layout.addWidget(QLabel(self.i18n.LOW_SPEED_COLOR_LABEL))
            self.low_speed_color_button = QPushButton()
            self.low_speed_color_button.setObjectName("low_speed_color")
            self.low_speed_color_button.setToolTip(self.i18n.LOW_SPEED_COLOR_TOOLTIP)
            color_container_layout.addWidget(self.low_speed_color_button, alignment=Qt.AlignmentFlag.AlignLeft)

            color_coding_main_layout.addWidget(self.color_container, 1, 0, 1, 2) 
            color_coding_main_layout.setColumnStretch(0, 0)
            color_coding_main_layout.setColumnStretch(1, 1)
            color_coding_main_layout.setRowStretch(2, 1)

            page_layout.addWidget(color_coding_group)
            page_layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up Appearance page: {e}", exc_info=True)


    def _setup_graph_page(self) -> None:
        self.logger.debug("Setting up Graph page")
        try:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(15)

            graph_group = QGroupBox(self.i18n.MINI_GRAPH_SETTINGS_GROUP)
            graph_layout = QGridLayout(graph_group) # Use QGridLayout
            graph_layout.setVerticalSpacing(10)
            graph_layout.setHorizontalSpacing(8)

            enable_graph_label = QLabel(self.i18n.ENABLE_GRAPH_LABEL)
            self.enable_graph = Win11Toggle(label_text="") # No internal label
            graph_layout.addWidget(enable_graph_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            graph_layout.addWidget(self.enable_graph, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
            note = QLabel(self.i18n.GRAPH_NOTE_TEXT)
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size: {constants.fonts.NOTE_FONT_SIZE}pt; color: grey;")
            graph_layout.addWidget(note, 1, 0, 1, 2) # Note spans 2 columns

            graph_layout.addWidget(QLabel(self.i18n.HISTORY_DURATION_LABEL), 2, 0, 1, 2)
            self.history_duration = Win11Slider()
            hist_min, hist_max = constants.ui.history.HISTORY_MINUTES_RANGE
            self.history_duration.setRange(hist_min, hist_max)
            graph_layout.addWidget(self.history_duration, 3, 0, 1, 2)

            graph_layout.addWidget(QLabel(self.i18n.GRAPH_OPACITY_LABEL), 4, 0, 1, 2)
            self.graph_opacity = Win11Slider()
            self.graph_opacity.setRange(constants.ui.sliders.OPACITY_MIN, constants.ui.sliders.OPACITY_MAX)
            graph_layout.addWidget(self.graph_opacity, 5, 0, 1, 2)

            graph_layout.setColumnStretch(0, 0)
            graph_layout.setColumnStretch(1, 1)
            graph_layout.setRowStretch(6, 1) # Stretch after last item

            page_layout.addWidget(graph_group)
            page_layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up Graph page: {e}", exc_info=True)


    def _setup_units_page(self) -> None:
        self.logger.debug("Setting up Units page")
        try:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(15)

            units_group = QGroupBox(self.i18n.UNITS_GROUP)
            units_layout = QGridLayout(units_group)
            units_layout.setVerticalSpacing(10)
            units_layout.setHorizontalSpacing(8)

            # Row 0: Speed Display Mode (Auto/Mbps only)
            self.speed_display_mode_label = QLabel(self.i18n.SPEED_DISPLAY_MODE_LABEL)
            self.speed_display_mode = Win11Slider()
            self.speed_display_mode.setRange(0, 1)  # 0 for auto, 1 for always_mbps
            units_layout.addWidget(self.speed_display_mode_label, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            units_layout.addWidget(self.speed_display_mode, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            # Row 1: Decimal Places (0, 1, 2)
            self.decimal_places_label = QLabel(self.i18n.DECIMAL_PLACES_LABEL)
            self.decimal_places = Win11Slider()
            self.decimal_places.setRange(0, 2)
            units_layout.addWidget(self.decimal_places_label, 1, 0, Qt.AlignmentFlag.AlignVCenter)
            units_layout.addWidget(self.decimal_places, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            # Row 2: Text Alignment (Left, Center, Right)
            self.text_alignment_label = QLabel(self.i18n.TEXT_ALIGNMENT_LABEL)
            self.text_alignment = Win11Slider()
            self.text_alignment.setRange(0, 2)  # 0 for left, 1 for center, 2 for right
            units_layout.addWidget(self.text_alignment_label, 2, 0, Qt.AlignmentFlag.AlignVCenter)
            units_layout.addWidget(self.text_alignment, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            # Row 3: Force Decimals
            self.force_decimals_label = QLabel(self.i18n.FORCE_DECIMALS_LABEL)
            self.force_decimals = Win11Toggle(label_text="")
            units_layout.addWidget(self.force_decimals_label, 3, 0, Qt.AlignmentFlag.AlignVCenter)
            units_layout.addWidget(self.force_decimals, 3, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            units_layout.setColumnStretch(0, 0)
            units_layout.setColumnStretch(1, 1)
            units_layout.setRowStretch(4, 1) 

            page_layout.addWidget(units_group)
            page_layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up Units page: {e}", exc_info=True)


    def _setup_interfaces_page(self) -> None:
        self.logger.debug("Setting up Interfaces page")
        try:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setSpacing(15)

            interfaces_group = QGroupBox(self.i18n.NETWORK_INTERFACES_GROUP)
            interfaces_layout = QVBoxLayout(interfaces_group)
            interfaces_layout.setSpacing(10)

            # --- Radio Buttons for mode selection ---
            mode_label = QLabel(self.i18n.MONITORING_MODE_LABEL)
            interfaces_layout.addWidget(mode_label)

            self.all_interfaces_radio = QRadioButton(self.i18n.ALL_INTERFACES_LABEL)
            self.auto_interface_radio = QRadioButton(self.i18n.AUTO_PRIMARY_LABEL)
            self.selected_interfaces_radio = QRadioButton(self.i18n.SELECTED_INTERFACES_LABEL)
            
            interfaces_layout.addWidget(self.all_interfaces_radio)
            interfaces_layout.addWidget(self.auto_interface_radio)
            interfaces_layout.addWidget(self.selected_interfaces_radio)

            self.interface_scroll = QScrollArea()
            self.interface_scroll.setWidgetResizable(True)
            self.interface_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

            interfaces_container = QWidget()
            self.interfaces_container_layout = QVBoxLayout(interfaces_container)
            self.interfaces_container_layout.setContentsMargins(5, 5, 5, 5)
            self.interfaces_container_layout.setSpacing(8)

            self.interface_checkboxes: Dict[str, Win11Toggle] = {}
            if self.available_interfaces:
                for iface in sorted(self.available_interfaces):
                    checkbox = Win11Toggle(iface)
                    self.interface_checkboxes[iface] = checkbox
                    self.interfaces_container_layout.addWidget(checkbox)
            else:
                no_iface_label = QLabel(self.i18n.NO_INTERFACES_FOUND)
                no_iface_label.setStyleSheet("color: grey; font-style: italic;")
                self.interfaces_container_layout.addWidget(no_iface_label)

            self.interfaces_container_layout.addStretch()
            self.interface_scroll.setWidget(interfaces_container)

            # Dynamic height calculation remains the same...
            if self.interface_checkboxes:
                example_toggle = next(iter(self.interface_checkboxes.values()))
                item_height = example_toggle.sizeHint().height()
                item_height_with_spacing = item_height + self.interfaces_container_layout.spacing()
                max_items_to_show = constants.ui.interfaces.MAX_VISIBLE_INTERFACES
                container_margins = self.interfaces_container_layout.contentsMargins()
                if item_height_with_spacing <= 0: item_height_with_spacing = 30
                calculated_max_height = (item_height_with_spacing * max_items_to_show)
                if max_items_to_show > 0 : calculated_max_height -= self.interfaces_container_layout.spacing()
                calculated_max_height += container_margins.top() + container_margins.bottom()
                effective_max_height = max(calculated_max_height, constants.ui.interfaces.SCROLL_MIN_HEIGHT)
                self.interface_scroll.setMaximumHeight(int(effective_max_height))
            else:
                self.interface_scroll.setMaximumHeight(constants.ui.interfaces.SCROLL_MAX_HEIGHT_EMPTY)

            interfaces_layout.addWidget(self.interface_scroll)
            page_layout.addWidget(interfaces_group)
            page_layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up Interfaces page: {e}", exc_info=True)


    def _setup_troubleshooting_page(self) -> None:
        self.logger.debug("Setting up Troubleshooting page")
        try:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setSpacing(15)
            troubleshooting_group = QGroupBox(self.i18n.TROUBLESHOOTING_GROUP)
            troubleshooting_layout = QVBoxLayout(troubleshooting_group)
            export_button = QPushButton(self.i18n.EXPORT_ERROR_LOG_BUTTON)
            export_button.setStyleSheet(button_style())
            export_button.setToolTip(self.i18n.EXPORT_ERROR_LOG_TOOLTIP)
            export_button.clicked.connect(self.export_error_log)
            troubleshooting_layout.addWidget(export_button, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(troubleshooting_group)
            layout.addStretch()
            self.stack.addWidget(page)
        except Exception as e:
            self.logger.error(f"Error setting up Troubleshooting page: {e}", exc_info=True)


    # --- Font Weight Logic ---
    def _snap_value_to_allowed(self, value: int, allowed_values: List[int]) -> int:
        """Snaps a value to the closest value in a list of allowed values."""
        if not allowed_values:
            self.logger.warning("Cannot snap value: allowed_values list is empty. Trying constants.fonts.WEIGHT_MAP.")
            if constants.fonts.WEIGHT_MAP: # Ensure WEIGHT_MAP exists
                return min(constants.fonts.WEIGHT_MAP.keys(), key=lambda w: abs(w - value))
            return value

        return min(allowed_values, key=lambda w: abs(w - value))


    def _update_font_weight_options(self, font_family: str) -> None:
        """
        Updates available font weight options based on the selected font family.
        Populates self.allowed_font_weights and self.font_weight_name_map.
        """
        self.logger.debug(f"Updating font weight options for family: {font_family}")
        style_strings = QFontDatabase.styles(font_family)

        raw_weights_to_styles: Dict[int, List[str]] = {}
        if style_strings:
            for style_name in style_strings:
                weight_val = QFontDatabase.weight(font_family, style_name)
                if weight_val > 0:
                    if weight_val not in raw_weights_to_styles:
                        raw_weights_to_styles[weight_val] = []
                    raw_weights_to_styles[weight_val].append(style_name)
        
        self.font_weight_name_map.clear()
        if raw_weights_to_styles:
            for weight_val, style_names_for_weight in sorted(raw_weights_to_styles.items()):
                display_name = constants.fonts.WEIGHT_MAP.get(weight_val) # Try standard name first
                
                if display_name is None: # Not a standard weight, derive from font's style names
                    plain_name = None
                    for name in style_names_for_weight: # Prefer non-italic/oblique
                        if "italic" not in name.lower() and "oblique" not in name.lower():
                            if plain_name is None or len(name) < len(plain_name): # Prefer shorter plain name
                                plain_name = name
                    
                    if plain_name is None: # All styles for this weight are italic/oblique or other
                        plain_name = min(style_names_for_weight, key=len, default=f"Weight {weight_val}")
                    display_name = plain_name
                
                self.font_weight_name_map[weight_val] = display_name
        
        if not self.font_weight_name_map: # Fallback if no specific weights found
            self.logger.warning(
                f"No specific weights found for font '{font_family}'. Falling back to standard constants.fonts.WEIGHT_MAP."
            )
            # Populate with standard weights and their i18n names if available
            for weight_val, key_name in constants.fonts.WEIGHT_NUM_TO_KEY.items():
                self.font_weight_name_map[weight_val] = getattr(self.i18n, key_name, f"Weight {weight_val}")


        self.allowed_font_weights = sorted(list(self.font_weight_name_map.keys()))
        self.logger.debug(f"Allowed font weights for '{font_family}': {self.font_weight_name_map}")
        self._update_font_weight_slider_range()


    def _update_font_weight_slider_range(self) -> None:
        """Sets the min and max range for the font weight slider."""
        if not hasattr(self, 'font_weight'):
            self.logger.error("Font weight slider UI element not found for range update.")
            return

        if self.allowed_font_weights:
            min_w = self.allowed_font_weights[0]
            max_w = self.allowed_font_weights[-1]
            self.font_weight.setRange(min_w, max_w if min_w != max_w else min_w + 1) # Needs min < max
            self.font_weight.setEnabled(len(self.allowed_font_weights) > 1)
            self.logger.debug(f"Font weight slider range set: {min_w}-{max_w}. Enabled: {self.font_weight.isEnabled()}")
        else:
            self.font_weight.setRange(constants.fonts.FONT_WEIGHT_NORMAL, constants.fonts.FONT_WEIGHT_NORMAL + 1) # Default fallback
            self.font_weight.setEnabled(False)
            self.font_weight.setValueText(self.i18n.DEFAULT_TEXT) # Use N/A from i18n
            self.logger.warning("No allowed font weights for slider range; slider set to default/disabled.")


    def update_font_weight_slider_controls(self, target_weight: int) -> None:
        """
        Sets the font weight slider's value (snapped) and updates its display text.
        Assumes _update_font_weight_options (and _update_font_weight_slider_range) was called.
        """
        if not hasattr(self, 'font_weight'):
            self.logger.error("Font weight slider UI not found for update_font_weight_slider_controls.")
            return

        if not self.allowed_font_weights:
            self.logger.warning("No allowed_font_weights; cannot set font weight slider value.")
            self.font_weight.setEnabled(False)
            self.font_weight.setValueText(self.i18n.DEFAULT_TEXT) # Use N/A
            return
        
        self.font_weight.setEnabled(len(self.allowed_font_weights) > 1)
        snapped_value = self._snap_value_to_allowed(target_weight, self.allowed_font_weights)
        self.logger.debug(f"Updating font weight slider: target_weight={target_weight}, snapped_to_allowed={snapped_value}")

        is_blocked = self.font_weight.slider.blockSignals(True)
        self.font_weight.setValue(snapped_value)
        self.font_weight.slider.blockSignals(is_blocked)
        self._update_font_weight_text_live(snapped_value, force_update_text=True)


    def _update_font_weight_text_live(self, current_slider_value: int, force_update_text: bool = False) -> None:
        """
        Updates font weight slider's text. Snaps value if not dragging & misaligned.
        """
        try:
            if not hasattr(self, 'font_weight') or not hasattr(self.font_weight, 'setValueText'):
                self.logger.warning("Cannot update font weight text: Slider or setValueText missing.")
                return
            
            if not self.allowed_font_weights or not self.font_weight_name_map:
                self.logger.warning("No allowed font weights/name map for text update. Using raw value or fallback.")
                # Use constants.fonts.WEIGHT_MAP for text if dynamic map is empty
                fb_snapped_val = self._snap_value_to_allowed(current_slider_value, list(constants.fonts.WEIGHT_MAP.keys()) if constants.fonts.WEIGHT_MAP else [current_slider_value])
                weight_text = constants.fonts.WEIGHT_MAP.get(fb_snapped_val, f"Weight {current_slider_value}")
                self.font_weight.setValueText(weight_text)
                return

            target_snapped_value = self._snap_value_to_allowed(current_slider_value, self.allowed_font_weights)
            weight_text = self.font_weight_name_map.get(target_snapped_value, f"Weight {target_snapped_value}")
            
            text_updated = False
            current_display_text = self.font_weight.getText() if hasattr(self.font_weight, 'getText') else \
                                  (self.font_weight._value_label.text() if hasattr(self.font_weight, '_value_label') else "")

            if current_display_text != weight_text or force_update_text:
                self.font_weight.setValueText(weight_text)
                text_updated = True
                self.logger.debug(f"Font weight text: '{weight_text}' (snap {target_snapped_value} from {current_slider_value})")

            if self.font_weight.value() != target_snapped_value and \
               (not self.font_weight.slider.isSliderDown() or force_update_text):
                self.logger.debug(f"Live snapping font_weight: {self.font_weight.value()} -> {target_snapped_value}")
                is_blocked = self.font_weight.slider.blockSignals(True)
                self.font_weight.setValue(target_snapped_value)
                self.font_weight.slider.blockSignals(is_blocked)
                # _schedule_settings_update will be called by valueChanged connection

        except Exception as e:
            self.logger.error(f"Error updating font weight text/snapping: {e}", exc_info=True)
            if hasattr(self, 'font_weight') and hasattr(self.font_weight, 'setValueText'):
                self.font_weight.setValueText("Error") # Simple error text


    def _snap_font_weight_on_release(self) -> None:
        """Snaps font weight slider to nearest allowed weight on release."""
        if not hasattr(self, 'font_weight'):
            self.logger.warning("Cannot snap slider on release: font_weight slider not found.")
            return
        try:
            current_value_on_release = self.font_weight.value()
            if not self.allowed_font_weights:
                self.logger.warning("Cannot snap on release: no allowed_font_weights. Using fallback map.")
                target_snapped_value = self._snap_value_to_allowed(current_value_on_release, list(constants.fonts.WEIGHT_MAP.keys()) if constants.fonts.WEIGHT_MAP else [current_value_on_release])
            else:
                target_snapped_value = self._snap_value_to_allowed(current_value_on_release, self.allowed_font_weights)

            if current_value_on_release != target_snapped_value:
                self.logger.info(f"Snapping font_weight ON RELEASE: {current_value_on_release} -> {target_snapped_value}")
                is_blocked = self.font_weight.slider.blockSignals(True)
                self.font_weight.setValue(target_snapped_value)
                self.font_weight.slider.blockSignals(is_blocked)
                self._update_font_weight_text_live(target_snapped_value, force_update_text=True)
                self._schedule_settings_update() # Value changed programmatically
            else:
                self._update_font_weight_text_live(current_value_on_release, force_update_text=True) # Ensure text is correct
                self.logger.debug(f"Slider released at exact weight {current_value_on_release}, no numerical snap needed.")
        except Exception as e:
            self.logger.error(f"Error snapping font weight slider value on release: {e}", exc_info=True)


    # --- Signal Handlers and UI Logic ---

    def _on_sidebar_selection_changed(self, index: int) -> None:
        self.logger.debug(f"Sidebar selection changed to index {index}")
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
        else:
            self.logger.warning(f"Invalid sidebar index {index}, stack count is {self.stack.count()}")


    def _connect_signals(self) -> None:
        try:
            self._connect_general_signals()
            self._connect_appearance_signals()
            self._connect_graph_signals()
            self._connect_units_signals()
            self._connect_interfaces_signals()
            self.logger.debug("UI signals connected.")
        except AttributeError as e:
             self.logger.error(f"Error connecting signals: Missing UI element? {e}", exc_info=True)
             QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"UI setup incomplete, cannot connect signals: {e}")
        except Exception as e:
             self.logger.error(f"Unexpected error connecting signals: {e}", exc_info=True)


    def _connect_general_signals(self) -> None:
        self.update_rate.valueChanged.connect(lambda v: self.update_rate.setValueText(self.rate_to_text(v)))
        self.update_rate.valueChanged.connect(self._schedule_settings_update)
        self.dynamic_update_rate.toggled.connect(self._schedule_settings_update)
        self.start_with_windows.toggled.connect(self._schedule_settings_update) # Actual toggle on accept
        self.font_size.valueChanged.connect(lambda v: self.font_size.setValueText(str(v)))
        self.font_size.valueChanged.connect(self._schedule_settings_update)
        self.font_family_button.clicked.connect(self.select_font)
        
        # Font Weight Signals
        self.font_weight.valueChanged.connect(self._update_font_weight_text_live) # Live text update
        self.font_weight.slider.sliderReleased.connect(self._snap_font_weight_on_release) # Final snap
        self.font_weight.valueChanged.connect(self._schedule_settings_update) # For live preview

        self.default_color_button.clicked.connect(lambda: self.choose_color(self.default_color_button))
        self.free_move.toggled.connect(self._on_free_move_toggled)
        self.language_combo.currentIndexChanged.connect(self._schedule_settings_update)


    def _connect_appearance_signals(self) -> None:
        # Font signals
        self.font_size.valueChanged.connect(lambda v: self.font_size.setValueText(str(v)))
        self.font_size.valueChanged.connect(self._schedule_settings_update)
        self.font_family_button.clicked.connect(self.select_font)
        self.font_weight.valueChanged.connect(self._update_font_weight_text_live)
        self.font_weight.slider.sliderReleased.connect(self._snap_font_weight_on_release)
        self.font_weight.valueChanged.connect(self._schedule_settings_update)
        self.default_color_button.clicked.connect(lambda: self.choose_color(self.default_color_button))

        # Color coding signals
        self.enable_colors.toggled.connect(self.toggle_color_settings)
        self.enable_colors.toggled.connect(self._schedule_settings_update)
        self.high_speed_color_button.clicked.connect(lambda: self.choose_color(self.high_speed_color_button))
        self.low_speed_color_button.clicked.connect(lambda: self.choose_color(self.low_speed_color_button))
        self.high_speed_threshold.valueChanged.connect(self.update_threshold_labels)
        self.high_speed_threshold.valueChanged.connect(self._schedule_settings_update)
        self.low_speed_threshold.valueChanged.connect(self.update_threshold_labels)
        self.low_speed_threshold.valueChanged.connect(self._schedule_settings_update)


    def _connect_graph_signals(self) -> None:
        self.enable_graph.toggled.connect(self._schedule_settings_update)
        self.history_duration.valueChanged.connect(
            lambda v: self.history_duration.setValueText(f"{v} {self.i18n.MINUTES_LABEL}")
        )
        self.history_duration.valueChanged.connect(self._schedule_settings_update)
        self.graph_opacity.valueChanged.connect(
            lambda v: self.graph_opacity.setValueText(f"{v}%")
        )
        self.graph_opacity.valueChanged.connect(self._schedule_settings_update)


    def _connect_units_signals(self) -> None:
        self.speed_display_mode.valueChanged.connect(self._on_speed_display_mode_changed)
        self.speed_display_mode.valueChanged.connect(self._schedule_settings_update)
        
        self.decimal_places.valueChanged.connect(self._on_decimal_places_changed)
        self.decimal_places.valueChanged.connect(self._schedule_settings_update)
        
        self.text_alignment.valueChanged.connect(self._on_text_alignment_changed)
        self.text_alignment.valueChanged.connect(self._schedule_settings_update)
        
        self.force_decimals.toggled.connect(self._schedule_settings_update)


    def _connect_interfaces_signals(self) -> None:
        # Connect all radio buttons to the same handler
        self.all_interfaces_radio.toggled.connect(self._on_interface_mode_changed)
        self.auto_interface_radio.toggled.connect(self._on_interface_mode_changed)
        self.selected_interfaces_radio.toggled.connect(self._on_interface_mode_changed)

        # Checkboxes still schedule updates as before
        for checkbox in self.interface_checkboxes.values():
            checkbox.toggled.connect(self._schedule_settings_update)


    def _on_interface_mode_changed(self) -> None:
        """Handles visibility of the interface list when a radio button is selected."""
        # This check ensures we only react to the button that is now 'on'
        if not self.sender().isChecked():
            return
            
        is_selection_visible = self.selected_interfaces_radio.isChecked()
        
        self.logger.debug(f"Interface mode changed. Selection visible: {is_selection_visible}")
        
        self.interface_scroll.setVisible(is_selection_visible)
        for checkbox in self.interface_checkboxes.values():
            checkbox.setEnabled(is_selection_visible)

        self.adjustSize()
        self._schedule_settings_update()


    def _init_ui_state(self) -> None:
        if not self._ui_setup_done:
            self.logger.error("Cannot initialize UI state: UI setup not complete.")
            return
        self.logger.debug("Initializing UI state from configuration.")

        # --- General Page ---
        # Initialize the language combo box
        lang_code_to_find = self.config.get("language", self.initial_language)
        index = self.language_combo.findData(lang_code_to_find)
        if index != -1:
            self.language_combo.setCurrentIndex(index)
        else:
            self.logger.warning(f"Saved language '{lang_code_to_find}' not found in dropdown. Defaulting.")

        update_rate_val_raw = self.config.get("update_rate")
        update_rate_val_config = constants.config.defaults.DEFAULT_UPDATE_RATE if update_rate_val_raw is None else float(update_rate_val_raw)
        update_rate_slider_val = int(update_rate_val_config * 2)
        self.update_rate.setValue(max(0, update_rate_slider_val))
        self.update_rate.setValueText(self.rate_to_text(self.update_rate.value()))

        dynamic_update_raw = self.config.get("dynamic_update_enabled")
        dynamic_update_config = constants.config.defaults.DEFAULT_DYNAMIC_UPDATE_ENABLED if dynamic_update_raw is None else bool(dynamic_update_raw)
        self.dynamic_update_rate.setChecked(dynamic_update_config)
        
        self.start_with_windows.setChecked(self.startup_enabled_initial_state)
        self.free_move.setChecked(self.config.get("free_move", False))

        # --- Appearance Page ---
        font_family_config = self.config.get("font_family")
        initial_font_family = constants.config.defaults.DEFAULT_FONT_FAMILY if not font_family_config else str(font_family_config)
        self.font_family_label.setText(initial_font_family)

        font_size_config = self.config.get("font_size")
        initial_font_size = constants.config.defaults.DEFAULT_FONT_SIZE if font_size_config is None else int(font_size_config)
        initial_font_size = max(constants.fonts.FONT_SIZE_MIN, min(initial_font_size, constants.fonts.FONT_SIZE_MAX))
        self.font_size.setValue(initial_font_size)
        self.font_size.setValueText(str(initial_font_size))

        self._update_font_weight_options(initial_font_family)
        font_weight_config = self.config.get("font_weight")
        initial_font_weight = constants.config.defaults.DEFAULT_FONT_WEIGHT if font_weight_config is None else int(font_weight_config)
        self.update_font_weight_slider_controls(initial_font_weight)

        default_color_config = self.config.get("default_color")
        initial_default_color_hex = constants.config.defaults.DEFAULT_COLOR if not default_color_config or not default_color_config.startswith("#") else str(default_color_config)
        self._set_color_button_style(self.default_color_button, initial_default_color_hex)
        
        color_coding_raw = self.config.get("color_coding")
        color_coding_enabled = constants.config.defaults.DEFAULT_COLOR_CODING if color_coding_raw is None else bool(color_coding_raw)
        self.enable_colors.setChecked(color_coding_enabled)

        high_thresh_raw = self.config.get("high_speed_threshold")
        high_thresh_config = constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD if high_thresh_raw is None else float(high_thresh_raw)
        self.high_speed_threshold.setValue(int(high_thresh_config * 10))

        low_thresh_raw = self.config.get("low_speed_threshold")
        low_thresh_config = constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD if low_thresh_raw is None else float(low_thresh_raw)
        self.low_speed_threshold.setValue(int(low_thresh_config * 10))
        
        high_color_config = self.config.get("high_speed_color")
        initial_high_color_hex = constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR if not high_color_config or not high_color_config.startswith("#") else str(high_color_config)
        self._set_color_button_style(self.high_speed_color_button, initial_high_color_hex)

        low_color_config = self.config.get("low_speed_color")
        initial_low_color_hex = constants.config.defaults.DEFAULT_LOW_SPEED_COLOR if not low_color_config or not low_color_config.startswith("#") else str(low_color_config)
        self._set_color_button_style(self.low_speed_color_button, initial_low_color_hex)
        
        self.toggle_color_settings(color_coding_enabled)

        # --- Graph Page ---
        graph_enabled_raw = self.config.get("graph_enabled")
        graph_enabled_config = constants.config.defaults.DEFAULT_GRAPH_ENABLED if graph_enabled_raw is None else bool(graph_enabled_raw)
        self.enable_graph.setChecked(graph_enabled_config)

        history_min_raw = self.config.get("history_minutes")
        history_min_config = constants.config.defaults.DEFAULT_HISTORY_MINUTES if history_min_raw is None else int(history_min_raw)
        hist_min_slider, hist_max_slider = constants.ui.history.HISTORY_MINUTES_RANGE
        history_min_config = max(hist_min_slider, min(history_min_config, hist_max_slider))
        self.history_duration.setValue(history_min_config)
        self.history_duration.setValueText(f"{self.history_duration.value()} {self.i18n.MINUTES_LABEL}")

        graph_opacity_raw = self.config.get("graph_opacity")
        graph_opacity_config = constants.config.defaults.DEFAULT_GRAPH_OPACITY if graph_opacity_raw is None else int(graph_opacity_raw)
        opacity_min_slider, opacity_max_slider = constants.ui.sliders.OPACITY_MIN, constants.ui.sliders.OPACITY_MAX
        graph_opacity_config = max(opacity_min_slider, min(graph_opacity_config, opacity_max_slider))
        self.graph_opacity.setValue(graph_opacity_config)
        self.graph_opacity.setValueText(f"{self.graph_opacity.value()}%")

        # --- Units Page ---
        self.update_threshold_labels()

        sdm_raw = self.config.get("speed_display_mode", "auto")
        sdm_val = self.SPEED_DISPLAY_MODE_MAP.get(str(sdm_raw).lower(), self.SPEED_DISPLAY_MODE_MAP["auto"])
        self.speed_display_mode.setValue(sdm_val)
        self._on_speed_display_mode_changed(sdm_val)

        dp_raw = self.config.get("decimal_places")
        dp_val = 2 if dp_raw is None else int(dp_raw)
        self.decimal_places.setValue(dp_val)
        self._on_decimal_places_changed(dp_val)

        ta_raw = self.config.get("text_alignment", "center")
        ta_val = self.TEXT_ALIGNMENT_MAP.get(str(ta_raw).lower(), self.TEXT_ALIGNMENT_MAP["center"])
        self.text_alignment.setValue(ta_val)
        self._on_text_alignment_changed(ta_val)
        
        fd_raw = self.config.get("force_decimals")
        fd_val = False if fd_raw is None else bool(fd_raw)
        self.force_decimals.setChecked(fd_val)

        # --- Interfaces Page ---
        # Default to 'auto' if the config value is missing or invalid
        interface_mode_config = self.config.get("interface_mode", "auto")
        
        if interface_mode_config == "selected":
            self.selected_interfaces_radio.setChecked(True)
        elif interface_mode_config == "all":
            self.all_interfaces_radio.setChecked(True)
        elif interface_mode_config == "auto" or not interface_mode_config:
            self.auto_interface_radio.setChecked(True)
        else:
            # Fallback: if config is missing or invalid, select auto by default
            self.auto_interface_radio.setChecked(True)

        is_selection_visible = self.selected_interfaces_radio.isChecked()
        self.interface_scroll.setVisible(is_selection_visible)

        selected_interfaces = self.config.get("selected_interfaces", [])
        for name, checkbox in self.interface_checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(name in selected_interfaces)
            checkbox.setEnabled(is_selection_visible)
            checkbox.blockSignals(False)

        self.adjustSize()
        self.logger.debug("UI state initialization complete.")


    def toggle_color_settings(self, enabled: bool) -> None:
        is_enabled = bool(enabled)
        self.color_container.setVisible(is_enabled)
        self.adjustSize()


    def toggle_all_interfaces_action(self, state: int | bool) -> None:
        """
        Handles the "Monitor All Interfaces" toggle. Enables/disables the
        specific interface list without changing the user's selections.
        """
        is_all_mode_now_active = bool(state)
        is_specific_mode_now_active = not is_all_mode_now_active

        self.logger.debug(
            f"'All Interfaces' toggle changed. New state: {'All Mode' if is_all_mode_now_active else 'Specific Mode'}"
        )

        # Enable or disable the checkboxes. Do not change their state.
        for checkbox in self.interface_checkboxes.values():
            checkbox.setEnabled(is_specific_mode_now_active)

        self.toggle_interface_settings(is_visible=is_specific_mode_now_active)
        self._schedule_settings_update()


    def toggle_interface_settings(self, is_visible: bool) -> None:
        self.interface_scroll.setVisible(is_visible)
        self.adjustSize()


    def _schedule_settings_update(self) -> None:
        if not self._ui_setup_done: return
        self._pending_update = True
        self._update_timer.start()


    def _emit_settings_changed_throttled(self) -> None:
        if not self._pending_update: return
        if not self._ui_setup_done:
            self.logger.warning("Attempted to emit settings before UI setup complete.")
            return

        self.logger.debug("Throttled preview: Applying settings without saving.")
        current_settings = self.get_settings()
        if current_settings:
            if hasattr(self.parent_widget, 'handle_settings_changed'):
                try:
                    # This applies the settings for a live preview WITHOUT saving to disk.
                    self.parent_widget.handle_settings_changed(current_settings, save_to_disk=False)
                except Exception as e:
                    self.logger.error(f"Error applying live settings preview to parent: {e}", exc_info=True)
            else:
                 self.logger.warning("Parent widget lacks handle_settings_changed for live update.")
        self._pending_update = False


    def _set_color_button_style(self, button: QPushButton, color_hex: str) -> None:
        try:
            button.setStyleSheet(color_button_style(color_hex))
            button.setFixedSize(constants.ui.dialogs.COLOR_BUTTON_WIDTH, constants.ui.dialogs.COLOR_BUTTON_HEIGHT)
        except Exception as e:
            self.logger.error(f"Error setting style for color button '{button.objectName()}': {e}")


    def choose_color(self, button: QPushButton) -> None:
        object_name = button.objectName()
        if object_name == "high_speed_color": default_color_hex = constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR
        elif object_name == "low_speed_color": default_color_hex = constants.config.defaults.DEFAULT_LOW_SPEED_COLOR
        else: default_color_hex = constants.config.defaults.DEFAULT_COLOR

        current_color_hex = self._get_color_from_button(button, default_color_hex)
        current_qcolor = QColor(current_color_hex)
        new_qcolor = QColorDialog.getColor(current_qcolor, self, self.i18n.SELECT_COLOR_TITLE)

        if new_qcolor.isValid():
            new_color_hex = new_qcolor.name(QColor.NameFormat.HexRgb)
            self.logger.debug(f"Color chosen for '{object_name}': {new_color_hex}")
            self._set_color_button_style(button, new_color_hex)
            self._schedule_settings_update()
        else:
             self.logger.debug(f"Color selection cancelled for '{object_name}'.")


    def select_font(self) -> None:
        """Opens a font dialog to select font family, size, and weight."""
        try:
            current_slider_val = self.font_weight.value()
            if self.allowed_font_weights:
                 current_qfont_weight = self._snap_value_to_allowed(current_slider_val, self.allowed_font_weights)
            else: # Fallback if allowed_font_weights is empty for some reason
                 fallback_weights = list(constants.fonts.WEIGHT_MAP.keys()) if constants.fonts.WEIGHT_MAP else [constants.fonts.FONT_WEIGHT_NORMAL]
                 current_qfont_weight = self._snap_value_to_allowed(current_slider_val, fallback_weights)

        except (ValueError, AttributeError) as e:
             self.logger.warning(f"Could not determine current font weight for dialog, using default: {e}")
             current_qfont_weight = constants.config.defaults.DEFAULT_FONT_WEIGHT
        
        current_font = QFont(
            self.font_family_label.text(),
            self.font_size.value(),
            current_qfont_weight # Use the numeric weight for QFont
        )

        font, ok = QFontDialog.getFont(current_font, self, self.i18n.SELECT_FONT_TITLE)

        if ok:
            self.logger.debug(f"Font selected: Family='{font.family()}', Size={font.pointSize()}, Weight(numeric)={font.weight()}")
            
            new_font_family = font.family()
            new_font_size_val = font.pointSize() # QFontDialog can return float
            new_font_numerical_weight = font.weight()

            if self.font_family_label.text() != new_font_family:
                self.font_family_label.setText(new_font_family)
                self._update_font_weight_options(new_font_family) # Update allowed weights for new family

            self.font_size.setValue(int(new_font_size_val)) # Update font size slider
            # self.font_size.setValueText will be called by its valueChanged signal
            
            # Update font weight slider using the weight from the dialog,
            # this will snap to an available weight for the new family.
            self.update_font_weight_slider_controls(new_font_numerical_weight)
            
            self._schedule_settings_update()
        else:
            self.logger.debug("Font selection cancelled.")


    # --- New Handler Methods for Units Page ---
    def _on_speed_display_mode_changed(self, value: int) -> None:
        """Updates the text for the speed display mode slider."""
        if value == self.SPEED_DISPLAY_MODE_MAP["auto"]:
            self.speed_display_mode.setValueText(self.i18n.SPEED_DISPLAY_MODE_AUTO)
        else: # "always_mbps"
            self.speed_display_mode.setValueText(self.i18n.SPEED_DISPLAY_MODE_MBPS)


    def _on_decimal_places_changed(self, value: int) -> None:
        """Updates the text for the decimal places slider."""
        self.decimal_places.setValueText(str(value))


    def _on_text_alignment_changed(self, value: int) -> None:
        """Updates the text for the text alignment slider."""
        if value == self.TEXT_ALIGNMENT_MAP["left"]:
            self.text_alignment.setValueText(self.i18n.ALIGN_LEFT)
        elif value == self.TEXT_ALIGNMENT_MAP["right"]:
            self.text_alignment.setValueText(self.i18n.ALIGN_RIGHT)
        else: # "center"
            self.text_alignment.setValueText(self.i18n.ALIGN_CENTER)


    def update_threshold_labels(self) -> None:
        try:
            high_value_raw = self.high_speed_threshold.value()
            low_value_raw = self.low_speed_threshold.value()

            if low_value_raw > high_value_raw:
                 self.low_speed_threshold.setValue(high_value_raw)
                 low_value_raw = high_value_raw
                 self.logger.debug("Low threshold > high threshold, adjusting low slider value.")

            high_value = high_value_raw / 10.0
            low_value = low_value_raw / 10.0
            unit = self.i18n.MBITS_UNIT

            self.high_speed_threshold.setValueText(f"{high_value:.1f} {unit}")
            self.low_speed_threshold.setValueText(f"{low_value:.1f} {unit}")
            
        except AttributeError as e:
             self.logger.error(f"Error updating threshold labels: Missing UI element? {e}")
        except Exception as e:
             self.logger.error(f"Error updating threshold labels: {e}", exc_info=True)


    def _set_initial_interface_checks(self) -> None:
        """
        Sets the initial checked state of individual interface checkboxes based on
        the loaded configuration. The mode determines if they are enabled.
        """
        selected_interfaces_from_config = self.config.get("selected_interfaces", [])
        is_all_mode_currently_active = self.all_interfaces.isChecked()

        self.logger.debug(
            f"Setting initial interface checks. All mode active: {is_all_mode_currently_active}. "
            f"Selected_interfaces from config: {selected_interfaces_from_config}"
        )

        for iface_name, checkbox in self.interface_checkboxes.items():
            # Individual checkboxes are enabled only if NOT in 'all_mode'
            checkbox.setEnabled(not is_all_mode_currently_active)

            # The checked state should ALWAYS reflect the saved config.
            # The mode only determines if the controls are enabled or disabled.
            should_be_checked = (iface_name in selected_interfaces_from_config)

            checkbox.blockSignals(True)
            checkbox.setChecked(should_be_checked)
            checkbox.blockSignals(False)

            self.logger.debug(
                f"Interface '{iface_name}': Initial state - Enabled={checkbox.isEnabled()}, Checked={checkbox.isChecked()}"
            )


    def export_error_log(self) -> None:
        log_path = os.path.join(get_app_data_path(), constants.logs.LOG_FILENAME)
        if not os.path.exists(log_path):
            QMessageBox.warning(self, self.i18n.NO_LOG_TITLE, self.i18n.NO_LOG_MESSAGE)
            return

        default_filename = f"{os.path.splitext(constants.logs.LOG_FILENAME)[0]}_export.log"
        default_path = os.path.join(os.path.expanduser("~"), "Documents", default_filename)
        dest_path, _ = QFileDialog.getSaveFileName(
            self, self.i18n.EXPORT_ERROR_LOG_TITLE, default_path,
            f"{self.i18n.LOG_FILES_FILTER} (*.log);;{self.i18n.ALL_FILES_FILTER} (*.*)"
        )

        if dest_path:
            try:
                shutil.copy2(log_path, dest_path)
                QMessageBox.information(
                    self, self.i18n.SUCCESS_TITLE,
                    self.i18n.LOG_EXPORT_SUCCESS_MESSAGE.format(file_path=dest_path)
                )
                self.logger.info(f"Error log exported successfully to: {dest_path}")
            except Exception as e:
                self.logger.error(f"Error exporting log file: {e}", exc_info=True)
                QMessageBox.critical(
                    self, self.i18n.ERROR_TITLE,
                    self.i18n.LOG_EXPORT_ERROR_MESSAGE.format(error=str(e))
                )


    def get_settings(self) -> Dict[str, Any]:
        if not self._ui_setup_done:
            self.logger.error("Cannot get settings: UI setup not complete.")
            return {} # Return empty dict on failure
        try:
            settings = self.original_config.copy() # Start with original to preserve unmanaged settings
            
            # Language Setting
            settings["language"] = self.language_combo.currentData()

            # General Page
            settings["update_rate"] = max(constants.config.defaults.MINIMUM_UPDATE_RATE, self.update_rate.value() / 2.0) if self.update_rate.value() > 0 else 0.0
            settings["font_size"] = self.font_size.value()
            settings["font_family"] = self.font_family_label.text()
            settings["font_weight"] = self.font_weight.value()
            settings["dynamic_update_enabled"] = self.dynamic_update_rate.isChecked()
            settings["free_move"] = self.free_move.isChecked()
            settings["start_with_windows"] = self.start_with_windows.isChecked() if hasattr(self, 'start_with_windows') else self.startup_enabled_initial_state
            settings["default_color"] = self._get_color_from_button(self.default_color_button, constants.config.defaults.DEFAULT_COLOR)

            settings["color_coding"] = self.enable_colors.isChecked()
            settings["high_speed_threshold"] = self.high_speed_threshold.value() / 10.0
            settings["low_speed_threshold"] = self.low_speed_threshold.value() / 10.0
            settings["high_speed_color"] = self._get_color_from_button(self.high_speed_color_button, constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR)
            settings["low_speed_color"] = self._get_color_from_button(self.low_speed_color_button, constants.config.defaults.DEFAULT_LOW_SPEED_COLOR)

            # Mini Graph Page
            settings["graph_enabled"] = self.enable_graph.isChecked()
            settings["history_minutes"] = self.history_duration.value()
            settings["graph_opacity"] = self.graph_opacity.value()

            # Units Page
            settings["speed_display_mode"] = self.SPEED_DISPLAY_MODE_MAP_INV.get(self.speed_display_mode.value(), "auto")
            settings["decimal_places"] = self.decimal_places.value()
            settings["text_alignment"] = self.TEXT_ALIGNMENT_MAP_INV.get(self.text_alignment.value(), "center")
            settings["force_decimals"] = self.force_decimals.isChecked()
            
            # Interface settings logic
            if self.selected_interfaces_radio.isChecked():
                settings["interface_mode"] = "selected"
            elif self.all_interfaces_radio.isChecked():
                settings["interface_mode"] = "all"
            else: # Default to auto
                settings["interface_mode"] = "auto"

            settings["selected_interfaces"] = [
                iface_name for iface_name, cb in self.interface_checkboxes.items() if cb.isChecked()
            ]
            
            self.logger.debug(
                f"Getting settings: interface_mode='{settings['interface_mode']}', "
                f"selected_interfaces={settings['selected_interfaces']}"
            )
            return settings
        except AttributeError as e:
             self.logger.error(f"Error getting settings: Missing UI element? {e}", exc_info=True)
             return {}
        except Exception as e:
             self.logger.error(f"Unexpected error getting settings: {e}", exc_info=True)
             return {}


    def _get_color_from_button(self, button: QPushButton, default: str) -> str:
        try:
            style = button.styleSheet()
            match = re.search(r"background-color:\s*(#[0-9a-fA-F]{6,8})\s*;", style)
            if match:
                return match.group(1).upper()
            else:
                start_idx = style.find("background-color:")
                if start_idx == -1: return default
                start = start_idx + len("background-color:")
                end = style.find(";", start)
                if end == -1: end = len(style)
                color_hex = style[start:end].strip()
                if color_hex.startswith("#") and len(color_hex) in (7, 9):
                    return color_hex.upper()
                self.logger.warning(f"Could not parse color '{color_hex}' from button '{button.objectName()}', using default '{default}'. Style: '{style}'")
                return default
        except Exception as e:
            self.logger.error(f"Error parsing color from button '{button.objectName()}': {e}")
            return default


    # --- Helper Methods ---
    def rate_to_text(self, value: int) -> str:
        if value == 0: return self.i18n.SMART_MODE_LABEL
        seconds = value / 2.0
        return f"{seconds:.1f} {self.i18n.SECONDS_LABEL}"


    def get_config(self) -> Dict[str, Any]:
        return self.config.copy()


    def should_update_startup_task(self) -> bool:
        if not self._ui_setup_done: return False
        return self.start_with_windows.isChecked() != self.startup_enabled_initial_state


    def is_startup_requested(self) -> bool:
        if not self._ui_setup_done: return False
        return self.start_with_windows.isChecked()
    
    def reset_with_config(self, config: Dict[str, Any]) -> None:
        """Resets the UI state with a new configuration dictionary."""
        self.config = config.copy()
        self.original_config = config.copy()
        self.initial_language = self.i18n.language
        self._init_ui_state()
        self.logger.debug("Settings dialog state has been reset with fresh config.")

    def _save_and_close(self) -> None:
        """Saves settings, handles startup task, and hides the window."""
        self.logger.info("Save button clicked. Finalizing and saving settings...")
        final_settings = self.get_settings()
        if not final_settings:
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.ERROR_GETTING_SETTINGS)
            return
        
        selected_language = final_settings.get("language")
        language_changed = selected_language and (selected_language != self.initial_language)

        if hasattr(self.parent_widget, 'handle_settings_changed'):
            try:
                self.parent_widget.handle_settings_changed(final_settings, save_to_disk=True)
            except Exception as e:
                self.logger.error(f"Failed to apply final settings: {e}", exc_info=True)
                QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Failed to apply settings: {e}")
                return
        
        if self.should_update_startup_task():
            requested_state = self.is_startup_requested()
            if hasattr(self.parent_widget, 'toggle_startup'):
                 self.parent_widget.toggle_startup(requested_state)

        if language_changed:
            QMessageBox.information(self, self.i18n.LANGUAGE_RESTART_TITLE, self.i18n.LANGUAGE_RESTART_MESSAGE)
            
        self.hide() # Use hide() instead of close()

    def _cancel_and_close(self) -> None:
        """Reverts any live preview changes and hides the window."""
        self.logger.info("Cancel button clicked. Reverting preview changes.")
        if hasattr(self.parent_widget, 'handle_settings_changed'):
            try:
                self.parent_widget.handle_settings_changed(self.original_config, save_to_disk=False)
            except Exception as e:
                 self.logger.error(f"Error reverting parent widget state on reject: {e}", exc_info=True)
        self.hide() # Use hide() instead of close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handles the window close event (e.g., 'X' button) to hide instead of close."""
        self.logger.debug("Close event triggered. Hiding settings window.")
        # Revert any live changes, just like clicking "Cancel"
        self._cancel_and_close()
        event.ignore()