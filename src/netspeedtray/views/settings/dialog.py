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

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QColor, QFont, QIcon, QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDialog, QFileDialog, QFontDialog,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget
)

# --- Custom Application Imports ---
from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.window_state import (restore_window_position, save_window_position,
                                             set_window_always_on_top)
from netspeedtray.utils.helpers import get_app_data_path, get_app_asset_path, reveal_in_explorer
from netspeedtray.utils.styles import is_dark_mode, shell_theme_text_color
from netspeedtray.utils.support_bundle import build_support_bundle
from netspeedtray.utils.dwm import apply_win11_chrome

# --- Settings Pages (2.0 IA: General · Widget · Appearance · Hardware · Network · Advanced) ---
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.pages.interfaces import InterfacesPage
from netspeedtray.views.settings.pages.general import GeneralPage
from netspeedtray.views.settings.pages.widget import WidgetPage
from netspeedtray.views.settings.pages.appearance import AppearancePage
from netspeedtray.views.settings.pages.hardware import HardwarePage
from netspeedtray.views.settings.pages.advanced import AdvancedPage
# units.py + colors.py are now embedded inside AppearancePage (the dialog reaches them via
# appearance_page.units_section / .colors_section for the Force-MB rule + color-picker routing).
from netspeedtray.views.widget.preview import PreviewWidget


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
    PAGE_NETWORK: int = 4   # Network Interfaces - hosts the "Data usage" / data-cap section

    # Reset all (Advanced). Settings no page shows - legacy / hand-edited / Monitor-flyout display
    # options - that Reset all still returns to factory, as 2.1.7 did:
    RESET_ALL_HIDDEN = (
        "text_alignment", "force_decimals", "dynamic_update_enabled", "dark_mode", "show_legend",
        "legend_position", "excluded_interfaces", "widget_cycle_interval", "secondary_clock_reserve_px",
        "monitor_graph_legend", "monitor_graph_smoothing", "monitor_graph_fixed_axis",
        "monitor_hw_graph_mode", "monitor_cpu_graph_color", "monitor_gpu_graph_color",
    )
    # ...and the app's STATE, which Reset all keeps: where windows and the widget sit, onboarding /
    # nudge / notice flags, the Monitor's remembered tab and period, pause, update bookkeeping, the
    # data-cap alert state, the schema version - and start_with_windows, which mirrors the Run key
    # (General page shows the live state).
    RESET_ALL_KEEPS = (
        "config_version", "position_x", "position_y", "tray_offset_x", "tray_offset_y",
        "settings_window_pos", "graph_window_pos", "app_activity_window_pos", "monitor_window_pos",
        "first_run_ever", "first_run_v2_seen", "tooltip_hint_shown_count", "temp_onboarding_dismissed",
        "location_onboarding_dismissed", "widgets_overlap_nudge_shown", "history_upgrade_notice_shown",
        "monitor_active_tab", "history_period", "history_period_slider_value", "history_period_chosen",
        "paused", "last_update_check", "skipped_version", "usage_alert_state", "start_with_windows",
        "pin_hint_shown_count", "side_bar_no_room_notice_shown",
    )

    def __init__(
        self,
        main_widget: "NetworkSpeedWidget",
        config: Dict[str, Any],
        version: str,
        i18n: constants.I18nStrings,
        available_interfaces: Optional[List[str]] = None,
        is_startup_enabled: bool = False,
        parent: QWidget | None = None,
        primary_interface: Optional[str] = None,
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
        self.available_interfaces = available_interfaces or []
        self.primary_interface = primary_interface   # the routing interface, marked in the picker
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
        margin = constants.layout.SETTINGS_CONTENT_MARGIN
        content_w = 560   # comfortable floor so the window is roomy and cards aren't pinned to their min
        floor_w = 0       # the widest page's floor: below it some page would scroll sideways
        try:
            # minimumSizeHint is the floor below which the page can't shrink
            # without forcing a horizontal scrollbar. Cards wrap their titles and
            # drop their control under the title when narrow, so these floors are
            # small (~300px) in every locale; the window may never go below them.
            page_widths = [
                self.stack.widget(i).widget().minimumSizeHint().width()
                for i in range(self.stack.count())
                if self.stack.widget(i).widget() is not None
            ]
            if page_widths:
                floor_w = max(page_widths)
                content_w = max(floor_w, content_w)   # keep the comfortable floor
        except Exception:
            pass
        # +40 leaves room for the vertical scrollbar plus a little slack so the
        # horizontal scrollbar stays hidden even with slightly wider translations.
        # The final width is bounded by the screen below, so this can't run away.
        sidebar_w = self.sidebar.maximumWidth()   # fixed; widened past SIDEBAR_WIDTH for long page names
        chrome_w = sidebar_w + (2 * margin) + 40
        desired_w = chrome_w + content_w

        # Resizing the window narrower never makes a page scroll sideways, and never clips the footer
        # (preview strip + Support bundle / Cancel / Save, whose labels are longer in some languages).
        footer_w = sidebar_w + self._content_widget.minimumSizeHint().width()
        min_w = max(640, chrome_w + floor_w, footer_w)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            min_w = min(min_w, avail.width())
        self.setMinimumSize(min_w, 400)
        if screen:
            width = min(desired_w, avail.width() - 80)
            # Open just tall enough for the short/moderate pages (General/Widget/Advanced ≈ 720px incl.
            # chrome) without scrolling, while staying compact. The genuinely long pages (Network,
            # Appearance) have too many options to ever fit a sane window, so they scroll by design.
            height = min(740, avail.height() - 80)
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
        # move-save would flush those un-applied edits to disk - which Cancel wouldn't
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
            # The sidebar never scrolls sideways either: it widens to its longest page name (de_DE
            # "Netzwerkschnittstellen" overflowed 200 px), and anything still too long elides.
            self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
            self.sidebar.setStyleSheet(style_utils.sidebar_style())
            # Keyboard-reachable (S4): Tab lands on the sidebar and the arrow keys switch pages; the
            # Ctrl+Tab family below switches from anywhere in the dialog.
            self.sidebar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._page_shortcuts = []
            for seq, delta in (("Ctrl+Tab", 1), ("Ctrl+PgDown", 1), ("Ctrl+Shift+Tab", -1),
                               ("Ctrl+Shift+Backtab", -1), ("Ctrl+PgUp", -1)):
                sc = QShortcut(QKeySequence(seq), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(lambda d=delta: self._step_page(d))
                self._page_shortcuts.append(sc)
            self.sidebar.setIconSize(QSize(16, 16))
            # (label, Segoe Fluent codepoint) - Win10-safe MDL2 glyphs; Hardware/Network match the
            # Monitor tabs for cross-window consistency. Settings/Personalize/Color/DeveloperTools/
            # FontSize/Ethernet/Repair.
            # 2.0 IA - 6 pages: Settings / View(Widget) / Personalize / DeveloperTools / Ethernet / Repair.
            self._sidebar_icons = [0xE713, 0xE8A9, 0xE771, 0xEC7A, 0xE839, 0xE90F]
            for label in (self.i18n.GENERAL_SETTINGS_GROUP, self.i18n.WIDGET_SETTINGS_GROUP,
                          self.i18n.APPEARANCE_SETTINGS_GROUP, self.i18n.HARDWARE_MONITORING_GROUP,
                          self.i18n.NETWORK_INTERFACES_GROUP, self.i18n.ADVANCED_SETTINGS_GROUP):
                self.sidebar.addItem(QListWidgetItem(label))
            self.sidebar.currentRowChanged.connect(lambda *_: self._tint_sidebar_icons())
            self.sidebar.setCurrentRow(0)
            self._tint_sidebar_icons()
            for row in range(self.sidebar.count()):     # the full name, should it ever elide
                self.sidebar.item(row).setToolTip(self.sidebar.item(row).text())
            needed = self.sidebar.sizeHintForColumn(0) + 2 * self.sidebar.frameWidth() + 4
            self.sidebar.setFixedWidth(min(max(constants.layout.SIDEBAR_WIDTH, needed),
                                           constants.layout.SIDEBAR_MAX_WIDTH))
            sidebar_layout.addWidget(self.sidebar)
            main_layout.addWidget(sidebar_container)

            # --- Content Area ---
            content_widget = QWidget()
            content_widget.setObjectName("contentWidget")
            self._content_widget = content_widget
            content_layout = QVBoxLayout(content_widget)
            # Generous horizontal inset around the card column (Win11 insets its content well clear of the
            # sidebar + window edge); matches the window-width budget so cards keep their width as the
            # inset grows. Vertical margin stays tighter so the preview strip + buttons sit close.
            _cm = constants.layout.SETTINGS_CONTENT_MARGIN
            content_layout.setContentsMargins(_cm, constants.layout.MAIN_MARGIN,
                                              _cm, constants.layout.MAIN_MARGIN)
            content_layout.setSpacing(constants.layout.MAIN_SPACING)

            # Page-level H1 (the native Win11 Settings header) - fixed above the scrolling content, giving
            # the page identity + the top breathing room WinUI opens with. Updated on sidebar selection.
            self._page_title = QLabel()
            _title_font = QFont()
            _title_font.setFamilies(["Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI"])
            _title_font.setPixelSize(24)
            _title_font.setWeight(QFont.Weight.DemiBold)
            self._page_title.setFont(_title_font)
            # Size + weight in the stylesheet too: the dialog's QSS sets a body font-size on every QLabel,
            # which beat setFont() and drew this H1 at 13 px (S9).
            self._page_title.setStyleSheet(
                f"color: {style_utils.semantic_colors()['text_primary']}; background: transparent;"
                f" font-size: 24px; font-weight: 600;")
            self._page_title.setContentsMargins(0, 4, 0, 6)
            try:   # seed from the initially-selected sidebar row (set before this label existed)
                self._page_title.setText(self.sidebar.item(max(0, self.sidebar.currentRow())).text())
            except Exception:
                pass
            content_layout.addWidget(self._page_title)

            self.stack = QStackedWidget()
            content_layout.addWidget(self.stack)

            # Instantiate Pages (2.0 IA)
            self.general_page = GeneralPage(self.i18n, self._schedule_settings_update)
            self.widget_page = WidgetPage(self.i18n, self._schedule_settings_update)
            self.appearance_page = AppearancePage(
                self.i18n,
                self._schedule_settings_update,
                self._open_font_dialog,
                self._open_color_dialog
            )
            # The old Color Coding + Display(Units) pages are now sections INSIDE Appearance. Keep
            # references so the existing color-picker routing + Force-MB rule reach the same objects.
            self.colors_page = self.appearance_page.colors_section
            self.units_page = self.appearance_page.units_section

            self.hardware_page = HardwarePage(self.i18n, self._schedule_settings_update,
                                              self._open_color_dialog,
                                              lhm_status_provider=self._lhm_status)
            self.interfaces_page = InterfacesPage(
                self.i18n,
                self.available_interfaces,
                self._schedule_settings_update,
                primary_interface=self.primary_interface,
            )
            self.advanced_page = AdvancedPage(
                self.i18n,
                self._schedule_settings_update,
                reset_page_callback=self._reset_advanced_page,
                reset_all_callback=self._reset_all_to_defaults,
                delete_history_callback=self._delete_all_history,
                history_path_callback=self._history_db_path,
                notify_callback=self._notify_backup_result,
            )
            # The Record-hardware-history card greys out live as CPU/GPU monitoring is switched.
            # The battery hover toggle counts too (#323) - see _sync_record_card.
            self.hardware_page.monitor_cpu.toggled.connect(self._sync_record_card)
            self.hardware_page.monitor_gpu.toggled.connect(self._sync_record_card)
            self.general_page.power_display.toggled.connect(self._sync_record_card)
            # Safety net for Delete all history: if the worker never answers, give the button back.
            self._delete_timeout = QTimer(self)
            self._delete_timeout.setSingleShot(True)
            self._delete_timeout.setInterval(constants.timeouts.DB_DELETE_HISTORY_UI_TIMEOUT_MS)
            self._delete_timeout.timeout.connect(self._on_delete_timeout)

            # Add pages wrapped in scroll areas (order matches sidebar)
            for page in [
                self.general_page,       # 0 - General
                self.widget_page,        # 1 - Widget (layout, placement, mini graph)
                self.appearance_page,    # 2 - Appearance (font/units/color-coding/background/arrows)
                self.hardware_page,      # 3 - Hardware
                self.interfaces_page,    # 4 - Network
                self.advanced_page,      # 5 - Advanced
            ]:
                self.stack.addWidget(self._wrap_in_scroll(page))

            # Hairline that anchors the fixed footer (preview + command buttons) below the scrolling
            # content - the native Win11 command-bar separator.
            footer_sep = QWidget()
            footer_sep.setObjectName("footerSep")
            footer_sep.setFixedHeight(1)
            footer_sep.setStyleSheet(
                f"#footerSep {{ background-color: {style_utils.semantic_colors()['card_stroke']}; }}")
            content_layout.addWidget(footer_sep)

            # --- Live preview strip (C5) - a faithful, inert render of the widget that
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

    def _sync_record_card(self, _on: bool = False) -> None:
        """Grey "Record hardware history" by the LIVE Hardware-page toggles - never by a config
        snapshot, which is stale once the user flips CPU/GPU in this session (S6). The battery hover
        toggle counts too: with CPU/GPU monitoring off it governs battery recording, and a user who
        switched the record card off could not switch it back on (#323 review)."""
        self.advanced_page.set_hardware_monitoring_active(
            self.hardware_page.monitor_cpu.isChecked()
            or self.hardware_page.monitor_gpu.isChecked()
            or self.general_page.power_display.isChecked())

    @staticmethod
    def _wrap_in_scroll(page: QWidget) -> QScrollArea:
        """Wraps a settings page in a frameless, transparent scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        # Let the dark content background show through the scroll region so cards FLOAT on it (the page
        # otherwise renders an opaque mid-gray that the cards blend into - the "cards section is a
        # different shade" bug). Mirrors how the Monitor's Overview scroll content is made transparent.
        scroll.viewport().setAutoFillBackground(False)
        page.setStyleSheet("background: transparent;")   # cards/inputs keep their own fill (more specific)
        scroll.setWidget(page)
        # Scrolling the page should scroll it - not change the slider/combo/spin box the cursor
        # happens to pass over. The guard forwards stray wheel events to this scroll area.
        from netspeedtray.utils.components import install_wheel_guard
        install_wheel_guard(page)
        return scroll

    def _init_ui_state(self) -> None:
        """Loads current configuration into all UI elements and restores window position."""
        self.logger.debug("Initializing UI state from config...")
        # Each page loads on its own (C1): one page raising used to abort the whole load, leaving
        # every LATER page on its blank constructor defaults - and Save then wrote those blanks over
        # ~25 real settings. A page that fails is remembered, and get_settings() leaves its keys at
        # their loaded values instead of reading them back from half-initialised controls.
        self._failed_pages = set()
        for name, load in (
            ("general", lambda: self.general_page.load_settings(self.config, self.startup_enabled_initial_state)),
            ("widget", lambda: self.widget_page.load_settings(self.config)),
            ("appearance", lambda: self.appearance_page.load_settings(self.config)),   # + units + colors
            ("hardware", lambda: self.hardware_page.load_settings(self.config)),
            ("interfaces", lambda: self.interfaces_page.load_settings(self.config)),
            ("advanced", lambda: self.advanced_page.load_settings(self.config)),
        ):
            try:
                load()
            except Exception as e:
                self._failed_pages.add(name)
                self.logger.error("Settings page '%s' failed to load; its settings will be kept as "
                                  "they are on Save: %s", name, e, exc_info=True)
        try:
            self._refresh_metrics_status()

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
        self.widget_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.appearance_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.interfaces_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.hardware_page.layout_changed.connect(self._adjust_size_and_reposition)
        self.units_page.unit_type.currentIndexChanged.connect(
            lambda _i: self.colors_page.set_unit_type(self.units_page.unit_type.currentData()))
        # Enabling a hardware monitor nudges the Widget page out of network-only (display-mode lives
        # there now), so the freshly-enabled stat is actually visible.
        try:
            self.hardware_page.hardware_enabled.connect(self.widget_page.ensure_hardware_visible)
        except Exception:
            pass

    def _step_page(self, delta: int) -> None:
        """Ctrl+Tab / Ctrl+PgDn (+1) and Ctrl+Shift+Tab / Ctrl+PgUp (-1): the next / previous page,
        wrapping - the keyboard way through the sidebar (S4)."""
        n = self.sidebar.count()
        if n:
            self.sidebar.setCurrentRow((max(0, self.sidebar.currentRow()) + delta) % n)

    def _on_sidebar_selection_changed(self, row: int) -> None:
        """Handles sidebar row changes to switch the stacked page + retitle the page header."""
        self.stack.setCurrentIndex(row)
        try:
            item = self.sidebar.item(row)
            if item is not None and hasattr(self, "_page_title"):
                self._page_title.setText(item.text())
        except Exception:
            pass

    def _tint_sidebar_icons(self) -> None:
        """Re-render each sidebar glyph in the right color (a QIcon pixmap can't be tinted by QSS):
        accent on the selected row to match the accent indicator bar, text_secondary on the rest."""
        try:
            c = style_utils.semantic_colors()
            accent = style_utils.get_accent_color().name()
            current = self.sidebar.currentRow()
            for i, cp in enumerate(getattr(self, "_sidebar_icons", [])):
                item = self.sidebar.item(i)
                if item is not None:
                    item.setIcon(style_utils.fluent_icon(cp, 16, accent if i == current else c["text_secondary"]))
        except Exception as e:
            self.logger.debug("sidebar icon tint failed: %s", e)

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
        # The emit applied the settings synchronously (incl. starting/stopping the metrics endpoint),
        # so the endpoint's status now describes what the user just chose.
        self._refresh_metrics_status()

    def _lhm_status(self):
        """(status, port) of the running LibreHardwareMonitor client, for Settings > Hardware."""
        mt = getattr(self.parent_widget, "monitor_thread", None)
        status, port = getattr(mt, "lhm_status", None), getattr(mt, "lhm_port", None)
        return (status, port) if isinstance(status, str) and isinstance(port, int) else None

    def _refresh_metrics_status(self) -> None:
        """Show the local metrics endpoint's live status (listening / port in use) under its address."""
        try:
            from netspeedtray.core.metrics_endpoint import EndpointStatus
            ep = getattr(self.parent_widget, "metrics_endpoint", None)
            status = getattr(ep, "status", None) if ep is not None else None
            self.advanced_page.set_metrics_endpoint_status(
                status if isinstance(status, EndpointStatus) else None)
        except Exception as e:
            self.logger.debug("metrics endpoint status unavailable: %s", e)

    def _update_preview(self, settings: Dict[str, Any]) -> None:
        """Refresh the live-preview strip to reflect the current (merged) settings."""
        pv = getattr(self, "preview_widget", None)
        if pv is None:
            return
        try:
            # Preview parity on a side (LEFT/RIGHT) taskbar: as wide as the bar, laid out as there.
            pv.set_bar_width(self._docked_side_bar_width())
            merged = {**self.config, **(settings or {})}
            pv.set_config(merged)
            self.widget_page.set_display_mode_note(self._display_mode_note_kind(merged, pv.side_fit))
        except Exception as e:
            self.logger.debug("preview update failed: %s", e)

    @staticmethod
    def _display_mode_note_kind(settings: Dict[str, Any], side_fit) -> Optional[str]:
        """Which note goes under Widget > Display Mode (WidgetPage.set_display_mode_note). The
        hover-card switch wins on every edge - it is the reason the bar shows network only, and the
        more specific explanation on a side bar too; else the side-bar note when a docked side bar
        has no room for the hardware; else none."""
        from netspeedtray.utils.helpers import hardware_in_hover_card_active
        if hardware_in_hover_card_active(settings):
            return "hover_card"
        if side_fit is not None and side_fit.hides_hardware:
            return "side_bar"
        return None

    def _docked_side_bar_width(self) -> Optional[int]:
        """The live widget's width when it is docked to a side taskbar (it is exactly the bar's
        thickness there), else None."""
        w = self.parent_widget
        try:
            if getattr(w, "_cached_layout_mode", None) == "vertical" and int(w.width()) > 0:
                return int(w.width())
        except Exception:
            pass
        return None

    def _pages(self) -> list:
        """The pages whose controls hold real values, in sidebar order (the Appearance page includes
        the embedded units + colour sections). A page that failed to load is left out: its keys are
        never written from controls that never received them (C1)."""
        failed = getattr(self, "_failed_pages", set())
        return [page for name, page in (
            ("general", self.general_page), ("widget", self.widget_page),
            ("appearance", self.appearance_page), ("hardware", self.hardware_page),
            ("interfaces", self.interfaces_page), ("advanced", self.advanced_page),
        ) if name not in failed]

    def get_settings(self) -> Dict[str, Any]:
        """Collects settings from all pages."""
        try:
            settings = self.config.copy()
            for page in self._pages():
                settings.update(page.get_settings())

            # Save current window position
            settings["settings_window_pos"] = {"x": self.pos().x(), "y": self.pos().y()}
            
            # The text colour is the user's own once they picked it in the dialog OR typed a different
            # hex (S3: a typed one stayed "automatic" and the next launch's theme sync reset it).
            typed = str(settings.get("default_color", "")).upper() != \
                str(self.config.get("default_color", "")).upper()
            if self._user_chose_default_color or typed:
                settings["color_is_automatic"] = False
            else:
                # The dialog's working baseline: equal to original_config on open, the factory value
                # after Reset all (which keeps original_config for Cancel - S5).
                settings["color_is_automatic"] = self.config.get("color_is_automatic", True)

            # A typed / picked colour set color_is_automatic False above, so the live theme colour
            # below never overrides it (S3 + #405).
            return self._with_live_auto_color(settings)
        except Exception as e:
            self.logger.error(f"Error collecting settings: {e}", exc_info=True)
            return {}

    def _with_live_auto_color(self, cfg: Dict[str, Any],
                              baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """``cfg`` with an AUTOMATIC text colour taken from the current Windows theme.

        The dialog loads ``default_color`` once, when it opens. If the Windows theme flips while it
        is open, the theme manager re-colours the widget - and the next live-preview tick (or Cancel,
        which re-applies ``original_config``) put the stale colour straight back. While the colour is
        automatic and the user has not changed it here (no pick, no edit of the field), the theme owns
        it, so the dialog passes the theme's colour instead of pinning the one it was opened with.

        ``baseline`` is the config ``cfg``'s colour is compared with to tell "untouched" from "typed":
        the dialog's working config by default (after Reset all that is the factory set, not the
        pre-dialog config Cancel restores), ``original_config`` for the Cancel revert."""
        # A colour picked in THIS session belongs to the session's settings - not to the pre-dialog
        # config a Cancel restores: on that path (baseline given) the pick is being undone, so an
        # automatic colour there still follows the theme (review item 2: pick, then Cancel, left a
        # white-but-automatic text colour on a light taskbar).
        if (self._user_chose_default_color and baseline is None) or not cfg.get("color_is_automatic", True):
            return cfg
        base = self.config if baseline is None else baseline
        opened_with = str(base.get("default_color", "") or "").upper()
        if str(cfg.get("default_color", "") or "").upper() != opened_with:
            return cfg   # the user typed a colour into the field
        live = shell_theme_text_color()
        if not live or live.upper() == opened_with:
            return cfg
        out = dict(cfg)
        out["default_color"] = live
        return out

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
        elif key_name in HardwarePage.LABEL_COLOR_KEYS:
            current_settings = self.hardware_page.get_settings()
        else:
            current_settings = self.appearance_page.get_settings()
            
        initial_hex = current_settings.get(key_name, "#FFFFFF")
        
        color = QColorDialog.getColor(QColor(initial_hex), self, self.i18n.SELECT_COLOR_TITLE)
        if color.isValid():
            new_hex = color.name().upper()
            if key_name in ["high_speed_color", "low_speed_color"]:
                self.colors_page.set_color_input(key_name.replace("_color", ""), new_hex)
            elif key_name in HardwarePage.LABEL_COLOR_KEYS:
                self.hardware_page.set_color_input(key_name, new_hex)
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
                f"{self.i18n.STATS_DETAIL_EXPORT_ZIP_LABEL} (*.zip)",   # shared with the stats export
            )

            if not dest_path:
                return

            written = build_support_bundle(
                destination_zip=Path(dest_path),
                config=self.config,
            )
            self.logger.info("Support bundle written to %s", written)
            # The next step is attaching the zip to an issue, so offer to show it in Explorer.
            box = QMessageBox(QMessageBox.Icon.Information, self.i18n.SUCCESS_TITLE,
                              self.i18n.SUPPORT_BUNDLE_SUCCESS_MESSAGE.format(file_path=str(written)),
                              parent=self)
            open_btn = box.addButton(self.i18n.STATS_DETAIL_OPEN_FOLDER, QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if box.clickedButton() is open_btn:
                reveal_in_explorer(str(written))
        except Exception as e:
            self.logger.error("Failed to export support bundle: %s", e, exc_info=True)
            QMessageBox.critical(
                self,
                self.i18n.ERROR_TITLE,
                self.i18n.SUPPORT_BUNDLE_ERROR_MESSAGE.format(error=str(e)),
            )

    def update_interface_list(self, new_interfaces: List[str],
                              primary_interface: Optional[str] = None) -> None:
        """Updates the list of available network interfaces and the marked primary one (None
        clears the mark - no route right now)."""
        self.available_interfaces = new_interfaces
        self.primary_interface = primary_interface
        if hasattr(self, 'interfaces_page'):
            # Read what the page shows NOW, before the rebuild throws its checkboxes away (S2): reading
            # after it found no boxes ticked, so re-opening Settings while it was open (tray, Monitor
            # "Set a limit" link) emptied selected_interfaces and a Save made the widget show 0.
            current = self.get_settings() or self.config
            self.interfaces_page.update_interface_list(new_interfaces, primary_interface)
            self.interfaces_page.load_settings(current)

    def reset_with_config(self, config: Dict[str, Any], is_startup_enabled: bool,
                          keep_baseline: bool = False) -> None:
        """Resets the UI state with a new configuration dictionary. ``keep_baseline`` (Reset all) keeps
        the pre-dialog config as what Cancel restores (S5); a fresh open replaces it."""
        self.config = config.copy()
        if not keep_baseline:
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
        self._sync_record_card()   # load_settings greyed it from the stale snapshot (S6)
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
        factory = constants.config.defaults.DEFAULT_CONFIG
        # Reset every SETTING - the keys the pages own plus RESET_ALL_HIDDEN, the settings no page
        # shows (2.1.7 reset them too; without Reset all the user has no way back to their defaults)
        # - and keep RESET_ALL_KEEPS, the app's state, as it is. test_rc_settings_fixes requires every
        # DEFAULT_CONFIG key to be in one of the three, so a new key forces a decision.
        owned = {"color_is_automatic"} | set(self.RESET_ALL_HIDDEN)
        for page in self._pages():
            owned |= set(page.get_settings())
        owned -= set(self.RESET_ALL_KEEPS)
        reset = dict(self.config)
        for k in owned:
            if k in factory:
                reset[k] = factory[k]
        self._user_chose_default_color = False
        # keep_baseline: Cancel after Reset all restores the config the dialog opened with (S5), and
        # the window-position save on Cancel then writes THAT, not the defaults, to disk.
        self.reset_with_config(reset, self.startup_enabled_initial_state, keep_baseline=True)
        self._schedule_settings_update()

    def _confirm_delete_history(self) -> Optional[bool]:
        """The destructive-action confirmation. Returns None if cancelled, else whether the user also
        ticked "reset this period's data usage" (unchecked by default: the odometer is the data cap's
        billing source, D5, and is the one thing here that history retention never touches)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.i18n.DELETE_HISTORY_CONFIRM_TITLE)
        box.setText(self.i18n.DELETE_HISTORY_CONFIRM_TEXT)
        # Said plainly: after a delete the cap still reads e.g. "120 of 200 GB" while "This month"
        # (computed from the now-empty speed history) drops to ~0 - by design, not a bug.
        box.setInformativeText(self.i18n.DELETE_HISTORY_USAGE_NOTE)
        usage = QCheckBox(self.i18n.DELETE_HISTORY_INCLUDE_USAGE)
        usage.setChecked(False)
        box.setCheckBox(usage)
        delete_btn = box.addButton(self.i18n.DELETE_HISTORY_CONFIRM_BUTTON,
                                   QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(self.i18n.CANCEL_BUTTON, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.setEscapeButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not delete_btn:
            return None
        return usage.isChecked()

    def _history_db_path(self):
        """The live history file for Settings > Advanced > Back up history (#334), or None."""
        ws = getattr(self.parent_widget, "widget_state", None)
        return getattr(ws, "history_db_path", None) if ws is not None else None

    def _notify_backup_result(self, title: str, body: str, path) -> None:
        """A backup that finished while Settings was closed: the widget's non-focus flyout (#334)."""
        notify = getattr(self.parent_widget, "show_backup_notice", None)
        if notify is not None:
            notify(title, body, path)

    def _delete_all_history(self) -> None:
        """Settings > Advanced > Delete all history (2.2 item C). Immediate - not deferred to Save."""
        ws = getattr(self.parent_widget, "widget_state", None)
        if ws is None or not hasattr(ws, "delete_all_history") or not ws.can_delete_history():
            # Checked BEFORE asking: no point confirming a delete nobody will carry out, and the
            # button must never sit disabled waiting for a worker that isn't there.
            self.logger.error("Delete all history: the history database is not available for writing.")
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.DELETE_HISTORY_FAILED)
            return
        include_usage = self._confirm_delete_history()
        if include_usage is None:
            return
        if not getattr(self, "_history_deleted_connected", False):
            try:
                ws.history_deleted.connect(self._on_history_deleted)
                self._history_deleted_connected = True
            except Exception as e:
                self.logger.debug("history_deleted not connectable: %s", e)
        self.advanced_page.delete_history_btn.setEnabled(False)   # re-enabled on completion/timeout
        self._delete_timeout.start()
        # The cap alerts are re-armed only once the delete has SUCCEEDED (_on_history_deleted): a
        # failed delete leaves the odometer as it was, so its alert state must stay too.
        self._delete_include_usage = bool(include_usage)
        ws.delete_all_history(include_usage=include_usage)
        try:
            self.parent_widget.update()   # repaint the mini graph from the now-empty series
        except Exception:
            pass

    def _on_history_deleted(self, status: str) -> None:
        """The worker's answer - a DatabaseWorker.DELETE_* status - reported as it is: 'compact_pending'
        means the rows are gone but the file is still being cleaned (the worker keeps retrying)."""
        from netspeedtray.core.database import DatabaseWorker
        self._delete_timeout.stop()
        try:
            self.advanced_page.delete_history_btn.setEnabled(True)
        except Exception:
            pass
        include_usage = getattr(self, "_delete_include_usage", False)
        self._delete_include_usage = False
        if status != DatabaseWorker.DELETE_FAILED and include_usage:
            # The odometer restarted at 0, so re-arm the 80%/100% cap alerts for this period. Written
            # into BOTH dialog snapshots too - Save writes self.config back, Cancel original_config,
            # and either would otherwise restore the stale "already alerted" state.
            self.config["usage_alert_state"] = ""
            self.original_config["usage_alert_state"] = ""
            if hasattr(self.parent_widget, "update_config"):
                self.parent_widget.update_config({"usage_alert_state": ""})
        if status != DatabaseWorker.DELETE_FAILED:
            # The hover card caches its today/this-month totals; they were computed from the
            # history that is now gone.
            if hasattr(self.parent_widget, "invalidate_usage_cache"):
                try:
                    self.parent_widget.invalidate_usage_cache()
                except Exception as e:
                    self.logger.debug("hover usage cache not invalidated: %s", e)
        if status == DatabaseWorker.DELETE_DONE:
            QMessageBox.information(self, self.i18n.SUCCESS_TITLE, self.i18n.DELETE_HISTORY_DONE)
        elif status == DatabaseWorker.DELETE_COMPACT_PENDING:
            QMessageBox.information(self, self.i18n.SUCCESS_TITLE, self.i18n.DELETE_HISTORY_DONE_COMPACTING)
        elif status == DatabaseWorker.DELETE_BACKUP_KEPT_UPGRADE:
            # Honest: the upgrade's backup is still needed by an unfinished upgrade - not "shortly".
            QMessageBox.information(self, self.i18n.SUCCESS_TITLE, self.i18n.DELETE_HISTORY_DONE_BACKUP_UPGRADE)
        elif status == DatabaseWorker.DELETE_BACKUP_KEPT:
            QMessageBox.warning(self, self.i18n.SUCCESS_TITLE, self.i18n.DELETE_HISTORY_DONE_BACKUP_KEPT)
        else:
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.DELETE_HISTORY_FAILED)

    def _on_delete_timeout(self) -> None:
        """No answer from the write thread in DB_DELETE_HISTORY_UI_TIMEOUT_MS: give the button back
        and say so, rather than leaving Delete disabled for the rest of the session."""
        self.logger.warning("Delete all history: no answer from the database worker; giving up waiting.")
        try:
            self.advanced_page.delete_history_btn.setEnabled(True)
        except Exception:
            pass
        QMessageBox.critical(self, self.i18n.ERROR_TITLE, self.i18n.DELETE_HISTORY_FAILED)

    def _save_and_close(self) -> None:
        """Saves settings and closes."""
        self.logger.debug("Save and close requested.")
        try:
            final_settings = self.get_settings()
            if not final_settings:
                # get_settings() returns {} only when a page raised while collecting. Don't silently
                # no-op (the user clicked Save and would see nothing happen) - tell them it failed (#18).
                self.logger.warning("Could not retrieve settings from pages.")
                QMessageBox.critical(
                    self, self.i18n.ERROR_TITLE,
                    getattr(self.i18n, "SETTINGS_SAVE_FAILED",
                            "Settings could not be saved - a settings page failed to provide its "
                            "values, so your changes were not applied. See the log for details."))
                return

            # Does a restart actually change the language? Compare EFFECTIVE locales, not raw
            # config values. Two ways to get this wrong, and the naive fixes hit one each:
            #   * `selected and (selected != initial)` - the old code - treated None ("Auto-detect")
            #     as "unchanged", so switching TO auto-detect never prompted (#234).
            #   * A plain `!=` on raw values prompts whenever the value changes but the language
            #     does not - e.g. a Korean user pinning the ko_KR that auto-detect already resolved,
            #     which is the common path now that auto-detect works.
            # i18n.language is the locale actually loaded in this process, so it is both the right
            # baseline and immune to going stale when this cached dialog is reopened.
            selected_language = final_settings.get("language")
            i18n_cls = constants.i18n.I18nStrings
            effective_language = (i18n_cls.resolve_language(selected_language) if selected_language
                                  else i18n_cls.detect_system_language())
            language_changed = effective_language != self.i18n.language

            # Apply settings to the main widget/application
            if hasattr(self.parent_widget, 'handle_settings_changed'):
                self.parent_widget.handle_settings_changed(final_settings, save_to_disk=True)
            self._refresh_metrics_status()
            
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
        self.reject()

    def reject(self) -> None:
        """Every non-Save close - Cancel, the title-bar X, and Esc (QDialog routes Esc straight here) -
        reverts the live preview first (S1: Esc used to close with the previewed changes still applied)."""
        self._revert_live_preview()
        super().reject()

    def _revert_live_preview(self) -> None:
        self._update_timer.stop()   # a pending throttled apply must not land after the revert
        if hasattr(self.parent_widget, 'handle_settings_changed'):
            self.parent_widget.handle_settings_changed(
                self._with_live_auto_color(self.original_config, baseline=self.original_config),
                save_to_disk=False)
        # Save the position AFTER the revert: handle_settings_changed merges
        # original_config back into the live config, which would otherwise clobber
        # settings_window_pos with the value from when the dialog opened.
        save_window_position(self, self.parent_widget, "settings_window_pos")

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
        # Always-on-top (#213). Applied here rather than as a Qt window flag: the dialog is a cached
        # instance that is hidden rather than destroyed, and setWindowFlags on a live window
        # re-creates the native handle - which is exactly what makes frame-vs-client geometry drift.
        set_window_always_on_top(self, bool(self.config.get("keep_windows_on_top", False)))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cancel_and_close()
        event.ignore()
