"""
Tray Icon Manager Module for NetSpeedTray.

This module encapsulates the logic for the application's icon and context menu,
which simulate a system tray experience. It handles icon loading, menu creation,
and smart menu positioning.
"""

import os
import sys
import logging
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QObject, QPoint, Qt, QRect
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import QMenu, QApplication, QWidget

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import set_rounded_corners, set_dark_titlebar

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.constants.i18n import I18nStrings


class TrayIconManager(QObject):
    """
    Manages the application icon and context menu logic.
    """
    def __init__(self, parent_widget: 'NetworkSpeedWidget', i18n: 'I18nStrings'):
        super().__init__(parent_widget)
        self.widget = parent_widget
        self.i18n = i18n
        self.logger = logging.getLogger("NetSpeedTray.TrayIconManager")
        
        self.context_menu: Optional[QMenu] = None
        self.app_icon: Optional[QIcon] = None
        
        # State tracking
        self.is_context_menu_visible: bool = False

        # Retrieve actions for external use if needed (e.g., toggling text)
        self.pause_action: Optional[QAction] = None
        self.pause_separator: Optional[QAction] = None
        # [(QAction, Segoe-Fluent-codepoint)] — icons are (re)tinted to the text colour on each open.
        self._menu_icon_actions: list = []

    def initialize(self) -> None:
        """Loads the icon and initializes the context menu."""
        self._load_and_set_icon()
        self._init_context_menu()

    def _load_and_set_icon(self) -> None:
        """
        Loads the application icon from resources and sets it on the parent widget.
        """
        try:
            # Determine base path for assets
            if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                base_path = sys._MEIPASS
            else:
                # Assuming this file is in src/netspeedtray/core/
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
                base_path = project_root

            icon_filename = constants.app.ICON_FILENAME
            icon_path = os.path.join(base_path, "assets", icon_filename)
            icon_path = os.path.normpath(icon_path)

            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.widget.setWindowIcon(self.app_icon)
                self.logger.debug("Application icon loaded and set successfully.")
            else:
                self.logger.warning("Application icon not found at '%s'. Using default system icon.", icon_path)
        except Exception as e:
            self.logger.error("Error loading application icon: %s", e, exc_info=True)

    def _init_context_menu(self) -> None:
        """Backbone for context menu creation."""
        self.logger.debug("Initializing context menu in manager...")
        try:
            self.context_menu = QMenu(self.widget)

            # The menu is a MAP, not a flat list — tiers, separated, so a new user can read
            # what the app does straight from the right-click. Live data usage (today / this
            # month / data-cap progress) now lives in the hover card over the widget, and the
            # cap is configured in Settings → Network — so the menu stays calm.

            # --- Tier 1: The app's surfaces (the windows most users never find) ---
            settings_action = self.context_menu.addAction(self.i18n.SETTINGS_MENU_ITEM)
            if hasattr(self.widget, 'show_settings'):
                settings_action.triggered.connect(self.widget.show_settings)

            # The unified Monitor (Overview / Network / Hardware) now replaces the separate Graph + App
            # Activity windows — its three tabs cover both — so the menu is one calm entry (and a
            # double-click on the widget opens it too).
            monitor_action = self.context_menu.addAction(self.i18n.SHOW_MONITOR_MENU_ITEM)
            if hasattr(self.widget, 'open_monitor_window'):
                monitor_action.triggered.connect(self.widget.open_monitor_window)

            self.context_menu.addSeparator()

            # --- Control (opt-in) — Pause/Resume plus its own divider, shown only when the user
            # has turned it on in Settings (pause_in_menu). Default off keeps the menu calm; the
            # pair's visibility is refreshed on every open in _refresh_dynamic_items. ---
            self.pause_action = self.context_menu.addAction(self.i18n.PAUSE_MENU_ITEM)
            self.pause_action.triggered.connect(self._toggle_pause)
            self.pause_separator = self.context_menu.addSeparator()

            # --- Tier 4: App-level (least frequent) ---
            update_action = self.context_menu.addAction(self.i18n.CHECK_FOR_UPDATES_MENU_ITEM)
            if hasattr(self.widget, 'check_for_updates'):
                update_action.triggered.connect(self.widget.check_for_updates)

            support_action = self.context_menu.addAction(self.i18n.SUPPORT_MENU_ITEM)
            if hasattr(self.widget, 'show_support_dialog'):
                support_action.triggered.connect(self.widget.show_support_dialog)

            self.context_menu.addSeparator()

            # --- Exit ---
            exit_action = self.context_menu.addAction(self.i18n.EXIT_MENU_ITEM)
            app_instance = QApplication.instance()
            if app_instance:
                # We connect to widget.close() usually, which handles cleanup
                exit_action.triggered.connect(self.widget.fully_exit_application)
            else:
                exit_action.setEnabled(False)

            # Native Win11 menu iconography: a Segoe Fluent glyph beside each item (tinted to the text
            # colour per theme in _apply_menu_style, which runs on every open). The pause glyph swaps
            # pause<->play with the action's state in _refresh_dynamic_items.
            self._menu_icon_actions = [
                (settings_action, 0xE713),    # Settings (gear)
                (monitor_action, 0xE9D9),     # Monitor (chart)
                (self.pause_action, 0xE769),  # Pause (→ play when paused)
                (update_action, 0xE895),      # Check for updates (sync)
                (support_action, 0xEB51),     # Support (heart)
                (exit_action, 0xE7E8),        # Exit (power)
            ]

            self.logger.debug("Context menu initialized successfully.")
        except Exception as e:
            self.logger.error("Error initializing context menu: %s", e, exc_info=True)

    def _toggle_pause(self, _checked: bool = False) -> None:
        """Toggle the widget's paused state from the tray menu."""
        try:
            if getattr(self.widget, "is_paused", False):
                self.widget.resume()
            else:
                self.widget.pause()
        except Exception as e:
            self.logger.error("Error toggling pause: %s", e, exc_info=True)

    def _apply_menu_style(self) -> None:
        """Style the context menu to fit Windows 11: a dark, rounded card with generous padding,
        rounded-pill item highlights on a subtle NEUTRAL hover (the Win11 taskbar/jump-list look, not
        a saturated accent block), and quiet separators. Theme- and accent-aware — re-applied on each
        open so it tracks an OS light/dark switch. Native rounded window corners are added in
        show_context_menu via DWM; the QSS radius is the fallback on Win10 / if DWM no-ops."""
        if not self.context_menu:
            return
        try:
            c = su.semantic_colors()
            self.context_menu.setStyleSheet(
                f"QMenu {{ background: {c['card_bg']}; color: {c['text_primary']};"
                f" border: 1px solid {c['card_stroke']}; border-radius: 8px; padding: 4px; }}"
                f" QMenu::item {{ padding: 7px 30px 7px 14px; margin: 1px 4px; border-radius: 4px; }}"
                f" QMenu::item:selected {{ background: {c['subtle_fill']}; color: {c['text_primary']}; }}"
                f" QMenu::item:disabled {{ color: {c['text_secondary']}; }}"
                f" QMenu::separator {{ height: 1px; background: {c['card_stroke']}; margin: 4px 10px; }}")
            # Tint the Fluent glyphs to the current text colour so they track the OS light/dark theme.
            for action, cp in self._menu_icon_actions:
                action.setIcon(su.fluent_icon(cp, 16, c['text_primary']))
        except Exception as e:
            self.logger.debug("Menu style apply failed: %s", e)

    def _refresh_dynamic_items(self) -> None:
        """Update menu items whose text depends on live state, just before showing."""
        try:
            # Pause/Resume is opt-in: show the action + its divider only when enabled in Settings.
            show_pause = bool(getattr(self.widget, "config", {}).get("pause_in_menu", False))
            if self.pause_action is not None:
                self.pause_action.setVisible(show_pause)
                paused = getattr(self.widget, "is_paused", False)
                self.pause_action.setText(
                    self.i18n.RESUME_MENU_ITEM if paused else self.i18n.PAUSE_MENU_ITEM
                )
                # Match the glyph to the state: play when paused (→ resume), pause when running.
                self.pause_action.setIcon(
                    su.fluent_icon(0xE768 if paused else 0xE769, 16, su.semantic_colors()['text_primary']))
            if self.pause_separator is not None:
                self.pause_separator.setVisible(show_pause)
        except Exception as e:
            self.logger.error("Error refreshing dynamic menu items: %s", e, exc_info=True)

    def show_context_menu(self) -> None:
        """
        Calculates position and shows the context menu.
        """
        if not self.context_menu:
            return

        try:
            self._apply_menu_style()
            self._refresh_dynamic_items()
            menu_pos = self._calculate_menu_position()

            self.is_context_menu_visible = True
            if hasattr(self.widget, '_is_context_menu_visible'):
                self.widget._is_context_menu_visible = True

            # Native Win11 rounded corners + dark frame on the popup window. winId() realizes the
            # menu's native handle before exec; best-effort (silent no-op on Win10 / older builds).
            try:
                hwnd = int(self.context_menu.winId())
                set_dark_titlebar(hwnd, su.is_dark_mode())
                set_rounded_corners(hwnd)
            except Exception:
                pass

            self.context_menu.exec(menu_pos)
            
            self.is_context_menu_visible = False
            if hasattr(self.widget, '_is_context_menu_visible'):
                self.widget._is_context_menu_visible = False
            
            # Trigger visibility refresh on close, as per original logic
            if hasattr(self.widget, '_execute_refresh'):
                self.widget._execute_refresh() # Using internal method as it was in the original class
                
        except Exception as e:
            self.logger.error("Error showing context menu: %s", e, exc_info=True)

    def _calculate_menu_position(self) -> QPoint:
        """
        Calculates the optimal global position for the context menu.
        """
        try:
            # Access renderer from widget if available. Use the full content union (network + any
            # CPU/GPU stats), not just the last-drawn segment, so the menu stays centered over the
            # whole widget when hardware monitoring widens it.
            renderer = getattr(self.widget, 'renderer', None)
            text_rect_local = renderer.get_content_bounds() if renderer else QRect()

            if not text_rect_local.isValid() or text_rect_local.isEmpty():
                ref_global_pos = self.widget.mapToGlobal(self.widget.rect().center())
                ref_top_global_y = self.widget.mapToGlobal(self.widget.rect().topLeft()).y()
            else:
                ref_global_pos = self.widget.mapToGlobal(text_rect_local.center())
                ref_top_global_y = self.widget.mapToGlobal(text_rect_local.topLeft()).y()

            menu_size = self.context_menu.sizeHint()
            menu_width = menu_size.width() if menu_size.width() > 0 else constants.ui.general.ESTIMATED_MENU_WIDTH
            menu_height = menu_size.height()

            target_x = ref_global_pos.x() - menu_width // 2
            target_y = ref_top_global_y - menu_height - constants.ui.general.MENU_PADDING_ABOVE
            target_pos = QPoint(int(round(target_x)), int(round(target_y)))

            screen = self.widget.screen() or QApplication.primaryScreen()
            if screen:
                screen_rect = screen.availableGeometry()
                validated_x = max(screen_rect.left(), min(target_pos.x(), screen_rect.right() - menu_width + 1))
                validated_y = max(screen_rect.top(), min(target_pos.y(), screen_rect.bottom() - menu_height + 1))
                target_pos.setX(validated_x)
                target_pos.setY(validated_y)
            
            return target_pos
        except Exception as e:
            self.logger.error("Error calculating menu position: %s", e, exc_info=True)
            return self.widget.mapToGlobal(self.widget.rect().center())
