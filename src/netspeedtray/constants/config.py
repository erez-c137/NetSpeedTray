"""
Constants for application configuration defaults and constraints.
"""
from typing import Final, Dict, Any

# --- IMPORT OTHER CONSTANTS TO CREATE A SINGLE SOURCE OF TRUTH ---
from .timers import timers
from .data import data
from .network import network
from .color import color
from .fonts import fonts


class ConfigMessages:
    # ... (no changes needed in this class)
    """Log message templates for configuration validation."""
    INVALID_NUMERIC: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'"
    INVALID_BOOLEAN: Final[str] = "Invalid {key} '{value}', resetting to boolean default '{default}'"
    INVALID_COLOR: Final[str] = "Invalid color '{value}' for {key}, resetting to default '{default}'"
    INVALID_CHOICE: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'. Valid choices: {choices}"
    INVALID_INTERFACES: Final[str] = "Invalid selected_interfaces value '{value}', resetting to default []"
    THRESHOLD_SWAP: Final[str] = "low_speed_threshold > high_speed_threshold, setting low to high's value"
    INVALID_POSITION: Final[str] = "Invalid {key} '{value}', resetting to None"


    def __init__(self) -> None:
        pass # Validation is not strictly necessary for simple string holders


class ConfigConstants:
    """Defines default values and constraints for all application settings."""
    # --- Default Values for Individual Settings (referencing other constants) ---
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    MINIMUM_UPDATE_RATE: Final[float] = timers.MINIMUM_INTERVAL_MS / 1000.0
    DEFAULT_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_FONT_SIZE: Final[int] = 9
    DEFAULT_FONT_WEIGHT: Final[int] = fonts.WEIGHT_DEMIBOLD
    DEFAULT_COLOR: Final[str] = color.WHITE # Referencing color palette
    DEFAULT_COLOR_CODING: Final[bool] = False
    DEFAULT_HIGH_SPEED_THRESHOLD: Final[float] = 5.0
    DEFAULT_LOW_SPEED_THRESHOLD: Final[float] = 1.0
    DEFAULT_HIGH_SPEED_COLOR: Final[str] = color.GREEN # Referencing color palette
    DEFAULT_LOW_SPEED_COLOR: Final[str] = color.ORANGE # Referencing color palette
    DEFAULT_GRAPH_ENABLED: Final[bool] = False
    DEFAULT_HISTORY_MINUTES: Final[int] = 30
    DEFAULT_GRAPH_OPACITY: Final[int] = 30
    DEFAULT_INTERFACE_MODE: Final[str] = network.interface.DEFAULT_MODE
    DEFAULT_KEEP_DATA_DAYS: Final[int] = data.retention.DAYS_MAP[6] # 365 days (1 Year) default
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "auto"
    DEFAULT_UNIT_TYPE: Final[str] = "bytes_binary"  # Most users prefer MB/s (Bytes) akin to file downloads
    DEFAULT_SWAP_UPLOAD_DOWNLOAD: Final[bool] = True # Download on top is standard convention
    DEFAULT_FIXED_WIDTH_VALUES: Final[bool] = False
    DEFAULT_HIDE_ARROWS: Final[bool] = False
    DEFAULT_HIDE_UNIT_SUFFIX: Final[bool] = False
    DEFAULT_DECIMAL_PLACES: Final[int] = 2
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"
    DEFAULT_FREE_MOVE: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    DEFAULT_START_WITH_WINDOWS: Final[bool] = True
    DEFAULT_TRAY_OFFSET_X: Final[int] = 10
    DEFAULT_LEGEND_POSITION: Final[str] = data.legend_position.DEFAULT_LEGEND_POSITION
    DEFAULT_SHOW_LEGEND: Final[bool] = True

    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    
    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
        "start_with_windows": DEFAULT_START_WITH_WINDOWS,
        "language": None,  # None means auto-detect
        "update_rate": DEFAULT_UPDATE_RATE,
        "font_family": DEFAULT_FONT_FAMILY,
        "font_size": DEFAULT_FONT_SIZE,
        "font_weight": DEFAULT_FONT_WEIGHT,
        "color_coding": DEFAULT_COLOR_CODING,
        "default_color": DEFAULT_COLOR,
        "color_is_automatic": True,
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
        "keep_data": DEFAULT_KEEP_DATA_DAYS,
        "dark_mode": DEFAULT_DARK_MODE,
        "history_period": data.history_period.DEFAULT_PERIOD,
        "legend_position": DEFAULT_LEGEND_POSITION,
        "position_x": None,
        "position_y": None,
        "paused": False,
        "dynamic_update_enabled": DEFAULT_DYNAMIC_UPDATE_ENABLED,
        "speed_display_mode": DEFAULT_SPEED_DISPLAY_MODE,
        "decimal_places": DEFAULT_DECIMAL_PLACES,
        "text_alignment": DEFAULT_TEXT_ALIGNMENT,
        "free_move": DEFAULT_FREE_MOVE,
        "force_decimals": DEFAULT_FORCE_DECIMALS,
        "unit_type": DEFAULT_UNIT_TYPE,
        "swap_upload_download": DEFAULT_SWAP_UPLOAD_DOWNLOAD,
        "fixed_width_values": DEFAULT_FIXED_WIDTH_VALUES,
        "hide_arrows": DEFAULT_HIDE_ARROWS,
        "hide_unit_suffix": DEFAULT_HIDE_UNIT_SUFFIX,
        "tray_offset_x": DEFAULT_TRAY_OFFSET_X,
        "graph_window_pos": None,
        "history_period_slider_value": 0,  # UI-specific state
        "show_legend": False,
    }


    def __init__(self) -> None:
        self.validate()


    def validate(self) -> None:
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
            "color_coding", "default_color", "color_is_automatic", "high_speed_threshold",
            "low_speed_threshold", "high_speed_color", "low_speed_color", "graph_enabled",
            "history_minutes", "graph_opacity", "interface_mode", "selected_interfaces",
            "excluded_interfaces", "keep_data", "dark_mode", "position_x", "position_y",
            "paused", "dynamic_update_enabled", "speed_display_mode", "decimal_places",
            "text_alignment", "free_move", "force_decimals", "tray_offset_x",
            "unit_type", "swap_upload_download", "fixed_width_values",
            "hide_arrows", "hide_unit_suffix",
            "graph_window_pos", "history_period_slider_value",
            "history_period", "legend_position", "show_legend"
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