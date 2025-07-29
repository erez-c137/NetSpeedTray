"""
Centralized constants for the NetSpeedTray application.

This module defines all constants used across the application, organized into classes based on
their functionality. Constants are immutable, type-hinted, and validated to ensure consistency.
"""

import sys
import logging

if sys.version_info < (3, 9):
    raise RuntimeError("NetSpeedTray requires Python 3.9 or later.")

from typing import Final, Dict, Any, List, Tuple, Set, Optional
from enum import Enum


# Application-level constants
class AppConstants:
    """
    Constants for application metadata and lifecycle management.
    """

    APP_NAME: Final[str] = "NetSpeedTray"
    """The name of the application."""

    VERSION: Final[str] = "1.0.6"
    """The current version of the application."""

    MUTEX_NAME: Final[str] = "Global\\NetSpeedTray_SingleInstanceMutex"
    """The name of the mutex used for single-instance enforcement on Windows."""

    def validate(self) -> None:
        """Validate the constants to ensure they meet constraints."""
        
        if not self.APP_NAME:
            raise ValueError("APP_NAME must not be empty")
        if not self.VERSION:
            raise ValueError("VERSION must not be empty")
        if not self.MUTEX_NAME:
            raise ValueError("MUTEX_NAME must not be empty")


# History period constants
class HistoryPeriodConstants:
    """
    Constants for history periods in graphs.

    Defines valid periods for displaying network speed history, mapped to their display names
    and cutoff days for data retention.
    """
    PERIOD_MAP: Final[Dict[int, str]] = {
        0: "System Uptime",
        1: "Session",
        2: "3 Hours",
        3: "6 Hours",
        4: "12 Hours",
        5: "24 Hours",
        6: "1 Week",
        7: "1 Month",
        8: "All",
    }
    """Mapping of history period indices to display names."""

    DEFAULT_PERIOD: Final[str] = PERIOD_MAP[0]
    """Default history period label."""

    CUTOFF_DAYS: Final[Dict[str, float]] = {
        "3 Hours": 3 / 24,  # in days
        "6 Hours": 6 / 24,
        "12 Hours": 12 / 24,
        "24 Hours": 1,
        "1 Week": 7,
        "1 Month": 30,  # Approximation
        "All": 365 * 10,  # Effectively infinite (10 years)
    }
    """Mapping of history periods to cutoff days for data retention."""

    def validate(self) -> None:
        """
        Validate the history period constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if not self.PERIOD_MAP:
            raise ValueError("PERIOD_MAP must not be empty")
        if self.DEFAULT_PERIOD not in self.PERIOD_MAP.values():
            raise ValueError("DEFAULT_PERIOD must be a value in PERIOD_MAP")
        if not self.CUTOFF_DAYS:
            raise ValueError("CUTOFF_DAYS must not be empty")
        for period, cutoff in self.CUTOFF_DAYS.items():
            if cutoff <= 0:
                raise ValueError(f"CUTOFF_DAYS value for {period} must be positive")
        expected_cutoff_keys = set(self.PERIOD_MAP.values()) - {"System Uptime", "Session"}
        if set(self.CUTOFF_DAYS.keys()) != (expected_cutoff_keys | {"All"}): # Make sure "All" is expected
            raise ValueError(f"CUTOFF_DAYS keys {set(self.CUTOFF_DAYS.keys())} mismatch PERIOD_MAP values (expected: {expected_cutoff_keys | {'All'}})")


# Timer-related constants
class TimerConstants:
    """
    Constants for timer intervals used within the application.
    """

    SMART_MODE_INTERVAL_MS: Final[int] = 2000  # Milliseconds
    """Interval used when 'smart mode' update rate is active."""

    MINIMUM_INTERVAL_MS: Final[int] = 100  # Milliseconds (0.1 seconds)
    """Minimum allowed interval for any timer updates."""

    CSV_FLUSH_INTERVAL_MS: Final[int] = 5000  # Milliseconds (5 seconds)
    """Interval for flushing buffered CSV data to disk."""

    POSITION_CHECK_INTERVAL_MS: Final[int] = 500  # Milliseconds
    """Interval for the timer checking the main widget's position relative to taskbar."""

    VISIBILITY_CHECK_INTERVAL_MS: Final[int] = 500  # Milliseconds
    """Interval for the timer checking taskbar/fullscreen visibility."""

    MAXIMUM_UPDATE_RATE_SECONDS: Final[float] = 10.0  # Seconds
    """Maximum allowed update rate for timers in seconds, suitable for dynamic update adjustments."""

    def validate(self) -> None:
        """
        Validate the timer constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if self.SMART_MODE_INTERVAL_MS <= 0:
            raise ValueError("SMART_MODE_INTERVAL_MS must be positive")
        if self.MINIMUM_INTERVAL_MS <= 0:
            raise ValueError("MINIMUM_INTERVAL_MS must be positive")
        if self.CSV_FLUSH_INTERVAL_MS <= 0:
            raise ValueError("CSV_FLUSH_INTERVAL_MS must be positive")
        if self.POSITION_CHECK_INTERVAL_MS <= 0:
            raise ValueError("POSITION_CHECK_INTERVAL_MS must be positive")
        if self.VISIBILITY_CHECK_INTERVAL_MS <= 0:
            raise ValueError("VISIBILITY_CHECK_INTERVAL_MS must be positive")
        if self.MAXIMUM_UPDATE_RATE_SECONDS <= 0:
            raise ValueError("MAXIMUM_UPDATE_RATE_SECONDS must be positive")
        if self.MAXIMUM_UPDATE_RATE_SECONDS * 1000 < self.MINIMUM_INTERVAL_MS:
            raise ValueError("MAXIMUM_UPDATE_RATE_SECONDS must allow intervals >= MINIMUM_INTERVAL_MS")


# --- LegendPositionConstants - DEFINED BEFORE ConfigConstants ---
class LegendPositionConstants:
    """
    Constants defining available positions for the graph legend.
    Simplified to Left, Center, Right, and Off.
    """
    OFF: Final[str] = "Off"
    LEFT: Final[str] = "Left"
    CENTER: Final[str] = "Center"
    RIGHT: Final[str] = "Right"

    LEGEND_LOC_MAP: Final[Dict[str, Optional[str]]] = {
        OFF: None,
        LEFT: "upper left",
        CENTER: "upper center",
        RIGHT: "upper right",
    }
    """Mapping of simplified display names to Matplotlib location strings."""

    UI_OPTIONS: Final[List[str]] = [OFF, LEFT, CENTER, RIGHT]
    """Ordered list of options for UI elements like ComboBoxes."""

    DEFAULT_LEGEND_POSITION: Final[str] = OFF 
    """Default legend position display string."""

    def validate(self) -> None:
        """
        Validate the legend position constants.
        """
        if not self.LEGEND_LOC_MAP:
            raise ValueError("LEGEND_LOC_MAP must not be empty")
        if set(self.UI_OPTIONS) != set(self.LEGEND_LOC_MAP.keys()):
            raise ValueError("UI_OPTIONS must exactly match LEGEND_LOC_MAP keys")
        if self.DEFAULT_LEGEND_POSITION not in self.UI_OPTIONS: # This will now correctly check against ["Off", "Left", ...]
            raise ValueError(f"DEFAULT_LEGEND_POSITION '{self.DEFAULT_LEGEND_POSITION}' must be one of {self.UI_OPTIONS}")
        for loc in self.LEGEND_LOC_MAP.values():
            if loc is not None and not isinstance(loc, str):
                raise ValueError(f"Invalid Matplotlib loc value: {loc}. Must be a string or None.")


# --- DataRetentionConstants - DEFINED BEFORE ConfigConstants ---
class DataRetentionConstants:
    """
    Constants specifically for managing the retention period of stored historical data.
    """
    MAX_RETENTION_DAYS: Final[int] = 365  # 1 year
    """Maximum allowed retention period in days."""

    DAYS_MAP: Final[Dict[int, int]] = {
        0: 1,     # 1 Day
        1: 7,     # 7 Days
        2: 14,    # 14 Days
        3: 30,    # 30 Days
        4: 90,    # 90 Days
        5: 180,   # 180 Days
        6: 365,   # 1 Year
    }
    """Predefined retention periods for UI selection."""

    def validate(self) -> None:
        """
        Validate the data retention constants, checking keys and values.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if not isinstance(self.DAYS_MAP, dict) or not self.DAYS_MAP:
            raise ValueError("DAYS_MAP must be a non-empty dictionary")
        expected_keys = list(range(len(self.DAYS_MAP)))
        actual_keys = sorted(self.DAYS_MAP.keys())
        if actual_keys != expected_keys:
            raise ValueError(f"DAYS_MAP keys must be sequential integers starting from 0 (Expected: {expected_keys}, Got: {actual_keys})")
        for days_value in self.DAYS_MAP.values():
            if not isinstance(days_value, int) or days_value <= 0:
                raise ValueError(f"DAYS_MAP values (days) must be positive integers. Found: {days_value}")
            if days_value > self.MAX_RETENTION_DAYS:
                raise ValueError(f"DAYS_MAP value {days_value} exceeds MAX_RETENTION_DAYS ({self.MAX_RETENTION_DAYS})")


