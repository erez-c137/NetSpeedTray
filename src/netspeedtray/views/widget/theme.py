import logging
from typing import TYPE_CHECKING

from netspeedtray import constants
from netspeedtray.utils import color_scheme

if TYPE_CHECKING:
    from netspeedtray.views.widget.main import NetworkSpeedWidget

class WidgetThemeManager:
    """
    Keeps the widget's colours on the Windows (taskbar) theme.

    Since the 2.2 rc.2 colour scheme the config holds a dark-taskbar AND a light-taskbar value for
    every themed colour, and RenderConfig.from_dict picks the current one (utils/color_scheme). A theme
    flip therefore never writes the config - in memory or on disk - it only refreshes the cached
    Windows-mode flag and rebuilds the renderer. (Before, an "automatic" text colour was overwritten in
    the file at startup and in memory on every flip, and an open Settings dialog had to fight it.)
    """

    def __init__(self, widget: "NetworkSpeedWidget"):
        self.widget = widget
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.ThemeManager")

    def on_theme_changed(self) -> None:
        """A Windows Light/Dark flip (the system event handler's theme_changed): repaint in the new
        taskbar's colours."""
        light = color_scheme.refresh_shell_theme()
        self.logger.info("Windows theme is now %s; repainting in its colours.", "light" if light else "dark")
        try:
            renderer = getattr(self.widget, "renderer", None)
            if renderer is not None:
                renderer.update_config(self.widget.config)   # rebuilds the pens, clears the graph cache
        except Exception as e:
            self.logger.error("Failed to apply the new theme's colours: %s", e, exc_info=True)
        self.widget.update()
