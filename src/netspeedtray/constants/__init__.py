"""
Centralized constants for the NetSpeedTray application.

This package contains all constants used across the application, organized into a single
constants.py file with classes for different categories. It also includes utilities for
internationalization and validation.

Usage:
    from netspeedtray.constants import AppConstants, ConfigConstants

    print(AppConstants.VERSION)  # Access the application version
    print(ConfigConstants.DEFAULT_CONFIG)  # Access default configuration
"""

from .constants import (
    AppConstants,
    ConfigConstants,
    HelperConstants,
    UnitConstants,
    NetworkSpeedConstants,
    TaskbarConstants,
    TaskbarEdge,
    PositionConstants,
    TimerConstants,
    RendererConstants,
    WidgetStateConstants,
    ControllerConstants,
    InterfaceConstants,
    HistoryConstants,
    UIConstants,
    DialogConstants,
    FontConstants,
    SliderConstants,
    LayoutConstants,
    InterfaceGroupConstants,
    DebugConstants,
    ColorConstants,
    UIStyleConstants,
    GraphConstants,
    HistoryPeriodConstants,
    DataRetentionConstants,
    LegendPositionConstants,
    ExportConstants,
    LogConstants,
)
from .i18n_strings import I18nStrings

__all__ = [
    "AppConstants",
    "ConfigConstants",
    "HelperConstants",
    "UnitConstants",
    "NetworkSpeedConstants",
    "TaskbarConstants",
    "TaskbarEdge",
    "PositionConstants",
    "TimerConstants",
    "RendererConstants",
    "WidgetStateConstants",
    "ControllerConstants",
    "InterfaceConstants",
    "HistoryConstants",
    "UIConstants",
    "DialogConstants",
    "FontConstants",
    "SliderConstants",
    "ToggleSwitchConstants",
    "LayoutConstants",
    "InterfaceGroupConstants",
    "DebugConstants",
    "ColorConstants",
    "UIStyleConstants",
    "GraphConstants",
    "HistoryPeriodConstants",
    "DataRetentionConstants",
    "LegendPositionConstants",
    "ExportConstants",
    "LogConstants",
    "I18nStrings",
]