# Configuration-related constants
class ConfigConstants:
    """
    Constants for application configuration defaults and constraints.
    """
    # --- Default Values for Individual Settings ---
    DEFAULT_START_WITH_WINDOWS: Final[bool] = True
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    MINIMUM_UPDATE_RATE: Final[float] = TimerConstants.MINIMUM_INTERVAL_MS / 1000.0
    DEFAULT_FONT_FAMILY: Final[str] = 'Segoe UI'
    DEFAULT_FONT_SIZE: Final[int] = 9
    DEFAULT_FONT_WEIGHT: Final[int] = 600
    DEFAULT_COLOR: Final[str] = "#FFFFFF"
    DEFAULT_COLOR_CODING: Final[bool] = False
    DEFAULT_HIGH_SPEED_THRESHOLD: Final[float] = 5.0
    DEFAULT_LOW_SPEED_THRESHOLD: Final[float] = 1.0
    DEFAULT_HIGH_SPEED_COLOR: Final[str] = "#00FF00"
    DEFAULT_LOW_SPEED_COLOR: Final[str] = "#FFA500"
    DEFAULT_GRAPH_ENABLED: Final[bool] = False
    DEFAULT_HISTORY_MINUTES: Final[int] = 30
    DEFAULT_GRAPH_OPACITY: Final[int] = 30
    DEFAULT_INTERFACE_MODE: Final[str] = "all"
    DEFAULT_HISTORY_PERIOD_DAYS: Final[int] = DataRetentionConstants.DAYS_MAP[6]  # 1 Year
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_LEGEND_POSITION: Final[str] = LegendPositionConstants.DEFAULT_LEGEND_POSITION
    VALID_INTERFACE_MODES: Final[Set[str]] = {"all", "selected"}
    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_MIN_UPDATE_RATE: Final[float] = MINIMUM_UPDATE_RATE
    DEFAULT_MAX_UPDATE_RATE: Final[float] = TimerConstants.MAXIMUM_UPDATE_RATE_SECONDS
    MINIMUM_HISTORY_POINTS: Final[int] = 10
    DEFAULT_HISTORY_POINTS: Final[int] = int(round((DEFAULT_HISTORY_MINUTES * 60) / DEFAULT_UPDATE_RATE))
    MAXIMUM_HISTORY_POINTS: Final[int] = 5000
    UI_MICA_LIGHT: Final[str] = "#F3F3F3"
    UI_SIDEBAR_BG: Final[str] = "#E8ECEF"
    UI_SIDEBAR_SELECTED: Final[str] = "#D1D6DB"
    UI_TEXT_COLOR: Final[str] = "#1F1F1F"
    UI_ACCENT_FALLBACK: Final[str] = "#0078D4"
    UI_BORDER_COLOR: Final[str] = "#A0A0A0"
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "always_mbps"  # 'auto' or 'always_mbps'
    DEFAULT_DECIMAL_PLACES: Final[int] = 2  # 0, 1, or 2
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"  # 'left', 'center', 'right'
    DEFAULT_FREE_MOVE: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    DEFAULT_TRAY_OFFSET_X: Final[int] = 10

    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
        "start_with_windows": DEFAULT_START_WITH_WINDOWS,
        "update_rate": DEFAULT_UPDATE_RATE,
        "font_family": DEFAULT_FONT_FAMILY,
        "font_size": DEFAULT_FONT_SIZE,
        "font_weight": DEFAULT_FONT_WEIGHT,
        "color_coding": DEFAULT_COLOR_CODING,
        "default_color": DEFAULT_COLOR,
        "high_speed_threshold": DEFAULT_HIGH_SPEED_THRESHOLD,
        "low_speed_threshold": DEFAULT_LOW_SPEED_THRESHOLD,
        "high_speed_color": DEFAULT_HIGH_SPEED_COLOR,
        "low_speed_color": DEFAULT_LOW_SPEED_COLOR,
        "graph_enabled": DEFAULT_GRAPH_ENABLED,
        "history_minutes": DEFAULT_HISTORY_MINUTES,
        "graph_opacity": DEFAULT_GRAPH_OPACITY,
        "interface_mode": DEFAULT_INTERFACE_MODE,
        "selected_interfaces": [],
        "keep_data": DEFAULT_HISTORY_PERIOD_DAYS,
        "dark_mode": DEFAULT_DARK_MODE,
        "history_period": HistoryPeriodConstants.DEFAULT_PERIOD,
        "legend_position": DEFAULT_LEGEND_POSITION,
        "position_x": None,
        "position_y": None,
        "paused": False,
        "dynamic_update_enabled": DEFAULT_DYNAMIC_UPDATE_ENABLED,
        "min_update_rate": DEFAULT_MIN_UPDATE_RATE,
        "max_update_rate": DEFAULT_MAX_UPDATE_RATE,
        "speed_display_mode": DEFAULT_SPEED_DISPLAY_MODE,
        "decimal_places": DEFAULT_DECIMAL_PLACES,
        "text_alignment": DEFAULT_TEXT_ALIGNMENT,
        "free_move": DEFAULT_FREE_MOVE,
        "force_decimals": DEFAULT_FORCE_DECIMALS,
        "tray_offset_x": DEFAULT_TRAY_OFFSET_X,
    }

    def validate(self) -> None:
        """
        Validate the configuration constants to ensure they meet constraints.
        """
        if self.DEFAULT_UPDATE_RATE <= 0:
            raise ValueError("DEFAULT_UPDATE_RATE must be positive")
        if self.DEFAULT_UPDATE_RATE < self.MINIMUM_UPDATE_RATE:
            raise ValueError(f"DEFAULT_UPDATE_RATE must be at least {self.MINIMUM_UPDATE_RATE} seconds (equivalent to TimerConstants.MINIMUM_INTERVAL_MS)")
        if not self.DEFAULT_FONT_FAMILY:
            raise ValueError("DEFAULT_FONT_FAMILY must not be empty")
        if self.DEFAULT_FONT_SIZE < 1:
            raise ValueError("DEFAULT_FONT_SIZE must be positive")
        if self.DEFAULT_FONT_WEIGHT < 1 or self.DEFAULT_FONT_WEIGHT > 1000:
            raise ValueError("DEFAULT_FONT_WEIGHT must be between 1 and 1000")
        for color_key in ["DEFAULT_COLOR", "DEFAULT_HIGH_SPEED_COLOR", "DEFAULT_LOW_SPEED_COLOR"]:
            color_val = getattr(self, color_key)
            if not (isinstance(color_val, str) and color_val.startswith("#") and len(color_val) == 7):
                raise ValueError(f"Color {color_key} ('{color_val}') must be a 7-character hex code (e.g., #FFFFFF)")
        if self.DEFAULT_HIGH_SPEED_THRESHOLD <= self.DEFAULT_LOW_SPEED_THRESHOLD:
            raise ValueError("DEFAULT_HIGH_SPEED_THRESHOLD must be greater than DEFAULT_LOW_SPEED_THRESHOLD")
        if self.DEFAULT_HISTORY_MINUTES < 1:
            raise ValueError("DEFAULT_HISTORY_MINUTES must be positive")
        if not (0 <= self.DEFAULT_GRAPH_OPACITY <= 100):
            raise ValueError("DEFAULT_GRAPH_OPACITY must be between 0 and 100")
        if not (1 <= self.DEFAULT_HISTORY_PERIOD_DAYS <= DataRetentionConstants.MAX_RETENTION_DAYS):
            raise ValueError(f"DEFAULT_HISTORY_PERIOD_DAYS ('{self.DEFAULT_HISTORY_PERIOD_DAYS}') must be between 1 and {DataRetentionConstants.MAX_RETENTION_DAYS}")
        if not self.VALID_INTERFACE_MODES:
            raise ValueError("VALID_INTERFACE_MODES must not be empty")
        if not self.CONFIG_FILENAME:
             raise ValueError("CONFIG_FILENAME must not be empty")

        # Validate DEFAULT_CONFIG dictionary content
        if self.DEFAULT_CONFIG["history_period"] not in HistoryPeriodConstants.PERIOD_MAP.values():
            raise ValueError(f"DEFAULT_CONFIG history_period ('{self.DEFAULT_CONFIG['history_period']}') must be in HistoryPeriodConstants.PERIOD_MAP")
        if self.DEFAULT_CONFIG["interface_mode"] not in self.VALID_INTERFACE_MODES:
            raise ValueError(f"DEFAULT_CONFIG interface_mode ('{self.DEFAULT_CONFIG['interface_mode']}') must be in VALID_INTERFACE_MODES")
        if self.DEFAULT_CONFIG["legend_position"] not in LegendPositionConstants.UI_OPTIONS:
            raise ValueError(f"DEFAULT_CONFIG legend_position ('{self.DEFAULT_CONFIG['legend_position']}') must be one of {LegendPositionConstants.UI_OPTIONS}")
        for color_key in ["UI_MICA_LIGHT", "UI_SIDEBAR_BG", "UI_SIDEBAR_SELECTED", "UI_TEXT_COLOR", "UI_ACCENT_FALLBACK", "UI_BORDER_COLOR"]:
            color_val = getattr(self, color_key)
            if not (isinstance(color_val, str) and color_val.startswith("#") and len(color_val) == 7):
                raise ValueError(f"UI color {color_key} ('{color_val}') must be a 7-character hex code")
        
        # This is the correct check for the new constant
        if not isinstance(self.DEFAULT_DYNAMIC_UPDATE_ENABLED, bool):
            raise ValueError("DEFAULT_DYNAMIC_UPDATE_ENABLED must be a boolean")

        if self.DEFAULT_MIN_UPDATE_RATE < self.MINIMUM_UPDATE_RATE:
            raise ValueError(f"DEFAULT_MIN_UPDATE_RATE must be at least {self.MINIMUM_UPDATE_RATE} seconds")
        if self.DEFAULT_MAX_UPDATE_RATE <= self.DEFAULT_MIN_UPDATE_RATE:
            raise ValueError("DEFAULT_MAX_UPDATE_RATE must be greater than DEFAULT_MIN_UPDATE_RATE")
        if self.DEFAULT_MAX_UPDATE_RATE > TimerConstants.MAXIMUM_UPDATE_RATE_SECONDS: 
            raise ValueError(f"DEFAULT_MAX_UPDATE_RATE must not exceed {TimerConstants.MAXIMUM_UPDATE_RATE_SECONDS} seconds")       
        if self.MINIMUM_HISTORY_POINTS < 1:
            raise ValueError("MINIMUM_HISTORY_POINTS must be positive")
        if self.DEFAULT_HISTORY_POINTS < self.MINIMUM_HISTORY_POINTS:
            raise ValueError("DEFAULT_HISTORY_POINTS must be at least MINIMUM_HISTORY_POINTS")
        if self.MAXIMUM_HISTORY_POINTS < self.DEFAULT_HISTORY_POINTS:
            raise ValueError("MAXIMUM_HISTORY_POINTS must be at least DEFAULT_HISTORY_POINTS")
        
        calculated_points = int(round((self.DEFAULT_HISTORY_MINUTES * 60) / self.DEFAULT_UPDATE_RATE))
        if self.DEFAULT_HISTORY_POINTS != calculated_points:
            raise ValueError(
                f"DEFAULT_HISTORY_POINTS ({self.DEFAULT_HISTORY_POINTS}) must match calculation "
                f"from DEFAULT_HISTORY_MINUTES and DEFAULT_UPDATE_RATE ({calculated_points})"
            )
        
        # This set is now also correct
        expected_keys_in_default_config = {
            "update_rate", "font_family", "font_size", "font_weight", "color_coding", "default_color",
            "high_speed_threshold", "low_speed_threshold", "high_speed_color", "low_speed_color",
            "graph_enabled", "history_minutes", "graph_opacity", "interface_mode",
            "selected_interfaces", "keep_data", "dark_mode", "history_period",
            "legend_position", "position_x", "position_y", "paused", "start_with_windows",
            "dynamic_update_enabled", "min_update_rate", "max_update_rate",
            "speed_display_mode", "decimal_places", "text_alignment",
            "free_move", "force_decimals", "tray_offset_x"
        }
        actual_keys_in_default_config = set(self.DEFAULT_CONFIG.keys())
        if actual_keys_in_default_config != expected_keys_in_default_config:
            missing = expected_keys_in_default_config - actual_keys_in_default_config
            extra = actual_keys_in_default_config - expected_keys_in_default_config
            error_msg = "DEFAULT_CONFIG key mismatch:"
            if missing: error_msg += f" Missing keys in DEFAULT_CONFIG definition: {missing}."
            if extra: error_msg += f" Extra keys found in DEFAULT_CONFIG instance: {extra}."
            raise ValueError(error_msg)
        

