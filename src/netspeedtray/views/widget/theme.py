
import logging
import winreg
from typing import TYPE_CHECKING, Optional

from PyQt6.QtGui import QColor

from netspeedtray import constants

if TYPE_CHECKING:
    from netspeedtray.views.widget.main import NetworkSpeedWidget

class WidgetThemeManager:
    """
    Manages theme synchronization (Light/Dark mode) for the NetworkSpeedWidget.
    Handles registry checks and auto-color updates.
    """

    def __init__(self, widget: "NetworkSpeedWidget"):
        self.widget = widget
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.ThemeManager")

    def apply_theme_aware_defaults(self) -> None:
        """
        Synchronizes the widget's text color with the Windows theme on startup,
        but ONLY if the user has not manually set a color.
        """
        try:
            config = self.widget.config
            is_automatic = config.get("color_is_automatic", True)

            if not is_automatic:
                self.logger.info("User has set a manual color. Skipping auto-theme sync.")
                return

            # Check Windows Theme
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            system_uses_light_theme, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            winreg.CloseKey(key)

            correct_color = constants.color.BLACK if system_uses_light_theme == 1 else constants.color.WHITE
            
            current_color = config.get("default_color", "")

            if current_color.upper() != correct_color.upper():
                theme_name = "Light" if system_uses_light_theme == 1 else "Dark"
                self.logger.debug(
                    f"Windows theme is {theme_name}, auto-color was {current_color}. Correcting to {correct_color}."
                )
                updates = {
                    "default_color": correct_color,
                    "color_is_automatic": True
                }
                config.update(updates)
                self.widget.update_config(updates)

        except Exception as e:
            self.logger.warning(f"Could not perform theme-aware color sync: {e}")

    def on_theme_changed(self) -> None:
        """Handles Windows theme change (Light/Dark mode)."""
        self.logger.info("Theme change detected. Refreshing styles.")
        self.apply_theme_aware_defaults()
        self.widget.update()

    def update_color_for_live_theme(self) -> None:
        """
        Checks the current Windows shell (taskbar) theme and updates the widget's
        text color in real-time (in-memory only).
        """
        self.logger.debug("Executing live theme color update check...")
        try:
            if not self.widget.config.get("color_is_automatic", True):
                return

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            system_uses_light_theme, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            winreg.CloseKey(key)

            if system_uses_light_theme == 1:
                target_color_hex = constants.color.BLACK
                theme_name = "Light"
            else:
                target_color_hex = constants.config.defaults.DEFAULT_COLOR
                theme_name = "Dark"
            
            current_color = self.widget.config.get("default_color", constants.config.defaults.DEFAULT_COLOR)
            
            if current_color.upper() != target_color_hex.upper():
                self.logger.info(f"Windows shell theme changed to {theme_name}. Updating text color to {target_color_hex}.")
                
                # Update in-memory config only
                self.widget.config["default_color"] = target_color_hex
                
                # Propagate to renderer
                if hasattr(self.widget, 'renderer'):
                    self.widget.renderer.update_config(self.widget.config)
                
                self.widget.update()
            
        except FileNotFoundError:
             self.logger.warning("Could not find theme registry key for live update.")
        except Exception as e:
            self.logger.error(f"Failed to perform live theme color update: {e}", exc_info=True)
