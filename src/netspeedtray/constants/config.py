"""
Constants for application configuration defaults and constraints.
"""
from typing import Final, Dict, Any

from .timers import timers
from .data import data
from .network import network

class ConfigMessages:
    # ... (no changes in this class)
    """Log message templates for configuration validation."""
    INVALID_NUMERIC: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'"
    INVALID_BOOLEAN: Final[str] = "Invalid {key} '{value}', resetting to boolean default '{default}'"
    INVALID_COLOR: Final[str] = "Invalid color '{value}' for {key}, resetting to default '{default}'"
    INVALID_CHOICE: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'. Valid choices: {choices}"
    INVALID_INTERFACES: Final[str] = "Invalid selected_interfaces value '{value}', resetting to default []"
    THRESHOLD_SWAP: Final[str] = "low_speed_threshold > high_speed_threshold, setting low to high's value"
    INVALID_POSITION: Final[str] = "Invalid {key} '{value}', resetting to None"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if not isinstance(value, str) or not value:
                    raise ValueError(f"ConfigMessages.{attr_name} must be a non-empty string.")


class ConfigConstants:
    """Defines default values and constraints for all application settings."""
    # --- Default Values for Individual Settings ---
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    MINIMUM_UPDATE_RATE: Final[float] = timers.MINIMUM_INTERVAL_MS / 1000.0
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
    DEFAULT_INTERFACE_MODE: Final[str] = network.interface.DEFAULT_MODE
    DEFAULT_HISTORY_PERIOD_DAYS: Final[int] = data.retention.DAYS_MAP[6]
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_LEGEND_POSITION: Final[str] = data.legend_position.DEFAULT_LEGEND_POSITION
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "always_mbps"
    DEFAULT_DECIMAL_PLACES: Final[int] = 2
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"
    DEFAULT_FREE_MOVE: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    DEFAULT_START_WITH_WINDOWS: Final[bool] = True

    # ADDED all missing default values
    DEFAULT_MIN_UPDATE_RATE: Final[float] = MINIMUM_UPDATE_RATE
    DEFAULT_MAX_UPDATE_RATE: Final[float] = timers.MAXIMUM_UPDATE_RATE_SECONDS
    DEFAULT_TRAY_OFFSET_X: Final[int] = 10
    MINIMUM_HISTORY_POINTS: Final[int] = 10
    MAXIMUM_HISTORY_POINTS: Final[int] = 5000
    DEFAULT_HISTORY_POINTS: Final[int] = int(round((DEFAULT_HISTORY_MINUTES * 60) / DEFAULT_UPDATE_RATE))

    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    
    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
        "start_with_windows": DEFAULT_START_WITH_WINDOWS,
        "language": None,
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
        "excluded_interfaces": network.interface.DEFAULT_EXCLUSIONS,
        "keep_data": DEFAULT_HISTORY_PERIOD_DAYS,
        "dark_mode": DEFAULT_DARK_MODE,
        "history_period": data.history_period.DEFAULT_PERIOD,
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
        "graph_window_pos": None,
        "history_period_slider_value": 0,
    }

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        # ... (validation method is unchanged)
        if self.DEFAULT_UPDATE_RATE < self.MINIMUM_UPDATE_RATE:
            raise ValueError(f"DEFAULT_UPDATE_RATE must be >= {self.MINIMUM_UPDATE_RATE}")
        if self.DEFAULT_FONT_SIZE < 1:
            raise ValueError("DEFAULT_FONT_SIZE must be positive")
        if not (0 <= self.DEFAULT_GRAPH_OPACITY <= 100):
            raise ValueError("DEFAULT_GRAPH_OPACITY must be between 0 and 100")
        if not self.CONFIG_FILENAME:
             raise ValueError("CONFIG_FILENAME must not be empty")

        actual_keys = set(self.DEFAULT_CONFIG.keys())
        expected_keys = {
            "start_with_windows", "language", "update_rate", "font_family", "font_size", "font_weight",
            "color_coding", "default_color", "high_speed_threshold", "low_speed_threshold",
            "high_speed_color", "low_speed_color", "graph_enabled", "history_minutes",
            "graph_opacity", "interface_mode", "selected_interfaces", "excluded_interfaces",
            "keep_data", "dark_mode", "history_period", "legend_position", "position_x", "position_y",
            "paused", "dynamic_update_enabled", "min_update_rate", "max_update_rate",
            "speed_display_mode", "decimal_places", "text_alignment", "free_move",
            "force_decimals", "tray_offset_x", "graph_window_pos", "history_period_slider_value"
        }
        if actual_keys != expected_keys:
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            raise ValueError(f"DEFAULT_CONFIG key mismatch. Missing: {missing or 'None'}. Extra: {extra or 'None'}.")


class ConfigurationConstants:
    """Container for configuration-related constant groups."""
    def __init__(self) -> None:
        self.defaults = ConfigConstants()
        self.messages = ConfigMessages()

# Singleton instance for easy access
config = ConfigurationConstants()