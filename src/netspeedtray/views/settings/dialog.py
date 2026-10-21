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
from PyQt6.QtGui import QColor, QFont, QIcon, QCloseEvent, QShowEvent
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
from netspeedtray.utils.styles import is_dark_mode
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
            self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
            self._page_title.setStyleSheet(
                f"color: {style_utils.semantic_colors()['text_primary']}; background: transparent;")
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
                                              self._open_color_dialog)
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
            )
            # The Record-hardware-history card greys out live as CPU/GPU monitoring is switched.
            def _sync_record_card(_on: bool = False) -> None:
                self.advanced_page.set_hardware_monitoring_active(
                    self.hardware_page.monitor_cpu.isChecked() or self.hardware_page.monitor_gpu.isChecked())
            self.hardware_page.monitor_cpu.toggled.connect(_sync_record_card)
            self.hardware_page.monitor_gpu.toggled.connect(_sync_record_card)
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
        try:
            self.general_page.load_settings(self.config, self.startup_enabled_initial_state)
            self.widget_page.load_settings(self.config)
            self.appearance_page.load_settings(self.config)   # also loads the embedded units + colors
            self.hardware_page.load_settings(self.config)
            self.interfaces_page.load_settings(self.config)
            self.advanced_page.load_settings(self.config)
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
        # Enabling a hardware monitor nudges the Widget page out of network-only (display-mode lives
        # there now), so the freshly-enabled stat is actually visible.
        try:
            self.hardware_page.hardware_enabled.connect(self.widget_page.ensure_hardware_visible)
        except Exception:
            pass

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

    def get_settings(self) -> Dict[str, Any]:
        """Collects settings from all pages."""
        try:
            settings = self.config.copy()
            settings.update(self.general_page.get_settings())
            settings.update(self.widget_page.get_settings())
            settings.update(self.appearance_page.get_settings())   # includes embedded units + colors
            settings.update(self.hardware_page.get_settings())
            settings.update(self.interfaces_page.get_settings())
            settings.update(self.advanced_page.get_settings())

            # Save current window position
            settings["settings_window_pos"] = {"x": self.pos().x(), "y": self.pos().y()}
            
            # Re-implement color logic check:
            if self._user_chose_default_color:
                settings["color_is_automatic"] = False
            else:
                 settings["color_is_automatic"] = self.original_config.get("color_is_automatic", True)
                 
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
            self.interfaces_page.update_interface_list(new_interfaces, primary_interface)
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
        # Always-on-top (#213). Applied here rather than as a Qt window flag: the dialog is a cached
        # instance that is hidden rather than destroyed, and setWindowFlags on a live window
        # re-creates the native handle - which is exactly what makes frame-vs-client geometry drift.
        set_window_always_on_top(self, bool(self.config.get("keep_windows_on_top", False)))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._cancel_and_close()
        event.ignore()