class SliderConstants:
    """
    Constants for configuring QSlider widgets in the settings dialog.
    Defines ranges and limits, but styling is handled by the native system theme.
    """
    SPEED_THRESHOLD_MIN_HIGH: Final[int] = 1 * 10
    SPEED_THRESHOLD_MAX_HIGH: Final[int] = 10000 * 10
    SPEED_THRESHOLD_MIN_LOW: Final[int] = 0 * 10
    SPEED_THRESHOLD_MAX_LOW: Final[int] = 9999 * 10
    OPACITY_MIN: Final[int] = 10
    OPACITY_MAX: Final[int] = 100
    SLIDER_STYLE: str = """
        QSlider::groove:horizontal {
            border: 1px solid #bbb; /* Gruvbox: #ebdbb2 */
            background: #555; /* Gruvbox: #3c3836 */
            height: 6px;
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8ec07c, stop:1 #b8bb26); /* Gruvbox green-yellow gradient */
            border: 1px solid #8ec07c; /* Gruvbox: #ebdbb2 */
            height: 6px;
            border-radius: 3px;
        }
        QSlider::add-page:horizontal {
            background: #777; /* Gruvbox: #504945 */
            border: 1px solid #bbb; /* Gruvbox: #665c54 */
            height: 6px;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #ddd; /* Gruvbox: #d5c4a1 */
            border: 1px solid #aaa; /* Gruvbox: #a89984 */
            width: 14px;
            height: 14px;
            margin: -4px 0; /* handle is usually larger than groove */
            border-radius: 7px;
        }
        QSlider::handle:horizontal:hover {
            background: #eee; /* Gruvbox: #ebdbb2 */
            border: 1px solid #999; /* Gruvbox: #928374 */
        }
    """
    def validate(self) -> None:
        if self.SPEED_THRESHOLD_MIN_HIGH < 0: raise ValueError("SPEED_THRESHOLD_MIN_HIGH non-negative")
        if self.SPEED_THRESHOLD_MAX_HIGH <= self.SPEED_THRESHOLD_MIN_HIGH: raise ValueError("SPEED_THRESHOLD_MAX_HIGH > MIN")
        if self.SPEED_THRESHOLD_MIN_LOW < 0: raise ValueError("SPEED_THRESHOLD_MIN_LOW non-negative")
        if self.SPEED_THRESHOLD_MAX_LOW < self.SPEED_THRESHOLD_MIN_LOW: raise ValueError("SPEED_THRESHOLD_MAX_LOW >= MIN")
        if self.SPEED_THRESHOLD_MAX_LOW >= self.SPEED_THRESHOLD_MAX_HIGH: raise ValueError("SPEED_THRESHOLD_MAX_LOW < MAX_HIGH")
        if self.OPACITY_MIN < 0: raise ValueError("OPACITY_MIN non-negative")
        if self.OPACITY_MAX < self.OPACITY_MIN: raise ValueError("OPACITY_MAX >= MIN")
        if not (0 <= self.OPACITY_MIN <= 100 and 0 <= self.OPACITY_MAX <= 100): raise ValueError("OPACITY values 0-100")


