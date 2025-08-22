"""
Provides centralized, immutable constants for the NetSpeedTray application.

This package exposes singleton instances of constant groups, ensuring they
are validated on import and easily accessible from a single namespace.

Usage:
    from netspeedtray import constants

    # Access application metadata
    print(constants.app.VERSION)

    # Access a translated string
    print(constants.i18n.get_i18n().SETTINGS_WINDOW_TITLE)

    # Access a default configuration value
    if is_dark_mode == constants.config.defaults.DEFAULT_DARK_MODE:
        # ...

    # Access a timer interval in milliseconds
    timer.start(constants.timers.VISIBILITY_CHECK_INTERVAL_MS)
"""

from .app import app
from .color import color
from .config import config
from .data import data
from .export import export
from .fonts import fonts
from .graph import graph
from .i18n import strings
from .layout import layout
from .logs import logs
from .network import network
from .renderer import renderer
from .shell import shell
from .state import state
from .styles import styles
from .taskbar import taskbar, TaskbarEdge
from .timers import timers
from .ui import ui

# No validation script is needed here; validation happens on instantiation
# of each singleton within its own module.

__all__ = [
    "app",
    "color",
    "config",
    "data",
    "export",
    "fonts",
    "graph",
    "strings",
    "I18nStrings",
    "layout",
    "logs",
    "network",
    "renderer",
    "shell",
    "state",
    "styles",
    "taskbar",
    "TaskbarEdge",
    "timers",
    "ui",
]