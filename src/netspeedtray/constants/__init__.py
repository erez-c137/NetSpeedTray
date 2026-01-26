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

from netspeedtray.constants.app import app
from netspeedtray.constants.color import color
from netspeedtray.constants.config import config
from netspeedtray.constants.data import data
from netspeedtray.constants.export import export
from netspeedtray.constants.fonts import fonts
from netspeedtray.constants.graph import graph
from netspeedtray.constants.i18n import strings, I18nStrings
from netspeedtray.constants.layout import layout
from netspeedtray.constants.logs import logs
from netspeedtray.constants.network import network
from netspeedtray.constants.renderer import renderer
from netspeedtray.constants.shell import shell
from netspeedtray.constants.state import state
from netspeedtray.constants.styles import styles
from netspeedtray.constants.taskbar import taskbar, TaskbarEdge
from netspeedtray.constants.timeouts import timeouts
from netspeedtray.constants.timers import timers
from netspeedtray.constants.ui import ui

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