class ConfigMessages:
    """
    Log message templates for configuration validation.
    """
    INVALID_UPDATE_RATE: str = "Invalid update_rate %s, setting to %.1f"
    INVALID_FONT_FAMILY: str = "Invalid font_family %s, setting to %s"
    INVALID_FONT_SIZE: str = "Invalid font_size %s, setting to %d"
    INVALID_FONT_WEIGHT: str = "Invalid font_weight %s, setting to %d"
    INVALID_COLOR: str = "Invalid color {value} for {key}, resetting to default"
    INVALID_HIGH_THRESHOLD: str = "Invalid high_speed_threshold %s, setting to %.1f"
    INVALID_LOW_THRESHOLD: str = "Invalid low_speed_threshold %s, setting to %.1f"
    THRESHOLD_SWAP: str = "low_speed_threshold exceeds high_speed_threshold, swapping values"
    INVALID_HISTORY: str = "Invalid history_minutes %s, setting to %d"
    INVALID_OPACITY: str = "Invalid graph_opacity %s, setting to %d"
    INVALID_KEEP_DATA: str = "Invalid keep_data %s, setting to %d"
    INVALID_DARK_MODE: str = "Invalid dark_mode %s, setting to False"
    INVALID_INTERFACES: str = "Invalid selected_interfaces %s, setting to []"
    INVALID_INTERFACE_MODE: str = "Invalid interface_mode %s, setting to 'all'"
    INVALID_HISTORY_PERIOD: str = "Invalid history_period %s, setting to default"
    INVALID_LEGEND_POSITION: str = "Invalid legend_position %s, setting to 'off'"
    INVALID_COLOR_CODING: str = "Invalid color_coding %s, setting to False"
    INVALID_GRAPH_ENABLED: str = "Invalid graph_enabled %s, setting to False"
    INVALID_PAUSED: str = "Invalid paused %s, setting to False"
    INVALID_DYNAMIC_UPDATE_ENABLED: str = "Invalid dynamic_update_enabled %s, setting to %s"
    INVALID_MIN_UPDATE_RATE: str = "Invalid min_update_rate %s, setting to %.1f"
    INVALID_MAX_UPDATE_RATE: str = "Invalid max_update_rate %s, setting to %.1f"
    INVALID_POSITION_X: str = "Invalid position_x %s, setting to None"
    INVALID_POSITION_Y: str = "Invalid position_y %s, setting to None"

    def validate(self) -> None:
        messages_map = {
            "INVALID_UPDATE_RATE": "update_rate", "INVALID_FONT_FAMILY": "font_family",
            "INVALID_FONT_SIZE": "font_size", "INVALID_FONT_WEIGHT": "font_weight",
            "INVALID_HIGH_THRESHOLD": "high_speed_threshold", "INVALID_LOW_THRESHOLD": "low_speed_threshold",
            "INVALID_HISTORY": "history_minutes", "INVALID_OPACITY": "graph_opacity",
            "INVALID_KEEP_DATA": "keep_data", "INVALID_DARK_MODE": "dark_mode", 
            "INVALID_INTERFACES": "selected_interfaces", "INVALID_INTERFACE_MODE": "interface_mode", 
            "INVALID_HISTORY_PERIOD": "history_period", "INVALID_LEGEND_POSITION": "legend_position",
            "INVALID_COLOR_CODING": "color_coding", "INVALID_GRAPH_ENABLED": "graph_enabled",
            "INVALID_PAUSED": "paused", "INVALID_DYNAMIC_UPDATE_ENABLED": "dynamic_update_enabled",
            "INVALID_MIN_UPDATE_RATE": "min_update_rate", "INVALID_MAX_UPDATE_RATE": "max_update_rate",
            "INVALID_POSITION_X": "position_x", "INVALID_POSITION_Y": "position_y",
        }
        # Non-mapped messages (no direct config key equivalent for the %s part)
        non_mapped_messages = ["INVALID_COLOR", "THRESHOLD_SWAP"]

        config_instance = ConfigConstants() # To access DEFAULT_CONFIG for key checking

        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name != 'validate':
                value = getattr(self, attr_name)
                if not isinstance(value, str) or not value:
                    raise ValueError(f"ConfigMessages.{attr_name} must be a non-empty string.")
                
                if attr_name in messages_map:
                    config_key = messages_map[attr_name]
                    if config_key not in config_instance.DEFAULT_CONFIG:
                        raise ValueError(f"ConfigMessages.{attr_name} references unknown config key '{config_key}'.")
                elif attr_name not in non_mapped_messages:
                    raise ValueError(f"ConfigMessages.{attr_name} is an unhandled message type in validation.")


# Helper-related constants
class HelperConstants:
    """
    Constants for utility functions and helpers.
    """
    DEFAULT_FONT: Final[str] = ConfigConstants.DEFAULT_FONT_FAMILY
    MAX_LOG_SIZE: Final[int] = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: Final[int] = 3
    BYTES_TO_MEGABYTES: Final[int] = 1024 * 1024 # Made Final and int

    def validate(self) -> None:
        if not self.DEFAULT_FONT: raise ValueError("DEFAULT_FONT must not be empty")
        if self.MAX_LOG_SIZE <= 0: raise ValueError("MAX_LOG_SIZE must be positive")
        if self.LOG_BACKUP_COUNT < 0: raise ValueError("LOG_BACKUP_COUNT must be non-negative")
        if self.BYTES_TO_MEGABYTES != 1024*1024: raise ValueError("BYTES_TO_MEGABYTES must be 1024*1024")


# Network speed-related constants
class NetworkSpeedConstants:
    """
    Constants for network speed calculations.
    """
    DEFAULT_UNIT_BITS: Final[str] = "bps"
    DEFAULT_UNIT_BYTES: Final[str] = "B/s"
    DEFAULT_SPEED: Final[float] = 0.0
    MIN_TIME_DIFF: Final[float] = 1e-6

    def validate(self) -> None:
        if not self.DEFAULT_UNIT_BITS: raise ValueError("DEFAULT_UNIT_BITS must not be empty")
        if not self.DEFAULT_UNIT_BYTES: raise ValueError("DEFAULT_UNIT_BYTES must not be empty")
        if self.DEFAULT_SPEED < 0: raise ValueError("DEFAULT_SPEED must be non-negative")
        if self.MIN_TIME_DIFF <= 0: raise ValueError("MIN_TIME_DIFF must be positive")


class UnitConstants:
    """
    Constants for unit conversions and labels in speed formatting.
    Uses SI prefixes (1000) for network speeds, consistent with common usage.
    """
    BITS_PER_BYTE: Final[int] = 8
    KILO_DIVISOR: Final[int] = 1_000
    MEGA_DIVISOR: Final[int] = 1_000_000
    GIGA_DIVISOR: Final[int] = 1_000_000_000
    KILO_THRESHOLD: Final[int] = KILO_DIVISOR
    MEGA_THRESHOLD: Final[int] = MEGA_DIVISOR
    GIGA_THRESHOLD: Final[int] = GIGA_DIVISOR
    BPS_LABEL: Final[str] = "B/s"
    KBPS_LABEL: Final[str] = "KB/s"
    MBPS_LABEL: Final[str] = "MB/s"
    GBPS_LABEL: Final[str] = "GB/s"
    BITS_LABEL: Final[str] = "bps"
    KBITS_LABEL: Final[str] = "Kbps"
    MBITS_LABEL: Final[str] = "Mbps"
    GBITS_LABEL: Final[str] = "Gbps"
    MINIMUM_DISPLAY_SPEED: Final[float] = 10_000

    def validate(self) -> None:
        if self.BITS_PER_BYTE != 8:
            raise ValueError("BITS_PER_BYTE must be 8")
        for divisor in [self.KILO_DIVISOR, self.MEGA_DIVISOR, self.GIGA_DIVISOR]:
            if divisor <= 0:
                raise ValueError("Divisors must be positive")
        for threshold in [self.KILO_THRESHOLD, self.MEGA_THRESHOLD, self.GIGA_THRESHOLD]:
            if threshold <= 0:
                raise ValueError("Thresholds must be positive")
        if not (self.KILO_THRESHOLD < self.MEGA_THRESHOLD < self.GIGA_THRESHOLD):
            raise ValueError("Thresholds must be in increasing order (Kilo < Mega < Giga)")
        for label in [self.BPS_LABEL, self.KBPS_LABEL, self.MBPS_LABEL, self.GBPS_LABEL,
                      self.BITS_LABEL, self.KBITS_LABEL, self.MBITS_LABEL, self.GBITS_LABEL]:
            if not label:
                raise ValueError("Labels must not be empty")
        if self.MINIMUM_DISPLAY_SPEED < 0:
            raise ValueError("MINIMUM_DISPLAY_SPEED must be non-negative")


# Taskbar-related constants
class TaskbarConstants:
    """
    Constants for taskbar-related calculations and interactions.
    """
    DEFAULT_HEIGHT: Final[int] = 40
    MIN_VISIBLE_SIZE: Final[int] = 10
    PADDING: Final[int] = 4
    DPI_DEFAULT: Final[float] = 1.0

    def validate(self) -> None:
        if self.DEFAULT_HEIGHT <= 0: raise ValueError("DEFAULT_HEIGHT must be positive")
        if self.MIN_VISIBLE_SIZE <= 0: raise ValueError("MIN_VISIBLE_SIZE must be positive")
        if self.PADDING < 0: raise ValueError("PADDING must be non-negative")
        if self.DPI_DEFAULT <= 0: raise ValueError("DPI_DEFAULT must be positive")


class TaskbarEdge(Enum):
    """
    Enum for taskbar edge positions relative to the screen.
    """
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"


# Position-related constants
class PositionConstants:
    """
    Constants for widget positioning calculations.
    """
    DEFAULT_PADDING: Final[int] = TaskbarConstants.PADDING
    FALLBACK_PADDING: Final[int] = 32
    FALLBACK_TASKBAR_HEIGHT: Final[int] = TaskbarConstants.DEFAULT_HEIGHT
    SCREEN_EDGE_MARGIN: Final[int] = 5
    DRAG_LOG_INTERVAL_SECONDS: Final[float] = 1.0

    def validate(self) -> None:
        if self.DEFAULT_PADDING < 0: raise ValueError("DEFAULT_PADDING must be non-negative")
        if self.FALLBACK_PADDING < 0: raise ValueError("FALLBACK_PADDING must be non-negative")
        if self.FALLBACK_TASKBAR_HEIGHT <= 0: raise ValueError("FALLBACK_TASKBAR_HEIGHT must be positive")
        if self.SCREEN_EDGE_MARGIN < 0: raise ValueError("SCREEN_EDGE_MARGIN must be non-negative")
        if self.DRAG_LOG_INTERVAL_SECONDS <= 0: raise ValueError("DRAG_LOG_INTERVAL_SECONDS must be positive")


# Renderer-related constants
class RendererConstants:
    """
    Constants influencing the rendering of the network speed widget display,
    including text formatting, graph elements, and default values.
    """
    MIN_GRAPH_POINTS: Final[int] = 2
    MIN_SPEED_FOR_COLOR: Final[float] = 0.01
    LINE_WIDTH: Final[int] = 1
    TEXT_MARGIN: Final[int] = 2
    GRAPH_MARGIN: Final[int] = 1
    GRAPH_HEIGHT_PERCENTAGE: Final[float] = 0.8
    GRAPH_LEFT_PADDING: Final[int] = 2
    GRAPH_RIGHT_PADDING: Final[int] = 2
    GRAPH_BOTTOM_PADDING: Final[int] = 1
    UPLOAD_ARROW: Final[str] = "↑"
    DOWNLOAD_ARROW: Final[str] = "↓"
    DEFAULT_ARROW_WIDTH: Final[int] = 5
    VALUE_UNIT_GAP: Final[int] = 5
    ARROW_NUMBER_GAP: Final[int] = 5
    MIN_Y_SCALE: Final[int] = 62500
    MIN_SPEED_THRESHOLD: Final[int] = 10000

    def validate(self) -> None:
        if self.MIN_GRAPH_POINTS < 2: raise ValueError("MIN_GRAPH_POINTS must be at least 2")
        if self.MIN_SPEED_FOR_COLOR < 0: raise ValueError("MIN_SPEED_FOR_COLOR must be non-negative")
        if self.LINE_WIDTH <= 0: raise ValueError("LINE_WIDTH must be positive")
        if self.TEXT_MARGIN < 0: raise ValueError("TEXT_MARGIN must be non-negative")
        if self.GRAPH_MARGIN < 0: raise ValueError("GRAPH_MARGIN must be non-negative")
        if not (0.0 < self.GRAPH_HEIGHT_PERCENTAGE <= 1.0): raise ValueError("GRAPH_HEIGHT_PERCENTAGE must be between 0 and 1")
        if self.GRAPH_LEFT_PADDING < 0: raise ValueError("GRAPH_LEFT_PADDING must be non-negative")
        if self.GRAPH_RIGHT_PADDING < 0: raise ValueError("GRAPH_RIGHT_PADDING must be non-negative")
        if self.GRAPH_BOTTOM_PADDING < 0: raise ValueError("GRAPH_BOTTOM_PADDING must be non-negative")
        if not self.UPLOAD_ARROW: raise ValueError("UPLOAD_ARROW must not be empty")
        if not self.DOWNLOAD_ARROW: raise ValueError("DOWNLOAD_ARROW must not be empty")
        if self.DEFAULT_ARROW_WIDTH < 0: raise ValueError("DEFAULT_ARROW_WIDTH must be non-negative")
        if self.MIN_Y_SCALE <= 0: raise ValueError("MIN_Y_SCALE must be positive")
        if self.MIN_SPEED_THRESHOLD <= 0: raise ValueError("MIN_SPEED_THRESHOLD must be positive")


# Widget state-related constants
class WidgetStateConstants:
    """
    Constants related to the internal state management of the main widget.
    """
    MAX_HISTORY_POINTS: Final[int] = ConfigConstants.DEFAULT_HISTORY_POINTS
    POSITION_TOLERANCE: Final[int] = 5
    MAX_SPEED_DISPLAY_TEMPLATE: Final[str] = "↑ 999.9 GB/s"
    CSV_FILE_NAME: Final[str] = "nst_speed_history.csv"
    CSV_HEADERS: Final[List[str]] = ["timestamp", "upload_bytes", "download_bytes"]

    def validate(self) -> None:
        if self.MAX_HISTORY_POINTS <= 0:
            raise ValueError("MAX_HISTORY_POINTS (derived) must be positive")
        if self.POSITION_TOLERANCE < 0:
            raise ValueError("POSITION_TOLERANCE must be non-negative")
        if not self.MAX_SPEED_DISPLAY_TEMPLATE:
            raise ValueError("MAX_SPEED_DISPLAY_TEMPLATE must not be empty")
        if not self.CSV_FILE_NAME:
            raise ValueError("CSV_FILE_NAME must not be empty")
        if not self.CSV_HEADERS or len(self.CSV_HEADERS) < 3:
            raise ValueError("CSV_HEADERS must not be empty and contain at least timestamp, upload, download")


# Controller-related constants
class ControllerConstants:
    """
    Constants specific to the application's main controller or logic flow.
    """
    SPEED_LOGGING_FREQUENCY: Final[int] = 30
    """Frequency of logging speeds (e.g., every N updates)."""

    def validate(self) -> None:
        """
        Validate the controller constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if self.SPEED_LOGGING_FREQUENCY <= 0:
            raise ValueError("SPEED_LOGGING_FREQUENCY must be positive")


# Interface-related constants
class InterfaceConstants:
    """
    Constants related to network interface management and selection.
    """
    DEFAULT_MODE: Final[str] = ConfigConstants.DEFAULT_INTERFACE_MODE
    """Default mode for network interface selection."""

    def validate(self) -> None:
        """
        Validate the interface constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if self.DEFAULT_MODE not in ConfigConstants.VALID_INTERFACE_MODES:
            raise ValueError(f"DEFAULT_MODE '{self.DEFAULT_MODE}' must be in ConfigConstants.VALID_INTERFACE_MODES")


# History-related constants
class HistoryConstants:
    """
    Constants specifically for managing historical data ranges and display.
    """
    HISTORY_MINUTES_RANGE: Final[Tuple[int, int]] = (1, 120)
    """Range of valid history minutes for the graph display duration slider."""

    def validate(self) -> None:
        """
        Validate the history constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        min_val, max_val = self.HISTORY_MINUTES_RANGE
        if min_val < 1:
            raise ValueError("HISTORY_MINUTES_RANGE minimum must be positive")
        if max_val < min_val:
            raise ValueError("HISTORY_MINUTES_RANGE maximum must be greater than or equal to minimum")


# UI-related constants
class UIConstants:
    """
    General UI constants for elements like menus, padding, etc.
    """
    MENU_PADDING_ABOVE: Final[int] = 5
    """Padding above context menus in pixels."""

    MENU_PADDING_BELOW: Final[int] = 5
    """Padding below context menus in pixels."""

    ESTIMATED_MENU_WIDTH: Final[int] = 150
    """Estimated width of context menus for positioning calculations."""

    def validate(self) -> None:
        """
        Validate the UI constants to ensure they meet constraints.

        Raises:
            ValueError: If any constant does not meet its constraints.
        """
        if self.MENU_PADDING_ABOVE < 0:
            raise ValueError("MENU_PADDING_ABOVE must be non-negative")
        if self.MENU_PADDING_BELOW < 0:
            raise ValueError("MENU_PADDING_BELOW must be non-negative")
        if self.ESTIMATED_MENU_WIDTH <= 0:
            raise ValueError("ESTIMATED_MENU_WIDTH must be positive")


class ComponentVisualConstants:
    """
    Constants defining visual dimensions for custom UI components.
    """
    # For Win11Toggle
    TOGGLE_TRACK_WIDTH: Final[int] = 40
    """Width of the track for the Win11Toggle component."""
    TOGGLE_TRACK_HEIGHT: Final[int] = 20
    """Height of the track for the Win11Toggle component."""
    TOGGLE_THUMB_DIAMETER: Final[int] = 14
    """Diameter of the thumb for the Win11Toggle component."""

    # PANEL_TEXT_COLOR: Final[str] = "#E0E0E0" # Example if you wanted to define it here
    # """Fixed light text color for elements on an always-dark panel."""


    def validate(self) -> None:
        """Validate component visual constants."""
        if self.TOGGLE_TRACK_WIDTH <= 0:
            raise ValueError("TOGGLE_TRACK_WIDTH must be positive.")
        if self.TOGGLE_TRACK_HEIGHT <= 0:
            raise ValueError("TOGGLE_TRACK_HEIGHT must be positive.")
        if self.TOGGLE_THUMB_DIAMETER <= 0:
            raise ValueError("TOGGLE_THUMB_DIAMETER must be positive.")
        if self.TOGGLE_THUMB_DIAMETER >= self.TOGGLE_TRACK_HEIGHT: # Thumb should fit within track
            raise ValueError("TOGGLE_THUMB_DIAMETER should generally be less than TOGGLE_TRACK_HEIGHT.")
        # if not (isinstance(self.PANEL_TEXT_COLOR, str) and self.PANEL_TEXT_COLOR.startswith("#") and len(self.PANEL_TEXT_COLOR) == 7):
        #     raise ValueError("PANEL_TEXT_COLOR must be a valid hex string.")


# Dialog Constants
class DialogConstants:
    """
    Constants for dialog sizing, positioning, and behavior.
    """
    MINIMUM_DIALOG_WIDTH: Final[int] = 300
    MINIMUM_DIALOG_HEIGHT: Final[int] = 200
    MAXIMUM_DIALOG_WIDTH: Final[int] = 350
    GAP_ABOVE_WIDGET: Final[int] = 40
    THROTTLE_INTERVAL_MS: Final[int] = 250
    COLOR_BUTTON_WIDTH: Final[int] = 40
    COLOR_BUTTON_HEIGHT: Final[int] = 18

    def validate(self) -> None:
        if self.MINIMUM_DIALOG_WIDTH <= 0: raise ValueError("MINIMUM_DIALOG_WIDTH must be positive")
        if self.MINIMUM_DIALOG_HEIGHT <= 0: raise ValueError("MINIMUM_DIALOG_HEIGHT must be positive")
        if self.MAXIMUM_DIALOG_WIDTH < self.MINIMUM_DIALOG_WIDTH:
            raise ValueError("MAXIMUM_DIALOG_WIDTH must be greater than or equal to MINIMUM_DIALOG_WIDTH")
        if self.GAP_ABOVE_WIDGET < 0: raise ValueError("GAP_ABOVE_WIDGET must be non-negative")
        if self.THROTTLE_INTERVAL_MS <= 0: raise ValueError("THROTTLE_INTERVAL_MS must be positive")
        if self.COLOR_BUTTON_WIDTH <= 0: raise ValueError("COLOR_BUTTON_WIDTH must be positive")
        if self.COLOR_BUTTON_HEIGHT <= 0: raise ValueError("COLOR_BUTTON_HEIGHT must be positive")


# Windows API Constants
class WindowsAPIConstants:
    """
    Constants for Windows API interactions, specifically for taskbar visibility detection.
    """
    ABM_GETTASKBARPOS: Final[int] = 0x00000005
    ABM_GETSTATE: Final[int] = 0x00000004
    ABS_AUTOHIDE: Final[int] = 0x00000001
    ABS_ALWAYSONTOP: Final[int] = 0x00000002
    ABE_LEFT: Final[int] = 0
    ABE_TOP: Final[int] = 1
    ABE_RIGHT: Final[int] = 2
    ABE_BOTTOM: Final[int] = 3

    def validate(self) -> None:
        constants_to_check = [
            self.ABM_GETTASKBARPOS, self.ABM_GETSTATE, self.ABS_AUTOHIDE,
            self.ABS_ALWAYSONTOP, self.ABE_LEFT, self.ABE_TOP,
            self.ABE_RIGHT, self.ABE_BOTTOM
        ]
        for value in constants_to_check:
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"WindowsAPIConstants value {value} must be a non-negative integer")


# Font Constants
class FontConstants:
    """
    Constants related to font selection, size, and weight used in the application.
    """
    FONT_SIZE_MIN: Final[int] = 7
    FONT_SIZE_MAX: Final[int] = 11
    FALLBACK_WEIGHTS: Final[List[int]] = [300, 400, 600, 700]
    WEIGHT_THIN: Final[int] = 100
    WEIGHT_EXTRALIGHT: Final[int] = 200
    WEIGHT_LIGHT: Final[int] = 300
    WEIGHT_NORMAL: Final[int] = 400
    WEIGHT_MEDIUM: Final[int] = 500
    WEIGHT_DEMIBOLD: Final[int] = 600
    WEIGHT_BOLD: Final[int] = 700
    WEIGHT_EXTRABOLD: Final[int] = 800
    WEIGHT_BLACK: Final[int] = 900
    FONT_WEIGHT_MIN_STANDARD: Final[int] = WEIGHT_LIGHT
    FONT_WEIGHT_MAX_STANDARD: Final[int] = WEIGHT_BLACK
    FONT_WEIGHT_STEP_STANDARD: Final[int] = 100
    FONT_WEIGHT_SLIDER_MIN_WIDTH: Final[int] = 120
    FONT_WEIGHT_LABEL_PADDING: Final[int] = 20
    NOTE_FONT_SIZE: Final[int] = 8
    WEIGHT_MAP: Final[Dict[int, str]] = {
        WEIGHT_THIN: "Thin", WEIGHT_EXTRALIGHT: "ExtraLight", WEIGHT_LIGHT: "Light",
        WEIGHT_NORMAL: "Normal", WEIGHT_MEDIUM: "Medium", WEIGHT_DEMIBOLD: "DemiBold",
        WEIGHT_BOLD: "Bold", WEIGHT_EXTRABOLD: "ExtraBold", WEIGHT_BLACK: "Black"
    }
    WEIGHT_NUM_TO_KEY: Final[Dict[int, str]] = {
        WEIGHT_THIN: "FONT_WEIGHT_THIN", WEIGHT_EXTRALIGHT: "FONT_WEIGHT_EXTRALIGHT",
        WEIGHT_LIGHT: "FONT_WEIGHT_LIGHT", WEIGHT_NORMAL: "FONT_WEIGHT_NORMAL",
        WEIGHT_MEDIUM: "FONT_WEIGHT_MEDIUM", WEIGHT_DEMIBOLD: "FONT_WEIGHT_DEMIBOLD",
        WEIGHT_BOLD: "FONT_WEIGHT_BOLD", WEIGHT_EXTRABOLD: "FONT_WEIGHT_EXTRABOLD",
        WEIGHT_BLACK: "FONT_WEIGHT_BLACK",
    }

    def validate(self) -> None:
        """
        Validate the font constants to ensure they meet constraints.
        (Keeping your original extensive validation here)
        """
        if self.FONT_SIZE_MIN < 1:
            raise ValueError("FONT_SIZE_MIN must be positive")
        if self.FONT_SIZE_MAX < self.FONT_SIZE_MIN:
            raise ValueError("FONT_SIZE_MAX must be greater than or equal to FONT_SIZE_MIN")
        if not self.FALLBACK_WEIGHTS:
            raise ValueError("FALLBACK_WEIGHTS must not be empty")
        for weight in self.FALLBACK_WEIGHTS:
            if weight < 1 or weight > 1000:
                raise ValueError(f"FALLBACK_WEIGHTS value {weight} must be between 1 and 1000")
        if self.FONT_WEIGHT_MIN_STANDARD < 0 or self.FONT_WEIGHT_MIN_STANDARD > 999: # QFont.Weight range
             raise ValueError("FONT_WEIGHT_MIN_STANDARD must be between 0 and 999")
        if self.FONT_WEIGHT_MAX_STANDARD < self.FONT_WEIGHT_MIN_STANDARD:
             raise ValueError("FONT_WEIGHT_MAX_STANDARD must be >= FONT_WEIGHT_MIN_STANDARD")
        if self.FONT_WEIGHT_STEP_STANDARD <= 0:
             raise ValueError("FONT_WEIGHT_STEP_STANDARD must be positive")
        if self.FONT_WEIGHT_SLIDER_MIN_WIDTH <= 0:
            raise ValueError("FONT_WEIGHT_SLIDER_MIN_WIDTH must be positive")
        if self.FONT_WEIGHT_LABEL_PADDING < 0:
            raise ValueError("FONT_WEIGHT_LABEL_PADDING must be non-negative")
        if self.NOTE_FONT_SIZE < 1:
            raise ValueError("NOTE_FONT_SIZE must be positive")
        if not self.WEIGHT_MAP:
             raise ValueError("WEIGHT_MAP must not be empty")
        if not hasattr(self, 'WEIGHT_NUM_TO_KEY') or not self.WEIGHT_NUM_TO_KEY:
             raise ValueError("WEIGHT_NUM_TO_KEY must be defined and not empty")
        if set(self.WEIGHT_MAP.keys()) != set(self.WEIGHT_NUM_TO_KEY.keys()):
             raise ValueError("Keys in WEIGHT_MAP and WEIGHT_NUM_TO_KEY must match")
        for key_val in self.WEIGHT_NUM_TO_KEY.values():
            if not key_val.startswith("FONT_WEIGHT_"):
                raise ValueError(f"Values in WEIGHT_NUM_TO_KEY should be i18n keys like 'FONT_WEIGHT_BOLD', found: {key_val}")


# Layout Constants
class LayoutConstants:
    """
    Constants defining default margins and spacing for UI layouts.
    """
    HORIZONTAL_SPACING_SMALL: Final[int] = 5
    HORIZONTAL_SPACING_MEDIUM: Final[int] = 8
    VERTICAL_SPACING: Final[int] = 5
    MARGIN: Final[int] = 10
    SPACING: Final[int] = 8
    WIDGET_DEFAULT_RIGHT_PADDING_PX: Final[int] = 10

    def validate(self) -> None:
        if self.HORIZONTAL_SPACING_SMALL < 0: raise ValueError("HORIZONTAL_SPACING_SMALL must be non-negative")
        if self.HORIZONTAL_SPACING_MEDIUM < 0: raise ValueError("HORIZONTAL_SPACING_MEDIUM must be non-negative")
        if self.VERTICAL_SPACING < 0: raise ValueError("VERTICAL_SPACING must be non-negative")
        if self.MARGIN < 0: raise ValueError("MARGIN must be non-negative")
        if self.SPACING < 0: raise ValueError("SPACING must be non-negative")
        if self.WIDGET_DEFAULT_RIGHT_PADDING_PX < 0: raise ValueError("WIDGET_DEFAULT_RIGHT_PADDING_PX must be non-negative")


# Interface Group Constants
class InterfaceGroupConstants:
    """
    Constants specific to the Network Interfaces group in the settings dialog.
    """
    MAX_VISIBLE_INTERFACES: Final[int] = 10
    SCROLL_MIN_HEIGHT: Final[int] = 80
    SCROLL_MAX_HEIGHT_EMPTY: Final[int] = 60

    def validate(self) -> None:
        if not hasattr(self, 'MAX_VISIBLE_INTERFACES') or self.MAX_VISIBLE_INTERFACES <= 0:
            raise ValueError("MAX_VISIBLE_INTERFACES must be positive")
        if not hasattr(self, 'SCROLL_MIN_HEIGHT') or self.SCROLL_MIN_HEIGHT <= 0:
            raise ValueError("SCROLL_MIN_HEIGHT must be positive")
        if not hasattr(self, 'SCROLL_MAX_HEIGHT_EMPTY') or self.SCROLL_MAX_HEIGHT_EMPTY <= 0:
            raise ValueError("SCROLL_MAX_HEIGHT_EMPTY must be positive")


# Debug Constants
class DebugConstants:
    """
    Constants for enabling and configuring debugging visuals or logging.
    """
    def validate(self) -> None:
        """ Placeholder validation for debug constants. """
        pass


# Color Constants
class ColorConstants:
    """
    Defines common named colors used throughout the application.
    """
    WHITE: Final[str] = "#FFFFFF"
    BLACK: Final[str] = "#000000"
    GREEN: Final[str] = "#00FF00"
    ORANGE: Final[str] = "#FFA500"
    BLUE: Final[str] = "#0000FF"
    RED: Final[str] = "#FF0000"
    SOFT_BLUE: Final[str] = "#4287F5"
    SOFT_GREEN: Final[str] = "#42B883"
    APP_USAGE_PROGRESS_CHUNK: Final[str] = SOFT_GREEN
    APP_USAGE_PROGRESS_BG_DARK: Final[str] = "#333333"
    APP_USAGE_PROGRESS_BG_LIGHT: Final[str] = "#E0E0E0"

    def validate(self) -> None:
        colors = [
            self.WHITE, self.BLACK, self.GREEN, self.ORANGE, self.BLUE, self.RED,
            self.SOFT_BLUE, self.SOFT_GREEN, self.APP_USAGE_PROGRESS_CHUNK, 
            self.APP_USAGE_PROGRESS_BG_DARK, self.APP_USAGE_PROGRESS_BG_LIGHT
        ]
        for color in colors:
            if not (isinstance(color, str) and color.startswith("#") and len(color) == 7): # Added isinstance check
                raise ValueError(f"Color '{color}' in ColorConstants must be a 7-character hex string (e.g., #FFFFFF)")


# UI Style Constants
class UIStyleConstants:
    """
    Constants defining specific UI element styles, like colors and stylesheets.
    """
    DARK_MODE_BG_COLOR: Final[str] = "#2B2B2B"
    DARK_MODE_TEXT_COLOR: Final[str] = ColorConstants.WHITE
    LIGHT_MODE_BG_COLOR: Final[str] = ColorConstants.WHITE
    LIGHT_MODE_TEXT_COLOR: Final[str] = ColorConstants.BLACK
    BORDER_COLOR: Final[str] = "#505050"
    HOVER_BORDER_COLOR: Final[str] = ColorConstants.WHITE
    LEGEND_DARK_FRAME_COLOR: Final[str] = "#404040"
    APP_LIST_BG_DARK: Final[str] = "#1E1E1E"
    APP_LIST_BG_LIGHT: Final[str] = "#F5F5F5"
    HAMBURGER_DARK_STYLE: Final[str] = f"QPushButton {{ border: none; font-size: 16px; color: {DARK_MODE_TEXT_COLOR}; background: transparent; }}"
    HAMBURGER_LIGHT_STYLE: Final[str] = f"QPushButton {{ border: none; font-size: 16px; color: {DARK_MODE_TEXT_COLOR}; background: transparent; }}" # Note: Uses DARK_MODE_TEXT_COLOR for visibility on light bg
    STATS_DARK_STYLE: Final[str] = f"color: {DARK_MODE_TEXT_COLOR}; background-color: {DARK_MODE_BG_COLOR}; padding: 2px; font-size: 14px; border: none;"
    STATS_LIGHT_STYLE: Final[str] = f"color: {LIGHT_MODE_TEXT_COLOR}; background: transparent; padding: 2px; font-size: 14px; border: none;"
    SETTINGS_STYLE: Final[str] = f"background-color: {DARK_MODE_BG_COLOR};"
    LABEL_STYLE: Final[str] = f"color: {DARK_MODE_TEXT_COLOR};"
    BUTTON_STYLE: Final[str] = """
        QPushButton {
            background-color: #4CAF50;
            color: white;
            border-radius: 4px;
            padding: 5px 15px;
            border: 1px solid #39843C;
            min-height: 20px;
        }
        QPushButton:hover {
            background-color: #45a049;
        }
        QPushButton:pressed {
            background-color: #39843C;
        }
        QPushButton:disabled {
            background-color: #505050;
            color: #909090;
            border: 1px solid #444;
        }
    """
    SETTINGS_PANEL_BG_DARK: Final[str] = "#1E1E1E"
    SETTINGS_PANEL_BG_LIGHT: Final[str] = "#FFFFFF"
    SETTINGS_PANEL_TEXT_DARK: Final[str] = "#FFFFFF"
    SETTINGS_PANEL_TEXT_LIGHT: Final[str] = "#1F1F1F"
    GRAPH_BG_DARK: Final[str] = "#1E1E1E"
    GRAPH_BG_LIGHT: Final[str] = "#FFFFFF"
    GRAPH_TEXT_DARK: Final[str] = "#FFFFFF"
    GRAPH_TEXT_LIGHT: Final[str] = "#1F1F1F"
    COMBOBOX_BG_DARK: Final[str] = "#2D2D2D"
    COMBOBOX_BG_LIGHT: Final[str] = "#F3F3F3"
    COMBOBOX_BORDER_DARK: Final[str] = "#3D3D3D"
    COMBOBOX_BORDER_LIGHT: Final[str] = "#CCCCCC"

    def validate(self) -> None:
        colors = [
            self.DARK_MODE_BG_COLOR, self.DARK_MODE_TEXT_COLOR, self.LIGHT_MODE_BG_COLOR,
            self.LIGHT_MODE_TEXT_COLOR, self.LEGEND_DARK_FRAME_COLOR, self.BORDER_COLOR,
            self.HOVER_BORDER_COLOR, self.APP_LIST_BG_DARK, self.APP_LIST_BG_LIGHT,
            self.SETTINGS_PANEL_BG_DARK, self.SETTINGS_PANEL_BG_LIGHT,
            self.SETTINGS_PANEL_TEXT_DARK, self.SETTINGS_PANEL_TEXT_LIGHT,
            self.GRAPH_BG_DARK, self.GRAPH_BG_LIGHT, self.GRAPH_TEXT_DARK, self.GRAPH_TEXT_LIGHT,
            self.COMBOBOX_BG_DARK, self.COMBOBOX_BG_LIGHT,
            self.COMBOBOX_BORDER_DARK, self.COMBOBOX_BORDER_LIGHT
        ]
        for color_val in colors:
             if not (isinstance(color_val, str) and color_val.startswith("#") and len(color_val) == 7): # Added isinstance
                  raise ValueError(f"Invalid color format for '{color_val}' in UIStyleConstants. Expected 7-char hex.")
        styles = [
            self.HAMBURGER_DARK_STYLE, self.HAMBURGER_LIGHT_STYLE, self.STATS_DARK_STYLE,
            self.STATS_LIGHT_STYLE, self.SETTINGS_STYLE, self.LABEL_STYLE, self.BUTTON_STYLE
        ]
        for style_val in styles:
             if not style_val:
                  raise ValueError("Stylesheet constants in UIStyleConstants must not be empty.")


# Graph Constants
class GraphConstants:
    """
    Constants specific to the rendering and behavior of the network speed graph window.
    """
    FIGURE_SIZE: Final[Tuple[float, float]] = (8, 6)
    GRAPH_WIDGET_WIDTH: Final[int] = 802
    GRAPH_WIDGET_HEIGHT: Final[int] = 602
    MIN_Y_AXIS_LIMIT: Final[float] = 1.0
    HAMBURGER_ICON_SIZE: Final[int] = 24
    HAMBURGER_ICON_OFFSET_X: Final[int] = 5
    HAMBURGER_ICON_OFFSET_Y: Final[int] = 5
    STATS_BAR_OFFSET_X: Final[int] = HAMBURGER_ICON_OFFSET_X
    STATS_BAR_OFFSET_Y: Final[int] = HAMBURGER_ICON_OFFSET_Y + HAMBURGER_ICON_SIZE + 5
    SETTINGS_WIDGET_WIDTH: Final[int] = 250
    REALTIME_UPDATE_INTERVAL_MS: Final[int] = 1000
    GRAPH_UPDATE_THROTTLE_MS: Final[int] = 200
    MAX_DATA_POINTS: Final[int] = 500 # Consider if this is still needed with time-based filtering
    UPLOAD_LINE_COLOR: Final[str] = ColorConstants.SOFT_BLUE
    DOWNLOAD_LINE_COLOR: Final[str] = ColorConstants.SOFT_GREEN
    LINE_WIDTH: Final[float] = 1.5
    GRID_ALPHA: Final[float] = 0.5
    GRID_LINESTYLE: Final[str] = ":"
    TICK_MIN: Final[int] = 4
    TICK_MAX: Final[int] = 10
    LABEL_FONTSIZE: Final[int] = 8
    TITLE_FONTSIZE: Final[int] = 10
    ERROR_MESSAGE_COLOR: Final[str] = ColorConstants.RED
    ERROR_MESSAGE_FONTSIZE: Final[int] = 12
    STATS_UPDATE_INTERVAL: Final[float] = 1.0
    DATE_FORMAT_HOUR: Final[str] = "%H:%M"
    DATE_FORMAT_MULTIDAY: Final[str] = "%d-%m-%Y" # Changed to DD-MM-YYYY for clarity
    DATE_FORMAT_SWITCH_HOURS_SECONDS: Final[int] = 2
    DATE_FORMAT_SWITCH_DAYS_MINUTES: Final[int] = 1
    DATE_FORMAT_SWITCH_WEEKS_DAYS: Final[int] = 7
    DATE_FORMAT_SECONDS: Final[str] = "%H:%M:%S"
    DATE_FORMAT_DAY_ABBREV_TIME: Final[str] = "%a %H:%M"
    MIN_TIME_RANGE_SECONDS: Final[int] = 60
    X_AXIS_LABEL_ROTATION: Final[int] = 45
    X_AXIS_TICKS: Final[int] = 6
    WINDOW_TITLE: Final[str] = AppConstants.APP_NAME + " - Network Speed Graph"
   
    def validate(self) -> None:
    
        if len(self.FIGURE_SIZE) != 2 or any(dim <= 0 for dim in self.FIGURE_SIZE): raise ValueError("FIGURE_SIZE must be tuple of 2 positive floats")
        if self.GRAPH_WIDGET_HEIGHT <= 0: raise ValueError("GRAPH_WIDGET_HEIGHT must be positive")
        if not hasattr(self, 'DATE_FORMAT_HOUR') or not self.DATE_FORMAT_HOUR: raise ValueError("DATE_FORMAT_HOUR must be defined and not empty")
        if not hasattr(self, 'DATE_FORMAT_MULTIDAY') or not self.DATE_FORMAT_MULTIDAY: raise ValueError("DATE_FORMAT_MULTIDAY must be defined and not empty")
        if self.DATE_FORMAT_SWITCH_HOURS_SECONDS <= 0: raise ValueError("DATE_FORMAT_SWITCH_HOURS_SECONDS must be positive")
        if self.DATE_FORMAT_SWITCH_DAYS_MINUTES <= 0: raise ValueError("DATE_FORMAT_SWITCH_DAYS_MINUTES must be positive")
        if self.DATE_FORMAT_SWITCH_WEEKS_DAYS <= 0: raise ValueError("DATE_FORMAT_SWITCH_WEEKS_DAYS must be positive")
        if not self.DATE_FORMAT_SECONDS: raise ValueError("DATE_FORMAT_SECONDS must not be empty")
        if not self.DATE_FORMAT_DAY_ABBREV_TIME: raise ValueError("DATE_FORMAT_DAY_ABBREV_TIME must not be empty")
        if self.MIN_TIME_RANGE_SECONDS <=0: raise ValueError("MIN_TIME_RANGE_SECONDS must be positive")
        if not self.WINDOW_TITLE: raise ValueError("WINDOW_TITLE must not be empty")


# Export Constants
class ExportConstants:
    """
    Constants related to exporting data (CSV) and graphs (Images).
    """
    CSV_SUGGESTED_NAME_TEMPLATE: Final[str] = "nst_history_{timestamp}.csv"
    IMAGE_SUGGESTED_NAME_TEMPLATE: Final[str] = "nst_graph_{timestamp}.png"
    TIMESTAMP_FORMAT: Final[str] = "%Y%m%d_%H%M%S"
    IMAGE_DPI: Final[int] = 150

    def validate(self) -> None:
        if not self.CSV_SUGGESTED_NAME_TEMPLATE or "{timestamp}" not in self.CSV_SUGGESTED_NAME_TEMPLATE:
             raise ValueError("CSV_SUGGESTED_NAME_TEMPLATE must contain {timestamp}")
        if not self.IMAGE_SUGGESTED_NAME_TEMPLATE or "{timestamp}" not in self.IMAGE_SUGGESTED_NAME_TEMPLATE:
             raise ValueError("IMAGE_SUGGESTED_NAME_TEMPLATE must contain {timestamp}")
        if not self.TIMESTAMP_FORMAT:
             raise ValueError("TIMESTAMP_FORMAT must not be empty")
        if self.IMAGE_DPI <= 0:
            raise ValueError("IMAGE_DPI must be positive")


# Log Constants
class LogConstants:
    """
    Constants related to logging configuration and file management.
    """
    LOG_FILENAME: Final[str] = "NetSpeedTray_Log.log"
    LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"
    LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
    FILE_LOG_LEVEL: Final[int] = logging.DEBUG # For more detailed file logs
    CONSOLE_LOG_LEVEL: Final[int] = logging.INFO # For general console output
    PRODUCTION_LOG_LEVEL: Final[int] = logging.WARNING # For production builds

    def validate(self) -> None:
        if not self.LOG_FILENAME: raise ValueError("LOG_FILENAME must not be empty") # Check the new constant
        if not self.LOG_FORMAT: raise ValueError("LOG_FORMAT must not be empty")
        if not self.LOG_DATE_FORMAT: raise ValueError("LOG_DATE_FORMAT must not be empty")
        valid_levels = [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
        if self.FILE_LOG_LEVEL not in valid_levels: raise ValueError(f"Invalid FILE_LOG_LEVEL: {self.FILE_LOG_LEVEL}")
        if self.CONSOLE_LOG_LEVEL not in valid_levels: raise ValueError(f"Invalid CONSOLE_LOG_LEVEL: {self.CONSOLE_LOG_LEVEL}")
        if self.PRODUCTION_LOG_LEVEL not in valid_levels: raise ValueError(f"Invalid PRODUCTION_LOG_LEVEL: {self.PRODUCTION_LOG_LEVEL}")


# Run validation for all constant classes at module import time
def validate_all_constants() -> None:
    """
    Validates all defined constant classes upon module import.
    """
    print("Validating constants...")
    ordered_classes_to_validate = [
        AppConstants,
        TimerConstants, 
        HistoryPeriodConstants, 
        LegendPositionConstants, 
        DataRetentionConstants, 
        ConfigConstants, 
        ConfigMessages, 
        HelperConstants, 
        NetworkSpeedConstants,
        UnitConstants,
        TaskbarConstants, 
        PositionConstants,
        RendererConstants,
        WidgetStateConstants, 
        ControllerConstants,
        InterfaceConstants, 
        HistoryConstants,
        UIConstants,
        ComponentVisualConstants,
        DialogConstants,
        WindowsAPIConstants,
        FontConstants,
        LayoutConstants,
        InterfaceGroupConstants,
        DebugConstants,
        ColorConstants, 
        UIStyleConstants,
        GraphConstants, 
        ExportConstants,
        LogConstants,
        SliderConstants 
    ]

    validated_count = 0
    for cls_to_validate in ordered_classes_to_validate:
        if not hasattr(cls_to_validate, 'validate'):
            continue
        try:
            instance = cls_to_validate()
            instance.validate()
            validated_count += 1
        except Exception as e:
            print(f"Validation FAILED for {cls_to_validate.__name__}: {e}")
            raise ValueError(f"Constant validation failed for {cls_to_validate.__name__}") from e
    print(f"Constants validation successful ({validated_count} classes checked).")


# --- Execute Validation ---
validate_all_constants()    